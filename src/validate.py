"""
src/validate.py — Fail-closed SQLGlot Snowflake-dialect AST validator.

Design rules
────────────
- Zero connections to Snowflake, zero network calls, zero file execution.
- Fail-closed: everything not explicitly permitted is rejected.
- Uses SQLGlot with read="snowflake".
- Strictly requires exactly one analytical SELECT statement.
- Recursively inspects the full AST (not just the root node) to detect forbidden
  operations: DDL, DML, administrative commands, transaction controls, stages,
  system functions, and disallowed procedures.
- Enforces allowlists for tables, schemas, and columns from dataset metadata.
- Safely resolves CTEs, table aliases, subqueries, and qualified identifiers.
- Enforces configured row limits (explicit LIMIT required unless single-row scalar aggregate).
- Returns structured ValidationResult with sanitized error codes and messages.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

import sqlglot
from pydantic import BaseModel, Field
import yaml
from sqlglot import exp

from src.models import ValidationResult

logger = logging.getLogger(__name__)


# ── Dataset Schema Metadata ──────────────────────────────────────────────────

class DatasetSchema(BaseModel):
    """
    Allowed schema context loaded from dataset YAML files (e.g. datasets/owid.yaml).
    Used by the validator to enforce fail-closed table and column allowlists.
    """
    name: str = ""
    database: str = ""
    schema_name: str = ""
    # Mapping of lowercase table_name -> set of lowercase column names
    tables: dict[str, set[str]] = Field(default_factory=dict)
    table_descriptions: dict[str, str] = Field(default_factory=dict)
    column_descriptions: dict[str, dict[str, str]] = Field(default_factory=dict)
    examples: list[dict[str, str]] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DatasetSchema":
        """Load and extract allowlists from a dataset semantic-layer YAML file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset file not found: {file_path}")

        with file_path.open("r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        name = str(raw.get("name", "")).strip()
        sf_meta = raw.get("snowflake", {})
        database = str(sf_meta.get("database", "")).strip().lower()
        schema_name = str(sf_meta.get("schema", "")).strip().lower()

        tables: dict[str, set[str]] = {}
        table_descriptions: dict[str, str] = {}
        column_descriptions: dict[str, dict[str, str]] = {}

        for tbl_name, tbl_info in raw.get("tables", {}).items():
            tbl_key = str(tbl_name).strip().lower()
            if isinstance(tbl_info, dict):
                table_descriptions[tbl_key] = str(tbl_info.get("description", "")).strip()
                cols = tbl_info.get("columns", {})
                col_set: set[str] = set()
                col_descs: dict[str, str] = {}
                for col_name, col_info in cols.items():
                    col_key = str(col_name).strip().lower()
                    col_set.add(col_key)
                    if isinstance(col_info, dict):
                        col_descs[col_key] = str(col_info.get("description", "")).strip()
                tables[tbl_key] = col_set
                column_descriptions[tbl_key] = col_descs

        raw_examples = raw.get("examples", [])
        examples: list[dict[str, str]] = []
        if isinstance(raw_examples, list):
            for ex in raw_examples:
                if isinstance(ex, dict) and "q" in ex and "sql" in ex:
                    examples.append({"q": str(ex["q"]), "sql": str(ex["sql"])})

        return cls(
            name=name,
            database=database,
            schema_name=schema_name,
            tables=tables,
            table_descriptions=table_descriptions,
            column_descriptions=column_descriptions,
            examples=examples,
        )

    def allowed_tables(self) -> set[str]:
        return set(self.tables.keys())

    def allowed_columns_for(self, table_name: str) -> set[str]:
        return self.tables.get(table_name.lower(), set())

    def format_context(self) -> str:
        """Render a clean text summary of allowed tables and columns for LLM prompts."""
        lines = []
        if self.database and self.schema_name:
            lines.append(f"Database: {self.database.upper()} | Schema: {self.schema_name.upper()}")
        for tbl, cols in self.tables.items():
            desc = self.table_descriptions.get(tbl, "")
            header = f"Table: {tbl}" + (f" ({desc})" if desc else "")
            lines.append(header)
            lines.append("Columns:")
            tbl_col_descs = self.column_descriptions.get(tbl, {})
            for c in sorted(cols):
                c_desc = tbl_col_descs.get(c, "")
                lines.append(f"  - {c}" + (f": {c_desc}" if c_desc else ""))
            lines.append("")
        return "\n".join(lines).strip()


# ── Forbidden AST Node Types ──────────────────────────────────────────────────

_FORBIDDEN_AST_NODES: tuple[type[exp.Expression], ...] = (
    # DML operations
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    # DDL operations
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    # Transaction controls
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    # Permissions and administrative commands
    exp.Grant,
    exp.Set,
    exp.Command,
    # Session / inspection commands
    exp.Use,
    exp.Kill,
    exp.Describe,
    exp.Show,
)

# Functions that perform system actions, introspection, reconnaissance, or sleep
_FORBIDDEN_FUNCTION_NAMES: frozenset[str] = frozenset({
    "current_user",
    "current_role",
    "current_account",
    "current_session",
    "current_ip_address",
    "current_client",
    "current_version",
    "get_ddl",
    "system$wait",
    "system$send_email",
    "system$whitelist",
    "system$clustering_information",
    "system$clustering_depth",
})

# Aggregate functions that collapse rows into a scalar value
_SCALAR_AGGREGATE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Count,
    exp.Avg,
    exp.Max,
    exp.Min,
    exp.Sum,
)


