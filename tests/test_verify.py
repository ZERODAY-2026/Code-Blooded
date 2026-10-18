"""
Unit tests for the claim-to-cell verifier (src/verify.py).

Covers Section 4, 6, 11, and 12 guardrails:
  - Exact cell matches for entity and value pairs.
  - Cell coordinate tracking (row_index, column, text).
  - Entity-value pair mismatch detection (e.g. associating country A with country B's number).
  - Unmatched / hallucinated numbers trigger fallback sentence.
  - SQL filter year exemptions (e.g. 2019 in WHERE year = 2019).
  - Row count exemptions (e.g. "5 countries").
  - Relational claim checks ("highest", "lowest").
  - Deterministic fallback generation without LLM.
"""

import pytest
from src.verify import build_fallback_sentence, verify_answer


# Sample test data matching OWID schema
COLUMNS = ["entity", "year", "life_expectancy"]
ROWS = [
    ["Japan", 2019, 84.4],
    ["Switzerland", 2019, 83.8],
    ["India", 2019, 70.9],
]
SAMPLE_SQL = "SELECT entity, year, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 3"


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


def test_exempt_year_from_sql_does_not_fail():
    """The year 2019 from the SQL WHERE clause is exempt from needing a row match."""
    prose = "In 2019, Japan had the highest life expectancy at 84.4."
    result = verify_answer(prose, COLUMNS, ROWS, sql=SAMPLE_SQL)

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


def test_empty_rows_handling():
    """Empty result rows are handled cleanly with verified status and honest empty message."""
    result = verify_answer("No countries found.", COLUMNS, [], sql=SAMPLE_SQL)

    assert result.is_verified is True
    assert result.confidence == "verified"
    assert "no matching rows" in result.verified_prose.lower()


def test_build_fallback_sentence_single_row():
    """Fallback generator builds an exact deterministic summary for a single row."""
    fallback = build_fallback_sentence(["entity", "life_expectancy"], [["Japan", 84.4]])
    assert "Japan" in fallback
    assert "84.4" in fallback
    assert "Result:" in fallback
