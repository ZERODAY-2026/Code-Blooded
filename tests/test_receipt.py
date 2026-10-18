"""
tests/test_receipt.py — Unit tests for M5 deterministic SQL receipt generator.

Rules enforced:
───────────────
- Zero Snowflake calls, zero network calls.
- Deterministic extraction: verified against AST representations.
- Tests simple SELECTs, qualified tables, aliases, joins, CTEs, filters,
  aggregates, window functions, GROUP BY, ORDER BY, LIMIT, malformed SQL,
  unsupported statements, and absence of hallucinated clauses.
"""

from __future__ import annotations

import pytest

from src.receipt import (
    SQLExplanation,
    build_receipt_explanation,
    explain_sql,
    explain_sql_ast,
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Simple SELECT, Filter, Order, and Limit
# ══════════════════════════════════════════════════════════════════════════════

class TestSimpleSelectReceipt:
    def test_basic_select_receipt(self) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5;"
        lines = explain_sql(sql)

        assert any("life_expectancy" in line and "Table" in line for line in lines)
        assert any("year = 2019" in line and "Filter" in line for line in lines)
        assert any("Sorted by" in line and "life_expectancy desc" in line.lower() for line in lines)
        assert any("5" in line for line in lines)

    def test_ast_details_structured(self) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5;"
        details = explain_sql_ast(sql)

        assert isinstance(details, SQLExplanation)
        assert details.tables == ["life_expectancy"]
        assert details.filters == ["year = 2019"]
        assert "life_expectancy DESC" in details.order_by
        assert details.limit == 5
        assert details.ctes == []
        assert details.joins == []


# ══════════════════════════════════════════════════════════════════════════════
# 2. Qualified Identifiers and Aliases
# ══════════════════════════════════════════════════════════════════════════════

class TestQualifiedAndAliased:
    def test_fully_qualified_table_name(self) -> None:
        sql = "SELECT entity, year FROM DATAMIND.OWID.life_expectancy LIMIT 10;"
        details = explain_sql_ast(sql)

        assert details.tables == ["DATAMIND.OWID.life_expectancy"]
        assert details.limit == 10
        assert any("DATAMIND.OWID.life_expectancy" in line for line in details.summary_lines)

    def test_aliased_columns_and_tables(self) -> None:
        sql = "SELECT t.entity AS country, t.life_expectancy AS le FROM life_expectancy AS t LIMIT 5;"
        details = explain_sql_ast(sql)

        assert details.tables == ["life_expectancy"]
        assert "t.entity AS country" in details.columns
        assert "t.life_expectancy AS le" in details.columns


# ══════════════════════════════════════════════════════════════════════════════
# 3. Joins
# ══════════════════════════════════════════════════════════════════════════════

class TestJoinsReceipt:
    def test_inner_join_extracted(self) -> None:
        sql = """
        SELECT le.entity, le.year, p.population
        FROM life_expectancy le
        JOIN population p ON le.entity = p.entity AND le.year = p.year
        LIMIT 10;
        """
        details = explain_sql_ast(sql)

        assert len(details.joins) == 1
        assert "population" in details.joins[0]
        assert "le.entity = p.entity" in details.joins[0]
        assert any("Join" in line and "population" in line for line in details.summary_lines)

    def test_left_join_extracted(self) -> None:
        sql = """
        SELECT le.entity, p.population
        FROM life_expectancy le
        LEFT JOIN population p ON le.entity = p.entity
        LIMIT 5;
        """
        details = explain_sql_ast(sql)

        assert len(details.joins) == 1
        assert "LEFT JOIN" in details.joins[0]


# ══════════════════════════════════════════════════════════════════════════════
# 4. CTEs
# ══════════════════════════════════════════════════════════════════════════════

class TestCTEReceipt:
    def test_cte_name_distinguished_from_tables(self) -> None:
        sql = """
        WITH top_10 AS (
            SELECT entity, life_expectancy
            FROM life_expectancy
            WHERE year = 2019
            LIMIT 10
        )
        SELECT entity, life_expectancy FROM top_10 LIMIT 5;
        """
        details = explain_sql_ast(sql)

        assert details.ctes == ["top_10"]
        # Only physical tables should be listed under tables
        assert details.tables == ["life_expectancy"]
        assert any("CTE" in line and "top_10" in line for line in details.summary_lines)
        assert any("Table" in line and "life_expectancy" in line for line in details.summary_lines)

    def test_multiple_ctes(self) -> None:
        sql = """
        WITH cte_a AS (SELECT entity FROM life_expectancy LIMIT 10),
             cte_b AS (SELECT entity FROM population LIMIT 10)
        SELECT cte_a.entity FROM cte_a JOIN cte_b ON cte_a.entity = cte_b.entity LIMIT 5;
        """
        details = explain_sql_ast(sql)

        assert "cte_a" in details.ctes
        assert "cte_b" in details.ctes
        assert "life_expectancy" in details.tables
        assert "population" in details.tables


# ══════════════════════════════════════════════════════════════════════════════
# 5. Aggregates, Group By, Having, and Window Functions
# ══════════════════════════════════════════════════════════════════════════════

class TestAggregatesAndGrouping:
    def test_aggregates_and_group_by(self) -> None:
        sql = "SELECT entity, AVG(life_expectancy) AS avg_le, COUNT(*) FROM life_expectancy GROUP BY entity LIMIT 10;"
        details = explain_sql_ast(sql)

        assert "entity" in details.group_by
        assert any("AVG(life_expectancy)" in a for a in details.aggregates)
        assert any("COUNT(*)" in a for a in details.aggregates)
        assert any("Group by" in line and "entity" in line for line in details.summary_lines)
        assert any("Aggregates" in line and "AVG" in line for line in details.summary_lines)

    def test_having_clause_recorded_in_filters(self) -> None:
        sql = "SELECT entity, COUNT(*) FROM life_expectancy GROUP BY entity HAVING COUNT(*) > 50 LIMIT 10;"
        details = explain_sql_ast(sql)

        assert any("HAVING COUNT(*) > 50" in f for f in details.filters)
        assert any("HAVING" in line for line in details.summary_lines)

    def test_window_function_recorded(self) -> None:
        sql = "SELECT entity, AVG(life_expectancy) OVER (PARTITION BY entity) AS moving_avg FROM life_expectancy LIMIT 5;"
        details = explain_sql_ast(sql)

        assert len(details.window_functions) >= 1
        assert any("OVER" in w for w in details.window_functions)
        assert any("Window" in line for line in details.summary_lines)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Absence of Hallucinated Clauses (Determinism)
# ══════════════════════════════════════════════════════════════════════════════

class TestNoHallucinations:
    def test_query_without_where_has_no_filter_line(self) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5;"
        lines = explain_sql(sql)

        assert not any("Filter:" in line for line in lines)
        assert not any("Join:" in line for line in lines)
        assert not any("CTE:" in line for line in lines)
        assert not any("Group by:" in line for line in lines)

    def test_query_without_limit(self) -> None:
        sql = "SELECT COUNT(*) FROM life_expectancy;"
        details = explain_sql_ast(sql)

        assert details.limit is None
        assert not any("Limit:" in line for line in details.summary_lines)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Malformed and Unsupported Queries (Graceful Failure)
# ══════════════════════════════════════════════════════════════════════════════

class TestGracefulFailures:
    def test_empty_sql_returns_graceful_line(self) -> None:
        lines = explain_sql("")
        assert lines == ["Empty query."]

    def test_whitespace_sql_returns_graceful_line(self) -> None:
        lines = explain_sql("   \n\t  ")
        assert lines == ["Empty query."]

    def test_malformed_sql_returns_graceful_line(self) -> None:
        lines = explain_sql("SELECT FROM WHERE")
        assert len(lines) == 1
        assert "Unable to parse" in lines[0]

    def test_unsupported_statement_type_returns_graceful_line(self) -> None:
        lines = explain_sql("DROP TABLE life_expectancy;")
        assert len(lines) == 1
        assert "not an analytical SELECT query" in lines[0]


# ══════════════════════════════════════════════════════════════════════════════
# 8. Assumptions Integration
# ══════════════════════════════════════════════════════════════════════════════

class TestAssumptionsIntegration:
    def test_build_receipt_explanation_appends_assumptions(self) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 5;"
        assumptions = [
            "Filtered to year 2019",
            "Assumed top 5 descending",
        ]
        lines = build_receipt_explanation(sql, assumptions=assumptions)

        assert any("Table:" in line for line in lines)
        assert "Assumption: Filtered to year 2019" in lines
        assert "Assumption: Assumed top 5 descending" in lines

    def test_build_receipt_explanation_with_no_assumptions(self) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 5;"
        lines = build_receipt_explanation(sql, assumptions=None)

        assert not any("Assumption:" in line for line in lines)
