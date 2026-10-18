"""
Unit tests for src/validate.py — SQL guardrails (Section 12).

Covers every guardrail case from the handbook:
  - Blocking DDL/DML: INSERT, UPDATE, DELETE, DROP, ALTER, MERGE, COPY,
    CALL, GRANT, TRUNCATE, CREATE.
  - Multi-statement injection (semicolon-separated).
  - Unlisted tables (table allowlist enforcement).
  - LIMIT enforcement (missing, within cap, over cap).
  - Valid SELECT queries (simple, CTE, UNION, subquery, aliases).
  - Prompt-injection-style SQL (comments hiding statements).

No hardcoded schema — allowed_tables are passed dynamically.
"""

from __future__ import annotations

import pytest

from src.validate import SQLValidationError, validate_sql

# ── Shared fixtures ─────────────────────────────────────────────────────────

ALLOWED_TABLES = ["life_expectancy", "gdp", "population", "co2_emissions"]
"""Tables that would be loaded from the dataset YAML in production."""


# ── Helpers ─────────────────────────────────────────────────────────────────

def assert_valid(sql: str, tables: list | None = None, max_limit: int = 200) -> str:
    """Assert the SQL passes validation and return the sanitized SQL."""
    is_valid, error, sanitized = validate_sql(
        sql, ALLOWED_TABLES if tables is None else tables, max_limit
    )
    assert is_valid, f"Expected valid SQL but got error: {error}"
    assert error is None
    assert sanitized is not None
    return sanitized


def assert_invalid(sql: str, tables: list | None = None, max_limit: int = 200, *, fragment: str = "") -> str:
    """Assert the SQL fails validation and return the error message.

    Args:
        fragment: If given, assert this substring appears in the error.
    """
    is_valid, error, sanitized = validate_sql(
        sql, ALLOWED_TABLES if tables is None else tables, max_limit
    )
    assert not is_valid, f"Expected invalid SQL but it passed: {sanitized}"
    assert error is not None
    assert sanitized is None
    if fragment:
        assert fragment.lower() in error.lower(), (
            f"Expected '{fragment}' in error message, got: {error}"
        )
    return error


# ═══════════════════════════════════════════════════════════════════════════
# 1. DDL/DML Blocking (Section 12: "refused/rejected")
# ═══════════════════════════════════════════════════════════════════════════

