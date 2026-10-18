---
name: sql-safety
description: Instructions and rules for enforcing SQL safety using sqlglot (Snowflake dialect), including SELECT-only enforcement, single statement validation, table allowlist checking from YAML, enforced LIMIT clause, EXPLAIN-before-run validation, and forbidden statement types.
---

# SQL Safety Guardrail Skill

This skill provides guidelines and patterns for validating, parsing, sanitizing, and securing all SQL queries generated in DataMind Receipts prior to execution against Snowflake.

## Core Principle

**Never rely on regex guards for SQL safety.**
SQL validation must be deterministic and AST-based using `sqlglot` with `read="snowflake"` dialect, combined with the database-level read-only role (`DM_READER`).

---

## Forbidden Statements

The validator must reject any statement that is not a pure read query. Specifically, the following statement types and AST expressions are strictly forbidden:

| Forbidden Statement | Risk / Reason |
| :--- | :--- |
| `INSERT` | Mutation of data (violates read-only execution) |
| `UPDATE` | Mutation of existing rows |
| `DELETE` | Deletion of records |
| `DROP` | DDL dropping tables, views, schemas, databases |
| `ALTER` | DDL altering schema or objects |
| `MERGE` | DML upsert/mutation |
| `COPY` | Data ingestion/export operations (e.g., `COPY INTO`) |
| `CALL` | Stored procedure execution (potential side effects) |
| `GRANT` / `REVOKE` | Security and privilege changes |
| `TRUNCATE` | Table wiping |

In addition, reject any administrative statements (`USE`, `SET`, `CREATE`, etc.) and system procedures.

---

## The 5-Point Validation Checklist

Every generated SQL string must pass the following checks in order before touching Snowflake:

### 1. Single Statement Enforcement
- The input must parse into **exactly one** SQL expression.
- Reject multiple statements separated by semicolons (e.g., `SELECT 1; DROP TABLE ...`).
- Trailing semicolons on a single statement should be normalized or stripped, but chained statements must raise a validation error.

### 2. SELECT-Only Expression
- The root AST expression must strictly be a `Select` statement (or a `Union` of `Select`s, or CTEs `With` leading into a `Select`).
- Explicitly check `isinstance(expression, exp.Select)` or that the root query only resolves to data reading.
- Ensure no subqueries, CTEs, or inline functions invoke forbidden operations or procedures.

### 3. Table Allowlist from YAML
- Extract all referenced tables using `sqlglot` AST traversal (e.g., `expression.find_all(exp.Table)`).
- Normalize table names (case-insensitive comparison).
- Verify that **every referenced table** exists in the current dataset's table allowlist loaded from the semantic layer YAML (`dataset.tables.keys()`).
- If an unapproved table (or system catalog/information schema not allowed) is referenced:
  - Reject the query immediately.
  - Return the allowed table list to Gemma for repair retry (max 2 retries).

### 4. Enforced LIMIT
- Protect Snowflake warehouse credits and response payloads by enforcing a query result limit.
- Inspect if a `LIMIT` clause exists:
  - If missing, inject a default `LIMIT <max_rows>` (from `config.yaml`, default 200).
  - If present, verify `limit <= config.limits.max_rows`. If greater, clamp it to `max_rows`.
- Generate the final SQL using `sqlglot` generator targeting Snowflake dialect (`expression.sql(dialect="snowflake")`).

### 5. EXPLAIN-Before-Run
- Before executing the query against Snowflake to retrieve results, validate it with:
  ```sql
  EXPLAIN <candidate_sql>;
  ```
- Running `EXPLAIN` validates syntax, column resolution, and schema existence in Snowflake without scanning data or producing billable warehouse runtime.
- If `EXPLAIN` fails, capture the Snowflake error message and send it back to Gemma's repair role (max 2 retries).

---

## Reference Implementation Pattern

```python
import sqlglot
from sqlglot import exp
from typing import Set

FORBIDDEN_EXPRESSIONS = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.Create,
    exp.Command,  # Covers CALL, COPY, GRANT, TRUNCATE, etc.
)

class SQLValidationError(Exception):
    pass

def validate_and_sanitize_sql(sql_str: str, allowed_tables: Set[str], max_rows: int = 200) -> str:
    # 1. Parse using Snowflake dialect
    try:
        statements = sqlglot.parse(sql_str, read="snowflake")
    except Exception as e:
        raise SQLValidationError(f"SQL parse error: {e}")

    # Enforce single statement
    if len(statements) != 1 or statements[0] is None:
        raise SQLValidationError("Only a single SQL statement is allowed.")

    statement = statements[0]

    # 2. Check forbidden expression types
    for forbidden in FORBIDDEN_EXPRESSIONS:
        if statement.find(forbidden):
            raise SQLValidationError(f"Forbidden SQL operation detected: {forbidden.__name__}")

    # Must be a Select (or CTE leading to Select)
    if not isinstance(statement, (exp.Select, exp.Union)):
        raise SQLValidationError("Query must be a SELECT statement.")

    # 3. Check table allowlist
    referenced_tables = {
        table.name.lower() for table in statement.find_all(exp.Table)
        if table.name
    }
    allowed_lower = {t.lower() for t in allowed_tables}
    disallowed = referenced_tables - allowed_lower
    if disallowed:
        raise SQLValidationError(
            f"Tables not in allowlist: {disallowed}. Allowed tables: {allowed_tables}"
        )

    # 4. Enforce LIMIT
    limit_node = statement.args.get("limit")
    if not limit_node:
        statement = statement.limit(max_rows)
    else:
        try:
            current_limit = int(limit_node.expression.this)
            if current_limit > max_rows:
                statement.set("limit", exp.Limit(this=exp.var(str(max_rows))))
        except (ValueError, AttributeError):
            statement.set("limit", exp.Limit(this=exp.var(str(max_rows))))

    return statement.sql(dialect="snowflake")
```

---

## Error Handling & Repair Workflow

1. If `validate_and_sanitize_sql` raises `SQLValidationError`, the error is returned to Gemma in role 3 (`Repair`).
2. Gemma receives the failed SQL and the specific error string.
3. At most 2 repair attempts are allowed.
4. If repair fails twice, the query pipeline returns status `error` with a clean error message and logs the trace in the receipt.
