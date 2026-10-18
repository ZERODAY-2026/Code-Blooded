"""
tests/test_sqlgen.py — Unit tests for M4 (SQL generation and bounded repair).

Rules enforced:
───────────────
- Zero real API keys or external network calls.
- Zero Snowflake calls.
- Uses mock Gemma providers and the actual fail-closed SQLValidator.
- Tests successful generation, clarify, refuse, repair on attempt 1, repair on attempt 2,
  retry limits, provider errors, and trace recording.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from src.llm.base import LLMProvider, ProviderError
from src.llm.gemma_api import GemmaAPIProvider
from src.llm.trace import GemmaCallTrace
from src.models import GenerateResult
from src.sqlgen import SQLGenResult, generate_validated_sql
from src.validate import DatasetSchema, SQLValidator


# ── Fixtures & Mock Provider ──────────────────────────────────────────────────

@pytest.fixture
def test_schema() -> DatasetSchema:
    return DatasetSchema(
        name="owid",
        database="datamind",
        schema_name="owid",
        tables={
            "life_expectancy": {"entity", "code", "year", "life_expectancy"},
        },
        table_descriptions={
            "life_expectancy": "Life expectancy by country and year",
        },
        column_descriptions={
            "life_expectancy": {
                "entity": "Country name",
                "code": "ISO3 code",
                "year": "Calendar year",
                "life_expectancy": "Life expectancy in years",
            }
        },
        examples=[
            {
                "q": "Top 5 countries by life expectancy in 2019",
                "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5;",
            }
        ],
    )


class MockLLMProvider(LLMProvider):
    """Simple configurable mock provider for testing sqlgen."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_history: list[dict] = []

    @property
    def provider_name(self) -> str:
        return "api"

    @property
    def model_name(self) -> str:
        return "gemma-3-27b-it"

    def raw_generate(
        self,
        role: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, bool]:
        self.call_history.append({
            "role": role,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        if not self._responses:
            raise ProviderError("Mock provider has no remaining responses.")
        next_resp = self._responses.pop(0)
        return next_resp, True


# ══════════════════════════════════════════════════════════════════════════════
# 1. Successful Generation (First Try)
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerationSuccess:
    def test_valid_sql_generated_first_try(self, test_schema: DatasetSchema) -> None:
        mock_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5;",
            "assumptions": ["Filtered to year 2019"],
            "resolved_question": "Top 5 life expectancy in 2019",
        })
        provider = MockLLMProvider([mock_resp])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Which 5 countries had highest life expectancy in 2019?",
            provider=provider,
            schema=test_schema,
            trace=trace,
        )

        assert isinstance(res, SQLGenResult)
        assert res.status == "answer"
        assert res.sql is not None
        assert "SELECT" in res.sql
        assert res.retries == []
        assert res.assumptions == ["Filtered to year 2019"]
        assert res.resolved_question == "Top 5 life expectancy in 2019"
        assert trace.call_count() == 1
        assert trace.to_list()[0]["role"] == "generate"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Clarification and Refusal Handling
# ══════════════════════════════════════════════════════════════════════════════

