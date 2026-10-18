"""
Unit tests for dynamic dataset switching and TPC-H schema validation (Section 4, 7, 9).

Verifies:
  1. Both owid.yaml and tpch.yaml load and validate through DatasetSchema.
  2. Dataset switching changes the allowed_tables dynamically without code changes.
  3. Queries for one dataset are rejected under the other dataset's allowlist.
  4. All few-shot examples in both datasets pass validate_sql.
  5. TPC-H is strictly read-only with no demo_mutation block.
"""

import pytest
from src.config import DatasetSchema, load_dataset
from src.validate import validate_sql


def test_load_owid_dataset():
    """Verify owid.yaml loads properly with required fields and demo_mutation."""
    owid = load_dataset("datasets/owid.yaml")
    assert owid.name == "owid"
    assert owid.snowflake.database == "DATAMIND"
    assert owid.snowflake.schema_name == "OWID"
    assert "life_expectancy" in owid.tables
    assert owid.allowed_tables == ["life_expectancy"]
    assert owid.demo_mutation is not None
    assert len(owid.examples) >= 1


def test_load_tpch_dataset():
    """Verify tpch.yaml loads properly with all 6 tables, joins, and no demo_mutation."""
    tpch = load_dataset("datasets/tpch.yaml")
    assert tpch.name == "tpch"
    assert tpch.snowflake.database == "SNOWFLAKE_SAMPLE_DATA"
    assert tpch.snowflake.schema_name == "TPCH_SF1"
    
    expected_tables = {"customer", "orders", "lineitem", "supplier", "nation", "region"}
    assert set(tpch.tables.keys()) == expected_tables
    assert set(tpch.allowed_tables) == expected_tables

    # TPC-H must have joins defined
    assert len(tpch.joins) >= 6

    # TPC-H is read-only shared data: no demo_mutation allowed (AGENTS.md Mistake #1)
    assert tpch.demo_mutation is None

    # Verify column structures
    assert "c_custkey" in tpch.tables["customer"].columns
    assert "o_orderkey" in tpch.tables["orders"].columns
    assert "l_orderkey" in tpch.tables["lineitem"].columns
    assert "s_suppkey" in tpch.tables["supplier"].columns
    assert "n_nationkey" in tpch.tables["nation"].columns
    assert "r_regionkey" in tpch.tables["region"].columns


def test_dataset_switch_enforces_different_allowlists():
    """Switching dataset dynamically changes what SQL statements are valid."""
    owid = load_dataset("datasets/owid.yaml")
    tpch = load_dataset("datasets/tpch.yaml")

    owid_sql = "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019"
    tpch_sql = "SELECT c_name, c_acctbal FROM customer ORDER BY c_acctbal DESC"

    # Under OWID allowlist:
    is_valid, err, _ = validate_sql(owid_sql, owid.allowed_tables)
    assert is_valid and err is None

    is_valid, err, _ = validate_sql(tpch_sql, owid.allowed_tables)
    assert not is_valid
    assert "customer" in err.lower()

    # Under TPC-H allowlist:
    is_valid, err, _ = validate_sql(tpch_sql, tpch.allowed_tables)
    assert is_valid and err is None

    is_valid, err, _ = validate_sql(owid_sql, tpch.allowed_tables)
    assert not is_valid
    assert "life_expectancy" in err.lower()


def test_owid_examples_are_valid_sql():
    """All few-shot examples in owid.yaml must pass validate_sql."""
    owid = load_dataset("datasets/owid.yaml")
    for ex in owid.examples:
        is_valid, err, sanitized = validate_sql(ex.sql, owid.allowed_tables)
        assert is_valid, f"OWID example failed validation: {ex.q} -> Error: {err}"
        assert sanitized is not None


def test_tpch_examples_are_valid_sql():
    """All few-shot examples in tpch.yaml must pass validate_sql."""
    tpch = load_dataset("datasets/tpch.yaml")
    assert len(tpch.examples) >= 3

    for ex in tpch.examples:
        is_valid, err, sanitized = validate_sql(ex.sql, tpch.allowed_tables)
        assert is_valid, f"TPC-H example failed validation: {ex.q} -> Error: {err}"
        assert sanitized is not None
