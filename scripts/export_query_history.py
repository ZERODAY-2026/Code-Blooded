#!/usr/bin/env python3
"""
scripts/export_query_history.py — Export Snowflake Query History.

Sections 7, 15, and 18 of the DataMind Receipts Handbook.

Fetches executed queries tagged with QUERY_TAG = 'datamind' from Snowflake
and exports query IDs, SQL text, execution times, and metrics to
docs/snowflake_query_history.csv.

Serves as an inspectable evidence artifact for hackathon submission (Rubric 3, Gate G2, Section 18).
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.execute import get_snowflake_connection, _make_json_safe


QUERY_HISTORY_SQL = """\
SELECT
    query_id,
    query_text,
    database_name,
    schema_name,
    query_type,
    execution_status,
    error_code,
    error_message,
    start_time,
    end_time,
    total_elapsed_time,
    bytes_scanned,
    rows_produced,
    query_tag
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
    RESULT_LIMIT => 100
))
WHERE query_tag ILIKE 'datamind%'
ORDER BY start_time DESC;
"""

CSV_COLUMNS = [
    "query_id",
    "query_text",
    "total_elapsed_time_ms",
    "execution_status",
    "start_time",
    "end_time",
    "rows_produced",
    "bytes_scanned",
    "database_name",
    "schema_name",
    "query_tag",
]


def export_query_history(
    output_csv: str = "docs/snowflake_query_history.csv",
    output_json: Optional[str] = "query_history.json",
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Fetch tagged queries from Snowflake and export to CSV and JSON."""
    close_at_end = False
    if conn is None:
        print("Connecting to Snowflake to fetch query history…")
        conn = get_snowflake_connection()
        close_at_end = True

    try:
        cur = conn.cursor()
        try:
            cur.execute(QUERY_HISTORY_SQL)
            col_names = [desc[0].lower() for desc in cur.description] if cur.description else []
            rows = cur.fetchall()

            records: List[Dict[str, Any]] = []
            for row in rows:
                raw_rec = {col: _make_json_safe(val) for col, val in zip(col_names, row)}
                rec = {
                    "query_id": raw_rec.get("query_id", ""),
                    "query_text": (raw_rec.get("query_text") or "").strip().replace("\r\n", " ").replace("\n", " "),
                    "total_elapsed_time_ms": raw_rec.get("total_elapsed_time", 0),
                    "execution_status": raw_rec.get("execution_status", ""),
                    "start_time": raw_rec.get("start_time", ""),
                    "end_time": raw_rec.get("end_time", ""),
                    "rows_produced": raw_rec.get("rows_produced", 0),
                    "bytes_scanned": raw_rec.get("bytes_scanned", 0),
                    "database_name": raw_rec.get("database_name", ""),
                    "schema_name": raw_rec.get("schema_name", ""),
                    "query_tag": raw_rec.get("query_tag", ""),
                }
                records.append(rec)

            # Export to CSV
            csv_path = Path(output_csv)
            if not csv_path.is_absolute():
                csv_path = PROJECT_ROOT / csv_path
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                for rec in records:
                    writer.writerow({col: rec.get(col, "") for col in CSV_COLUMNS})

            try:
                display_csv = csv_path.relative_to(PROJECT_ROOT)
            except ValueError:
                display_csv = csv_path
            print(f"✓ Exported {len(records)} query history records to {display_csv}")

            # Optionally export JSON as well for backward compatibility
            if output_json:
                json_path = Path(output_json)
                if not json_path.is_absolute():
                    json_path = PROJECT_ROOT / json_path
                json_path.parent.mkdir(parents=True, exist_ok=True)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(records, f, indent=2)
                try:
                    display_json = json_path.relative_to(PROJECT_ROOT)
                except ValueError:
                    display_json = json_path
                print(f"✓ Updated {display_json}")

            return records
        finally:
            cur.close()
    finally:
        if close_at_end:
            conn.close()


def main() -> None:
    records = export_query_history()
    if records:
        print("\nRecent Tagged Snowflake Queries (Top 5):")
        print("-" * 80)
        for r in records[:5]:
            q_id = r.get("query_id", "N/A")
            elapsed = r.get("total_elapsed_time_ms", 0)
            status = r.get("execution_status", "UNKNOWN")
            sql_snippet = r.get("query_text", "")[:50]
            print(f"  [{status:7s}] ID: {q_id} ({elapsed}ms) — {sql_snippet}…")
        print("-" * 80)
    else:
        print("No recent queries with QUERY_TAG='datamind' found in session window.")


if __name__ == "__main__":
    main()
