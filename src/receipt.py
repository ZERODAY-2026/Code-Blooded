"""
src/receipt.py — Deterministic SQL Receipt and Explanation Generator.

Rules enforced:
───────────────
- Zero connections to Snowflake, zero network calls, zero file execution.
- Deterministic: parses already-validated SQL using SQLGlot (Snowflake dialect).
- Gemma is NEVER used to explain SQL queries. The explanation is extracted
  strictly from the parsed SQL AST.
- Never claims that a filter, join, or calculation exists unless it is actually
  represented in the parsed query.
- Extracts:
  - CTE names (distinguished from physical tables)
  - Referenced physical tables (handling schemas and database qualification)
  - Selected columns and projections
  - Joins (table and ON condition)
  - Filters (WHERE conditions and HAVING clauses)
  - Aggregations and Window Functions
  - Grouping (GROUP BY)
  - Ordering (ORDER BY) and row limits (LIMIT)
- Fails gracefully on malformed or unsupported statements without raising exceptions.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)


# ── Structured Explanation Model ─────────────────────────────────────────────

class SQLExplanation(BaseModel):
    """Structured representation of query components extracted from SQLGlot AST."""
    ctes: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[str] = Field(default_factory=list)
    window_functions: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    summary_lines: list[str] = Field(default_factory=list)


# ── Core Parser and Extractor ────────────────────────────────────────────────

def explain_sql_ast(sql: str) -> SQLExplanation:
    """
    Parse a Snowflake SQL query and extract its components deterministically.
    Fails gracefully on empty, invalid, or non-SELECT queries.
    """
    raw_sql = (sql or "").strip()
    if not raw_sql:
        return SQLExplanation(summary_lines=["Empty query."])

    try:
        statements = sqlglot.parse(raw_sql, read="snowflake")
    except Exception as err:
        return SQLExplanation(summary_lines=[f"Unable to parse SQL query: {err}"])

    if not statements or statements[0] is None:
        return SQLExplanation(summary_lines=["Empty query."])

    stmt = statements[0]

    if not isinstance(stmt, (exp.Select, exp.Union)):
        return SQLExplanation(
            summary_lines=[f"Statement of type '{stmt.__class__.__name__}' is not an analytical SELECT query."]
        )

    # 1. CTE extraction
    ctes: list[str] = []
    cte_names_set: set[str] = set()
    for cte in stmt.ctes:
        c_name = cte.alias_or_name
        if c_name:
            ctes.append(c_name)
            cte_names_set.add(c_name.lower())

    # 2. Physical Table extraction (excluding CTE references)
    tables: list[str] = []
    seen_tables: set[str] = set()
    for tbl in stmt.find_all(exp.Table):
        tbl_name = tbl.name
        if not tbl_name or tbl_name.lower() in cte_names_set:
            continue

        parts: list[str] = []
        if tbl.catalog:
            parts.append(tbl.catalog)
        if tbl.db:
            parts.append(tbl.db)
        parts.append(tbl_name)
        full_tbl_name = ".".join(parts)

        if full_tbl_name.lower() not in seen_tables:
            seen_tables.add(full_tbl_name.lower())
            tables.append(full_tbl_name)

    # 3. Selected projections
    columns: list[str] = []
    if isinstance(stmt, exp.Select):
        for s in stmt.selects:
            columns.append(s.sql(dialect="snowflake"))

    # 4. Joins
    joins: list[str] = []
    for j in stmt.find_all(exp.Join):
        join_sql = j.sql(dialect="snowflake")
        joins.append(join_sql)

    # 5. Filters (WHERE clauses and HAVING clauses)
    filters: list[str] = []
    for w in stmt.find_all(exp.Where):
        pred_sql = w.this.sql(dialect="snowflake")
        filters.append(pred_sql)

    for h in stmt.find_all(exp.Having):
        having_sql = h.this.sql(dialect="snowflake")
        filters.append(f"HAVING {having_sql}")

    # 6. Aggregates and Window Functions
    aggregates: list[str] = []
    seen_aggs: set[str] = set()
    for agg_type in (exp.Count, exp.Avg, exp.Max, exp.Min, exp.Sum):
        for agg in stmt.find_all(agg_type):
            agg_str = agg.sql(dialect="snowflake")
            if agg_str not in seen_aggs:
                seen_aggs.add(agg_str)
                aggregates.append(agg_str)

    window_functions: list[str] = []
    seen_windows: set[str] = set()
    for win in stmt.find_all(exp.Window):
        win_str = win.sql(dialect="snowflake")
        if win_str not in seen_windows:
            seen_windows.add(win_str)
            window_functions.append(win_str)

    # 7. Grouping
    group_by: list[str] = []
    for g in stmt.find_all(exp.Group):
        for expr in g.expressions:
            group_by.append(expr.sql(dialect="snowflake"))

    # 8. Ordering
    order_by: list[str] = []
    for o in stmt.find_all(exp.Order):
        for expr in o.expressions:
            order_by.append(expr.sql(dialect="snowflake"))

    # 9. Limit
    limit_val: int | None = None
    limit_node = stmt.args.get("limit") or stmt.find(exp.Limit)
    if limit_node is not None:
        count_node = limit_node.expression if isinstance(limit_node, exp.Limit) else limit_node
        try:
            limit_val = int(count_node.this) if hasattr(count_node, "this") else int(str(count_node))
        except (ValueError, TypeError):
            pass

    # 10. Build deterministic summary lines matching handbook receipt format
    summary_lines: list[str] = []

    if ctes:
        summary_lines.append(f"CTE: {', '.join(ctes)}")

    if tables:
        summary_lines.append(f"Table: {', '.join(tables)}")

    if columns:
        summary_lines.append(f"Selected: {', '.join(columns)}")

    if joins:
        summary_lines.append(f"Join: {'; '.join(joins)}")

    if filters:
        summary_lines.append(f"Filter: {'; '.join(filters)}")

    if aggregates:
        summary_lines.append(f"Aggregates: {', '.join(aggregates)}")

    if window_functions:
        summary_lines.append(f"Window: {', '.join(window_functions)}")

    if group_by:
        summary_lines.append(f"Group by: {', '.join(group_by)}")

    # Combine Order and Limit cleanly if both exist
    if order_by and limit_val is not None:
        summary_lines.append(f"Sorted by {', '.join(order_by).lower()}, limit {limit_val}")
    elif order_by:
        summary_lines.append(f"Sorted by: {', '.join(order_by)}")
    elif limit_val is not None:
        summary_lines.append(f"Limit: {limit_val}")

    return SQLExplanation(
        ctes=ctes,
        tables=tables,
        columns=columns,
        joins=joins,
        filters=filters,
        group_by=group_by,
        aggregates=aggregates,
        window_functions=window_functions,
        order_by=order_by,
        limit=limit_val,
        summary_lines=summary_lines,
    )


# ── Public API ───────────────────────────────────────────────────────────────

def explain_sql(sql: str) -> list[str]:
    """
    Return a list of human-readable explanation strings derived deterministically
    from the parsed SQL query.

    Matches AskResponse.explanation: list[str].
    Example output:
      ["Table: life_expectancy", "Filter: year = 2019", "Sorted by life_expectancy desc, limit 5"]
    """
    return explain_sql_ast(sql).summary_lines


def build_receipt_explanation(
    sql: str,
    assumptions: list[str] | None = None,
) -> list[str]:
    """
    Combine deterministic SQL explanation lines with any stated model assumptions.
    """
    lines = explain_sql(sql)
    if assumptions:
        for a in assumptions:
            cleaned = a.strip()
            if cleaned:
                lines.append(f"Assumption: {cleaned}")
    return lines
