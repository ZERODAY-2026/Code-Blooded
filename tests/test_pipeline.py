"""
tests/test_pipeline.py — Unit tests for M6 DataMind pipeline orchestration.

Rules enforced:
───────────────
- Zero real API keys or external network calls.
- Zero live Snowflake connections.
- Mocked Gemma provider, injectable executor, and injectable verifier.
- Validates that unvalidated SQL never reaches the executor.
- Covers success, clarify, refuse, repair, execution failure, empty rows,
  verified/partial/unverifiable claims, and missing dependencies.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from src.config import AppConfig
from src.llm.base import LLMProvider
from src.models import (
    AskResponse,
    EvidenceCell,
    ExecuteResult,
    RowSet,
    VerifyResult,
)
from src.pipeline import DataMindPipeline, run_pipeline
from src.validate import DatasetSchema


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_config() -> AppConfig:
    return AppConfig(
        provider="api",
        model="gemma-3-27b-it",
        prompt_token_budget=3000,
        rows_to_gemma=50,
        dataset="datasets/owid.yaml",
        snowsight_base_url="https://app.snowflake.com/test",
    )


@pytest.fixture
def test_schema() -> DatasetSchema:
    return DatasetSchema(
        name="owid",
        database="datamind",
        schema_name="owid",
        tables={
            "life_expectancy": {"entity", "code", "year", "life_expectancy"},
            "population": {"entity", "code", "year", "population"},
        },
        table_descriptions={
            "life_expectancy": "Life expectancy at birth by country and year",
        },
        column_descriptions={
            "life_expectancy": {
                "entity": "Country name",
                "code": "ISO3 code",
                "year": "Calendar year",
                "life_expectancy": "Life expectancy in years",
            }
        },
    )


class MockPipelineLLM(LLMProvider):
    """Configurable mock provider returning responses in sequence."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.recorded_roles: list[str] = []

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
        self.recorded_roles.append(role)
        if not self._responses:
            raise RuntimeError(f"Mock provider ran out of responses for role '{role}'")
        return self._responses.pop(0), True


# ══════════════════════════════════════════════════════════════════════════════
# 1. Successful End-to-End Flow
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineSuccess:
    def test_successful_flow_verified(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        # Step 1 response: valid SQL
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 1;",
            "assumptions": ["Filtered to 2019"],
            "resolved_question": "Top country in 2019",
        })
        # Step 2 response (Explain): prose
        prose_resp = json.dumps({
            "prose": "In 2019, Japan had the highest life expectancy at 84.4 years.",
        })
        provider = MockPipelineLLM([sql_resp, prose_resp])

        # Mock Executor returning RowSet
        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(
                columns=["entity", "life_expectancy"],
                data=[["Japan", 84.4]],
            ),
            query_id="Q123456",
            timings_ms={"snowflake": 45},
            flags=[],
        )

        # Mock Verifier returning VerifyResult
        mock_verifier = MagicMock()
        mock_verifier.return_value = VerifyResult(
            verified_answer="In 2019, Japan had the highest life expectancy at 84.4 years.",
            evidence=[
                EvidenceCell(text="Japan", row_index=0, column="entity"),
                EvidenceCell(text="84.4", row_index=0, column="life_expectancy"),
            ],
            confidence="verified",
            unverifiable_claims=[],
            status="answered",
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp: AskResponse = pipeline.ask("Which country had the highest life expectancy in 2019?")

        assert resp.status == "answered"
        assert "Japan" in (resp.answer or "")
        assert resp.confidence == "verified"
        assert len(resp.evidence) == 2
        assert resp.sql is not None
        assert "LIMIT 1" in resp.sql
        assert resp.query_id == "Q123456"
        assert resp.snowsight_url == "https://app.snowflake.com/test/#/compute/history/queries/Q123456"
        assert len(resp.explanation) > 0
        assert resp.timings_ms["snowflake"] == 45
        assert len(resp.gemma_calls) == 2
        mock_executor.assert_called_once()
        mock_verifier.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Clarification and Refusal Routing (Zero Execution)
# ══════════════════════════════════════════════════════════════════════════════

class TestClarifyAndRefuseRouting:
    def test_clarify_does_not_execute_sql(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        clarify_resp = json.dumps({
            "status": "clarify",
            "clarifying_question": "Do you want data for 2019 or 2020?",
        })
        provider = MockPipelineLLM([clarify_resp])
        mock_executor = MagicMock()
        mock_verifier = MagicMock()

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("Tell me about life expectancy")

        assert resp.status == "clarify"
        assert resp.clarifying_question == "Do you want data for 2019 or 2020?"
        assert resp.sql is None
        mock_executor.assert_not_called()
        mock_verifier.assert_not_called()

    def test_refuse_does_not_execute_sql(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        refuse_resp = json.dumps({
            "status": "refuse",
            "refusal_reason": "Deleting rows is forbidden.",
        })
        provider = MockPipelineLLM([refuse_resp])
        mock_executor = MagicMock()
        mock_verifier = MagicMock()

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("DROP TABLE life_expectancy")

        assert resp.status == "refused"
        assert resp.refusal_reason == "Deleting rows is forbidden."
        assert resp.sql is None
        mock_executor.assert_not_called()
        mock_verifier.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Invalid SQL Never Reaches Executor
# ══════════════════════════════════════════════════════════════════════════════

class TestInvalidSQLBlocked:
    def test_unauthorized_table_never_reaches_executor(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        # Returns unauthorized table repeatedly
        bad_sql = json.dumps({
            "status": "answer",
            "sql": "SELECT * FROM secret_table LIMIT 5;",
        })
        provider = MockPipelineLLM([bad_sql, bad_sql, bad_sql])
        mock_executor = MagicMock()

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
        )

        resp = pipeline.ask("Show secret table")

        assert resp.status == "error"
        # Executor must NEVER be called with unvalidated SQL
        mock_executor.assert_not_called()
        assert len(resp.retries) > 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. Repair Succeeded Followed by Execution
# ══════════════════════════════════════════════════════════════════════════════

class TestRepairThenExecution:
    def test_repair_then_execution(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        # Attempt 1: missing LIMIT
        resp_1 = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy;",
        })
        # Attempt 2 (Repair): adds LIMIT 5
        resp_2 = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy LIMIT 5;",
        })
        # Attempt 3 (Explain)
        resp_3 = json.dumps({
            "prose": "Top 5 countries are listed.",
        })
        provider = MockPipelineLLM([resp_1, resp_2, resp_3])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity"], data=[["A"], ["B"], ["C"], ["D"], ["E"]]),
            query_id="Q_REPAIRED",
            timings_ms={"snowflake": 30},
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
        )

        resp = pipeline.ask("List countries")

        assert resp.status == "answered"
        assert resp.sql is not None
        assert "LIMIT 5" in resp.sql
        assert len(resp.retries) == 1
        mock_executor.assert_called_once_with(resp.sql)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Execution Error and Empty Results