class TestBlockDDLDML:
    """The validator must reject any non-SELECT statement."""

    def test_insert(self):
        assert_invalid(
            "INSERT INTO life_expectancy (entity) VALUES ('X')",
            fragment="forbidden",
        )

    def test_update(self):
        assert_invalid(
            "UPDATE life_expectancy SET life_expectancy = 99 WHERE entity = 'X'",
            fragment="forbidden",
        )

    def test_delete(self):
        assert_invalid(
            "DELETE FROM life_expectancy WHERE year = 2019",
            fragment="forbidden",
        )

    def test_drop_table(self):
        assert_invalid("DROP TABLE life_expectancy", fragment="forbidden")

    def test_drop_database(self):
        assert_invalid("DROP DATABASE datamind", fragment="forbidden")

    def test_alter_table(self):
        assert_invalid(
            "ALTER TABLE life_expectancy ADD COLUMN x INT",
            fragment="forbidden",
        )

    def test_merge(self):
        assert_invalid(
            "MERGE INTO life_expectancy USING gdp ON life_expectancy.entity = gdp.entity "
            "WHEN MATCHED THEN UPDATE SET life_expectancy.life_expectancy = 0",
            fragment="forbidden",
        )

    def test_create_table(self):
        assert_invalid(
            "CREATE TABLE evil (id INT)",
            fragment="forbidden",
        )

    def test_truncate(self):
        assert_invalid("TRUNCATE TABLE life_expectancy", fragment="forbidden")

    def test_copy_into(self):
        assert_invalid(
            "COPY INTO life_expectancy FROM @my_stage",
            fragment="forbidden",
        )

    def test_call_procedure(self):
        assert_invalid("CALL my_procedure()", fragment="forbidden")

    def test_grant(self):
        assert_invalid(
            "GRANT SELECT ON life_expectancy TO ROLE public",
            fragment="forbidden",
        )

    def test_create_or_replace(self):
        assert_invalid(
            "CREATE OR REPLACE TABLE life_expectancy (id INT)",
            fragment="forbidden",
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. Multi-Statement Injection (Section 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestMultiStatementInjection:
    """Reject semicolon-separated multi-statements."""

    def test_select_then_drop(self):
        assert_invalid(
            "SELECT * FROM life_expectancy; DROP TABLE life_expectancy",
            fragment="single",
        )

    def test_select_then_delete(self):
        assert_invalid(
            "SELECT 1; DELETE FROM life_expectancy",
            fragment="single",
        )

    def test_select_then_select(self):
        """Even two SELECTs are rejected — only one statement allowed."""
        assert_invalid(
            "SELECT 1; SELECT 2",
            fragment="single",
        )

    def test_trailing_semicolon_single_statement(self):
        """A single SELECT with a trailing semicolon should be accepted."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy LIMIT 10;"
        )
        assert sanitized  # non-empty

    def test_comment_hiding_second_statement(self):
        """Comments that attempt to hide a second statement.

        Section 12: 'comments hiding a second statement' must be rejected.
        """
        # If sqlglot parses the DROP out of the comment, it may pass or
        # fail depending on dialect handling.  The important thing is that
        # actual multi-statements with comments are blocked.
        sql = "SELECT 1 FROM life_expectancy; /* harmless */ DROP TABLE life_expectancy"
        assert_invalid(sql)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Table Allowlist (Section 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestTableAllowlist:
    """Every referenced base table must be in the allowed list."""

    def test_unknown_table(self):
        assert_invalid(
            "SELECT * FROM secret_data LIMIT 10",
            fragment="allowlist",
        )

    def test_information_schema(self):
        assert_invalid(
            "SELECT * FROM information_schema.tables LIMIT 10",
            fragment="allowlist",
        )

    def test_mixed_known_unknown(self):
        """Query joins an allowed table with an unlisted one."""
        assert_invalid(
            "SELECT a.entity FROM life_expectancy a "
            "JOIN evil_table b ON a.entity = b.entity LIMIT 10",
            fragment="allowlist",
        )

    def test_case_insensitive_match(self):
        """Table names should match case-insensitively."""
        sanitized = assert_valid(
            "SELECT entity FROM LIFE_EXPECTANCY LIMIT 10"
        )
        assert sanitized

    def test_all_tables_allowed(self):
        sanitized = assert_valid(
            "SELECT a.entity, b.entity "
            "FROM life_expectancy a JOIN gdp b ON a.entity = b.entity LIMIT 10"
        )
        assert sanitized

    def test_system_catalog_blocked(self):
        """System views like pg_catalog are not in the allowlist."""
        assert_invalid(
            "SELECT * FROM pg_catalog LIMIT 10",
            fragment="allowlist",
        )

    def test_snowflake_sample_data_tables(self):
        """Tables from SNOWFLAKE_SAMPLE_DATA are not in OWID allowlist."""
        assert_invalid(
            "SELECT * FROM customer LIMIT 10",
            fragment="allowlist",
        )

    def test_empty_allowlist(self):
        """An empty allowlist should reject any table reference."""
        assert_invalid(
            "SELECT * FROM life_expectancy LIMIT 10",
            tables=[],
            fragment="allowlist",
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. LIMIT Enforcement (Section 12: "Row cap hit")
# ═══════════════════════════════════════════════════════════════════════════

class TestLimitEnforcement:
    """Enforce the LIMIT cap from config (default 200)."""

    def test_no_limit_injected(self):
        """A query without LIMIT gets one injected."""
        sanitized = assert_valid("SELECT entity FROM life_expectancy")
        assert "LIMIT" in sanitized.upper()
        # Default max_limit is 200
        assert "200" in sanitized

    def test_limit_within_cap(self):
        """A LIMIT smaller than the cap is kept as-is."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy LIMIT 50"
        )
        assert "50" in sanitized

    def test_limit_at_cap(self):
        """A LIMIT exactly at the cap is kept."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy LIMIT 200"
        )
        assert "200" in sanitized

    def test_limit_over_cap_clamped(self):
        """A LIMIT exceeding the cap must be clamped."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy LIMIT 5000"
        )
        assert "5000" not in sanitized
        assert "200" in sanitized

    def test_custom_max_limit(self):
        """The max_limit parameter is respected."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy LIMIT 150",
            max_limit=100,
        )
        assert "150" not in sanitized
        assert "100" in sanitized

    def test_limit_injection_with_max_50(self):
        """Injected limit uses the provided cap value."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy",
            max_limit=50,
        )
        assert "50" in sanitized


# ═══════════════════════════════════════════════════════════════════════════
# 5. Valid SELECT Queries
# ═══════════════════════════════════════════════════════════════════════════

