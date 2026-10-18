"""
src/verify.py — Evidence verification for DataMind Receipts.

Verifies claim-level entity/value pairs from Gemma's prose against actual query result rows.
Does not perform LLM calls; uses deterministic substring and numerical tolerance matching.
Never claims verification if result rows do not support the numbers/entities.
"""

from __future__ import annotations

import re
from typing import Any

from src.models import EvidenceCell, RowSet, VerifyResult


def _extract_numbers(text: str) -> list[tuple[str, float]]:
    """Extract raw number substrings and their float values from text."""
    # Match integers and floats, including percentages and decimals
    pattern = r"\b\d+(?:\.\d+)?%?\b"
    matches = []
    for match in re.finditer(pattern, text):
        raw_str = match.group(0)
        clean_num = raw_str.rstrip("%")
        try:
            val = float(clean_num)
            matches.append((raw_str, val))
        except ValueError:
            continue
    return matches


def verify_answer(
    prose: str,
    columns: list[str],
    rows: list[list[Any]] | RowSet,
    sql: str = "",
    is_simulated: bool = False,
) -> VerifyResult:
    """
    Verify claim-level entity/value pairs from Gemma against query rows.

    Args:
        prose: Output prose written by Gemma.
        columns: List of result column names.
        rows: List of data rows (or RowSet instance).
        sql: Executed SQL query.

    Returns:
        VerifyResult with evidence cells, confidence, and verified answer.
    """
    # Normalize rows
    raw_rows: list[list[Any]] = rows.data if isinstance(rows, RowSet) else rows
    raw_cols: list[str] = rows.columns if isinstance(rows, RowSet) else columns

    if not raw_rows:
        return VerifyResult(
            verified_answer="The query returned no matching rows.",
            evidence=[],
            confidence="partial",
            unverifiable_claims=["No result rows available to verify claims."],
            status="unverifiable",
        )

    evidence: list[EvidenceCell] = []
    matched_coords: set[tuple[int, str]] = set()
    unmatched_claims: list[str] = []

    # Map all string and number values in the rows
    row_cells: list[dict[str, tuple[Any, float | None, str]]] = []
    for r_idx, row in enumerate(raw_rows):
        col_map = {}
        for c_idx, col_name in enumerate(raw_cols):
            if c_idx < len(row):
                val = row[c_idx]
                flt_val = None
                if isinstance(val, (int, float)):
                    flt_val = float(val)
                col_map[col_name] = (val, flt_val, str(val).lower() if val is not None else "")
        row_cells.append(col_map)

    # 1. Match numbers from prose
    number_claims = _extract_numbers(prose)
    row_count = len(raw_rows)

    for raw_str, val in number_claims:
        # Ignore obvious counts matching row count (e.g. "top 5")
        if int(val) == row_count and val.is_integer() and f"{int(val)} " in prose.lower():
            continue

        found = False
        for r_idx, col_map in enumerate(row_cells):
            for col_name, (_, flt, _) in col_map.items():
                if flt is not None:
                    # Tolerance within 0.01 or 0.1%
                    if abs(flt - val) < 0.01 or (val != 0 and abs(flt - val) / abs(val) < 0.001):
                        found = True
                        coord = (r_idx, col_name)
                        if coord not in matched_coords:
                            matched_coords.add(coord)
                            evidence.append(
                                EvidenceCell(
                                    text=raw_str,
                                    row_index=r_idx,
                                    column=col_name,
                                )
                            )
                        break
            if found:
                break

        if not found:
            unmatched_claims.append(f"Number '{raw_str}' not found in returned rows")

    # 2. Match entity text strings from rows in prose
    for r_idx, col_map in enumerate(row_cells):
        for col_name, (orig, flt, str_val) in col_map.items():
            if str_val and len(str_val) >= 3 and flt is None:
                pattern = rf"\b{re.escape(str_val)}\b"
                match = re.search(pattern, prose, re.IGNORECASE)
                if match:
                    coord = (r_idx, col_name)
                    if coord not in matched_coords:
                        matched_coords.add(coord)
                        evidence.append(
                            EvidenceCell(
                                text=match.group(0),
                                row_index=r_idx,
                                column=col_name,
                            )
                        )

    # 3. Determine confidence
    if is_simulated:
        confidence = "partial"
        unmatched_claims.append("Simulated results: not verified against live Snowflake data.")
        status = "answered"
    elif not unmatched_claims and evidence:
        confidence = "verified"
        status = "answered"
    elif evidence:
        confidence = "partial"
        status = "answered"
    else:
        confidence = "partial"
        status = "answered"

    return VerifyResult(
        verified_answer=prose,
        evidence=evidence,
        confidence=confidence,
        unverifiable_claims=unmatched_claims,
        status=status,
    )
