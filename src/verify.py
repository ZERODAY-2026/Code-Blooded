"""
Deterministic Claim-to-Cell Verifier (AGENTS.md, Sections 4, 6, 11, 12).

Architectural Rules:
  - Gemma writes prose only; Gemma NEVER selects evidence cells or computes numbers.
  - Code extracts claims (capitalized entity names, numbers, relational comparisons) from Gemma's prose.
  - Code matches claim-level entity/value pairs to the EXACT same result row.
  - Relational claims ("highest", "lowest", "increased", "decreased", "doubled") are validated deterministically.
  - Years in SQL filters and row counts are exempt from mismatch penalties.
  - Unmatched or hallucinated claims yield a deterministic fallback sentence.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field


class EvidenceItem(dict):
    """An inspectable evidence cell matching a claim in the prose.

    Conforms to Section 11 API contract:
        {"text": "84.4", "row_index": 0, "column": "life_expectancy"}
    Supports both dict indexing (item["text"]) and attribute access (item.text).
    """

    def __init__(self, text: str, row_index: int, column: str, **kwargs: Any) -> None:
        super().__init__(text=str(text), row_index=int(row_index), column=str(column), **kwargs)

    @property
    def text(self) -> str:
        return self["text"]

    @property
    def row_index(self) -> int:
        return self["row_index"]

    @property
    def column(self) -> str:
        return self["column"]

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "row_index": self.row_index, "column": self.column}


class VerificationResult(BaseModel):
    """Outcome of verifying Gemma's prose against Snowflake query rows."""

    is_verified: bool
    confidence: str  # "verified", "partial", "unverifiable"
    evidence: List[EvidenceItem] = Field(default_factory=list)
    fallback_sentence: str
    unmatched_claims: List[str] = Field(default_factory=list)
    verified_prose: str

    model_config = {"arbitrary_types_allowed": True}

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


# ── Stopwords and Non-Entity Tokens ──────────────────────────────────────────

STOPWORDS: Set[str] = {
    "A", "An", "The", "In", "On", "At", "By", "For", "With", "About", "Against",
    "Between", "Into", "Through", "During", "Before", "After", "Above", "Below",
    "To", "From", "Up", "Down", "Of", "Off", "Over", "Under", "Again", "Further",
    "Then", "Once", "Here", "There", "When", "Where", "Why", "How", "All", "Any",
    "Both", "Each", "Few", "More", "Most", "Other", "Some", "Such", "No", "Nor",
    "Not", "Only", "Own", "Same", "So", "Than", "Too", "Very", "Can", "Will",
    "Just", "Don", "Should", "Now", "According", "However", "Although", "Top",
    "Bottom", "Result", "Results", "Overall", "Total", "Average", "Mean", "Median",
    "First", "Second", "Third", "Last", "Table", "Data", "Source", "Query", "Snowflake",
    "This", "That", "These", "Those", "It", "Its", "They", "Their", "We", "Our",
    "He", "His", "She", "Her", "Who", "Whom", "Which", "What", "Whose", "As", "And",
    "Or", "But", "If", "Because", "While", "Followed", "Based", "Highest", "Lowest",
    "Largest", "Smallest", "Maximum", "Minimum", "Least", "Most",
}

MONTHS: Set[str] = {
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
}

UNITS_AND_TERMS: Set[str] = {
    "Year", "Years", "Percent", "Percentage", "Dollars", "USD", "Metric",
    "Tons", "Gdp", "Capita", "Co2", "Population", "Life", "Expectancy",
}


# ── Number and Token Extraction ──────────────────────────────────────────────

def _extract_numbers(text: str) -> List[Tuple[str, float]]:
    """Extract numeric tokens and their float equivalents from text."""
    pattern = r"(?<![#\w])\b\d+(?:,\d{3})*(?:\.\d+)?\b"
    matches = []
    for m in re.finditer(pattern, text):
        raw_str = m.group(0)
        clean_str = raw_str.replace(",", "")
        try:
            val = float(clean_str)
            matches.append((raw_str, val))
        except ValueError:
            continue
    return matches


def _extract_exempt_years_from_sql(sql: str) -> Set[int]:
    """Extract 4-digit calendar years from the SQL query (e.g. WHERE year = 2019)."""
    if not sql:
        return set()
    years = set()
    for m in re.finditer(r"\b(19\d{2}|20\d{2})\b", sql):
        try:
            years.add(int(m.group(0)))
        except ValueError:
            pass
    return years


