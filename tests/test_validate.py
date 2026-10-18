"""
tests/test_validate.py

Pytest tests for sqlglot-based SQL validation layer.
Tests the SQL parsing, LIMIT enforcement, and structural checks from handbook.

Interface contract (Person A implements this):
    from src.validate import validate_sql, enforce_limit, is_single_statement
    validate_sql(sql, allowed_tables) -> (bool, str)
    enforce_limit(sql, max_limit) -> str       # adds/clamps LIMIT clause
    is_single_statement(sql) -> bool

Run:
    pytest tests/test_validate.py -v
"""

import pytest

try:
    import sqlglot
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

try:
    from src.validate import validate_sql, enforce_limit, is_single_statement
    VALIDATE_AVAILABLE = True
except ImportError:
    VALIDATE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not VALIDATE_AVAILABLE,
    reason="src.validate not yet implemented — tests run when Person A ships it",
)

ALLOWED = ["CUSTOMER", "ORDERS", "LINEITEM", "NATION", "REGION", "SUPPLIER", "PART", "PARTSUPP"]


# ═══════════════════════════════════════════════════════════════════════════════
# Section A — LIMIT enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestLimitEnforcement:
    """enforce_limit() must add a LIMIT when missing, and clamp when too high."""

    def test_no_limit_gets_added(self):
        sql = "SELECT * FROM CUSTOMER"
        result = enforce_limit(sql, max_limit=200)
        parsed = result.upper()
        assert "LIMIT" in parsed, "LIMIT must be added when missing"

    def test_limit_above_max_gets_clamped(self):
        sql = "SELECT * FROM CUSTOMER LIMIT 10000"
        result = enforce_limit(sql, max_limit=200)
        # Parse the result and confirm LIMIT <= 200
        import re
        match = re.search(r"LIMIT\s+(\d+)", result, re.IGNORECASE)
        assert match, "LIMIT clause must be present after enforcement"
        assert int(match.group(1)) <= 200, "LIMIT must be clamped to max_limit=200"

    def test_limit_below_max_unchanged(self):
        sql = "SELECT * FROM CUSTOMER LIMIT 5"
        result = enforce_limit(sql, max_limit=200)
        import re
        match = re.search(r"LIMIT\s+(\d+)", result, re.IGNORECASE)
        assert match and int(match.group(1)) == 5, "LIMIT 5 must not be changed"

    def test_limit_exactly_max_unchanged(self):
        sql = "SELECT * FROM ORDERS LIMIT 200"
        result = enforce_limit(sql, max_limit=200)
        import re
        match = re.search(r"LIMIT\s+(\d+)", result, re.IGNORECASE)
        assert match and int(match.group(1)) == 200, "LIMIT exactly at max must be unchanged"


# ═══════════════════════════════════════════════════════════════════════════════
# Section B — Single statement detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleStatement:
    """is_single_statement() must return True only for single SQL statements."""

    def test_single_select_is_true(self):
        assert is_single_statement("SELECT * FROM ORDERS LIMIT 10") is True

    def test_two_selects_is_false(self):
        assert is_single_statement("SELECT 1; SELECT 2") is False

    def test_select_then_drop_is_false(self):
        assert is_single_statement("SELECT * FROM ORDERS; DROP TABLE ORDERS") is False

    def test_empty_string_is_false(self):
        assert is_single_statement("") is False

    def test_cte_single_is_true(self):
        sql = (
            "WITH cte AS (SELECT O_CUSTKEY FROM ORDERS LIMIT 100) "
            "SELECT * FROM CUSTOMER JOIN cte ON CUSTOMER.C_CUSTKEY = cte.O_CUSTKEY LIMIT 10"
        )
        assert is_single_statement(sql) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Section C — sqlglot parsing integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestSqlglotParsing:
    """Confirm that sqlglot is the validation engine (not regex)."""

    @pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")
    def test_sqlglot_can_parse_valid_sql(self):
        import sqlglot
        result = sqlglot.parse("SELECT * FROM CUSTOMER LIMIT 10")
        assert result and len(result) == 1

    @pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")
    def test_sqlglot_detects_drop(self):
        import sqlglot
        result = sqlglot.parse("DROP TABLE CUSTOMER")
        assert result
        stmt_type = type(result[0]).__name__
        assert stmt_type == "Drop", f"Expected Drop, got {stmt_type}"

    @pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")
    def test_sqlglot_detects_insert(self):
        import sqlglot
        result = sqlglot.parse("INSERT INTO CUSTOMER VALUES (1, 'x')")
        assert result
        stmt_type = type(result[0]).__name__
        assert stmt_type == "Insert", f"Expected Insert, got {stmt_type}"

    @pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")
    def test_sqlglot_parses_cte(self):
        import sqlglot
        sql = (
            "WITH top AS (SELECT O_CUSTKEY FROM ORDERS LIMIT 10) "
            "SELECT * FROM CUSTOMER c JOIN top t ON c.C_CUSTKEY = t.O_CUSTKEY LIMIT 10"
        )
        result = sqlglot.parse(sql)
        assert result and len(result) == 1

    @pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")
    def test_sqlglot_extracts_table_names(self):
        """Confirm sqlglot can extract table names — required for allowlist checking."""
        import sqlglot
        sql = "SELECT c.C_NAME, o.O_TOTALPRICE FROM CUSTOMER c JOIN ORDERS o ON c.C_CUSTKEY = o.O_CUSTKEY LIMIT 10"
        tables = {
            t.name.upper()
            for tree in sqlglot.parse(sql)
            for t in tree.find_all(sqlglot.exp.Table)
        }
        assert "CUSTOMER" in tables
        assert "ORDERS" in tables


# ═══════════════════════════════════════════════════════════════════════════════
# Section D — Full validate_sql integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateSqlIntegration:
    """End-to-end validate_sql() tests — combines all checks."""

    def test_valid_simple_select(self):
        is_valid, error = validate_sql("SELECT * FROM CUSTOMER LIMIT 10", ALLOWED)
        assert is_valid
        assert error == ""

    def test_valid_join(self):
        sql = (
            "SELECT c.C_NAME, SUM(l.L_EXTENDEDPRICE) AS revenue "
            "FROM CUSTOMER c "
            "JOIN ORDERS o ON c.C_CUSTKEY = o.O_CUSTKEY "
            "JOIN LINEITEM l ON o.O_ORDERKEY = l.L_ORDERKEY "
            "GROUP BY c.C_NAME ORDER BY revenue DESC LIMIT 10"
        )
        is_valid, error = validate_sql(sql, ALLOWED)
        assert is_valid, f"Valid 3-table join must pass. Error: {error}"

    def test_returns_false_for_drop(self):
        is_valid, error = validate_sql("DROP TABLE CUSTOMER", ALLOWED)
        assert not is_valid
        assert len(error) > 0

    def test_returns_false_for_unlisted_table(self):
        is_valid, error = validate_sql("SELECT * FROM secret_table LIMIT 10", ALLOWED)
        assert not is_valid
        assert len(error) > 0

    def test_error_message_is_informative(self):
        """Error messages must say WHY — not just 'invalid'."""
        is_valid, error = validate_sql("DROP TABLE ORDERS", ALLOWED)
        assert not is_valid
        # Error should mention something meaningful
        assert any(keyword in error.lower() for keyword in ["drop", "not allowed", "blocked", "select only", "statement"]), \
            f"Error message must be informative, got: '{error}'"
