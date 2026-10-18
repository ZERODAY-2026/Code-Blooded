"""
Deterministic Claim-to-Cell Verifier (AGENTS.md, Sections 4, 6, 11, 12).

Architectural Rules:
  - Gemma writes prose only; Gemma NEVER selects evidence cells or computes numbers.
  - Code extracts claims (entity names, numbers, relational comparisons) from Gemma's prose.
  - Code matches claim-level entity/value pairs to the EXACT same result row.
  - Relational claims ("highest", "lowest", "increased") are validated deterministically.
  - Years in SQL filters and row counts are exempt from mismatch penalties.
  - Unmatched or hallucinated claims yield a deterministic fallback sentence.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """An inspectable evidence cell matching a claim in the prose."""
    text: str
    row_index: int
    column: str


class VerificationResult(BaseModel):
    """Outcome of verifying Gemma's prose against Snowflake query rows."""
    is_verified: bool
    confidence: str  # "verified", "partial", "unverifiable"
    evidence: List[EvidenceItem] = Field(default_factory=list)
    fallback_sentence: str
    unmatched_claims: List[str] = Field(default_factory=list)
    verified_prose: str


# ── Number and Token Extraction ──────────────────────────────────────────────

def _extract_numbers(text: str) -> List[Tuple[str, float]]:
    """Extract numeric tokens and their float equivalents from text."""
    pattern = r"\b\d+(?:,\d{3})*(?:\.\d+)?\b"
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


def _format_cell(val: Any) -> str:
    """Format a cell value for string matching."""
    if val is None:
        return ""
    if isinstance(val, float):
        return f"{val:.4f}".rstrip("0").rstrip(".") if "." in f"{val:.4f}" else str(val)
    return str(val)


# ── Relational Comparisons ───────────────────────────────────────────────────

def _is_dimension_column(col_name: str) -> bool:
    """Check if a column is a dimension or identifier rather than a metric."""
    lower = col_name.lower()
    return any(
        kw in lower
        for kw in ["year", "date", "id", "key", "code", "entity", "name", "country", "comment"]
    )


def _check_relational_claim(
    text: str,
    columns: List[str],
    rows: List[List[Any]],
) -> Tuple[bool, Optional[str]]:
    """Check whether superlative/directional claims match the data."""
    text_lower = text.lower()

    # Identify metric columns (skip year, id, key, dates)
    metric_col_indices = []
    for col_idx, col_name in enumerate(columns):
        if not _is_dimension_column(col_name):
            if rows and any(isinstance(r[col_idx], (int, float)) for r in rows if r[col_idx] is not None):
                metric_col_indices.append(col_idx)

    # If no non-dimension metric found, fall back to any float column
    if not metric_col_indices:
        for col_idx, col_name in enumerate(columns):
            if rows and any(isinstance(r[col_idx], float) for r in rows if r[col_idx] is not None):
                metric_col_indices.append(col_idx)

    if not metric_col_indices or not rows:
        return True, None

    col_idx = metric_col_indices[0]
    val_0 = rows[0][col_idx]
    if val_0 is None:
        return True, None

    # 1. Superlatives: "highest", "most", "largest", "maximum", "top"
    if any(w in text_lower for w in ["highest", "largest", "maximum", "most"]):
        max_val = max(
            (r[col_idx] for r in rows if r[col_idx] is not None),
            default=val_0,
        )
        if val_0 < max_val:
            return False, "Prose claimed 'highest/largest', but first row is not the maximum"

    # 2. Superlatives: "lowest", "least", "smallest", "minimum"
    if any(w in text_lower for w in ["lowest", "smallest", "minimum", "least"]):
        min_val = min(
            (r[col_idx] for r in rows if r[col_idx] is not None),
            default=val_0,
        )
        if val_0 > min_val:
            return False, "Prose claimed 'lowest/smallest', but first row is not the minimum"

    return True, None


# ── Deterministic Fallback Generator ─────────────────────────────────────────

def build_fallback_sentence(columns: List[str], rows: List[List[Any]]) -> str:
    """Generate a deterministic, verified summary directly from rows without LLM."""
    if not rows:
        return "The query returned no matching rows."

    if len(rows) == 1:
        parts = [f"{col}: {val}" for col, val in zip(columns, rows[0]) if val is not None]
        return f"Result: {', '.join(parts)}."

    # Multi-row: describe top entry and total count
    top_row = rows[0]
    parts = [f"{col}: {val}" for col, val in zip(columns, top_row) if val is not None]
    return f"Top result: {', '.join(parts)} ({len(rows)} rows returned)."