def _extract_capitalized_entities(text: str, columns: List[str]) -> List[str]:
    """Extract candidate capitalized entity names (e.g. 'Japan', 'United States') from prose."""
    pattern = r"\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*\b"
    col_terms = {c.lower().replace("_", " ") for c in columns}
    col_tokens = {tok.lower() for c in columns for tok in c.split("_")}

    candidates = []
    for m in re.finditer(pattern, text):
        raw = m.group(0).strip()
        tokens = raw.split()
        # Skip if all tokens are stopwords, months, or units
        if all(
            t in STOPWORDS or t in MONTHS or t in UNITS_AND_TERMS or t.lower() in col_tokens
            for t in tokens
        ):
            continue
        # Skip if matches column name
        if raw.lower() in col_terms:
            continue
        candidates.append(raw)
    return candidates


# ── Relational Comparisons ───────────────────────────────────────────────────

def _is_dimension_column(col_name: str) -> bool:
    """Check if a column is a dimension or identifier rather than a metric."""
    lower = col_name.lower()
    return any(
        kw in lower
        for kw in ["year", "date", "id", "key", "code", "entity", "name", "country", "comment", "nation"]
    )


def _check_relational_claim(
    text: str,
    columns: List[str],
    rows: List[List[Any]],
) -> Tuple[bool, Optional[str]]:
    """Check whether superlative, comparative, or directional claims match the data deterministically."""
    text_lower = text.lower()
    if not rows:
        return True, None

    # Identify metric columns
    metric_col_indices = []
    for col_idx, col_name in enumerate(columns):
        if not _is_dimension_column(col_name):
            if any(isinstance(r[col_idx], (int, float)) for r in rows if r[col_idx] is not None):
                metric_col_indices.append(col_idx)

    if not metric_col_indices:
        for col_idx, col_name in enumerate(columns):
            if any(isinstance(r[col_idx], float) for r in rows if r[col_idx] is not None):
                metric_col_indices.append(col_idx)

    # 1. Superlatives: highest / largest / maximum / most
    if re.search(r"\b(highest|largest|maximum|most)\b", text_lower):
        if metric_col_indices:
            col_idx = metric_col_indices[0]
            val_0 = rows[0][col_idx]
            max_val = max((r[col_idx] for r in rows if r[col_idx] is not None), default=val_0)
            if val_0 is not None and max_val is not None and val_0 < max_val:
                return False, f"Prose claimed 'highest/largest', but top row value ({val_0}) is not the maximum ({max_val})"

    # 2. Superlatives: lowest / smallest / minimum / least
    if re.search(r"\b(lowest|smallest|minimum|least)\b", text_lower):
        if metric_col_indices:
            col_idx = metric_col_indices[0]
            val_0 = rows[0][col_idx]
            min_val = min((r[col_idx] for r in rows if r[col_idx] is not None), default=val_0)
            if val_0 is not None and min_val is not None and val_0 > min_val:
                return False, f"Prose claimed 'lowest/smallest', but top row value ({val_0}) is not the minimum ({min_val})"

    # 3. Directional / Trend: increased / increase / rose / grew / doubled
    claimed_increase = bool(re.search(r"\b(increased|increase|grew|growth|rose|risen)\b", text_lower))
    claimed_decrease = bool(re.search(r"\b(decreased|decrease|dropped|fell|declined)\b", text_lower))
    claimed_doubled = bool(re.search(r"\b(doubled|double)\b", text_lower))

    if claimed_increase or claimed_decrease or claimed_doubled:
        # Find year/date column
        time_col_idx = None
        for col_idx, col_name in enumerate(columns):
            if any(kw in col_name.lower() for kw in ["year", "date", "time"]):
                time_col_idx = col_idx
                break

        if time_col_idx is None or len(rows) < 2 or not metric_col_indices:
            return (
                False,
                "Prose claimed a comparative trend ('increased/decreased'), but query results do not contain comparative historical rows",
            )

        # Evaluate trend chronologically
        m_idx = metric_col_indices[0]
        # Sort rows by time column
        sorted_rows = sorted(
            [r for r in rows if r[time_col_idx] is not None and r[m_idx] is not None],
            key=lambda r: r[time_col_idx],
        )
        if len(sorted_rows) < 2:
            return (
                False,
                "Prose claimed a comparative trend ('increased/decreased'), but fewer than 2 valid historical timepoints exist",
            )

        val_earliest = float(sorted_rows[0][m_idx])
        val_latest = float(sorted_rows[-1][m_idx])

        if claimed_increase and val_latest <= val_earliest:
            return False, f"Prose claimed 'increased', but metric did not increase (earliest: {val_earliest}, latest: {val_latest})"

        if claimed_decrease and val_latest >= val_earliest:
            return False, f"Prose claimed 'decreased', but metric did not decrease (earliest: {val_earliest}, latest: {val_latest})"

        if claimed_doubled and val_latest < 2.0 * val_earliest:
            return False, f"Prose claimed 'doubled', but latest ({val_latest}) is less than 2x earliest ({val_earliest})"

    return True, None


