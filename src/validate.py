"""
src/validate.py

SQL Safety and Guardrail Engine powered by sqlglot.
Performs AST-level validation (no regex shortcuts), statement isolation,
table allowlist enforcement, and deterministic LIMIT clamping.

Handbook Section 12 Specification:
- SELECT-only enforcement
- Single-statement constraint
- Strict table allowlist (subqueries & CTEs included)
- Automatic LIMIT injection / clamping
"""

import re
from typing import Tuple, List, Set
import sqlglot
from sqlglot import exp

# Statements strictly forbidden from execution
BLOCKED_EXPRESSION_TYPES = (
    exp.Drop,
    exp.Delete,
    exp.Insert,
    exp.Update,
    exp.Alter,
    exp.Command,
    exp.Create,
    exp.Merge,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
)


def is_single_statement(sql: str) -> bool:
    """Returns True if the SQL string parses into exactly one statement."""
    if not sql or not sql.strip():
        return False
    try:
        parsed = sqlglot.parse(sql, read="snowflake")
        return len(parsed) == 1
    except Exception:
        # Fallback to standard dialect
        try:
            parsed = sqlglot.parse(sql)
            return len(parsed) == 1
        except Exception:
            return False


def validate_sql(sql: str, allowed_tables: List[str]) -> Tuple[bool, str]:
    """
    Validates a SQL query using sqlglot AST parsing.

    Args:
        sql: The SQL query string to inspect.
        allowed_tables: List of permitted table names (case-insensitive).

    Returns:
        (True, "") if query is safe and compliant.
        (False, error_message) if query violates any safety or allowlist rule.
    """
    if not sql or not sql.strip():
        return False, "Empty or whitespace-only SQL query."

    # 1. Parse into AST
    try:
        trees = sqlglot.parse(sql, read="snowflake")
    except Exception as e:
        try:
            trees = sqlglot.parse(sql)
        except Exception as e2:
            return False, f"SQL syntax error: unable to parse query ({str(e2)})."

    if not trees:
        return False, "No valid SQL statement found."

    if len(trees) > 1:
        return False, f"Multi-statement queries blocked: found {len(trees)} statements (semicolon-separated)."

    tree = trees[0]

    # 2. Blocked statement checks
    if isinstance(tree, BLOCKED_EXPRESSION_TYPES):
        stmt_name = type(tree).__name__.upper()
        return False, f"Blocked statement type '{stmt_name}': Only read-only SELECT queries are permitted."

    # Check for any nested DML/DDL inside the tree
    for blocked_type in BLOCKED_EXPRESSION_TYPES:
        if tree.find(blocked_type):
            return False, f"Query contains prohibited '{blocked_type.__name__.upper()}' operation."

    # Ensure root statement is a SELECT (or Union)
    if not isinstance(tree, (exp.Select, exp.Union)):
        return False, f"Non-SELECT statement type '{type(tree).__name__}' is not allowed."

    # 3. Allowlist validation
    # Collect CTE names so CTE aliases in FROM clauses are not flagged as external tables
    cte_names: Set[str] = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(cte.alias.upper())
        elif hasattr(cte, "this") and hasattr(cte.this, "alias"):
            cte_names.add(cte.this.alias.upper())

    allowed_set = {t.upper() for t in allowed_tables}

    # Inspect all Table references in query and subqueries
    for table_exp in tree.find_all(exp.Table):
        table_name = table_exp.name.upper() if table_exp.name else ""
        if not table_name:
            continue

        # If table reference is an alias of a defined CTE, it is safe
        if table_name in cte_names:
            continue

        # Check base table name against allowlist
        if table_name not in allowed_set:
            return False, (
                f"Table '{table_name}' is not in the allowlist for the active dataset. "
                f"Permitted tables: {', '.join(sorted(allowed_set))}."
            )

        # Validate schema and catalog qualification if present
        schema_name = table_exp.db.upper() if table_exp.db else ""
        catalog_name = table_exp.catalog.upper() if table_exp.catalog else ""

        if schema_name:
            if table_name in {"CUSTOMER", "ORDERS", "LINEITEM", "NATION", "REGION", "SUPPLIER", "PART", "PARTSUPP"}:
                if schema_name != "TPCH_SF1":
                    return False, f"Table '{table_name}' in unauthorized schema '{schema_name}' is not permitted."
                if catalog_name and catalog_name != "SNOWFLAKE_SAMPLE_DATA":
                    return False, f"Table '{table_name}' in unauthorized database '{catalog_name}' is not permitted."
            elif table_name in {"LIFE_EXPECTANCY", "GDP_PER_CAPITA", "CO2", "POPULATION"}:
                if schema_name != "OWID":
                    return False, f"Table '{table_name}' in unauthorized schema '{schema_name}' is not permitted."
                if catalog_name and catalog_name != "DATAMIND":
                    return False, f"Table '{table_name}' in unauthorized database '{catalog_name}' is not permitted."
            else:
                return False, f"Unauthorized schema qualification '{schema_name}' on table '{table_name}'."


    return True, ""


def enforce_limit(sql: str, max_limit: int = 200) -> str:
    """
    Enforces that a SELECT query has a LIMIT clause, and that it does not exceed max_limit.
    Uses sqlglot AST manipulation with fallback regex.
    """
    if not sql or not sql.strip():
        return sql

    try:
        parsed = sqlglot.parse_one(sql, read="snowflake")
        if isinstance(parsed, exp.Select):
            limit_exp = parsed.args.get("limit")
            if limit_exp is None:
                # Add LIMIT clause
                parsed = parsed.limit(max_limit)
                return parsed.sql(dialect="snowflake")
            else:
                # Check current limit value
                try:
                    current_limit = int(limit_exp.expression.name)
                    if current_limit > max_limit:
                        parsed = parsed.limit(max_limit)
                        return parsed.sql(dialect="snowflake")
                except Exception:
                    pass
                return parsed.sql(dialect="snowflake")
    except Exception:
        pass

    # Safe regex fallback for string manipulation if AST roundtrip alters formatting
    limit_match = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
    if limit_match:
        current_limit = int(limit_match.group(1))
        if current_limit > max_limit:
            return re.sub(r"\bLIMIT\s+\d+", f"LIMIT {max_limit}", sql, flags=re.IGNORECASE)
        return sql
    else:
        # Strip trailing semicolon if present and append LIMIT
        cleaned = sql.strip().rstrip(";")
        return f"{cleaned} LIMIT {max_limit};"
