"""
tests/test_validate.py — Unit tests for M3 SQLGlot Snowflake-dialect validator.

Rules enforced:
───────────────
- Zero Snowflake connections.
- Zero network calls.
- Validates fail-closed behavior against SQLGlot AST nodes.
- Covers syntax, multi-statements, DDL, DML, CTE attacks, system functions,
  allowlists (table, schema, database, column), SELECT *, and LIMIT constraints.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from src.models import ValidationResult
from src.validate import (
    DatasetSchema,
    SQLValidator,
    enforce_limit,
    is_single_statement,
    validate_sql,
)


@pytest.fixture
def owid_schema() -> DatasetSchema:
    return DatasetSchema(
        name="owid",
        database="datamind",
        schema_name="owid",
        tables={
            "life_expectancy": {"entity", "code", "year", "life_expectancy"},
            "population": {"entity", "code", "year", "population"},
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Valid Queries (Positive Tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestValidQueries:
    def test_simple_select_valid(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True
        assert res.sql is not None
        assert "SELECT" in res.sql
        assert res.error == ""
        assert res.error_code == ""

    def test_fully_qualified_identifiers_valid(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, year FROM DATAMIND.OWID.life_expectancy WHERE year = 2020 LIMIT 10;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True
        assert res.error_code == ""

    def test_table_alias_and_qualified_columns_valid(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT t.entity, t.life_expectancy FROM life_expectancy AS t WHERE t.year = 2015 LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True

    def test_scalar_aggregate_without_group_by_allows_missing_limit(self, owid_schema: DatasetSchema) -> None:
        # Scalar aggregate without GROUP BY always returns 1 row; LIMIT is optional
        sql = "SELECT COUNT(*) FROM life_expectancy WHERE year = 2019;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True

    def test_multiple_scalar_aggregates_without_group_by_valid(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT AVG(life_expectancy) AS avg_le, MAX(year) AS max_yr FROM life_expectancy;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True

    def test_group_by_with_explicit_limit_valid(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, AVG(life_expectancy) AS avg_le FROM life_expectancy GROUP BY entity ORDER BY avg_le DESC LIMIT 10;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True

    def test_cte_valid(self, owid_schema: DatasetSchema) -> None:
        sql = """
        WITH top_countries AS (
            SELECT entity, life_expectancy
            FROM life_expectancy
            WHERE year = 2019
            ORDER BY life_expectancy DESC
            LIMIT 10
        )
        SELECT entity, life_expectancy FROM top_countries LIMIT 5;
        """
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True

    def test_subquery_in_where_clause_valid(self, owid_schema: DatasetSchema) -> None:
        sql = """
        SELECT entity, life_expectancy
        FROM life_expectancy
        WHERE year = (SELECT MAX(year) FROM life_expectancy)
        ORDER BY life_expectancy DESC
        LIMIT 5;
        """
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True

    def test_join_between_allowed_tables_valid(self, owid_schema: DatasetSchema) -> None:
        sql = """
        SELECT le.entity, le.year, le.life_expectancy, p.population
        FROM life_expectancy le
        JOIN population p ON le.entity = p.entity AND le.year = p.year
        WHERE le.year = 2019
        LIMIT 10;
        """
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. Syntax, Empty, and Multi-Statement Errors
# ══════════════════════════════════════════════════════════════════════════════

class TestSyntaxAndStructure:
    def test_empty_sql_rejected(self) -> None:
        res = validate_sql("")
        assert res.valid is False
        assert res.error_code == "EMPTY_QUERY"

    def test_whitespace_sql_rejected(self) -> None:
        res = validate_sql("   \n\t  ")
        assert res.valid is False
        assert res.error_code == "EMPTY_QUERY"

    def test_syntax_error_rejected(self) -> None:
        res = validate_sql("SELECT FROM WHERE")
        assert res.valid is False
        assert res.error_code == "SYNTAX_ERROR"

    def test_multi_statement_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 5; SELECT entity FROM population LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "MULTI_STATEMENT"

    def test_multi_statement_with_drop_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 5; DROP TABLE life_expectancy;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "MULTI_STATEMENT"


# ══════════════════════════════════════════════════════════════════════════════
# 3. DDL, DML, and Administrative Commands Rejection
# ══════════════════════════════════════════════════════════════════════════════

class TestForbiddenOperations:
    @pytest.mark.parametrize(
        "query,expected_code",
        [
            ("DROP TABLE life_expectancy;", "FORBIDDEN_OPERATION"),
            ("DELETE FROM life_expectancy WHERE year = 2019;", "FORBIDDEN_OPERATION"),
            ("UPDATE life_expectancy SET life_expectancy = 99.0 WHERE entity = 'Japan';", "FORBIDDEN_OPERATION"),
            ("INSERT INTO life_expectancy VALUES ('Test', 'TST', 2020, 80.0);", "FORBIDDEN_OPERATION"),
            ("TRUNCATE TABLE life_expectancy;", "FORBIDDEN_OPERATION"),
            ("ALTER TABLE life_expectancy DROP COLUMN code;", "FORBIDDEN_OPERATION"),
            ("CREATE TABLE test (id INT);", "FORBIDDEN_OPERATION"),
            ("GRANT SELECT ON ALL TABLES TO PUBLIC;", "FORBIDDEN_OPERATION"),
            ("REVOKE ALL PRIVILEGES ON DATABASE DATAMIND FROM PUBLIC;", "FORBIDDEN_OPERATION"),
            ("USE ROLE ACCOUNTADMIN;", "FORBIDDEN_OPERATION"),
            ("SHOW TABLES IN SCHEMA OWID;", "FORBIDDEN_OPERATION"),
            ("DESCRIBE TABLE life_expectancy;", "FORBIDDEN_OPERATION"),
        ],
    )
    def test_forbidden_operations_rejected(self, query: str, expected_code: str, owid_schema: DatasetSchema) -> None:
        res = validate_sql(query, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == expected_code

    def test_forbidden_dml_inside_cte_rejected(self, owid_schema: DatasetSchema) -> None:
        # Attacking via CTE
        sql = "WITH malicious AS (DELETE FROM life_expectancy WHERE year = 2019) SELECT 1 LIMIT 1;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_OPERATION"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Forbidden Functions & Reconnaissance
# ══════════════════════════════════════════════════════════════════════════════

class TestForbiddenFunctions:
    def test_system_wait_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT SYSTEM$WAIT(10) FROM life_expectancy LIMIT 1;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_FUNCTION"

    def test_current_user_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT CURRENT_USER() LIMIT 1;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_FUNCTION"

    def test_current_role_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT CURRENT_ROLE() LIMIT 1;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_FUNCTION"

    def test_get_ddl_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT GET_DDL('TABLE', 'life_expectancy') LIMIT 1;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_FUNCTION"


# ══════════════════════════════════════════════════════════════════════════════
# 5. SELECT * (Star) Rejection
# ══════════════════════════════════════════════════════════════════════════════

class TestStarRejection:
    def test_select_star_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT * FROM life_expectancy LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_STAR"
        assert "SELECT *" in res.error

    def test_select_table_star_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT t.* FROM life_expectancy t LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_STAR"

    def test_count_star_permitted(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT COUNT(*) FROM life_expectancy LIMIT 1;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True


# ══════════════════════════════════════════════════════════════════════════════
# 6. Table, Schema, and Database Allowlists
# ══════════════════════════════════════════════════════════════════════════════

class TestTableAllowlist:
    def test_unauthorized_table_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT * FROM users LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        # Catches either FORBIDDEN_STAR or FORBIDDEN_TABLE
        assert res.error_code in ("FORBIDDEN_STAR", "FORBIDDEN_TABLE")

        sql2 = "SELECT username FROM users LIMIT 5;"
        res2 = validate_sql(sql2, schema=owid_schema)
        assert res2.valid is False
        assert res2.error_code == "FORBIDDEN_TABLE"
        assert "users" in res2.error

    def test_unauthorized_schema_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM PUBLIC.life_expectancy LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_SCHEMA"

    def test_unauthorized_database_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM PROD_DB.OWID.life_expectancy LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_DATABASE"

    def test_cte_name_not_treated_as_unauthorized_table(self, owid_schema: DatasetSchema) -> None:
        sql = "WITH my_cte AS (SELECT entity, year FROM life_expectancy LIMIT 10) SELECT entity FROM my_cte LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is True


# ══════════════════════════════════════════════════════════════════════════════
# 7. Column Allowlist
# ══════════════════════════════════════════════════════════════════════════════

class TestColumnAllowlist:
    def test_unauthorized_column_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, secret_salary FROM life_expectancy LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_COLUMN"
        assert "secret_salary" in res.error

    def test_unauthorized_column_with_alias_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT t.admin_notes FROM life_expectancy t LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_COLUMN"

    def test_unauthorized_column_in_where_clause_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM life_expectancy WHERE credit_card IS NOT NULL LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_COLUMN"

    def test_unauthorized_column_in_cte_outer_select_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = """
        WITH cte AS (
            SELECT entity, year FROM life_expectancy LIMIT 10
        )
        SELECT cte.entity, cte.non_existent_column FROM cte LIMIT 5;
        """
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_COLUMN"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Row Limit Enforcement (LIMIT Clause)
# ══════════════════════════════════════════════════════════════════════════════

class TestLimitEnforcement:
    def test_missing_limit_on_analytical_query_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "MISSING_LIMIT"

    def test_missing_limit_on_group_by_query_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, COUNT(*) FROM life_expectancy GROUP BY entity;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "MISSING_LIMIT"

    def test_excessive_limit_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy LIMIT 500;"
        res = validate_sql(sql, schema=owid_schema, max_rows=200)
        assert res.valid is False
        assert res.error_code == "EXCESSIVE_LIMIT"
        assert "500" in res.error

    def test_limit_exactly_at_cap_permitted(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy LIMIT 200;"
        res = validate_sql(sql, schema=owid_schema, max_rows=200)
        assert res.valid is True

    def test_negative_or_zero_limit_rejected(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 0;"
        res = validate_sql(sql, schema=owid_schema)
        assert res.valid is False
        assert res.error_code == "INVALID_LIMIT"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Dataset Schema YAML Loading
# ══════════════════════════════════════════════════════════════════════════════

class TestDatasetSchemaLoading:
    def test_load_from_owid_yaml(self) -> None:
        owid_path = Path("datasets/owid.yaml")
        assert owid_path.exists()

        schema = DatasetSchema.from_yaml(owid_path)
        assert schema.name == "owid"
        assert schema.database == "datamind"
        assert schema.schema_name == "owid"
        assert "life_expectancy" in schema.allowed_tables()
        cols = schema.allowed_columns_for("life_expectancy")
        assert "entity" in cols
        assert "year" in cols
        assert "life_expectancy" in cols

    def test_load_non_existent_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            DatasetSchema.from_yaml("datasets/non_existent.yaml")


# ══════════════════════════════════════════════════════════════════════════════
# 10. Single-Statement Enforcement (is_single_statement)
# ══════════════════════════════════════════════════════════════════════════════

class TestSingleStatement:
    def test_single_select_is_true(self) -> None:
        assert is_single_statement("SELECT 1;") is True
        assert is_single_statement("SELECT entity FROM life_expectancy LIMIT 10") is True

    def test_cte_with_select_is_true(self) -> None:
        sql = "WITH cte AS (SELECT 1 AS val) SELECT val FROM cte LIMIT 1;"
        assert is_single_statement(sql) is True

    def test_multi_statement_is_false(self) -> None:
        assert is_single_statement("SELECT 1; SELECT 2;") is False
        assert is_single_statement("SELECT 1; DROP TABLE users;") is False

    def test_empty_and_whitespace_is_false(self) -> None:
        assert is_single_statement("") is False
        assert is_single_statement("   \n\t  ") is False
        assert is_single_statement("-- just a comment") is False

    def test_syntax_error_is_false(self) -> None:
        assert is_single_statement("SELECT FROM WHERE") is False


# ══════════════════════════════════════════════════════════════════════════════
# 11. LIMIT Insertion and Clamping (enforce_limit)
# ══════════════════════════════════════════════════════════════════════════════

class TestEnforceLimit:
    def test_inserts_limit_when_missing(self) -> None:
        sql = "SELECT entity, year FROM life_expectancy"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT 200" in res

    def test_clamps_excessive_limit(self) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 500"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT 200" in res
        assert "500" not in res

    def test_preserves_smaller_limit(self) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 50"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT 50" in res

    def test_scalar_aggregate_without_group_by_preserved_without_forced_limit(self) -> None:
        sql = "SELECT COUNT(*) FROM life_expectancy"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT" not in res
        assert "COUNT" in res

    def test_scalar_aggregates_multiple_preserved(self) -> None:
        sql = "SELECT AVG(life_expectancy), MAX(year), MIN(year) FROM life_expectancy"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT" not in res

    def test_aggregate_with_group_by_receives_limit(self) -> None:
        sql = "SELECT year, AVG(life_expectancy) FROM life_expectancy GROUP BY year"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT 200" in res

    def test_zero_or_negative_limit_clamped_to_max(self) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 0"
        res = enforce_limit(sql, max_limit=200)
        assert "LIMIT 200" in res

    def test_empty_or_invalid_sql_returns_input_safely(self) -> None:
        assert enforce_limit("") == ""
        assert enforce_limit("SELECT FROM WHERE") == "SELECT FROM WHERE"


# ══════════════════════════════════════════════════════════════════════════════
# 12. Legacy validate_sql Interface (tuple[bool, str])
# ══════════════════════════════════════════════════════════════════════════════

class TestLegacyValidateSql:
    def test_valid_query_returns_tuple_true(self) -> None:
        sql = "SELECT entity, year FROM life_expectancy LIMIT 10;"
        res = validate_sql(sql, ["life_expectancy"])
        assert isinstance(res, tuple)
        valid, out = res
        assert valid is True
        assert "SELECT" in out

    def test_unauthorized_table_returns_tuple_false(self) -> None:
        sql = "SELECT username FROM users LIMIT 10;"
        res = validate_sql(sql, ["life_expectancy"])
        assert isinstance(res, tuple)
        valid, err = res
        assert valid is False
        assert "users" in err

    def test_multi_statement_returns_tuple_false(self) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 10; DROP TABLE life_expectancy;"
        res = validate_sql(sql, ["life_expectancy"])
        assert isinstance(res, tuple)
        valid, err = res
        assert valid is False
        assert "Multiple statements" in err or "Forbidden operation" in err

    def test_empty_allowed_tables_fails_closed(self) -> None:
        sql = "SELECT entity FROM life_expectancy LIMIT 10;"
        res = validate_sql(sql, [])
        assert isinstance(res, tuple)
        valid, err = res
        assert valid is False


# ══════════════════════════════════════════════════════════════════════════════
# 13. Fail-Closed Behavior for Missing/Empty Schema
# ══════════════════════════════════════════════════════════════════════════════

class TestFailClosedMissingSchema:
    def test_none_schema_rejects_physical_table_queries(self) -> None:
        validator = SQLValidator(schema=None)
        res = validator.validate("SELECT entity FROM life_expectancy LIMIT 5;")
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_TABLE"

    def test_empty_schema_rejects_physical_table_queries(self) -> None:
        empty_schema = DatasetSchema(name="empty", tables={})
        validator = SQLValidator(schema=empty_schema)
        res = validator.validate("SELECT entity FROM life_expectancy LIMIT 5;")
        assert res.valid is False
        assert res.error_code == "FORBIDDEN_TABLE"

    def test_table_less_query_permitted_with_none_schema(self) -> None:
        validator = SQLValidator(schema=None)
        res = validator.validate("SELECT 1;")
        assert res.valid is True
        assert res.sql is not None


# ══════════════════════════════════════════════════════════════════════════════
# 14. ValidationResult Tuple Unpacking and Indexing Compatibility
# ══════════════════════════════════════════════════════════════════════════════

class TestValidationResultCompatibility:
    def test_validation_result_supports_unpacking(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        valid, out = res
        assert valid is True
        assert "SELECT" in out

    def test_validation_result_supports_indexing(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        assert res[0] is True
        assert "SELECT" in res[1]

    def test_failed_validation_result_unpacks_error(self, owid_schema: DatasetSchema) -> None:
        sql = "SELECT entity FROM secret_table LIMIT 5;"
        res = validate_sql(sql, schema=owid_schema)
        valid, err = res
        assert valid is False
        assert "secret_table" in err

