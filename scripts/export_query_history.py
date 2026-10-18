#!/usr/bin/env python3
"""
Export Snowflake Query History for DataMind Receipts (Section 7, 15, 18).

Fetches executed queries tagged with QUERY_TAG = 'datamind' from Snowflake
using the DM_READER connection. Exports the queries to query_history.json
and prints execution metrics.

Evidence artifact for hackathon submission (Rubric 3, Gate G2, Section 18).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

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
WHERE query_tag = 'datamind'
ORDER BY start_time DESC;
"""


def export_query_history(output_file: str = "query_history.json") -> List[Dict[str, Any]]:
    """Fetch tagged queries from Snowflake and write them to output_file."""
    print("Connecting to Snowflake as DM_READER to export query history…")
    conn = get_snowflake_connection()
    try:
        cur = conn.cursor()
        try:
            cur.execute(QUERY_HISTORY_SQL)
            columns = [desc[0].lower() for desc in cur.description] if cur.description else []
            rows = cur.fetchall()

            records = []
            for row in rows:
                rec = {col: _make_json_safe(val) for col, val in zip(columns, row)}
                records.append(rec)

            out_path = PROJECT_ROOT / output_file
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)

            print(f"✓ Exported {len(records)} query history records to {out_path.name}")
            return records
        finally:
            cur.close()
    finally:
        conn.close()


def main() -> None:
    records = export_query_history()
    if records:
        print("\nRecent Tagged Snowflake Queries:")
        for r in records[:5]:
            q_id = r.get("query_id", "N/A")
            elapsed = r.get("total_elapsed_time", 0)
            status = r.get("execution_status", "UNKNOWN")
            sql_snippet = (r.get("query_text") or "").strip().replace("\n", " ")[:60]
            print(f"  [{status}] ID: {q_id} ({elapsed}ms) — {sql_snippet}…")
    else:
        print("No recent queries with QUERY_TAG='datamind' found in session window.")


if __name__ == "__main__":
    main()