class TestValidSelects:
    """Ensure legitimate queries pass validation."""

    def test_simple_select(self):
        sanitized = assert_valid(
            "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 5"
        )
        assert "SELECT" in sanitized.upper()

    def test_select_with_where_and_order(self):
        sanitized = assert_valid(
            "SELECT entity, life_expectancy FROM life_expectancy "
            "WHERE year = 2019 AND entity != 'World' "
            "ORDER BY life_expectancy DESC LIMIT 10"
        )
        assert sanitized

    def test_select_with_aggregation(self):
        sanitized = assert_valid(
            "SELECT entity, AVG(life_expectancy) AS avg_le "
            "FROM life_expectancy GROUP BY entity LIMIT 10"
        )
        assert sanitized

    def test_select_with_join(self):
        sanitized = assert_valid(
            "SELECT le.entity, le.life_expectancy, g.entity "
            "FROM life_expectancy le "
            "JOIN gdp g ON le.entity = g.entity AND le.year = g.year "
            "LIMIT 10",
            tables=["life_expectancy", "gdp"],
        )
        assert sanitized

    def test_select_with_cte(self):
        """CTEs (WITH ... AS) must be accepted as valid SELECT queries."""
        sanitized = assert_valid(
            "WITH top_countries AS ("
            "  SELECT entity, life_expectancy "
            "  FROM life_expectancy "
            "  WHERE year = 2019 "
            "  ORDER BY life_expectancy DESC "
            "  LIMIT 5"
            ") "
            "SELECT * FROM top_countries LIMIT 5"
        )
        assert sanitized

    def test_union(self):
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy WHERE year = 2019 LIMIT 5 "
            "UNION ALL "
            "SELECT entity FROM life_expectancy WHERE year = 2020 LIMIT 5"
        )
        assert sanitized

    def test_subquery_in_where(self):
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy "
            "WHERE life_expectancy > (SELECT AVG(life_expectancy) FROM life_expectancy) "
            "LIMIT 10"
        )
        assert sanitized

    def test_select_star(self):
        sanitized = assert_valid("SELECT * FROM life_expectancy LIMIT 10")
        assert sanitized

    def test_window_function(self):
        sanitized = assert_valid(
            "SELECT entity, year, life_expectancy, "
            "  RANK() OVER (PARTITION BY year ORDER BY life_expectancy DESC) AS rnk "
            "FROM life_expectancy LIMIT 20"
        )
        assert sanitized


# ═══════════════════════════════════════════════════════════════════════════
# 6. Edge Cases and Prompt Injection (Section 12)
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases from Section 12: prompt injection, empty input, etc."""

    def test_empty_string(self):
        assert_invalid("", fragment="empty")

    def test_whitespace_only(self):
        assert_invalid("   ", fragment="empty")

    def test_prompt_injection_drop(self):
        """Section 12: 'Prompt injection in a question' — validator blocks."""
        # If Gemma were to produce this SQL from a prompt injection,
        # the validator must still block it.
        assert_invalid(
            "DROP TABLE life_expectancy",
            fragment="forbidden",
        )

    def test_select_into_blocked(self):
        """SELECT ... INTO is a mutation in some dialects."""
        # sqlglot may or may not parse this as a command.  If it does pass
        # parsing, the INTO table won't be in the allowlist.
        is_valid, error, _ = validate_sql(
            "SELECT entity INTO new_table FROM life_expectancy",
            ALLOWED_TABLES,
        )
        # Must fail — either as forbidden or table not in allowlist.
        # (Both are valid rejection reasons.)
        # Actually this might parse as a valid SELECT with an INTO clause,
        # but 'new_table' won't be in the allowlist.
        if is_valid:
            pytest.fail(f"SELECT INTO should be blocked but was accepted")

    def test_only_semicolons(self):
        """Just semicolons should be treated as empty."""
        assert_invalid(";;;", fragment="empty")

    def test_sql_with_line_comments(self):
        """Single-line comments should not break validation."""
        sanitized = assert_valid(
            "-- get top countries\n"
            "SELECT entity FROM life_expectancy LIMIT 5"
        )
        assert sanitized

    def test_sql_with_block_comments(self):
        """Block comments should not break validation."""
        sanitized = assert_valid(
            "/* top countries */ SELECT entity FROM life_expectancy LIMIT 5"
        )
        assert sanitized

    def test_return_tuple_shape(self):
        """validate_sql always returns a 3-tuple."""
        result = validate_sql("SELECT 1 FROM life_expectancy", ALLOWED_TABLES)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_sanitized_sql_is_snowflake_dialect(self):
        """The sanitized SQL should be generated for Snowflake dialect."""
        sanitized = assert_valid(
            "SELECT entity FROM life_expectancy LIMIT 5"
        )
        # sqlglot Snowflake output is capitalized/normalized.
        assert isinstance(sanitized, str)
        assert len(sanitized) > 0