class TestClarifyAndRefuse:
    def test_clarify_outcome_does_not_validate_or_execute_sql(self, test_schema: DatasetSchema) -> None:
        mock_resp = json.dumps({
            "status": "clarify",
            "sql": None,
            "clarifying_question": "Did you mean life expectancy at birth or at age 65?",
            "assumptions": [],
        })
        provider = MockLLMProvider([mock_resp])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="What is the life expectancy?",
            provider=provider,
            schema=test_schema,
            trace=trace,
        )

        assert res.status == "clarify"
        assert res.sql is None
        assert "birth" in (res.clarifying_question or "")
        assert res.retries == []
        assert trace.call_count() == 1

    def test_refuse_outcome_does_not_validate_or_execute_sql(self, test_schema: DatasetSchema) -> None:
        mock_resp = json.dumps({
            "status": "refuse",
            "sql": None,
            "refusal_reason": "Deleting data is not permitted.",
            "assumptions": [],
        })
        provider = MockLLMProvider([mock_resp])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Delete all rows for 2020",
            provider=provider,
            schema=test_schema,
            trace=trace,
        )

        assert res.status == "refuse"
        assert res.sql is None
        assert "Deleting" in (res.refusal_reason or "")
        assert res.retries == []
        assert trace.call_count() == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. Bounded Repair Loop
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairLoop:
    def test_repair_succeeds_on_first_retry(self, test_schema: DatasetSchema) -> None:
        # Attempt 1: missing LIMIT
        resp_1 = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019;",
            "assumptions": ["Missing limit"],
        })
        # Attempt 2 (Repair): adds LIMIT 5
        resp_2 = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 5;",
            "assumptions": ["Added LIMIT 5"],
        })
        provider = MockLLMProvider([resp_1, resp_2])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Life expectancy in 2019",
            provider=provider,
            schema=test_schema,
            trace=trace,
            max_retries=2,
        )

        assert res.status == "answer"
        assert res.sql is not None
        assert "LIMIT 5" in res.sql
        # Should record exactly 1 retry in history
        assert len(res.retries) == 1
        assert "MISSING_LIMIT" in res.retries[0]["error"] or "LIMIT" in res.retries[0]["error"]
        assert trace.call_count() == 2
        roles = [c["role"] for c in trace.to_list()]
        assert roles == ["generate", "repair"]

    def test_repair_succeeds_on_second_retry(self, test_schema: DatasetSchema) -> None:
        # Attempt 1: forbidden column 'salary'
        resp_1 = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, salary FROM life_expectancy LIMIT 5;",
        })
        # Attempt 2 (Repair 1): SELECT * (forbidden star)
        resp_2 = json.dumps({
            "status": "answer",
            "sql": "SELECT * FROM life_expectancy LIMIT 5;",
        })
        # Attempt 3 (Repair 2): valid query
        resp_3 = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5;",
            "assumptions": ["Fixed columns and limit"],
        })
        provider = MockLLMProvider([resp_1, resp_2, resp_3])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Life expectancy",
            provider=provider,
            schema=test_schema,
            trace=trace,
            max_retries=2,
        )

        assert res.status == "answer"
        assert len(res.retries) == 2
        assert "salary" in res.retries[0]["error"] or "FORBIDDEN_COLUMN" in res.retries[0]["error"]
        assert "SELECT *" in res.retries[1]["error"] or "FORBIDDEN_STAR" in res.retries[1]["error"]
        assert trace.call_count() == 3

    def test_repair_exhausts_max_retries_returns_error(self, test_schema: DatasetSchema) -> None:
        # 3 successive bad queries (initial + 2 repairs)
        bad_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, invalid_col FROM life_expectancy LIMIT 5;",
        })
        provider = MockLLMProvider([bad_resp, bad_resp, bad_resp])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Life expectancy",
            provider=provider,
            schema=test_schema,
            trace=trace,
            max_retries=2,
        )

        assert res.status == "error"
        assert res.sql is None
        assert res.error_code in ("FORBIDDEN_COLUMN", "VALIDATION_FAILED")
        assert "validation failed after 2 repair attempts" in (res.error or "").lower()
        # Initial failed + 2 repaired failures = 3 entries
        assert len(res.retries) == 3
        assert trace.call_count() == 3

    def test_zero_max_retries_returns_error_immediately(self, test_schema: DatasetSchema) -> None:
        bad_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM unknown_table LIMIT 5;",
        })
        provider = MockLLMProvider([bad_resp])
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Question",
            provider=provider,
            schema=test_schema,
            trace=trace,
            max_retries=0,
        )

        assert res.status == "error"
        assert res.error_code == "FORBIDDEN_TABLE"
        assert len(res.retries) == 1
        assert trace.call_count() == 1  # Only the generate call, no repair calls

    def test_repair_outcome_can_switch_to_refuse(self, test_schema: DatasetSchema) -> None:
        # Initial attempt generates invalid SQL
        resp_1 = json.dumps({
            "status": "answer",
            "sql": "DROP TABLE life_expectancy;",
        })
        # On repair, Gemma realizes this must be refused
        resp_2 = json.dumps({
            "status": "refuse",
            "sql": None,
            "refusal_reason": "Drop table operation is forbidden.",
        })
        provider = MockLLMProvider([resp_1, resp_2])

        res = generate_validated_sql(
            question="Drop table",
            provider=provider,
            schema=test_schema,
            max_retries=1,
        )

        assert res.status == "refuse"
        assert "forbidden" in (res.refusal_reason or "").lower()
        assert len(res.retries) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 4. Provider Error Handling
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderErrors:
    def test_provider_error_on_generate_returns_clean_error_result(self, test_schema: DatasetSchema) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("AI Studio Connection Failed")

        provider = GemmaAPIProvider(model_name="gemma-3-27b-it", client=mock_client)

        res = generate_validated_sql(
            question="Life expectancy",
            provider=provider,
            schema=test_schema,
        )

        assert res.status == "error"
        assert res.error_code == "PROVIDER_ERROR"
        assert "Connection Failed" in (res.error or "")

    def test_provider_error_on_repair_returns_clean_error_result(self, test_schema: DatasetSchema) -> None:
        # Initial generate succeeds with bad SQL
        resp_1 = MagicMock(text='{"status": "answer", "sql": "SELECT invalid_col FROM t;"}')
        # Repair fails with provider error
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            resp_1,
            RuntimeError("Rate limit / Quota exceeded"),
        ]

        provider = GemmaAPIProvider(
            model_name="gemma-3-27b-it",
            client=mock_client,
            max_retries=0,  # fail fast on rate limit
        )

        res = generate_validated_sql(
            question="Life expectancy",
            provider=provider,
            schema=test_schema,
            max_retries=2,
        )

        assert res.status == "error"
        assert res.error_code == "PROVIDER_ERROR"
        assert len(res.retries) == 1  # Contains the first failed SQL


# ══════════════════════════════════════════════════════════════════════════════
# 5. Integration with GemmaAPIProvider
# ══════════════════════════════════════════════════════════════════════════════

class TestGemmaAPIProviderIntegration:
    def test_gemma_api_provider_end_to_end_mocked(self, test_schema: DatasetSchema) -> None:
        mock_client = MagicMock()
        # Mocking generate_content response for GemmaAPIProvider
        mock_resp = MagicMock(
            text='{"status": "answer", "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 5;", "assumptions": ["2019"]}'
        )
        mock_client.models.generate_content.return_value = mock_resp

        provider = GemmaAPIProvider(model_name="gemma-3-27b-it", client=mock_client)
        trace = GemmaCallTrace()

        res = generate_validated_sql(
            question="Top 5 in 2019",
            provider=provider,
            schema=test_schema,
            trace=trace,
        )

        assert res.status == "answer"
        assert res.sql is not None
        assert "LIMIT 5" in res.sql
        assert trace.call_count() == 1
        assert trace.to_list()[0]["model"] == "gemma-3-27b-it"
