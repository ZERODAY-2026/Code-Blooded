"""
Unit tests for the claim-to-cell verifier (src/verify.py).

Covers Section 4, 6, 11, and 12 guardrails:
  - Exact cell matches for entity and value pairs.
  - Section 11 API contract for evidence items: [{"text": "84.4", "row_index": 0, "column": "life_expectancy"}].
  - Calling verify() with rows dictionary {"columns": [...], "data": [...]}.
  - Entity-value pair mismatch detection (associating entity A with entity B's number).
  - Unmatched / hallucinated numbers trigger fallback sentence.
  - Unmatched / hallucinated capitalized entity names trigger fallback sentence.
  - SQL filter year exemptions (e.g. 2019 in WHERE year = 2019).
  - Row count exemptions (e.g. "top 3 countries").
  - Deterministic relational claim checks ("highest", "lowest", "increased", "decreased", "doubled").
  - Fallback deterministic sentence format (e.g., "Top result: Japan (life_expectancy: 84.4).").
"""

import json
import pytest
from src.verify import (
    EvidenceItem,
    VerificationResult,
    build_fallback_sentence,
    verify,
    verify_answer,
)


# Sample test data matching OWID schema
COLUMNS = ["entity", "year", "life_expectancy"]
ROWS = [
    ["Japan", 2019, 84.4],
    ["Switzerland", 2019, 83.8],
    ["India", 2019, 70.9],
]
SAMPLE_SQL = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 3"

ROWS_DICT = {
    "columns": COLUMNS,
    "data": ROWS,
}


def test_perfect_verification():
    """Valid prose mentioning correct entity and value pairs passes verification."""
    prose = "In 2019, Japan had a life expectancy of 84.4 years, followed by Switzerland at 83.8 years."
    result = verify_answer(prose, COLUMNS, ROWS, sql=SAMPLE_SQL)

    assert result.is_verified is True
    assert result.confidence == "verified"
    assert result.verified_prose == prose
    assert len(result.unmatched_claims) == 0

    # Check evidence cells
    evidence_texts = [e.text for e in result.evidence]
    assert "84.4" in evidence_texts
    assert "83.8" in evidence_texts
    assert any(e.text == "Japan" and e.column == "entity" and e.row_index == 0 for e in result.evidence)
    assert any(e.text == "84.4" and e.column == "life_expectancy" and e.row_index == 0 for e in result.evidence)


def test_verify_with_rows_dict_api_format():
    """Calling verify() with rows dict returns Section 11 evidence contract."""
    prose = "Japan had a life expectancy of 84.4 in 2019."
    result = verify(prose, ROWS_DICT, sql=SAMPLE_SQL)

    assert result.is_verified is True
    assert result["is_verified"] is True
    assert result.confidence == "verified"

    # Evidence items must conform to Section 11 contract
    item = next(e for e in result.evidence if e.column == "life_expectancy")
    assert item["text"] == "84.4"
    assert item["row_index"] == 0
    assert item["column"] == "life_expectancy"
    assert item.text == "84.4"
    assert item.row_index == 0
    assert item.column == "life_expectancy"

    # JSON serializability check
    serialized = json.dumps(result.evidence)
    data = json.loads(serialized)
    assert any(d == {"text": "84.4", "row_index": 0, "column": "life_expectancy"} for d in data)


def test_exempt_year_from_sql_does_not_fail():
    """The year 2019 from the SQL WHERE clause is exempt from needing a row match."""
    prose = "In 2019, Japan had the highest life expectancy at 84.4."
    result = verify_answer(prose, COLUMNS, ROWS, sql=SAMPLE_SQL)

    assert result.is_verified is True
    assert result.confidence == "verified"


def test_row_count_exemption():
    """Row count in prose (e.g. 'top 3 countries') is exempt from being flagged as unmatched metric."""
    prose = "The top 3 countries in 2019 are Japan at 84.4, Switzerland at 83.8, and India at 70.9."
    result = verify(prose, ROWS_DICT, sql=SAMPLE_SQL)

    assert result.is_verified is True
    assert result.confidence == "verified"


def test_hallucinated_number_triggers_fallback():
    """A number not present in any returned cell triggers fallback prose."""
    prose = "Japan had a life expectancy of 99.5 years in 2019."
    result = verify_answer(prose, COLUMNS, ROWS, sql=SAMPLE_SQL)

    assert result.is_verified is False
    assert result.confidence in ("partial", "unverifiable")
    assert any("99.5" in err for err in result.unmatched_claims)
    # The verified prose must be the deterministic fallback, not the hallucinated prose
    assert result.verified_prose == result.fallback_sentence
    assert "Japan" in result.verified_prose