# ── SQL Validator Implementation ─────────────────────────────────────────────

class SQLValidator:
    """
    Fail-closed validator for Snowflake SQL queries.
    """

    def __init__(
        self,
        schema: DatasetSchema | None = None,
        *,
        max_rows: int = 200,
    ) -> None:
        self.schema = schema
        self.max_rows = max_rows

    def validate(self, sql: str) -> ValidationResult:
        """
        Validate an analytical SQL query against security policies and allowlists.

        Returns ValidationResult with:
          - valid: True if all checks pass, False otherwise
          - sql: Normalised Snowflake SQL string if valid, None if invalid
          - error: Human-readable sanitised error message if invalid, "" if valid
          - error_code: Machine-readable error code if invalid, "" if valid
        """
        raw_sql = (sql or "").strip()
        if not raw_sql:
            return ValidationResult(
                valid=False,
                sql=None,
                error="SQL query is empty.",
                error_code="EMPTY_QUERY",
            )

        # Check for explicitly forbidden statement prefixes that may fail SQLGlot parser
        upper_trimmed = raw_sql.upper().lstrip()
        if upper_trimmed.startswith("REVOKE"):
            return ValidationResult(
                valid=False,
                sql=None,
                error="Forbidden operation 'REVOKE' detected. Administrative and permission commands are strictly forbidden.",
                error_code="FORBIDDEN_OPERATION",
            )

        # 1. Parse using SQLGlot Snowflake dialect
        try:
            statements = sqlglot.parse(raw_sql, read="snowflake")
        except Exception as parse_err:
            return ValidationResult(
                valid=False,
                sql=None,
                error="SQL parse error: query syntax is invalid for Snowflake.",
                error_code="SYNTAX_ERROR",
            )

        if not statements or statements[0] is None:
            return ValidationResult(
                valid=False,
                sql=None,
                error="SQL query is empty or contains no executable statements.",
                error_code="EMPTY_QUERY",
            )

        # 2. Require exactly one statement
        if len(statements) > 1:
            return ValidationResult(
                valid=False,
                sql=None,
                error="Multiple statements are forbidden. Exactly one analytical query is permitted.",
                error_code="MULTI_STATEMENT",
            )

        stmt = statements[0]

        # 3. Deep search for forbidden AST nodes anywhere in the tree (including inside CTEs or subqueries)
        for forbidden_type in _FORBIDDEN_AST_NODES:
            found = stmt.find(forbidden_type)
            if found is not None:
                op_name = forbidden_type.__name__
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error=f"Forbidden operation '{op_name}' detected. DDL, DML, and administrative commands are strictly forbidden.",
                    error_code="FORBIDDEN_OPERATION",
                )

        # 4. Require top-level expression to be Select or Union
        if not isinstance(stmt, (exp.Select, exp.Union)):
            return ValidationResult(
                valid=False,
                sql=None,
                error=f"Statement of type '{stmt.__class__.__name__}' is not permitted. Only analytical SELECT queries are allowed.",
                error_code="NOT_SELECT",
            )

        # 5. Check for disallowed functions (system functions, session introspection, etc.)
        if stmt.find(exp.CurrentUser):
            return ValidationResult(
                valid=False,
                sql=None,
                error="Function 'CURRENT_USER' is forbidden.",
                error_code="FORBIDDEN_FUNCTION",
            )

        for func in stmt.find_all(exp.Func, exp.Anonymous):
            func_name = getattr(func, "name", "")
            if not func_name and isinstance(func, exp.Func):
                func_name = func.__class__.__name__
            func_name_lower = func_name.lower()

            if func_name_lower.startswith("system$") or func_name_lower in _FORBIDDEN_FUNCTION_NAMES:
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error=f"Function '{func_name}' is forbidden.",
                    error_code="FORBIDDEN_FUNCTION",
                )

        # 6. Check for SELECT * (forbidden unless inside COUNT(*))
        for star in stmt.find_all(exp.Star):
            parent = star.parent
            if not isinstance(parent, exp.Count):
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error="SELECT * is forbidden. Specify explicit column names.",
                    error_code="FORBIDDEN_STAR",
                )

        # 7. Collect CTE and subquery aliases to permit valid non-physical table references
        cte_names: set[str] = set()
        cte_columns: dict[str, set[str]] = {}
        for cte in stmt.ctes:
            cte_name = cte.alias_or_name.lower()
            cte_names.add(cte_name)
            # Collect output columns of CTE
            cte_selects = getattr(cte.this, "selects", [])
            cte_columns[cte_name] = {
                s.alias_or_name.lower() for s in cte_selects if getattr(s, "alias_or_name", None)
            }

        subquery_aliases: set[str] = set()
        subquery_columns: dict[str, set[str]] = {}
        for sub in stmt.find_all(exp.Subquery):
            if sub.alias:
                sub_name = sub.alias.lower()
                subquery_aliases.add(sub_name)
                sub_selects = getattr(sub.this, "selects", [])
                subquery_columns[sub_name] = {
                    s.alias_or_name.lower() for s in sub_selects if getattr(s, "alias_or_name", None)
                }

        non_physical_tables = cte_names | subquery_aliases

        # 8. Enforce Table Allowlist & Schema Qualification
        allowed_tables = self.schema.allowed_tables() if self.schema else set()
        allowed_db = self.schema.database.lower() if self.schema and self.schema.database else None
        allowed_sch = self.schema.schema_name.lower() if self.schema and self.schema.schema_name else None

        alias_to_table: dict[str, str] = {}
        physical_tables_used: set[str] = set()

        for tbl in stmt.find_all(exp.Table):
            tbl_name = tbl.name.lower()

            # Skip CTEs and subquery aliases
            if tbl_name in non_physical_tables:
                continue

            # Fail closed: table must be explicitly permitted by the schema allowlist
            if not allowed_tables or tbl_name not in allowed_tables:
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error=f"Table '{tbl.name}' is not in the allowed tables list.",
                    error_code="FORBIDDEN_TABLE",
                )

            physical_tables_used.add(tbl_name)
            if tbl.alias:
                alias_to_table[tbl.alias.lower()] = tbl_name

            # Check schema qualification
            if tbl.db:
                if not allowed_sch or tbl.db.lower() != allowed_sch:
                    return ValidationResult(
                        valid=False,
                        sql=None,
                        error=f"Schema '{tbl.db}' is not permitted. Only schema '{self.schema.schema_name if self.schema else ''}' is allowed.",
                        error_code="FORBIDDEN_SCHEMA",
                    )

            # Check database qualification
            if tbl.catalog:
                if not allowed_db or tbl.catalog.lower() != allowed_db:
                    return ValidationResult(
                        valid=False,
                        sql=None,
                        error=f"Database '{tbl.catalog}' is not permitted. Only database '{self.schema.database if self.schema else ''}' is allowed.",
                        error_code="FORBIDDEN_DATABASE",
                    )

        # 9. Enforce Column Allowlist
        if self.schema and self.schema.tables:
            # Aggregate all allowed columns across physical tables in the query
            all_query_allowed_cols: set[str] = set()
            for t in physical_tables_used:
                all_query_allowed_cols.update(self.schema.allowed_columns_for(t))
            for cols in cte_columns.values():
                all_query_allowed_cols.update(cols)
            for cols in subquery_columns.values():
                all_query_allowed_cols.update(cols)
            for alias_node in stmt.find_all(exp.Alias):
                if alias_node.alias:
                    all_query_allowed_cols.add(alias_node.alias.lower())

            for col in stmt.find_all(exp.Column):
                col_name = col.name.lower()
                qualifier = col.table.lower() if col.table else None

                if qualifier:
                    if qualifier in cte_columns:
                        # Column belongs to a CTE
                        if cte_columns[qualifier] and col_name not in cte_columns[qualifier]:
                            return ValidationResult(
                                valid=False,
                                sql=None,
                                error=f"Column '{col.name}' is not defined in CTE '{col.table}'.",
                                error_code="FORBIDDEN_COLUMN",
                            )
                    elif qualifier in subquery_columns:
                        # Column belongs to a subquery alias
                        if subquery_columns[qualifier] and col_name not in subquery_columns[qualifier]:
                            return ValidationResult(
                                valid=False,
                                sql=None,
                                error=f"Column '{col.name}' is not defined in subquery '{col.table}'.",
                                error_code="FORBIDDEN_COLUMN",
                            )
                    else:
                        # Column belongs to a physical table or table alias
                        target_tbl = alias_to_table.get(qualifier, qualifier)
                        tbl_allowed = self.schema.allowed_columns_for(target_tbl)
                        if tbl_allowed and col_name not in tbl_allowed:
                            return ValidationResult(
                                valid=False,
                                sql=None,
                                error=f"Column '{col.name}' is not permitted for table '{target_tbl}'.",
                                error_code="FORBIDDEN_COLUMN",
                            )
                else:
                    # Unqualified column
                    if all_query_allowed_cols and col_name not in all_query_allowed_cols:
                        return ValidationResult(
                            valid=False,
                            sql=None,
                            error=f"Column '{col.name}' is not recognized in the schema.",
                            error_code="FORBIDDEN_COLUMN",
                        )

        # 10. Enforce Row Limits (LIMIT clause)
        limit_expr = stmt.args.get("limit") or stmt.find(exp.Limit)
        if limit_expr is not None:
            # Extract limit count
            count_node = limit_expr.expression if isinstance(limit_expr, exp.Limit) else limit_expr
            try:
                limit_val = int(count_node.this) if hasattr(count_node, "this") else int(str(count_node))
            except (ValueError, TypeError):
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error="LIMIT clause must be a positive integer literal.",
                    error_code="INVALID_LIMIT",
                )

            if limit_val <= 0:
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error="LIMIT value must be greater than 0.",
                    error_code="INVALID_LIMIT",
                )

            if limit_val > self.max_rows:
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error=f"Query LIMIT ({limit_val}) exceeds maximum permitted rows ({self.max_rows}).",
                    error_code="EXCESSIVE_LIMIT",
                )
        else:
            # No LIMIT clause. Check if it's an ungrouped scalar aggregate returning 1 row.
            is_single_row_scalar_aggregate = False
            if isinstance(stmt, exp.Select):
                has_group_by = stmt.args.get("group") is not None
                if not has_group_by and stmt.expressions:
                    # Check if all top-level expressions are aggregates or literals
                    all_aggregates = True
                    for expr in stmt.expressions:
                        if isinstance(expr, exp.Alias):
                            expr = expr.this
                        if not isinstance(expr, _SCALAR_AGGREGATE_TYPES) and not isinstance(expr, exp.Literal):
                            all_aggregates = False
                            break
                    if all_aggregates:
                        is_single_row_scalar_aggregate = True

            if not is_single_row_scalar_aggregate:
                return ValidationResult(
                    valid=False,
                    sql=None,
                    error=f"Missing LIMIT clause. Analytical queries must include an explicit LIMIT <= {self.max_rows}.",
                    error_code="MISSING_LIMIT",
                )

        # 11. Normalise SQL output using Snowflake dialect
        normalised_sql = stmt.sql(dialect="snowflake")

        return ValidationResult(
            valid=True,
            sql=normalised_sql,
            error="",
            error_code="",
        )


