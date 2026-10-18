"""
Unit tests for administrative and demo scripts:
  - scripts/admin_update_demo.py (mutate, time-travel, reset)
  - scripts/export_query_history.py (CSV export to docs/snowflake_query_history.csv)

Ensures zero-drift reset, Time Travel AT(OFFSET => -300) query semantics, and CSV export.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.admin_update_demo import (
    _get_demo_sqls,
    run_mutate,
    run_reset,
    run_time_travel,
)
from scripts.export_query_history import CSV_COLUMNS, export_query_history


def test_get_demo_sqls_from_yaml():
    """Verify demo mutation and reset SQL are loaded dynamically from datasets/owid.yaml."""
    mutation_sql, reset_sql = _get_demo_sqls("datasets/owid.yaml")
    assert "UPDATE life_expectancy" in mutation_sql
    assert "95.0" in mutation_sql
    assert "Japan" in mutation_sql
    assert "2019" in mutation_sql

    assert "UPDATE life_expectancy" in reset_sql
    assert "life_expectancy_backup" in reset_sql
    assert "Japan" in reset_sql


def test_admin_mutate_execution():
    """Verify run_mutate executes mutation SQL and queries the updated row."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.sfqid = "01abc-mutate-qid"
    mock_cur.rowcount = 1
    mock_cur.fetchall.return_value = [["Japan", 2019, 95.0]]

    result = run_mutate(conn=mock_conn, dataset_path="datasets/owid.yaml")

    assert result["action"] == "mutate"
    assert result["query_id"] == "01abc-mutate-qid"
    assert result["rows_affected"] == 1
    assert result["current_row"] == ["Japan", 2019, 95.0]

    # Verify SQL calls made
    calls = [call[0][0] for call in mock_cur.execute.call_args_list]
    assert any("SET life_expectancy = 95.0" in c for c in calls)
    assert any("SELECT entity, year, life_expectancy" in c for c in calls)


def test_admin_time_travel_execution():
    """Verify run_time_travel queries both current state and AT(OFFSET => -300)."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.sfqid = "01abc-tt-qid"
    mock_cur.fetchall.side_effect = [
        [["Japan", 2019, 95.0]],  # Current state
        [["Japan", 2019, 84.4]],  # Time travel state (-300s)
    ]

    result = run_time_travel(conn=mock_conn, dataset_path="datasets/owid.yaml")

    assert result["action"] == "time-travel"
    assert result["current_row"] == ["Japan", 2019, 95.0]
    assert result["timetravel_row"] == ["Japan", 2019, 84.4]

    calls = [call[0][0] for call in mock_cur.execute.call_args_list]
    assert any("AT(OFFSET => -300)" in c for c in calls)


def test_admin_reset_execution():
    """Verify run_reset executes reset SQL from the backup clone."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_cur.sfqid = "01abc-reset-qid"
    mock_cur.rowcount = 1
    mock_cur.fetchall.return_value = [["Japan", 2019, 84.4]]

    result = run_reset(conn=mock_conn, dataset_path="datasets/owid.yaml")

    assert result["action"] == "reset"
    assert result["query_id"] == "01abc-reset-qid"
    assert result["current_row"] == ["Japan", 2019, 84.4]

    calls = [call[0][0] for call in mock_cur.execute.call_args_list]
    assert any("life_expectancy_backup" in c for c in calls)


def test_export_query_history_csv(tmp_path: Path):
    """Verify query history exports to CSV with all required columns."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_cur.description = [
        ("QUERY_ID", None),
        ("QUERY_TEXT", None),
        ("DATABASE_NAME", None),
        ("SCHEMA_NAME", None),
        ("QUERY_TYPE", None),
        ("EXECUTION_STATUS", None),
        ("ERROR_CODE", None),
        ("ERROR_MESSAGE", None),
        ("START_TIME", None),
        ("END_TIME", None),
        ("TOTAL_ELAPSED_TIME", None),
        ("BYTES_SCANNED", None),
        ("ROWS_PRODUCED", None),
        ("QUERY_TAG", None),
    ]

    mock_cur.fetchall.return_value = [
        [
            "01a2b3-0001",
            "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5",
            "DATAMIND",
            "OWID",
            "SELECT",
            "SUCCESS",
            None,
            None,
            "2026-10-18T10:00:00",
            "2026-10-18T10:00:01",
            120,
            4096,
            5,
            "datamind",
        ]
    ]

    csv_file = tmp_path / "test_history.csv"
    json_file = tmp_path / "test_history.json"

    records = export_query_history(
        output_csv=str(csv_file),
        output_json=str(json_file),
        conn=mock_conn,
    )

    assert len(records) == 1
    assert records[0]["query_id"] == "01a2b3-0001"
    assert records[0]["total_elapsed_time_ms"] == 120

    # Verify CSV file on disk
    assert csv_file.exists()
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["query_id"] == "01a2b3-0001"
        assert rows[0]["query_tag"] == "datamind"
        assert rows[0]["total_elapsed_time_ms"] == "120"
        for col in CSV_COLUMNS:
            assert col in reader.fieldnames
