"""
src/execute.py — Snowflake query execution using DM_READER credentials.

Architectural rules:
- Connects using parameterized configuration from environment variables or .env.
- Enforces DM_READER role (strictly read-only) by default.
- Sets QUERY_TAG = 'datamind' on every session.
- Enforces statement timeout.
- Never prints, logs, or exposes credentials.
- Returns real query ID from cursor.sfqid. Never invents a fake query ID.
- Executes only validated SQL (validation is enforced before calling execute).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.models import ExecuteResult, RowSet

logger = logging.getLogger(__name__)

# Load .env if present
try:
    from dotenv import load_dotenv

    _project_root = Path(__file__).resolve().parent.parent
    _env_file = _project_root / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass


def _make_json_safe(value: Any) -> Any:
    """Convert database types (Decimal, datetime, date, bytes) to JSON-serializable types."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def get_snowflake_connection_params() -> dict[str, Any]:
    """
    Read Snowflake connection parameters from environment variables.
    Raises EnvironmentError if required credentials are missing.
    """
    account = os.environ.get("SNOWFLAKE_ACCOUNT", "").strip()
    user = os.environ.get("SNOWFLAKE_USER", "").strip()
    password = os.environ.get("SNOWFLAKE_PASSWORD", "").strip()
    private_key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "").strip()

    missing = []
    if not account:
        missing.append("SNOWFLAKE_ACCOUNT")
    if not user:
        missing.append("SNOWFLAKE_USER")
    if not password and not private_key_path:
        missing.append("SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH")

    if missing:
        raise EnvironmentError(
            f"Missing required Snowflake configuration: {', '.join(missing)}. "
            "Set them in your local .env file."
        )

    role = os.environ.get("SNOWFLAKE_ROLE", "DM_READER").strip()
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "DM_WH").strip()
    database = os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND").strip()
    schema = os.environ.get("SNOWFLAKE_SCHEMA", "OWID").strip()
    host = os.environ.get("SNOWFLAKE_HOST", "").strip()

    params: dict[str, Any] = {
        "account": account,
        "user": user,
        "role": role,
        "warehouse": warehouse,
        "database": database,
        "schema": schema,
        "session_parameters": {
            "QUERY_TAG": os.environ.get("SNOWFLAKE_QUERY_TAG", "datamind").strip()
        },
    }

    if password:
        params["password"] = password

    if host:
        params["host"] = host

    return params


def get_snowflake_connection():
    """Create and return a live Snowflake connection using DM_READER credentials."""
    import snowflake.connector

    params = get_snowflake_connection_params()
    return snowflake.connector.connect(**params)


def execute_query(sql: str, timeout_s: int = 30) -> dict[str, Any]:
    """
    Execute a validated SQL query against Snowflake.

    Returns dictionary:
        {
            "columns": ["col1", "col2", ...],
            "data": [[val1, val2, ...], ...],
            "query_id": "01abc...",
            "row_count": int,
        }
    """
    conn = get_snowflake_connection()
    try:
        cur = conn.cursor()
        try:
            cur.execute("ALTER SESSION SET QUERY_TAG = 'datamind'")
            cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(timeout_s)}")

            cur.execute(sql)
            query_id = cur.sfqid

            columns = [desc[0].lower() for desc in (cur.description or [])]
            raw_rows = cur.fetchall()
            data = [[_make_json_safe(cell) for cell in row] for row in raw_rows]

            return {
                "columns": columns,
                "data": data,
                "query_id": query_id,
                "row_count": len(data),
            }
        finally:
            cur.close()
    finally:
        conn.close()


def execute_snowflake(sql: str, timeout_s: int = 30) -> ExecuteResult:
    """
    Execute validated SQL query and return a structured ExecuteResult model for DataMindPipeline.
    """
    t0 = time.monotonic()
    try:
        res = execute_query(sql=sql, timeout_s=timeout_s)
        sf_ms = int((time.monotonic() - t0) * 1000)
        return ExecuteResult(
            rows=RowSet(columns=res["columns"], data=res["data"]),
            query_id=res["query_id"],
            timings_ms={"snowflake": sf_ms},
            flags=[],
        )
    except Exception as exc:
        sf_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("Snowflake execution failure: %s", exc)
        return ExecuteResult(
            rows=None,
            error=str(exc),
            timings_ms={"snowflake": sf_ms},
            flags=[f"Snowflake error: {exc}"],
        )