# ══════════════════════════════════════════════════════════════════════════════

class TestExecutionErrorsAndEmpty:
    def test_snowflake_execution_error_handled(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy LIMIT 5;",
        })
        provider = MockPipelineLLM([sql_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=None,
            query_id="Q_TIMEOUT",
            error="Statement timeout reached: 30s",
            flags=["query_timeout_exceeded"],
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
        )

        resp = pipeline.ask("Countries")

        assert resp.status == "error"
        assert "Database query failed" in (resp.answer or "")
        assert resp.query_id == "Q_TIMEOUT"
        assert any("timeout" in f.lower() for f in resp.flags)

    def test_empty_result_set_status_empty(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy WHERE year = 1800 LIMIT 5;",
        })
        provider = MockPipelineLLM([sql_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity"], data=[]),
            query_id="Q_EMPTY",
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
        )

        resp = pipeline.ask("Data for 1800")

        # Handled by code: status must be 'empty'
        assert resp.status == "empty"
        assert "No matching data" in (resp.answer or "")
        assert resp.rows is not None
        assert resp.rows.is_empty()
        assert resp.query_id == "Q_EMPTY"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Verifier Statuses: Partial and Unverifiable
# ══════════════════════════════════════════════════════════════════════════════

class TestVerifierStatuses:
    def test_partially_verified_answer(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy LIMIT 1;",
        })
        prose_resp = json.dumps({"prose": "Japan had life expectancy of 84.4 years and 100% literacy."})
        provider = MockPipelineLLM([sql_resp, prose_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity", "life_expectancy"], data=[["Japan", 84.4]]),
            query_id="Q1",
        )

        mock_verifier = MagicMock()
        mock_verifier.return_value = VerifyResult(
            verified_answer="Japan had life expectancy of 84.4 years.",
            evidence=[EvidenceCell(text="84.4", row_index=0, column="life_expectancy")],
            confidence="partial",
            unverifiable_claims=["100% literacy"],
            status="answered",
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("Japan stats")

        assert resp.status == "answered"
        assert resp.confidence == "partial"
        assert len(resp.evidence) == 1

    def test_unverifiable_answer(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy LIMIT 1;",
        })
        prose_resp = json.dumps({"prose": "Total population was 10 billion."})
        provider = MockPipelineLLM([sql_resp, prose_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity"], data=[["Japan"]]),
            query_id="Q1",
        )

        mock_verifier = MagicMock()
        mock_verifier.return_value = VerifyResult(
            verified_answer="Top result: entity=Japan",
            evidence=[],
            confidence="partial",
            unverifiable_claims=["10 billion"],
            status="unverifiable",
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("Population")

        assert resp.status == "unverifiable"
        assert any("unverifiable" in f for f in resp.flags)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Missing Dependencies and Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingDependenciesAndEdgeCases:
    def test_missing_executor_dependency(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy LIMIT 5;",
        })
        provider = MockPipelineLLM([sql_resp])

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=None,  # No executor configured
        )

        resp = pipeline.ask("Countries")

        assert resp.status == "error"
        assert "executor is not configured" in (resp.answer or "")
        assert "missing_executor_dependency" in resp.flags

    def test_missing_verifier_dependency_falls_back_gracefully(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity FROM life_expectancy LIMIT 1;",
        })
        prose_resp = json.dumps({"prose": "Japan is listed."})
        provider = MockPipelineLLM([sql_resp, prose_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity"], data=[["Japan"]]),
            query_id="Q1",
        )

        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=provider,
            executor=mock_executor,
            verifier=None,  # No verifier configured
        )

        resp = pipeline.ask("Countries")

        assert resp.status == "answered"
        assert resp.answer == "Japan is listed."
        assert "verifier_not_configured" in resp.flags
        assert resp.evidence == []

    def test_empty_question_returns_error(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        pipeline = DataMindPipeline(
            config=mock_config,
            schema=test_schema,
            provider=MockPipelineLLM([]),
        )
        resp = pipeline.ask("   ")
        assert resp.status == "error"
        assert "Question cannot be empty" in (resp.answer or "")

    def test_functional_run_pipeline_entry_point(
        self,
        mock_config: AppConfig,
        test_schema: DatasetSchema,
    ) -> None:
        sql_resp = json.dumps({
            "status": "clarify",
            "clarifying_question": "Which country?",
        })
        provider = MockPipelineLLM([sql_resp])

        resp = run_pipeline(
            question="Tell me",
            config=mock_config,
            schema=test_schema,
            provider=provider,
        )
        assert resp.status == "clarify"