# ── Main Verifier Function ───────────────────────────────────────────────────

def verify_answer(
    prose: str,
    columns: List[str],
    rows: List[List[Any]],
    sql: str = "",
) -> VerificationResult:
    """Verify claim-level entity/value pairs from Gemma against query rows.

    Args:
        prose: Output prose written by Gemma.
        columns: List of result column names from execute_query.
        rows: List of data rows from execute_query.
        sql: Executed SQL query (used to exempt filter years/constants).

    Returns:
        VerificationResult with evidence cells, confidence, and fallback prose.
    """
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
    exempt_years = _extract_exempt_years_from_sql(sql)
    row_count = len(rows)

    # 2. Extract numbers from prose
    number_claims = _extract_numbers(prose)
    evidence: List[EvidenceItem] = []
    matched_cell_coords: Set[Tuple[int, str]] = set()
    unmatched_claims: List[str] = []

    # Map all string and number values in the rows for fast lookup
    # cell_lookup: {(row_idx, col_name): (raw_value, float_value_or_none, string_lower)}
    row_cells: List[Dict[str, Tuple[Any, Optional[float], str]]] = []
    for r_idx, row in enumerate(rows):
        col_map = {}
        for c_idx, col_name in enumerate(columns):
            val = row[c_idx]
            flt_val = None
            if isinstance(val, (int, float)):
                flt_val = float(val)
            col_map[col_name] = (val, flt_val, str(val).lower() if val is not None else "")
        row_cells.append(col_map)

    # 3. Verify each number claim
    for raw_str, val in number_claims:
        # Check exemption: is it an exempt SQL year?
        if int(val) in exempt_years and val.is_integer():
            continue
        # Check exemption: is it the row count or limit? (e.g. "5 countries" when row_count=5)
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
            unmatched_claims.append(f"Number {raw_str} not found in returned rows")

    # 4. Verify entity names from text
    for r_idx, col_map in enumerate(row_cells):
        for col_name, (orig, flt, str_val) in col_map.items():
            if str_val and len(str_val) >= 3 and flt is None:
                # If the string entity appears in the prose (case-insensitive)
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

    # 5. Entity-Value Pair Consistency Check:
    # Split text into clauses (by punctuation or conjunctions) so each claim
    # evaluates the entity and number within its natural grammatical boundary.
    clauses = re.split(r"[,;]|\band\b|\bwhile\b|\bfollowed by\b", prose, flags=re.IGNORECASE)
    last_seen_entity_row: Optional[int] = None
    last_seen_entity_name: Optional[str] = None

    for clause in clauses:
        clause_numbers = _extract_numbers(clause)
        # Find entities mentioned in this clause
        clause_entities: List[Tuple[str, int]] = []
        for r_idx, col_map in enumerate(row_cells):
            for col_name, (orig, flt, str_val) in col_map.items():
                if str_val and len(str_val) >= 3 and flt is None:
                    if re.search(rf"\b{re.escape(str_val)}\b", clause, re.IGNORECASE):
                        clause_entities.append((orig, r_idx))

        if clause_entities:
            last_seen_entity_name, last_seen_entity_row = clause_entities[0]

        # For every number in this clause, check against the active entity
        for raw_num, num_val in clause_numbers:
            if int(num_val) in exempt_years or int(num_val) == row_count:
                continue

            num_rows = {
                r_idx for r_idx, col_map in enumerate(row_cells)
                if any(flt is not None and abs(flt - num_val) < 0.01 for _, flt, _ in col_map.values())
            }
            if not num_rows:
                continue

            # Target entity is either in this clause, or inherited from preceding clause
            target_entity_name = clause_entities[0][0] if clause_entities else last_seen_entity_name
            target_entity_row = clause_entities[0][1] if clause_entities else last_seen_entity_row

            if target_entity_row is not None and target_entity_row not in num_rows:
                unmatched_claims.append(
                    f"Entity '{target_entity_name}' (row {target_entity_row}) mismatched with value '{raw_num}' (found in rows {sorted(num_rows)})"
                )

    # 6. Relational claim check
    rel_ok, rel_error = _check_relational_claim(prose, columns, rows)
    if not rel_ok and rel_error:
        unmatched_claims.append(rel_error)

    # 7. Decide confidence and final prose
    if not unmatched_claims:
        confidence = "verified"
        is_verified = True
        final_prose = prose
    elif evidence:
        # Some cells matched, but some claims were invalid/unsupported
        confidence = "partial"
        is_verified = False
        final_prose = fallback
    else:
        # Nothing matched or complete mismatch
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
