"""
Smoke test: verify Snowflake DM_READER connectivity.

Loads credentials from .env and runs SELECT 1 using the DM_READER role.
Intended to be run as:
    pytest tests/test_connection.py -v

Or standalone:
    python tests/test_connection.py
"""

import os
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env from the project root (best-effort; no hard dependency on python-dotenv)."""
    try:
        from dotenv import load_dotenv

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(project_root, ".env")
        load_dotenv(dotenv_path)
    except ImportError:
        # python-dotenv not installed; assume env vars are set externally
        pass


def _get_env(key: str) -> str:
    """Return an env var or skip the test with a clear message."""
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Environment variable {key} is not set (check .env)")
    return val


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def snowflake_reader_conn():
    """Return a live Snowflake connection using DM_READER credentials."""
    _load_dotenv()

    try:
        import snowflake.connector
    except ImportError:
        pytest.skip("snowflake-connector-python is not installed")

    account   = _get_env("SNOWFLAKE_ACCOUNT")
    user      = _get_env("SNOWFLAKE_USER")
    password  = _get_env("SNOWFLAKE_PASSWORD")
    role      = _get_env("SNOWFLAKE_ROLE")       # should be DM_READER
    warehouse = _get_env("SNOWFLAKE_WAREHOUSE")   # should be DM_WH
    database  = _get_env("SNOWFLAKE_DATABASE")    # should be DATAMIND
    schema    = _get_env("SNOWFLAKE_SCHEMA")      # should be OWID
    query_tag = os.environ.get("SNOWFLAKE_QUERY_TAG", "datamind")

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
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_select_one(snowflake_reader_conn):
    """SELECT 1 must return 1 — proves authentication and warehouse are live."""
    cur = snowflake_reader_conn.cursor()
    try:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        assert row is not None, "Query returned no rows"
        assert row[0] == 1, f"Expected 1, got {row[0]}"
    finally:
        cur.close()


def test_reader_role_is_active(snowflake_reader_conn):
    """Verify the session is running as the DM_READER role."""
    cur = snowflake_reader_conn.cursor()
    try:
        cur.execute("SELECT CURRENT_ROLE()")
        role = cur.fetchone()[0]
        assert role == "DM_READER", f"Expected DM_READER, got {role}"
    finally:
        cur.close()


def test_query_tag_is_set(snowflake_reader_conn):
    """Verify the session QUERY_TAG is 'datamind'."""
    cur = snowflake_reader_conn.cursor()
    try:
        cur.execute("SHOW PARAMETERS LIKE 'QUERY_TAG' IN SESSION")
        rows = cur.fetchall()
        # SHOW PARAMETERS returns (key, value, default, level, description, type)
        tag_value = next((r[1] for r in rows if r[0] == "QUERY_TAG"), None)
        assert tag_value == "datamind", f"Expected query tag 'datamind', got '{tag_value}'"
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _load_dotenv()
    try:
        import snowflake.connector
    except ImportError:
        print("ERROR: snowflake-connector-python is not installed.")
        print("  pip install snowflake-connector-python python-dotenv")
        raise SystemExit(1)

    account   = os.environ.get("SNOWFLAKE_ACCOUNT", "")
    user      = os.environ.get("SNOWFLAKE_USER", "")
    password  = os.environ.get("SNOWFLAKE_PASSWORD", "")
    role      = os.environ.get("SNOWFLAKE_ROLE", "DM_READER")
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "DM_WH")
    database  = os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND")
    schema    = os.environ.get("SNOWFLAKE_SCHEMA", "OWID")
    query_tag = os.environ.get("SNOWFLAKE_QUERY_TAG", "datamind")

    missing = [k for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")
               if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        print("  Copy .env.example to .env and fill in your values.")
        raise SystemExit(1)

    print(f"Connecting to {account} as {user} (role={role}, wh={warehouse}) …")
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
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1")
        result = cur.fetchone()[0]
        assert result == 1
        print(f"✓ SELECT 1 returned {result}")

        cur.execute("SELECT CURRENT_ROLE()")
        active_role = cur.fetchone()[0]
        print(f"✓ Active role: {active_role}")

        cur.execute("SELECT CURRENT_WAREHOUSE()")
        active_wh = cur.fetchone()[0]
        print(f"✓ Active warehouse: {active_wh}")

        print("\n✓ Connection test PASSED.")
    finally:
        cur.close()
        conn.close()
