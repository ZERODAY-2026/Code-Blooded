"""
scripts/verify_ground_truth.py

Runs every ground_truth_sql in eval/questions.csv against live Snowflake
(using DM_READER credentials from .env) and prints:
  - The captured result snapshot (JSON for the 'expected' column)
  - Whether the SQL succeeded or failed
  - A ready-to-paste CSV update for each row

Does NOT write to the CSV automatically — review output first.
"""

import csv
import json
import os
import sys
from decimal import Decimal
from datetime import date, datetime, time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import snowflake.connector
except ImportError:
    print("ERROR: snowflake-connector-python not installed.", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.csv"


def make_json_safe(val):
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime,)):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, time):
        return val.isoformat()
    if isinstance(val, bytes):
        return val.hex()
    return val


def get_conn():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.environ.get("SNOWFLAKE_ROLE", "DM_READER"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "DM_WH"),
    )


def run_sql(conn, sql):
    clean = sql.strip().rstrip(";")
    with conn.cursor() as cur:
        cur.execute(clean)
        cols = [d[0].upper() for d in cur.description] if cur.description else []
        rows = [[make_json_safe(c) for c in r] for r in cur.fetchall()]
    return cols, rows


def main():
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        questions = list(csv.DictReader(f))

    conn = get_conn()
    print(f"Connected as {os.environ.get('SNOWFLAKE_USER')} / {os.environ.get('SNOWFLAKE_ROLE', 'DM_READER')}\n")
    print("=" * 70)

    results = []
    for q in questions:
        qid = q["id"]
        dataset = q["dataset"]
        question = q["question"]
        sql = q.get("ground_truth_sql", "").strip()
        expected_status = q.get("expected_status", "answered")
        category = q.get("category", "")

        print(f"\n[Q{qid:>2}] [{dataset.upper():4}] [{category:<22}] {question[:55]}")

        # No SQL needed for behaviour-only questions
        if not sql:
            if expected_status in ("clarify", "refused"):
                print(f"       No SQL (expected_status={expected_status}) -- mark verified, leave expected=''")
                results.append({**q, "status": "verified", "expected": "", "note": "no_sql_behaviour"})
            elif expected_status == "answered" and not sql:
                # Ambiguous -- needs behaviour check (assumptions)
                print(f"       Ambiguous question -- no SQL, expects assumptions from model")
                print(f"         Mark verified only after confirming API returns assumptions")
                results.append({**q, "status": "draft", "expected": "", "note": "ambiguous_needs_api"})
            else:
                print(f"       No SQL, skipping")
                results.append({**q, "note": "no_sql"})
            continue

        # Run the SQL
        try:
            cols, rows = run_sql(conn, sql)
            snapshot = {"columns": cols, "data": rows}
            snapshot_str = json.dumps(snapshot, separators=(",", ":"))
            row_count = len(rows)

            if expected_status == "empty" and row_count == 0:
                print(f"       SQL OK -- returns 0 rows (expected 'empty') PASS")
                results.append({**q, "status": "verified", "expected": snapshot_str, "note": "ok_empty"})
            elif expected_status == "empty" and row_count > 0:
                print(f"       WARN: SQL returned {row_count} rows but expected_status=empty -- check SQL")
                results.append({**q, "status": "draft", "expected": snapshot_str, "note": "warn_empty_but_got_rows"})
            else:
                print(f"       SQL OK -- {row_count} row(s) returned")
                if row_count > 0:
                    # Show first row as sample
                    print(f"         Cols: {cols}")
                    print(f"         Row1: {rows[0]}")
                    if row_count > 1:
                        print(f"         ...  ({row_count} total rows)")
                results.append({**q, "status": "verified", "expected": snapshot_str, "note": "ok"})

        except Exception as e:
            print(f"       FAIL: SQL ERROR: {e}")
            results.append({**q, "status": "draft", "expected": q.get("expected", ""), "note": f"sql_error:{e}"})

    conn.close()

    # Summary
    print("\n" + "=" * 70)
    verified = [r for r in results if r.get("status") == "verified"]
    draft = [r for r in results if r.get("status") == "draft"]
    errors = [r for r in results if "sql_error" in r.get("note", "")]
    print(f"\nSUMMARY: {len(verified)} verified | {len(draft)} draft | {len(errors)} SQL errors\n")

    if errors:
        print("SQL ERRORS to fix:")
        for r in errors:
            print(f"  Q{r['id']}: {r['question'][:60]} → {r['note']}")

    # Write updated CSV
    out_path = PROJECT_ROOT / "eval" / "questions_verified.csv"
    fieldnames = list(questions[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in fieldnames})

    print(f"\nWrote verified CSV to: {out_path}")
    print("Review it, then replace eval/questions.csv when satisfied.")


if __name__ == "__main__":
    main()