def is_single_statement(sql: str) -> bool:
    """
    Check if the SQL string contains exactly one valid SQL statement.
    Returns False for empty queries, parse errors, comments-only, or multiple statements.
    """
    raw_sql = (sql or "").strip()
    if not raw_sql:
        return False
    try:
        statements = sqlglot.parse(raw_sql, read="snowflake")
    except Exception:
        return False
    valid_stmts = [s for s in statements if s is not None]
    return len(valid_stmts) == 1


def enforce_limit(sql: str, max_limit: int = 200) -> str:
    """
    Ensure the SQL query has a LIMIT clause <= max_limit.
    - Clamps existing LIMIT values exceeding max_limit to max_limit.
    - Inserts LIMIT max_limit if missing, unless query is an ungrouped scalar aggregate.
    - Preserves existing LIMIT if 0 < LIMIT <= max_limit.
    - Returns the modified SQL string (or raw SQL on empty/parse error).
    """
    raw_sql = (sql or "").strip()
    if not raw_sql:
        return sql
    try:
        parsed = sqlglot.parse_one(raw_sql, read="snowflake")
    except Exception:
        return sql

    if not isinstance(parsed, (exp.Select, exp.Union)):
        return sql

    # Check for single-row scalar aggregate exception (COUNT, AVG, etc. without GROUP BY)
    is_scalar_agg = False
    if isinstance(parsed, exp.Select):
        has_group = parsed.args.get("group") is not None
        if not has_group and parsed.expressions:
            all_agg = True
            for e in parsed.expressions:
                if isinstance(e, exp.Alias):
                    e = e.this
                if not isinstance(e, _SCALAR_AGGREGATE_TYPES) and not isinstance(e, exp.Literal):
                    all_agg = False
                    break
            if all_agg:
                is_scalar_agg = True

    if is_scalar_agg:
        return parsed.sql(dialect="snowflake")

    limit_expr = parsed.args.get("limit") or parsed.find(exp.Limit)
    if limit_expr is not None:
        count_node = limit_expr.expression if isinstance(limit_expr, exp.Limit) else limit_expr
        try:
            val = int(count_node.this) if hasattr(count_node, "this") else int(str(count_node))
            if val > max_limit or val <= 0:
                parsed.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))
        except Exception:
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))
    else:
        parsed.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))

    return parsed.sql(dialect="snowflake")


