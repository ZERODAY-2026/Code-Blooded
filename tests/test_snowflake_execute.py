"""
tests/test_snowflake_execute.py — Focused tests for Snowflake execution and safety contracts.

Validates:
1. Parameterized connection handling and error on missing credentials.
2. Successful execution result conversion using mocked Snowflake connector.
3. Real query ID extraction and error handling.
4. Ensuring invalid/mutating SQL is stopped by the validator before reaching execution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from src.execute import (
    execute_query,
    execute_snowflake,
    get_snowflake_connection_params,
)
from src.models import ExecuteResult, RowSet
from src.pipeline import DataMindPipeline
from src.validate import DatasetSchema, SQLValidator


def test_missing_credentials_raises_environment_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)
    monkeypatch.delenv("SNOWFLAKE_PRIVATE_KEY_PATH", raising=False)

    with pytest.raises(EnvironmentError) as exc_info:
        get_snowflake_connection_params()
    assert "Missing required Snowflake configuration" in str(exc_info.value)


def test_successful_result_conversion_with_mocked_connector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "test_acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "test_user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "test_pass")

    mock_conn = MagicMock()
    mock_cur = MagicMock()

    # Simulate Snowflake cursor description: [("ENTITY", ...), ("LIFE_EXPECTANCY", ...)]
    mock_cur.description = [("ENTITY", 1), ("LIFE_EXPECTANCY", 2)]
    mock_cur.fetchall.return_value = [["Japan", 84.4], ["Switzerland", 83.8]]
    mock_cur.sfqid = "01b3d4f5-0000-1111-0000-222233334444"

    mock_conn.cursor.return_value = mock_cur

    with patch("snowflake.connector.connect", return_value=mock_conn):
        res: ExecuteResult = execute_snowflake("SELECT entity, life_expectancy FROM life_expectancy LIMIT 2;")

    assert res.error is None
    assert res.query_id == "01b3d4f5-0000-1111-0000-222233334444"
    assert res.rows is not None
    assert res.rows.columns == ["entity", "life_expectancy"]
    assert len(res.rows.data) == 2
    assert res.rows.data[0] == ["Japan", 84.4]

    # Verify session parameters were enforced
    calls = [call_args[0][0] for call_args in mock_cur.execute.call_args_list]
    assert any("ALTER SESSION SET QUERY_TAG = 'datamind'" in c for c in calls)
    assert any("STATEMENT_TIMEOUT_IN_SECONDS" in c for c in calls)


def test_connection_failure_returns_structured_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "test_acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "test_user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "test_pass")

    with patch("snowflake.connector.connect", side_effect=Exception("Connection refused")):
        res: ExecuteResult = execute_snowflake("SELECT 1;")

    assert res.rows is None
    assert res.error is not None
    assert "Connection refused" in res.error
    assert res.query_id is None
    assert any("Snowflake error" in flag for flag in res.flags)


def test_invalid_mutation_sql_never_reaches_executor():
    """Verify that forbidden SQL operations fail validation and never get passed to executor."""
    schema = DatasetSchema(
        name="owid",
        tables={"life_expectancy": {"entity", "year", "life_expectancy"}},
    )
    validator = SQLValidator(schema=schema, max_rows=100)

    forbidden_queries = [
        "DROP TABLE life_expectancy;",
        "DELETE FROM life_expectancy WHERE year = 2020;",
        "UPDATE life_expectancy SET life_expectancy = 100;",
        "SELECT * FROM unapproved_table;",
        "SELECT entity FROM life_expectancy; DROP TABLE life_expectancy;",
    ]

    mock_executor = MagicMock()

    for sql in forbidden_queries:
        val_res = validator.validate(sql)
        assert val_res.valid is False, f"Expected SQL to be rejected: {sql}"
        # Executor must never be called on invalid validation results
        if not val_res.valid:
            continue
        mock_executor(val_res.sql)

    mock_executor.assert_not_called()
