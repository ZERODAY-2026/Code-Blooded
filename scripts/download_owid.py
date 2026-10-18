#!/usr/bin/env python3
"""
Download the OWID Life Expectancy CSV and load it into Snowflake.

This script:
  1. Downloads the public CSV from Our World in Data (life expectancy).
  2. Prints the CC BY 4.0 licence attribution.
  3. Connects to Snowflake using DM_LOADER credentials (from .env).
  4. Creates the life_expectancy table in DATAMIND.OWID.
  5. Inserts the CSV rows.
  6. Creates a life_expectancy_backup CLONE for zero-drift demo resets.

Usage:
    python scripts/download_owid.py

Requires:
    pip install snowflake-connector-python python-dotenv
"""

from __future__ import annotations

import csv
import io
import os
import sys
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWID_LIFE_EXPECTANCY_URL = (
    "https://ourworldindata.org/grapher/life-expectancy.csv"
    "?v=1&csvType=full&useColumnShortNames=false"
)

LICENCE_NOTICE = """
================================================================================
  DATA SOURCE AND LICENCE
================================================================================
  Dataset : Life Expectancy — Our World in Data
  URL     : https://ourworldindata.org/grapher/life-expectancy
  Licence : Creative Commons Attribution 4.0 International (CC BY 4.0)
            https://creativecommons.org/licenses/by/4.0/
  Citation: Our World in Data — Life Expectancy.
            Based on data from the UN World Population Prospects and
            other sources compiled by Our World in Data.

  You are free to share and adapt this data, provided you give
  appropriate credit (attribution) to the source.
================================================================================
"""

# Snowflake DDL — table matches the YAML schema (datasets/owid.yaml)
CREATE_TABLE_SQL = """\
CREATE OR REPLACE TABLE life_expectancy (
    entity           VARCHAR(256)  NOT NULL,
    code             VARCHAR(10),
    year             INTEGER       NOT NULL,
    life_expectancy  FLOAT
);
"""

CREATE_BACKUP_SQL = (
    "CREATE OR REPLACE TABLE life_expectancy_backup CLONE life_expectancy;"
)

INSERT_SQL = (
    "INSERT INTO life_expectancy (entity, code, year, life_expectancy) "
    "VALUES (%s, %s, %s, %s)"
)

BATCH_SIZE = 5000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_dotenv() -> None:
    """Best-effort .env loading."""
    try:
        from dotenv import load_dotenv as _load
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _load(os.path.join(project_root, ".env"))
    except ImportError:
        pass


def require_env(key: str) -> str:
    """Return an env var or exit with a clear message."""
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: Environment variable {key} is not set. Check your .env file.")
        sys.exit(1)
    return val


def download_csv(url: str) -> list[dict[str, str]]:
    """Download a CSV from *url* and return rows as dicts."""
    print(f"Downloading CSV from:\n  {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "DataMind-Receipts/1.0"})
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    print(f"  → {len(rows):,} rows downloaded.")
    return rows


def parse_float_or_none(value: str) -> float | None:
    """Parse a float string, returning None for empty / unparseable values."""
    if not value or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def connect_snowflake_loader():
    """Return a Snowflake connection using DM_LOADER credentials."""
    try:
        import snowflake.connector
    except ImportError:
        print("ERROR: snowflake-connector-python is not installed.")
        print("  pip install snowflake-connector-python")
        sys.exit(1)

    account   = require_env("SNOWFLAKE_ACCOUNT")
    user      = require_env("SNOWFLAKE_LOADER_USER")
    password  = require_env("SNOWFLAKE_LOADER_PASSWORD")
    role      = require_env("SNOWFLAKE_LOADER_ROLE")   # should be DM_LOADER
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "DM_WH")
    database  = os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND")
    schema    = os.environ.get("SNOWFLAKE_SCHEMA", "OWID")
    query_tag = os.environ.get("SNOWFLAKE_QUERY_TAG", "datamind")

    print(f"Connecting to Snowflake as {user} (role={role}, wh={warehouse}) …")
    conn = snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        role=role,
        warehouse=warehouse,
        database=database,
        schema=schema,
        session_parameters={"QUERY_TAG": query_tag},
    )
    return conn


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    # ── 1. Licence attribution ────────────────────────────────────────────
    print(LICENCE_NOTICE)

    # ── 2. Download CSV ───────────────────────────────────────────────────
    rows = download_csv(OWID_LIFE_EXPECTANCY_URL)
    if not rows:
        print("ERROR: No rows downloaded.")
        sys.exit(1)

    # Map CSV header names → our Snowflake column names
    # CSV columns: "Entity", "Code", "Year", "Life expectancy"
    params: list[tuple] = []
    skipped = 0
    for row in rows:
        entity = row.get("Entity", "").strip()
        code   = row.get("Code", "").strip() or None
        year_s = row.get("Year", "").strip()
        le_s   = row.get("Life expectancy", "").strip()

        if not entity or not year_s:
            skipped += 1
            continue

        try:
            year = int(year_s)
        except ValueError:
            skipped += 1
            continue

        life_exp = parse_float_or_none(le_s)
        params.append((entity, code, year, life_exp))

    print(f"  → {len(params):,} valid rows to insert ({skipped} skipped).")

    # ── 3. Connect and load ───────────────────────────────────────────────
    conn = connect_snowflake_loader()
    cur = conn.cursor()
    try:
        # Create table
        print("Creating table life_expectancy …")
        cur.execute(CREATE_TABLE_SQL)

        # Insert in batches
        print(f"Inserting {len(params):,} rows (batch size {BATCH_SIZE}) …")
        for i in range(0, len(params), BATCH_SIZE):
            batch = params[i : i + BATCH_SIZE]
            cur.executemany(INSERT_SQL, batch)
            loaded = min(i + BATCH_SIZE, len(params))
            print(f"  … {loaded:,} / {len(params):,}")

        # Verify row count
        cur.execute("SELECT COUNT(*) FROM life_expectancy")
        count = cur.fetchone()[0]
        print(f"✓ life_expectancy loaded: {count:,} rows.")

        # ── 4. Create backup clone for demo reset ─────────────────────────
        print("Creating life_expectancy_backup CLONE …")
        cur.execute(CREATE_BACKUP_SQL)
        print("✓ life_expectancy_backup created.")

        print("\n✓ OWID data load COMPLETE.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
