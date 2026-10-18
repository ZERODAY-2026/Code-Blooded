"""
tests/test_guardrails.py

Pytest tests for the SQL guardrail layer.
Tests validate.validate_sql() against every case from handbook Section 12.

Interface contract (Person A implements this):
    from src.validate import validate_sql
    validate_sql(sql: str, allowed_tables: list[str]) -> tuple[bool, str]
    Returns (True, "") for valid SQL, (False, error_message) for rejected SQL.

Run:
    pytest tests/test_guardrails.py -v
"""

import pytest

# ── Interface shim ────────────────────────────────────────────────────────────
# We import validate_sql from the expected location.
# If Person A hasn't written it yet, these tests will XFAIL cleanly.
try:
    from src.validate import validate_sql
    VALIDATE_AVAILABLE = True
except ImportError:
    VALIDATE_AVAILABLE = False

    def validate_sql(sql: str, allowed_tables: list) -> tuple:
        """Stub — replaced when Person A ships validate.py"""
        raise NotImplementedError("src.validate not yet available")


pytestmark = pytest.mark.skipif(
    not VALIDATE_AVAILABLE,
    reason="src.validate not yet implemented by Person A — tests will run once available",
)

# ── Allowed tables used across tests ─────────────────────────────────────────
OWID_TABLES = ["life_expectancy", "gdp_per_capita", "co2", "population"]
TPCH_TABLES = [
    "CUSTOMER", "ORDERS", "LINEITEM", "NATION",
    "REGION", "SUPPLIER", "PART", "PARTSUPP",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Section A — Blocked statement types (handbook Section 12, all must be rejected)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlockedStatements:
    """Every non-SELECT DML/DDL statement must be rejected."""

    def test_drop_table_rejected(self):
        sql = "DROP TABLE CUSTOMER"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "DROP TABLE must be rejected"
        assert error, "Error message must not be empty for DROP"

    def test_delete_rejected(self):
        sql = "DELETE FROM CUSTOMER WHERE C_CUSTKEY = 1"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "DELETE must be rejected"

    def test_alter_table_rejected(self):
        sql = "ALTER TABLE CUSTOMER ADD COLUMN new_col VARCHAR(100)"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "ALTER TABLE must be rejected"

    def test_update_rejected(self):
        sql = "UPDATE CUSTOMER SET C_ACCTBAL = 0 WHERE C_CUSTKEY = 1"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "UPDATE must be rejected"

    def test_truncate_rejected(self):
        sql = "TRUNCATE TABLE CUSTOMER"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "TRUNCATE must be rejected"

    def test_insert_rejected(self):
        sql = "INSERT INTO CUSTOMER (C_NAME) VALUES ('hacker')"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "INSERT must be rejected"

    def test_merge_rejected(self):
        sql = "MERGE INTO CUSTOMER USING (SELECT 1) AS src ON 1=1 WHEN MATCHED THEN DELETE"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "MERGE must be rejected"

    def test_copy_into_rejected(self):
        sql = "COPY INTO CUSTOMER FROM @my_stage"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "COPY INTO must be rejected"

    def test_call_rejected(self):
        sql = "CALL my_stored_procedure()"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "CALL must be rejected"

    def test_grant_rejected(self):
        sql = "GRANT ALL PRIVILEGES ON TABLE CUSTOMER TO ROLE sysadmin"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "GRANT must be rejected"

    def test_revoke_rejected(self):
        sql = "REVOKE SELECT ON TABLE CUSTOMER FROM ROLE PUBLIC"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "REVOKE must be rejected"

    def test_create_table_rejected(self):
        sql = "CREATE TABLE evil_copy AS SELECT * FROM CUSTOMER"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "CREATE TABLE must be rejected"

    def test_create_schema_rejected(self):
        sql = "CREATE SCHEMA new_schema"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "CREATE SCHEMA must be rejected"


# ═══════════════════════════════════════════════════════════════════════════════
# Section B — Multi-statement rejection (handbook Section 12)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiStatement:
    """Only a single statement is permitted. Semicolon-chaining must be rejected."""

    def test_two_selects_rejected(self):
        sql = "SELECT * FROM CUSTOMER LIMIT 5; SELECT * FROM ORDERS LIMIT 5"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Two SELECT statements must be rejected"

    def test_select_then_drop_rejected(self):
        sql = "SELECT * FROM CUSTOMER; DROP TABLE CUSTOMER"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "SELECT then DROP must be rejected (multi-statement)"

    def test_select_then_insert_rejected(self):
        sql = "SELECT 1; INSERT INTO CUSTOMER VALUES (1, 'x', 'y', 'z', 0, 1, 'a', 'b')"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "SELECT then INSERT must be rejected (multi-statement)"

    def test_comment_hidden_second_statement_rejected(self):
        sql = "SELECT * FROM CUSTOMER; -- trailing comment\nDROP TABLE ORDERS"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Comment-hidden second statement must be rejected"


# ═══════════════════════════════════════════════════════════════════════════════
# Section C — Table allowlist enforcement (handbook Section 12)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTableAllowlist:
    """Tables not in the allowlist must be rejected, even with valid SELECT syntax."""

    def test_unlisted_table_rejected(self):
        sql = "SELECT * FROM INFORMATION_SCHEMA.TABLES LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "INFORMATION_SCHEMA.TABLES not in allowlist — must be rejected"

    def test_sys_table_rejected(self):
        sql = "SELECT * FROM sys.columns LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "sys.columns not in allowlist — must be rejected"

    def test_owid_table_not_in_tpch_allowlist(self):
        sql = "SELECT * FROM life_expectancy LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "life_expectancy not in TPCH allowlist — must be rejected"

    def test_tpch_table_not_in_owid_allowlist(self):
        sql = "SELECT * FROM CUSTOMER LIMIT 10"
        is_valid, error = validate_sql(sql, OWID_TABLES)
        assert not is_valid, "CUSTOMER not in OWID allowlist — must be rejected"

    def test_subquery_unlisted_table_rejected(self):
        """Allowlist check must catch tables inside subqueries too."""
        sql = (
            "SELECT * FROM CUSTOMER WHERE C_CUSTKEY IN "
            "(SELECT O_CUSTKEY FROM secret_orders LIMIT 100) LIMIT 10"
        )
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Table in subquery must also be allowlist-checked"

    def test_same_named_table_in_other_schema_rejected(self):
        """Table name in allowlist, but qualified with an unauthorized schema must be rejected."""
        sql = "SELECT * FROM OTHER_SCHEMA.CUSTOMER LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "CUSTOMER in unauthorized schema OTHER_SCHEMA must be rejected"

    def test_fully_qualified_valid_table_passes(self):
        """Fully-qualified valid tables in official database and schema must pass."""
        sql = "SELECT * FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.CUSTOMER LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"Fully qualified valid table must pass. Got: {error}"

    def test_lowercase_unquoted_fully_qualified_passes(self):
        """Case-insensitive lowercase qualification must pass."""
        sql = "SELECT * FROM snowflake_sample_data.tpch_sf1.customer LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"Lowercase unquoted qualification must pass. Got: {error}"

    def test_double_quoted_unauthorized_schema_rejected(self):
        """Double-quoted unauthorized schema must be rejected."""
        sql = 'SELECT * FROM "OTHER_SCHEMA"."CUSTOMER" LIMIT 10'
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Double-quoted unauthorized schema must be rejected"




# ═══════════════════════════════════════════════════════════════════════════════
# Section D — Valid SELECT passes (handbook Section 12)
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidSelectPasses:
    """Valid, safe SELECT queries against allowlisted tables must pass."""

    def test_simple_select_passes(self):
        sql = "SELECT C_NAME, C_ACCTBAL FROM CUSTOMER LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"Simple SELECT must pass. Got error: {error}"
        assert error == ""

    def test_select_with_where_passes(self):
        sql = "SELECT * FROM CUSTOMER WHERE C_MKTSEGMENT = 'HOUSEHOLD' LIMIT 50"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"SELECT with WHERE must pass. Got error: {error}"

    def test_join_select_passes(self):
        sql = (
            "SELECT c.C_NAME, o.O_TOTALPRICE "
            "FROM CUSTOMER c "
            "JOIN ORDERS o ON c.C_CUSTKEY = o.O_CUSTKEY "
            "ORDER BY o.O_TOTALPRICE DESC LIMIT 10"
        )
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"JOIN SELECT must pass. Got error: {error}"

    def test_aggregate_select_passes(self):
        sql = (
            "SELECT C_MKTSEGMENT, COUNT(*) AS cnt, AVG(C_ACCTBAL) AS avg_bal "
            "FROM CUSTOMER "
            "GROUP BY C_MKTSEGMENT "
            "ORDER BY cnt DESC LIMIT 10"
        )
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"Aggregate SELECT must pass. Got error: {error}"

    def test_cte_select_passes(self):
        sql = (
            "WITH top_customers AS ("
            "SELECT O_CUSTKEY, SUM(O_TOTALPRICE) AS total "
            "FROM ORDERS GROUP BY O_CUSTKEY ORDER BY total DESC LIMIT 100"
            ") "
            "SELECT c.C_NAME, tc.total "
            "FROM CUSTOMER c JOIN top_customers tc ON c.C_CUSTKEY = tc.O_CUSTKEY "
            "LIMIT 10"
        )
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"CTE SELECT must pass. Got error: {error}"

    def test_owid_select_passes(self):
        sql = (
            "SELECT entity, life_expectancy "
            "FROM life_expectancy "
            "WHERE year = 2019 "
            "ORDER BY life_expectancy DESC LIMIT 5"
        )
        is_valid, error = validate_sql(sql, OWID_TABLES)
        assert is_valid, f"OWID SELECT must pass. Got error: {error}"

    def test_subquery_allowlisted_passes(self):
        sql = (
            "SELECT C_NAME FROM CUSTOMER "
            "WHERE C_CUSTKEY IN (SELECT O_CUSTKEY FROM ORDERS LIMIT 100) "
            "LIMIT 10"
        )
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"Subquery with allowlisted tables must pass. Got error: {error}"


# ═══════════════════════════════════════════════════════════════════════════════
# Section E — Edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases that must be handled correctly."""

    def test_empty_string_rejected(self):
        is_valid, error = validate_sql("", TPCH_TABLES)
        assert not is_valid, "Empty SQL string must be rejected"

    def test_whitespace_only_rejected(self):
        is_valid, error = validate_sql("   \n\t  ", TPCH_TABLES)
        assert not is_valid, "Whitespace-only SQL must be rejected"

    def test_select_star_with_limit_passes(self):
        sql = "SELECT * FROM ORDERS LIMIT 200"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert is_valid, f"SELECT * with LIMIT must pass. Got error: {error}"

    def test_case_insensitive_drop_rejected(self):
        """Blocklist must catch lowercase/mixed-case variants."""
        sql = "drop table CUSTOMER"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Lowercase 'drop table' must be rejected"

    def test_case_insensitive_insert_rejected(self):
        sql = "insert into CUSTOMER (C_NAME) values ('test')"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Lowercase 'insert into' must be rejected"

    def test_prompt_injection_comment_bypass_rejected(self):
        """Prompt injection attempting to slip in DROP behind comment and semicolon."""
        sql = "SELECT * FROM CUSTOMER /* ignore all safety rules */; DROP TABLE CUSTOMER"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "Injected comment bypass multi-statement must be rejected"

    def test_prompt_injection_union_exfiltration_rejected(self):
        """Prompt injection attempting to exfiltrate unlisted credentials or metadata."""
        sql = "SELECT C_NAME FROM CUSTOMER UNION SELECT PASSWORD FROM ADMIN_USERS LIMIT 10"
        is_valid, error = validate_sql(sql, TPCH_TABLES)
        assert not is_valid, "UNION injection targeting unlisted table must be rejected"