def validate_sql(
    sql: str,
    allowed_tables: DatasetSchema | Iterable[str] | None = None,
    *,
    schema: DatasetSchema | None = None,
    max_rows: int = 200,
) -> ValidationResult | tuple[bool, str]:
    """
    Validate a SQL query against security policies and schema allowlists.

    Supports both:
    1. Legacy signature:
       validate_sql(sql, allowed_tables) -> tuple[bool, str]
       where allowed_tables is a list, set, or iterable of table names.
    2. Pipeline signature:
       validate_sql(sql, schema=DatasetSchema, max_rows=200) -> ValidationResult
       or validate_sql(sql, DatasetSchema) -> ValidationResult.
    """
    is_legacy_call = False
    active_schema: DatasetSchema | None = None

    if schema is not None:
        active_schema = schema
    elif isinstance(allowed_tables, DatasetSchema):
        active_schema = allowed_tables
    elif allowed_tables is not None:
        is_legacy_call = True
        table_set = {str(t).strip().lower() for t in allowed_tables}
        active_schema = DatasetSchema(
            name="legacy",
            tables={t: set() for t in table_set},
        )

    validator = SQLValidator(schema=active_schema, max_rows=max_rows)
    result = validator.validate(sql)

    if is_legacy_call:
        msg = result.sql if result.valid else result.error
        return (result.valid, msg or "")

    return result


__all__ = [
    "DatasetSchema",
    "SQLValidator",
    "ValidationResult",
    "validate_sql",
    "is_single_statement",
    "enforce_limit",
]