# ── Deterministic Fallback Generator ─────────────────────────────────────────

def build_fallback_sentence(columns: List[str], rows: List[List[Any]]) -> str:
    """Generate a deterministic, verified summary directly from rows without LLM.

    Format matches Section 6 & 11 specification:
        e.g. "Top result: Japan (life_expectancy: 84.4)."
        e.g. "Result: Japan (life_expectancy: 84.4)."
    """
    if not rows:
        return "The query returned no matching rows."

    # Identify primary entity column (e.g. entity, country, name, or first string col)
    entity_col_idx = None
    for idx, col in enumerate(columns):
        if any(kw in col.lower() for kw in ["entity", "country", "name", "nation"]):
            entity_col_idx = idx
            break
    if entity_col_idx is None:
        for idx, col in enumerate(columns):
            if any(isinstance(r[idx], str) for r in rows if r[idx] is not None):
                entity_col_idx = idx
                break

    target_row = rows[0]

    if entity_col_idx is not None and target_row[entity_col_idx] is not None:
        entity_val = str(target_row[entity_col_idx])
        metric_parts = [
            f"{col}: {target_row[idx]}"
            for idx, col in enumerate(columns)
            if idx != entity_col_idx and target_row[idx] is not None
        ]
        if metric_parts:
            detail = f"{entity_val} ({', '.join(metric_parts)})"
        else:
            detail = entity_val
    else:
        parts = [f"{col}: {val}" for col, val in zip(columns, target_row) if val is not None]
        detail = ", ".join(parts)

    prefix = "Result:" if len(rows) == 1 else "Top result:"
    return f"{prefix} {detail}."


# ── Helper for Flexible Input Handling ───────────────────────────────────────

def _normalize_rows_input(
    rows_or_columns: Any,
    rows_or_sql: Any = None,
    sql: str = "",
    columns: Optional[List[str]] = None,
) -> Tuple[List[str], List[List[Any]], str]:
    """Normalize input formats (dict with columns/data, separate columns and rows, or list of dicts)."""
    # 1. Dict input: {"columns": [...], "data": [...]}
    if isinstance(rows_or_columns, dict):
        cols = list(rows_or_columns.get("columns", []))
        data = rows_or_columns.get("data")
        if data is None:
            data = rows_or_columns.get("rows", [])
        executed_sql = rows_or_sql if isinstance(rows_or_sql, str) else sql
        return cols, list(data), executed_sql

    # 2. Separate columns and rows: (columns: List[str], rows: List[List[Any]], sql: str)
    if isinstance(rows_or_columns, list) and isinstance(rows_or_sql, list):
        return list(rows_or_columns), list(rows_or_sql), sql

    # 3. List of row dicts: [{"entity": "Japan", "life_expectancy": 84.4}]
    if isinstance(rows_or_columns, list) and len(rows_or_columns) > 0 and isinstance(rows_or_columns[0], dict):
        cols = list(rows_or_columns[0].keys())
        data = [[row.get(c) for c in cols] for row in rows_or_columns]
        executed_sql = rows_or_sql if isinstance(rows_or_sql, str) else sql
        return cols, data, executed_sql

    # 4. Data rows with explicit columns param
    if isinstance(rows_or_columns, list) and columns is not None:
        executed_sql = rows_or_sql if isinstance(rows_or_sql, str) else sql
        return list(columns), list(rows_or_columns), executed_sql

    cols = list(columns) if columns else []
    data = list(rows_or_columns) if isinstance(rows_or_columns, list) else []
    executed_sql = rows_or_sql if isinstance(rows_or_sql, str) else sql
    return cols, data, executed_sql