def test_hallucinated_capitalized_entity_triggers_fallback():
    """An entity not present in any returned cell triggers fallback prose."""
    # France is not in the query results
    prose = "France had a life expectancy of 84.4 years in 2019."
    result = verify(prose, ROWS_DICT, sql=SAMPLE_SQL)

    assert result.is_verified is False
    assert result.confidence in ("partial", "unverifiable")
    assert any("France" in err for err in result.unmatched_claims)
    assert result.verified_prose == result.fallback_sentence


def test_entity_value_mismatch_detected():
    """Associating an entity with another row's value is blocked even if both appear in the table."""
    # India is row 2 with 70.9, but prose claims Japan had 70.9
    prose = "In 2019, Japan had a life expectancy of 70.9 years."
    result = verify_answer(prose, COLUMNS, ROWS, sql=SAMPLE_SQL)

    assert result.is_verified is False
    assert result.confidence in ("partial", "unverifiable")
    assert any("mismatched" in err.lower() for err in result.unmatched_claims)
    assert result.verified_prose == result.fallback_sentence


def test_invalid_relational_claim():
    """Claiming 'lowest' when the entity has the highest value is rejected."""
    # Japan has 84.4 (the highest), but prose claims it was the lowest
    prose = "Japan had the lowest life expectancy at 84.4."
    result = verify_answer(prose, COLUMNS, ROWS, sql=SAMPLE_SQL)

    assert result.is_verified is False
    assert any("lowest" in err.lower() for err in result.unmatched_claims)


def test_relational_claim_increased_valid():
    """Prose claiming 'increased' is accepted when chronological rows confirm growth."""
    time_series_rows = [
        ["India", 2015, 68.0],
        ["India", 2019, 70.9],
    ]
    prose = "Life expectancy in India increased from 68.0 in 2015 to 70.9 in 2019."
    sql = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity = 'India' ORDER BY year ASC"
    result = verify_answer(prose, COLUMNS, time_series_rows, sql=sql)

    assert result.is_verified is True
    assert result.confidence == "verified"


def test_relational_claim_increased_invalid_trend():
    """Prose claiming 'increased' is rejected when data actually decreased."""
    decreasing_rows = [
        ["CountryX", 2015, 75.0],
        ["CountryX", 2019, 70.0],
    ]
    prose = "Life expectancy in CountryX increased from 2015 to 2019."
    sql = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity = 'CountryX' ORDER BY year ASC"
    result = verify_answer(prose, COLUMNS, decreasing_rows, sql=sql)

    assert result.is_verified is False
    assert any("increased" in err.lower() for err in result.unmatched_claims)


def test_relational_claim_trend_without_comparative_data():
    """Prose claiming a trend without comparative rows to back it up is rejected."""
    single_row = [["India", 2019, 70.9]]
    prose = "Life expectancy in India increased to 70.9."
    result = verify_answer(prose, COLUMNS, single_row, sql=SAMPLE_SQL)

    assert result.is_verified is False
    assert any("comparative" in err.lower() or "trend" in err.lower() for err in result.unmatched_claims)


def test_relational_claim_doubled_rejected_if_not_2x():
    """Prose claiming 'doubled' is rejected if change is under 2x."""
    growth_rows = [
        ["TechCorp", 2015, 100.0],
        ["TechCorp", 2019, 150.0],
    ]
    prose = "TechCorp doubled from 100.0 in 2015 to 150.0 in 2019."
    cols = ["entity", "year", "value"]
    result = verify_answer(prose, cols, growth_rows, sql="SELECT entity, year, value FROM test")

    assert result.is_verified is False
    assert any("doubled" in err.lower() for err in result.unmatched_claims)


def test_empty_rows_handling():
    """Empty result rows are handled cleanly with verified status and honest empty message."""
    result = verify_answer("No countries found.", COLUMNS, [], sql=SAMPLE_SQL)

    assert result.is_verified is True
    assert result.confidence == "verified"
    assert "no matching rows" in result.verified_prose.lower()


def test_build_fallback_sentence_single_row():
    """Fallback generator builds an exact deterministic summary for a single row."""
    fallback = build_fallback_sentence(["entity", "life_expectancy"], [["Japan", 84.4]])
    assert fallback == "Result: Japan (life_expectancy: 84.4)."
    assert "Japan" in fallback
    assert "84.4" in fallback
    assert "Result:" in fallback


def test_build_fallback_sentence_multi_row():
    """Fallback generator builds an exact deterministic summary matching the prompt example."""
    fallback = build_fallback_sentence(["entity", "life_expectancy"], [["Japan", 84.4], ["Switzerland", 83.8]])
    assert fallback == "Top result: Japan (life_expectancy: 84.4)."
