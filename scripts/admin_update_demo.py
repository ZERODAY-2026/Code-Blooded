#!/usr/bin/env python3
"""
scripts/admin_update_demo.py — Local Operator Data-Change Demo with Snowflake Time Travel.

Sections 4, 9, 15, and 18 of the DataMind Receipts Handbook.

Architectural and Zero-Tolerance Rules (AGENTS.md):
  1. The data-change demo NEVER uses the LLM path.
  2. It is an operator-invoked local script with fixed, reviewed statements.
  3. Uses DM_LOADER credentials (loader role is never exposed to the web app).
  4. Never mutates SNOWFLAKE_SAMPLE_DATA (read-only shared database).
     Mutations run exclusively on our own DATAMIND tables.
  5. Reset restores exact values from life_expectancy_backup (zero drift).

CLI Commands:
  python scripts/admin_update_demo.py mutate
  python scripts/admin_update_demo.py time-travel
  python scripts/admin_update_demo.py reset
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import snowflake.connector

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_dataset


def _load_dotenv() -> None:
    """Load environment variables from project root .env if present."""
    try:
        from dotenv import load_dotenv
        dotenv_path = PROJECT_ROOT / ".env"
        load_dotenv(dotenv_path)
    except ImportError:
        pass


def get_loader_connection() -> snowflake.connector.SnowflakeConnection:
    """Establish a Snowflake connection using DM_LOADER credentials from .env.

    Raises:
        EnvironmentError: If required credentials are missing.
    """
    _load_dotenv()

    account = os.environ.get("SNOWFLAKE_ACCOUNT")
    user = os.environ.get("SNOWFLAKE_LOADER_USER") or os.environ.get("SNOWFLAKE_USER")
    password = os.environ.get("SNOWFLAKE_LOADER_PASSWORD") or os.environ.get("SNOWFLAKE_PASSWORD")

    missing = []
    if not account:
        missing.append("SNOWFLAKE_ACCOUNT")
    if not user:
        missing.append("SNOWFLAKE_LOADER_USER / SNOWFLAKE_USER")
    if not password:
        missing.append("SNOWFLAKE_LOADER_PASSWORD / SNOWFLAKE_PASSWORD")

    if missing:
        raise EnvironmentError(
            f"Missing required Snowflake loader credentials: {', '.join(missing)}. "
            "Ensure .env has your account, user, and password set."
        )

    role = os.environ.get("SNOWFLAKE_LOADER_ROLE", "DM_LOADER")
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "DM_WH")
    database = os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND")
    schema = os.environ.get("SNOWFLAKE_SCHEMA", "OWID")

    conn = snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        role=role,
        warehouse=warehouse,
        database=database,
        schema=schema,
        session_parameters={"QUERY_TAG": "datamind_admin"},
    )
    return conn


def _get_demo_sqls(dataset_path: str | Path = "datasets/owid.yaml") -> Tuple[str, str]:
    """Retrieve mutation SQL and reset SQL from the dataset semantic layer."""
    full_path = PROJECT_ROOT / dataset_path if not Path(dataset_path).is_absolute() else Path(dataset_path)
    schema = load_dataset(full_path)
    if not schema.demo_mutation:
        raise ValueError(f"Dataset at {dataset_path} does not define a 'demo_mutation' section.")

    mutation_sql = schema.demo_mutation.sql.strip()
    reset_sql = schema.demo_mutation.reset_sql.strip()
    return mutation_sql, reset_sql


def run_mutate(
    conn: Optional[snowflake.connector.SnowflakeConnection] = None,
    dataset_path: str | Path = "datasets/owid.yaml",
) -> Dict[str, Any]:
    """Execute the YAML sql mutation to visibly change the demo answer."""
    mutation_sql, _ = _get_demo_sqls(dataset_path)
    close_at_end = False
    if conn is None:
        conn = get_loader_connection()
        close_at_end = True

    try:
        cur = conn.cursor()
        try:
            print("=" * 70)
            print("  DataMind Receipts — Operator Demo Mutation (DM_LOADER)")
            print("=" * 70)
            print(f"Executing Mutation SQL:\n  {mutation_sql}\n")

            cur.execute(mutation_sql)
            query_id = cur.sfqid
            rows_affected = cur.rowcount

            # Verify updated row
            check_sql = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity = 'Japan' AND year = 2019"
            cur.execute(check_sql)
            check_rows = cur.fetchall()

            result = {
                "action": "mutate",
                "query_id": query_id,
                "rows_affected": rows_affected,
                "current_row": check_rows[0] if check_rows else None,
                "sql": mutation_sql,
            }

            print(f"✓ Mutation succeeded!")
            print(f"  Snowflake Query ID : {query_id}")
            print(f"  Rows Affected      : {rows_affected}")
            if check_rows:
                print(f"  Current Row State  : entity={check_rows[0][0]}, year={check_rows[0][1]}, life_expectancy={check_rows[0][2]}")
            print("\n→ Re-asking 'Which 5 countries had the highest life expectancy in 2019?'")
            print("  will now reflect Japan at 95.0, proving live Snowflake execution at question time.")
            return result
        finally:
            cur.close()
    finally:
        if close_at_end:
            conn.close()


def run_time_travel(
    conn: Optional[snowflake.connector.SnowflakeConnection] = None,
    dataset_path: str | Path = "datasets/owid.yaml",
) -> Dict[str, Any]:
    """Query the table using Snowflake Time Travel (AT(OFFSET => -300)) to show before-and-after rows."""
    close_at_end = False
    if conn is None:
        conn = get_loader_connection()
        close_at_end = True

    try:
        cur = conn.cursor()
        try:
            print("=" * 70)
            print("  DataMind Receipts — Snowflake Time Travel Demonstration")
            print("=" * 70)

            # 1. Query Current State (After Mutation)
            current_sql = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity = 'Japan' AND year = 2019"
            cur.execute(current_sql)
            current_qid = cur.sfqid
            current_rows = cur.fetchall()

            print("1. Current State (After Mutation):")
            print(f"   Query ID : {current_qid}")
            print(f"   SQL      : {current_sql}")
            if current_rows:
                print(f"   Data     : entity={current_rows[0][0]}, year={current_rows[0][1]}, life_expectancy={current_rows[0][2]}")

            # 2. Query Time Travel State (Before Mutation, 5 minutes ago)
            timetravel_sql = (
                "SELECT entity, year, life_expectancy "
                "FROM life_expectancy AT(OFFSET => -300) "
                "WHERE entity = 'Japan' AND year = 2019"
            )
            print("\n2. Time Travel State 5 Minutes Ago (AT(OFFSET => -300)):")
            print(f"   SQL      : {timetravel_sql}")

            try:
                cur.execute(timetravel_sql)
                tt_qid = cur.sfqid
                tt_rows = cur.fetchall()
                if tt_rows:
                    print(f"   Query ID : {tt_qid}")
                    print(f"   Data     : entity={tt_rows[0][0]}, year={tt_rows[0][1]}, life_expectancy={tt_rows[0][2]}")
            except snowflake.connector.errors.ProgrammingError as e:
                # If table was created less than 5 minutes ago, offset -300 may precede table creation.
                # Fall back to checking 60s ago or report gracefully.
                print(f"   Note: AT(OFFSET => -300) preceding table creation ({e.msg}). Falling back to -60s...")
                timetravel_sql = (
                    "SELECT entity, year, life_expectancy "
                    "FROM life_expectancy AT(OFFSET => -60) "
                    "WHERE entity = 'Japan' AND year = 2019"
                )
                cur.execute(timetravel_sql)
                tt_qid = cur.sfqid
                tt_rows = cur.fetchall()
                if tt_rows:
                    print(f"   Query ID : {tt_qid}")
                    print(f"   Data     : entity={tt_rows[0][0]}, year={tt_rows[0][1]}, life_expectancy={tt_rows[0][2]}")

            result = {
                "action": "time-travel",
                "current_query_id": current_qid,
                "current_row": current_rows[0] if current_rows else None,
                "timetravel_query_id": tt_qid,
                "timetravel_row": tt_rows[0] if tt_rows else None,
            }

            print("\n✓ Snowflake Time Travel demonstrates native point-in-time consistency.")
            print("  Both query IDs are inspectable evidence from live Snowflake history.")
            return result
        finally:
            cur.close()
    finally:
        if close_at_end:
            conn.close()


def run_reset(
    conn: Optional[snowflake.connector.SnowflakeConnection] = None,
    dataset_path: str | Path = "datasets/owid.yaml",
) -> Dict[str, Any]:
    """Execute reset_sql to restore exact values from life_expectancy_backup."""
    _, reset_sql = _get_demo_sqls(dataset_path)
    close_at_end = False
    if conn is None:
        conn = get_loader_connection()
        close_at_end = True

    try:
        cur = conn.cursor()
        try:
            print("=" * 70)
            print("  DataMind Receipts — Zero-Drift Demo Reset (DM_LOADER)")
            print("=" * 70)
            print(f"Executing Reset SQL from backup clone:\n  {reset_sql}\n")

            cur.execute(reset_sql)
            query_id = cur.sfqid
            rows_affected = cur.rowcount

            # Verify restored row
            check_sql = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity = 'Japan' AND year = 2019"
            cur.execute(check_sql)
            check_rows = cur.fetchall()

            result = {
                "action": "reset",
                "query_id": query_id,
                "rows_affected": rows_affected,
                "current_row": check_rows[0] if check_rows else None,
                "sql": reset_sql,
            }

            print(f"✓ Table successfully reset from backup clone!")
            print(f"  Snowflake Query ID : {query_id}")
            print(f"  Rows Affected      : {rows_affected}")
            if check_rows:
                print(f"  Restored Row State : entity={check_rows[0][0]}, year={check_rows[0][1]}, life_expectancy={check_rows[0][2]}")
            print("\n→ Zero-drift guarantee: Exact values restored without cumulative drift.")
            return result
        finally:
            cur.close()
    finally:
        if close_at_end:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DataMind Receipts: Local Operator Data-Change Demo with Snowflake Time Travel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        choices=["mutate", "time-travel", "reset"],
        help="Action to perform: 'mutate' changes row, 'time-travel' compares AT(OFFSET => -300), 'reset' restores from backup",
    )
    parser.add_argument(
        "--dataset",
        default="datasets/owid.yaml",
        help="Path to dataset YAML file (default: datasets/owid.yaml)",
    )

    args = parser.parse_args()

    if args.action == "mutate":
        run_mutate(dataset_path=args.dataset)
    elif args.action == "time-travel":
        run_time_travel(dataset_path=args.dataset)
    elif args.action == "reset":
        run_reset(dataset_path=args.dataset)


if __name__ == "__main__":
    main()