# ── Main Verifier Functions ──────────────────────────────────────────────────

def verify_answer(
    prose: str,
    columns_or_rows: Any,
    rows_or_sql: Any = None,
    sql: str = "",
) -> VerificationResult:
    """Verify claim-level entity/value pairs from Gemma against query rows.

    Args:
        prose: Output prose written by Gemma.
        columns_or_rows: Column names list OR a rows dictionary {"columns": [...], "data": [...]}.
        rows_or_sql: Rows list [[...], ...] OR SQL string if rows_dict was passed.
        sql: Executed SQL query (used to exempt filter years/constants).

    Returns:
        VerificationResult with evidence cells, confidence, and fallback prose.
    """
    columns, rows, executed_sql = _normalize_rows_input(columns_or_rows, rows_or_sql, sql)
    fallback = build_fallback_sentence(columns, rows)

    if not rows:
        return VerificationResult(
            is_verified=True,
            confidence="verified",
            evidence=[],
            fallback_sentence=fallback,
            unmatched_claims=[],
            verified_prose=fallback,
        )

    # 1. Exempt years and counts
    exempt_years = _extract_exempt_years_from_sql(executed_sql)
    row_count = len(rows)

    # 2. Extract numbers and capitalized entity candidates from prose
    number_claims = _extract_numbers(prose)
    candidate_entities = _extract_capitalized_entities(prose, columns)

    evidence: List[EvidenceItem] = []
    matched_cell_coords: Set[Tuple[int, str]] = set()
    unmatched_claims: List[str] = []

    # Map all string and number values in the rows for fast lookup
    # cell_lookup: {(row_idx, col_name): (raw_value, float_value_or_none, string_lower)}
    row_cells: List[Dict[str, Tuple[Any, Optional[float], str]]] = []
    all_table_strings: Dict[str, List[Tuple[int, str, Any]]] = {}

    for r_idx, row in enumerate(rows):
        col_map = {}
        for c_idx, col_name in enumerate(columns):
            val = row[c_idx] if c_idx < len(row) else None
            flt_val = None
            if isinstance(val, (int, float)):
                flt_val = float(val)
            str_val = str(val).strip() if val is not None else ""
            col_map[col_name] = (val, flt_val, str_val.lower())

            if str_val and flt_val is None:
                lower_s = str_val.lower()
                all_table_strings.setdefault(lower_s, []).append((r_idx, col_name, val))
        row_cells.append(col_map)

    # 3. Check for hallucinated capitalized entities in prose
    # If prose mentions an entity not found in any row string cell
    for candidate in candidate_entities:
        c_lower = candidate.lower()
        if executed_sql and c_lower in executed_sql.lower():
            continue
        matched_in_table = any(
            c_lower == s or c_lower in s or s in c_lower
            for s in all_table_strings
        )
        if not matched_in_table:
            unmatched_claims.append(f"Entity '{candidate}' not found in returned rows")

    # 4. Verify each number claim in prose
    for raw_str, val in number_claims:
        # Check exemption: is it an exempt SQL year?
        if int(val) in exempt_years and val.is_integer():
            continue
        # Check exemption: is it the row count or limit?
        if int(val) == row_count and val.is_integer() and (f"{int(val)} " in prose.lower()):
            continue

        # Find matching cell
        found = False
        for r_idx, col_map in enumerate(row_cells):
            for col_name, (orig, flt, str_val) in col_map.items():
                if flt is not None:
                    # Floating-point tolerance check (within 0.01 or 0.1%)
                    if abs(flt - val) < 0.01 or (val != 0 and abs(flt - val) / abs(val) < 0.001):
                        found = True
                        if (r_idx, col_name) not in matched_cell_coords:
                            matched_cell_coords.add((r_idx, col_name))
                            evidence.append(
                                EvidenceItem(
                                    text=raw_str,
                                    row_index=r_idx,
                                    column=col_name,
                                )
                            )
                        break
            if found:
                break

        if not found:
            for r_idx, col_map in enumerate(row_cells):
                for col_name, (orig, flt, str_val) in col_map.items():
                    if str_val and (raw_str.lower() in str_val or str_val in raw_str.lower()):
                        found = True
                        break
                if found:
                    break

        if not found:
            unmatched_claims.append(f"Number {raw_str} not found in returned rows")

    # 5. Extract and add confirmed entity names appearing in prose to evidence
    for r_idx, col_map in enumerate(row_cells):
        for col_name, (orig, flt, str_val) in col_map.items():
            if str_val and len(str_val) >= 2 and flt is None:
                pattern = rf"\b{re.escape(str_val)}\b"
                match = re.search(pattern, prose, re.IGNORECASE)
                if match:
                    if (r_idx, col_name) not in matched_cell_coords:
                        matched_cell_coords.add((r_idx, col_name))
                        evidence.append(
                            EvidenceItem(
                                text=match.group(0),
                                row_index=r_idx,
                                column=col_name,
                            )
                        )

    # 6. Entity-Value Pair Consistency Check across clauses
    clauses = re.split(r"(?<!\d)\.(?!\d)|[,;]|\band\b|\bwhile\b|\bfollowed by\b|\bwhereas\b", prose, flags=re.IGNORECASE)
    last_seen_entity_rows: Set[int] = set()
    last_seen_entity_name: Optional[str] = None

    for clause in clauses:
        clause_numbers = _extract_numbers(clause)
        # Find entities mentioned in this clause
        clause_entities: List[Tuple[str, int]] = []
        for r_idx, col_map in enumerate(row_cells):
            for col_name, (orig, flt, str_val) in col_map.items():
                if str_val and len(str_val) >= 2 and flt is None:
                    if re.search(rf"\b{re.escape(str_val)}\b", clause, re.IGNORECASE):
                        clause_entities.append((str(orig), r_idx))

        if clause_entities:
            last_seen_entity_name = clause_entities[0][0]
            last_seen_entity_rows = {r for name, r in clause_entities if name.lower() == last_seen_entity_name.lower()}

        # For every number in this clause, check against the active entity
        for raw_num, num_val in clause_numbers:
            if int(num_val) in exempt_years or int(num_val) == row_count:
                continue

            num_rows = {
                r_idx
                for r_idx, col_map in enumerate(row_cells)
                if any(flt is not None and (abs(flt - num_val) < 0.01 or (num_val != 0 and abs(flt - num_val) / abs(num_val) < 0.001)) for _, flt, _ in col_map.values())
            }
            if not num_rows:
                continue

            # Target entity is either in this clause, or inherited from preceding clause
            target_entity_name = clause_entities[0][0] if clause_entities else last_seen_entity_name
            target_entity_rows = (
                {r for name, r in clause_entities if name.lower() == target_entity_name.lower()}
                if clause_entities
                else last_seen_entity_rows
            )

            if target_entity_rows and not (target_entity_rows & num_rows):
                unmatched_claims.append(
                    f"Entity '{target_entity_name}' (rows {sorted(target_entity_rows)}) mismatched with value '{raw_num}' (found in rows {sorted(num_rows)})"
                )

    # 7. Relational claim check
    rel_ok, rel_error = _check_relational_claim(prose, columns, rows)
    if not rel_ok and rel_error:
        unmatched_claims.append(rel_error)

    # 8. Decide confidence and final prose
    if not unmatched_claims:
        confidence = "verified"
        is_verified = True
        final_prose = prose
    elif evidence:
        confidence = "partial"
        is_verified = False
        final_prose = fallback
    else:
        confidence = "unverifiable"
        is_verified = False
        final_prose = fallback

    return VerificationResult(
        is_verified=is_verified,
        confidence=confidence,
        evidence=evidence,
        fallback_sentence=fallback,
        unmatched_claims=unmatched_claims,
        verified_prose=final_prose,
    )


def verify(
    prose: str,
    rows: Any,
    sql: str = "",
    columns: Optional[List[str]] = None,
) -> VerificationResult:
    """Convenience wrapper accepting (prose, rows_dict, sql) matching Person A task description.

    Args:
        prose: Gemma's prose string.
        rows: Rows dictionary {"columns": [...], "data": [...]} or list of rows.
        sql: Executed SQL query.
        columns: Optional list of column names if rows is raw list.
    """
    if columns is not None:
        return verify_answer(prose, columns_or_rows=columns, rows_or_sql=rows, sql=sql)
    return verify_answer(prose, columns_or_rows=rows, rows_or_sql=sql, sql=sql)
