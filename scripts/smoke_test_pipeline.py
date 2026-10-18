"""
scripts/smoke_test_pipeline.py — Opt-in live end-to-end integration smoke test.

IMPORTANT NOTICE
────────────────
This script makes REAL live calls to Google AI Studio and optionally connects
to Snowflake to execute a read-only query as DM_READER.

Prerequisites:
  1. GOOGLE_GEMMA_API_KEY set in .env or environment.
  2. Snowflake credentials set in .env:
     - SNOWFLAKE_ACCOUNT
     - SNOWFLAKE_USER (must have DM_READER role ONLY)
     - SNOWFLAKE_PASSWORD (or SNOWFLAKE_PRIVATE_KEY_PATH)
  3. OWID dataset loaded in Snowflake (database: DATAMIND, schema: OWID).

This test is strictly opt-in and does NOT run in automated CI or pytest.

Usage:
  python scripts/smoke_test_pipeline.py
  python scripts/smoke_test_pipeline.py --model gemma-3-27b-it
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

from src.config import _API_KEY_ENV_VAR, load_config
from src.llm import GemmaAPIProvider
from src.models import ExecuteResult, RowSet
from src.pipeline import DataMindPipeline
from src.validate import DatasetSchema


def create_live_snowflake_executor(
    account: str,
    user: str,
    password: str | None = None,
    private_key_path: str | None = None,
    warehouse: str = "DM_WH",
    database: str = "DATAMIND",
    schema: str = "OWID",
    role: str = "DM_READER",
):
    """Create a live read-only Snowflake executor function."""
    import snowflake.connector

    conn_params: dict[str, Any] = {
        "account": account,
        "user": user,
        "warehouse": warehouse,
        "database": database,
        "schema": schema,
        "role": role,
    }
    if password:
        conn_params["password"] = password

    def _execute(sql: str) -> ExecuteResult:
        import time
        t0 = time.monotonic()
        try:
            ctx = snowflake.connector.connect(**conn_params)
            cs = ctx.cursor()
            try:
                # Set query tag per hackathon rules
                cs.execute("ALTER SESSION SET QUERY_TAG = 'datamind';")
                cs.execute(sql)
                rows = cs.fetchall()
                cols = [desc[0].lower() for desc in (cs.description or [])]
                sf_ms = int((time.monotonic() - t0) * 1000)
                query_id = cs.sfqid
                return ExecuteResult(
                    rows=RowSet(columns=cols, data=rows),
                    query_id=query_id,
                    timings_ms={"snowflake": sf_ms},
                    flags=[],
                )
            finally:
                cs.close()
                ctx.close()
        except Exception as exc:
            sf_ms = int((time.monotonic() - t0) * 1000)
            return ExecuteResult(
                rows=None,
                error=str(exc),
                timings_ms={"snowflake": sf_ms},
                flags=["snowflake_connection_error"],
            )

    return _execute


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in live integration test for DataMind pipeline (Gemma + Snowflake)."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Gemma model ID override",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  DataMind Receipts -- Live Integration Smoke Test (Gemma + Snowflake)")
    print("=" * 70)
    print("NOTICE: This test performs live API calls to Google AI Studio and")
    print("optionally queries Snowflake as DM_READER.")
    print("-" * 70)

    # 1. Check Gemma API Key
    api_key = os.environ.get(_API_KEY_ENV_VAR, "").strip()
    if not api_key:
        print(f"[MISSING PREREQUISITE] {_API_KEY_ENV_VAR} is not set.")
        print("To run live Gemma inference:")
        print("  1. Obtain an API key from https://aistudio.google.com/apikey")
        print(f"  2. Set {_API_KEY_ENV_VAR} in your .env file.")
        print("Live integration test skipped.")
        return 0

    masked_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
    print(f"[OK] Gemma API Key found: {masked_key}")

    # 2. Check Snowflake Credentials
    sf_account = os.environ.get("SNOWFLAKE_ACCOUNT", "").strip()
    sf_user = os.environ.get("SNOWFLAKE_USER", "").strip()
    sf_password = os.environ.get("SNOWFLAKE_PASSWORD", "").strip()
    sf_private_key = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "").strip()

    has_snowflake = bool(sf_account and sf_user and (sf_password or sf_private_key))
    if not has_snowflake:
        print("[MISSING PREREQUISITE] Snowflake credentials are not configured in .env.")
        print("Required for live Snowflake execution:")
        print("  - SNOWFLAKE_ACCOUNT")
        print("  - SNOWFLAKE_USER (DM_READER role only)")
        print("  - SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH")
        print("Live Snowflake execution will be skipped.")
        executor = None
    else:
        print(f"[OK] Snowflake credentials found for user '{sf_user}' on account '{sf_account}'.")
        try:
            executor = create_live_snowflake_executor(
                account=sf_account,
                user=sf_user,
                password=sf_password or None,
                private_key_path=sf_private_key or None,
            )
            print("[OK] Snowflake executor created.")
        except Exception as err:
            print(f"[WARN] Failed to create Snowflake executor: {err}")
            executor = None

    # 3. Load config and dataset schema
    try:
        cfg = load_config()
    except Exception:
        from src.config import AppConfig
        cfg = AppConfig(
            provider="api",
            model="gemma-3-27b-it",
            dataset="datasets/owid.yaml",
        )

    model_to_use = args.model or cfg.model or "gemma-3-27b-it"
    print(f"[*] Target model: {model_to_use}")

    try:
        provider = GemmaAPIProvider(model_name=model_to_use)
    except Exception as err:
        print(f"[FAIL] Provider initialisation failed: {err}")
        return 1

    dataset_path = Path(cfg.dataset)
    if not dataset_path.exists():
        print(f"[FAIL] Dataset file not found: {dataset_path}")
        return 1

    schema = DatasetSchema.from_yaml(dataset_path)
    print(f"[OK] Dataset schema loaded from {dataset_path} ({len(schema.tables)} tables)")

    # 4. Run pipeline
    pipeline = DataMindPipeline(
        config=cfg,
        schema=schema,
        provider=provider,
        executor=executor,
    )

    test_q = "Which 5 countries had the highest life expectancy in 2019?"
    print(f"[*] Submitting question: '{test_q}'")

    resp = pipeline.ask(test_q)

    print("-" * 70)
    print("  Pipeline Result Summary")
    print("-" * 70)
    print(f"  Status:       {resp.status}")
    print(f"  Answer:       {resp.answer}")
    print(f"  SQL:          {resp.sql}")
    print(f"  Query ID:     {resp.query_id}")
    print(f"  Timings:      {resp.timings_ms}")
    print(f"  Receipt:      {resp.explanation}")
    print(f"  Flags:        {resp.flags}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
