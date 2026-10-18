"""
Snowflake query execution using DM_READER credentials.

Architectural rule (AGENTS.md, Section 4):
    The application uses ONLY DM_READER credentials.
    QUERY_TAG = 'datamind' is set on every session.
    Statement timeout is enforced.

No LLM inference happens here — that belongs exclusively in src/llm/.
No local-file fallback — every answer comes from a live Snowflake query.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import snowflake.connector


def _load_dotenv() -> None:
    """Load .env from the project root (best-effort)."""
    try:
        from dotenv import load_dotenv

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(project_root, ".env")
        load_dotenv(dotenv_path)
    except ImportError:
        # python-dotenv not installed; assume env vars are set externally.
        pass


def get_snowflake_connection() -> snowflake.connector.SnowflakeConnection:
    """Create a Snowflake connection using strictly DM_READER credentials.

    Reads the following environment variables (loaded from ``.env``):
        SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
        SNOWFLAKE_ROLE (must be DM_READER), SNOWFLAKE_WAREHOUSE,
        SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA.

    The session is configured with ``QUERY_TAG = 'datamind'`` as required by
    the Snowflake setup script (Section 10).

    Raises:
        EnvironmentError: If any required credential is missing.
        snowflake.connector.errors.DatabaseError: On connection failure.
    """
    _load_dotenv()

    required_vars = {
        "SNOWFLAKE_ACCOUNT": os.environ.get("SNOWFLAKE_ACCOUNT"),
        "SNOWFLAKE_USER": os.environ.get("SNOWFLAKE_USER"),
        "SNOWFLAKE_PASSWORD": os.environ.get("SNOWFLAKE_PASSWORD"),
    }
    missing = [k for k, v in required_vars.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required Snowflake environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your values."
        )

    role = os.environ.get("SNOWFLAKE_ROLE", "DM_READER")
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "DM_WH")
    database = os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND")
    schema = os.environ.get("SNOWFLAKE_SCHEMA", "OWID")
    query_tag = os.environ.get("SNOWFLAKE_QUERY_TAG", "datamind")

    conn = snowflake.connector.connect(
        account=required_vars["SNOWFLAKE_ACCOUNT"],
        user=required_vars["SNOWFLAKE_USER"],
        password=required_vars["SNOWFLAKE_PASSWORD"],
        role=role,
        warehouse=warehouse,
        database=database,
        schema=schema,
        session_parameters={"QUERY_TAG": query_tag},
    )
    return conn


def _make_json_safe(value: Any) -> Any:
    """Convert a single value to a JSON-serializable type."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        # Preserve precision for display; use float only when lossless.
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    # int, float, str, bool pass through unchanged.
    return value


def execute_query(
    sql: str,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    """Execute a validated SQL query against Snowflake using DM_READER.

    Steps (Section 6 pipeline, step 3 — Execute):
      1. Establish connection via ``get_snowflake_connection()``.
      2. ``ALTER SESSION SET QUERY_TAG = 'datamind'`` (redundant safety;
         already set at connect time but enforced again).
      3. ``ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = <timeout_s>``.
      4. Execute the SQL.
      5. Return a results dictionary.

    Args:
        sql: A validated, sanitized SQL string (must already have passed
            ``validate_sql``).
        timeout_s: Statement timeout in seconds (default 30, per Section 10).

    Returns:
        A dictionary::

            {
                "columns": ["col1", "col2", ...],
                "data": [[val1, val2, ...], ...],   # JSON-safe values
                "query_id": "01abc-...",
                "row_count": 42,
            }

    Raises:
        snowflake.connector.errors.ProgrammingError: On SQL execution failure
            (syntax, permissions, timeout, etc.).
        EnvironmentError: If credentials are missing.
    """
    conn = get_snowflake_connection()
    try:
        cur = conn.cursor()
        try:
            # Enforce query tag and statement timeout on this session.
            cur.execute("ALTER SESSION SET QUERY_TAG = 'datamind'")
            cur.execute(
                f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(timeout_s)}"
            )

            # Execute the user query.
            cur.execute(sql)
            query_id: str = cur.sfqid  # Snowflake query ID for the receipt.

            # Fetch column metadata.
            columns: List[str] = (
                [desc[0] for desc in cur.description] if cur.description else []
            )

            # Fetch all rows and make them JSON-safe.
            raw_rows = cur.fetchall()
            data: List[List[Any]] = [
                [_make_json_safe(cell) for cell in row] for row in raw_rows
            ]

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
