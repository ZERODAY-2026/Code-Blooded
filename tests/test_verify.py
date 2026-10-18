"""
tests/test_verify.py

Pytest tests for the verifier layer.
The verifier (Person A implements src/verify.py) checks that every number
and entity name in Gemma's prose answer actually exists in the query result cells.

Interface contract (Person A implements this):
    from src.verify import verify_answer

    verify_answer(
        prose: str,
        rows: dict,          # {"columns": [...], "data": [[...], ...]}
        sql: str,
    ) -> VerifyResult

    @dataclass
    class VerifyResult:
        passed: bool
        mismatches: list[str]   # list of (claim, expected_value) descriptions
        status: str             # "verified" | "mismatch" | "unverifiable"

Run:
    pytest tests/test_verify.py -v
"""

import pytest
from dataclasses import dataclass
from typing import Any

# ── Interface shim ────────────────────────────────────────────────────────────
try:
    from src.verify import verify_answer, VerifyResult
    VERIFY_AVAILABLE = True
except ImportError:
    VERIFY_AVAILABLE = False

    @dataclass
    class VerifyResult:
        passed: bool
        mismatches: list
        status: str

    def verify_answer(prose, rows, sql):
        raise NotImplementedError("src.verify not yet available")


pytestmark = pytest.mark.skipif(
    not VERIFY_AVAILABLE,
    reason="src.verify not yet implemented — tests run when Person A ships it",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def make_rows(columns: list[str], data: list[list[Any]]) -> dict:
    """Build the rows dict format that matches the API contract."""
    return {"columns": columns, "data": data}


# ═══════════════════════════════════════════════════════════════════════════════
# Section A — Number matching
# Numbers in prose must appear in query result cells (within tolerance)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNumberMatching:

    def test_exact_number_in_result_passes(self):
        prose = "Japan had a life expectancy of 84.3 years in 2020."
        rows = make_rows(
            ["entity", "year", "life_expectancy"],
            [["Japan", 2020, 84.3]]
        )
        result = verify_answer(prose, rows, "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity='Japan' AND year=2020 LIMIT 1")
        assert result.passed, f"84.3 is in result — should pass. Mismatches: {result.mismatches}"
        assert result.status == "verified"

    def test_number_not_in_result_fails(self):
        prose = "Japan had a life expectancy of 99.9 years in 2020."
        rows = make_rows(
            ["entity", "year", "life_expectancy"],
            [["Japan", 2020, 84.3]]
        )
        result = verify_answer(prose, rows, "SELECT entity, year, life_expectancy FROM life_expectancy WHERE entity='Japan' AND year=2020 LIMIT 1")
        assert not result.passed, "99.9 is NOT in result — must fail verification"
        assert result.status == "mismatch"
        assert len(result.mismatches) > 0

    def test_rounded_number_within_tolerance_passes(self):
        """Prose may round 84.31 → 84.3 — this is acceptable."""
        prose = "Japan had a life expectancy of about 84 years in 2020."
        rows = make_rows(
            ["entity", "year", "life_expectancy"],
            [["Japan", 2020, 84.3]]
        )
        result = verify_answer(prose, rows, "SELECT * FROM life_expectancy LIMIT 1")
        assert result.passed, "Rounded number within tolerance must pass"

    def test_count_in_prose_matches_result(self):
        prose = "There are 30,000 customers in the HOUSEHOLD segment."
        rows = make_rows(["customer_count"], [[30000]])
        result = verify_answer(prose, rows, "SELECT COUNT(*) FROM CUSTOMER LIMIT 1")
        assert result.passed, "30000 is in result — must pass"

    def test_invented_count_fails(self):
        prose = "There are 500,000 customers in the HOUSEHOLD segment."
        rows = make_rows(["customer_count"], [[30000]])
        result = verify_answer(prose, rows, "SELECT COUNT(*) FROM CUSTOMER LIMIT 1")
        assert not result.passed, "500000 is not in result — must fail"

    def test_multiple_numbers_all_match(self):
        prose = "Revenue was 1234567.89 and there were 42 orders."
        rows = make_rows(
            ["revenue", "order_count"],
            [[1234567.89, 42]]
        )
        result = verify_answer(prose, rows, "SELECT revenue, order_count FROM ORDERS LIMIT 1")
        assert result.passed, "Both numbers in result — must pass"

    def test_one_number_mismatch_fails(self):
        prose = "Revenue was 1234567.89 and there were 999 orders."
        rows = make_rows(
            ["revenue", "order_count"],
            [[1234567.89, 42]]
        )
        result = verify_answer(prose, rows, "SELECT revenue, order_count FROM ORDERS LIMIT 1")
        assert not result.passed, "999 not in result — must fail even if other number matched"


# ═══════════════════════════════════════════════════════════════════════════════
# Section B — Entity name matching
# Country/company names in prose must appear in query result cells
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntityNameMatching:

    def test_entity_name_in_result_passes(self):
        prose = "Japan has the highest life expectancy."
        rows = make_rows(
            ["entity", "life_expectancy"],
            [["Japan", 84.3], ["Switzerland", 83.8], ["Singapore", 83.5]]
        )
        result = verify_answer(prose, rows, "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5")
        assert result.passed, "Japan is in result — must pass"

    def test_entity_name_not_in_result_fails(self):
        prose = "Australia has the highest life expectancy."
        rows = make_rows(
            ["entity", "life_expectancy"],
            [["Japan", 84.3], ["Switzerland", 83.8], ["Singapore", 83.5]]
        )
        result = verify_answer(prose, rows, "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5")
        assert not result.passed, "Australia NOT in result — must fail"

    def test_company_name_in_result_passes(self):
        prose = "Customer #1 (Customer#000000001) had the highest spend."
        rows = make_rows(
            ["C_NAME", "total_spend"],
            [["Customer#000000001", 500000.0]]
        )
        result = verify_answer(prose, rows, "SELECT C_NAME, SUM(revenue) FROM CUSTOMER LIMIT 1")
        assert result.passed, "Customer name is in result — must pass"


# ═══════════════════════════════════════════════════════════════════════════════
# Section C — Relational claim verification
# "increased", "largest", "doubled" etc. verified deterministically from data
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelationalClaims:

    def test_increased_claim_true(self):
        """Claim: life expectancy increased. Data confirms it."""
        prose = "Life expectancy in Japan increased from 80.5 to 84.3 between 2000 and 2020."
        rows = make_rows(
            ["year", "life_expectancy"],
            [[2000, 80.5], [2020, 84.3]]
        )
        result = verify_answer(prose, rows, "SELECT year, life_expectancy FROM life_expectancy LIMIT 2")
        assert result.passed, "Increase claim is supported by data — must pass"

    def test_decreased_claim_contradicted_by_data_fails(self):
        """Claim: life expectancy decreased. Data shows it increased — must fail."""
        prose = "Life expectancy in Japan decreased from 80.5 to 84.3."
        rows = make_rows(
            ["year", "life_expectancy"],
            [[2000, 80.5], [2020, 84.3]]
        )
        result = verify_answer(prose, rows, "SELECT year, life_expectancy FROM life_expectancy LIMIT 2")
        assert not result.passed, "Decrease claim contradicted by data — must fail"

    def test_largest_claim_matches_top_row(self):
        prose = "Japan has the largest life expectancy at 84.3 years."
        rows = make_rows(
            ["entity", "life_expectancy"],
            [["Japan", 84.3], ["Switzerland", 83.8]]
        )
        result = verify_answer(prose, rows, "SELECT entity, life_expectancy FROM life_expectancy ORDER BY life_expectancy DESC LIMIT 5")
        assert result.passed, "Japan is top row — largest claim must pass"


# ═══════════════════════════════════════════════════════════════════════════════
# Section D — Empty result handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyResultHandling:

    def test_empty_result_returns_unverifiable(self):
        """When rows are empty, verifier cannot check — status must be 'unverifiable'."""
        prose = "No data found for Atlantis."
        rows = make_rows(["entity", "life_expectancy"], [])
        result = verify_answer(prose, rows, "SELECT * FROM life_expectancy WHERE entity='Atlantis' LIMIT 1")
        assert result.status == "unverifiable", \
            f"Empty result must return unverifiable status, got: {result.status}"

    def test_empty_result_passed_flag_false(self):
        """passed must be False when status is unverifiable."""
        prose = "No data found."
        rows = make_rows(["count"], [])
        result = verify_answer(prose, rows, "SELECT COUNT(*) FROM CUSTOMER WHERE 1=0 LIMIT 1")
        assert not result.passed, "Unverifiable result must have passed=False"


# ═══════════════════════════════════════════════════════════════════════════════
# Section E — VerifyResult structure
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifyResultStructure:

    def test_result_has_passed_field(self):
        prose = "Japan had 84.3 years."
        rows = make_rows(["entity", "life_expectancy"], [["Japan", 84.3]])
        result = verify_answer(prose, rows, "SELECT * FROM life_expectancy LIMIT 1")
        assert hasattr(result, "passed"), "VerifyResult must have 'passed' field"

    def test_result_has_mismatches_field(self):
        prose = "Japan had 99.9 years."
        rows = make_rows(["entity", "life_expectancy"], [["Japan", 84.3]])
        result = verify_answer(prose, rows, "SELECT * FROM life_expectancy LIMIT 1")
        assert hasattr(result, "mismatches"), "VerifyResult must have 'mismatches' field"
        assert isinstance(result.mismatches, list), "'mismatches' must be a list"

    def test_result_has_status_field(self):
        prose = "Japan had 84.3 years."
        rows = make_rows(["entity", "life_expectancy"], [["Japan", 84.3]])
        result = verify_answer(prose, rows, "SELECT * FROM life_expectancy LIMIT 1")
        assert hasattr(result, "status"), "VerifyResult must have 'status' field"
        assert result.status in ("verified", "mismatch", "unverifiable"), \
            f"Status must be one of verified/mismatch/unverifiable, got: {result.status}"

    def test_passing_result_has_empty_mismatches(self):
        prose = "Japan had 84.3 years."
        rows = make_rows(["entity", "life_expectancy"], [["Japan", 84.3]])
        result = verify_answer(prose, rows, "SELECT * FROM life_expectancy LIMIT 1")
        assert result.passed
        assert result.mismatches == [], "Passing result must have empty mismatches list"
