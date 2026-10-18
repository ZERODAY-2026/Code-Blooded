"""
SQL validation and sanitization using sqlglot (Snowflake dialect).

Enforces all guardrails defined in the handbook (Section 6, 10, 12) and
the sql-safety skill:
  1. Single statement only (no multi-statement injection).
  2. SELECT-only — blocks DDL/DML/admin operations.
  3. Table allowlist — every referenced table must be in the allowed set.
  4. LIMIT enforcement — injects or clamps to max_limit.

All schema information is loaded dynamically from dataset YAML files;
no table or column names are hardcoded here.
"""

from __future__ import annotations

from typing import List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

# ── Forbidden AST expression types ──────────────────────────────────────────
# Covers every mutation, DDL, DCL, and admin statement listed in the handbook
# (Section 12) and AGENTS.md.  exp.Command catches CALL, COPY, GRANT,
# TRUNCATE, and any other free-form Snowflake command.
FORBIDDEN_EXPRESSIONS: Tuple[type, ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.Create,
    exp.Command,       # Covers CALL, SET, USE, and other free-form commands.
    exp.TruncateTable, # TRUNCATE TABLE — parsed as its own type in sqlglot.
    exp.Copy,          # COPY INTO — parsed as its own type in sqlglot.
    exp.Grant,         # GRANT — parsed as its own type in sqlglot.
)


class SQLValidationError(Exception):
    """Raised when a SQL string fails any validation check."""


def validate_sql(
    sql: str,
    allowed_tables: List[str],
    max_limit: int = 200,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validate and sanitize a SQL string against the project guardrails.

    Args:
        sql: Raw SQL string (potentially from Gemma).
        allowed_tables: List of table names permitted by the current dataset
            YAML.  Compared case-insensitively.
        max_limit: Maximum number of rows the query may return.  A missing
            LIMIT clause is injected; an excessive one is clamped.

    Returns:
        A 3-tuple ``(is_valid, error_message, sanitized_sql)``.
        On success ``is_valid`` is ``True``, ``error_message`` is ``None``,
        and ``sanitized_sql`` contains the Snowflake-dialect SQL with a
        guaranteed LIMIT.
        On failure ``is_valid`` is ``False``, ``error_message`` describes the
        problem, and ``sanitized_sql`` is ``None``.
    """
    try:
        sanitized = _validate_and_sanitize(sql, allowed_tables, max_limit)
        return (True, None, sanitized)
    except SQLValidationError as exc:
        return (False, str(exc), None)


# ── Internal implementation ─────────────────────────────────────────────────

def _validate_and_sanitize(
    sql: str,
    allowed_tables: List[str],
    max_limit: int,
) -> str:
    """Core validation logic.  Raises ``SQLValidationError`` on any failure."""

    # ── 1. Parse using Snowflake dialect ────────────────────────────────────
    try:
        statements = sqlglot.parse(sql, read="snowflake")
    except sqlglot.errors.ParseError as exc:
        raise SQLValidationError(f"SQL parse error: {exc}") from exc

    # Filter out None entries that sqlglot may return for empty/trailing
    # semicolons on a single statement (e.g. "SELECT 1;").
    statements = [s for s in statements if s is not None]

    if len(statements) == 0:
        raise SQLValidationError("Empty SQL input.")

    if len(statements) > 1:
        raise SQLValidationError(
            "Only a single SQL statement is allowed. "
            "Multiple statements (including semicolon-separated) are rejected."
        )

    statement = statements[0]

    # ── 2. Reject forbidden (non-SELECT) expression types ──────────────────
    # Check the root node itself first.
    if isinstance(statement, FORBIDDEN_EXPRESSIONS):
        raise SQLValidationError(
            f"Forbidden SQL operation: {type(statement).__name__}. "
            "Only SELECT queries are allowed."
        )

    # Walk the entire AST to catch forbidden operations hidden inside CTEs,
    # subqueries, or other nested constructs.
    for node in statement.walk():
        if isinstance(node, FORBIDDEN_EXPRESSIONS):
            raise SQLValidationError(
                f"Forbidden SQL operation detected in query: "
                f"{type(node).__name__}. Only SELECT queries are allowed."
            )

    # The root must be a data-reading statement: SELECT, UNION, or a CTE
    # (WITH) that ultimately resolves to a SELECT.
    if not isinstance(statement, (exp.Select, exp.Union, exp.Subquery)):
        # Also allow exp.Paren wrapping a select, or CTEs via exp.Select
        # with a WITH clause — sqlglot represents CTEs as part of
        # the Select node via the "with" arg, so a valid CTE query will
        # already be an exp.Select.
        raise SQLValidationError(
            "Query must be a SELECT statement (including CTEs and UNIONs). "
            f"Got: {type(statement).__name__}."
        )

    # ── 3. Table allowlist ──────────────────────────────────────────────────
    allowed_lower: Set[str] = {t.lower() for t in allowed_tables}

    # Collect CTE names so they are not treated as base tables.
    cte_names: Set[str] = set()
    for cte_node in statement.find_all(exp.CTE):
        alias = cte_node.alias
        if alias:
            cte_names.add(alias.lower())

    referenced_tables: Set[str] = set()
    disallowed: Set[str] = set()
    for table_node in statement.find_all(exp.Table):
        table_name = table_node.name
        schema_name = table_node.db
        if table_name and table_name.lower() not in cte_names:
            if schema_name and schema_name.lower() not in {"owid", "tpch_sf1", "public", ""}:
                disallowed.add(f"{schema_name}.{table_name}")
            referenced_tables.add(table_name.lower())

    disallowed.update(referenced_tables - allowed_lower)
    if disallowed:
        raise SQLValidationError(
            f"Tables not in allowlist: {sorted(disallowed)}. "
            f"Allowed tables: {sorted(allowed_tables)}."
        )

    # ── 4. Enforce LIMIT ───────────────────────────────────────────────────
    # For UNIONs, sqlglot stores the top-level LIMIT on the Union node.
    # For plain SELECTs, it's on the Select node.
    _enforce_limit(statement, max_limit)

    # ── Generate clean Snowflake SQL ────────────────────────────────────────
    return statement.sql(dialect="snowflake")


def _enforce_limit(statement: exp.Expression, max_limit: int) -> None:
    """Inject or clamp a LIMIT clause on *statement* in-place."""
    limit_node = statement.args.get("limit")
    if limit_node is None:
        # No LIMIT present — inject one.
        statement.set(
            "limit",
            exp.Limit(expression=exp.Literal.number(max_limit)),
        )
    else:
        # LIMIT present — clamp if it exceeds the cap.
        try:
            limit_expr = limit_node.expression
            current_limit = int(limit_expr.this) if isinstance(limit_expr, exp.Literal) else int(str(limit_expr))
            if current_limit > max_limit:
                statement.set(
                    "limit",
                    exp.Limit(expression=exp.Literal.number(max_limit)),
                )
        except (ValueError, AttributeError, TypeError):
            # Cannot determine the limit value — replace with the cap to be safe.
            statement.set(
                "limit",
                exp.Limit(expression=exp.Literal.number(max_limit)),
            )
