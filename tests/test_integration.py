"""
tests/test_integration.py — End-to-end integration tests for DataMind pipeline.

Rules enforced:
───────────────
- Zero live network or Snowflake calls in the unit-test suite.
- Uses real AppConfig, real DatasetSchema from datasets/owid.yaml, real SQLValidator,
  real receipt generator, and realistic mock implementations of Person A's
  ExecuteResult and VerifyResult contracts.
- Verifies:
  1. Only validated SQL reaches the executor.
  2. Execution errors cannot produce fabricated evidence.
  3. Verifier's confidence and status are accurately propagated to AskResponse.
  4. SQL receipts match the exact SQL actually executed.
  5. Query IDs, timings, and metadata are preserved throughout.
"""

from __future__ import annotations

import json
from pathlib import Path
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
from src.pipeline import DataMindPipeline
from src.validate import DatasetSchema


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def owid_dataset_schema() -> DatasetSchema:
    owid_path = Path("datasets/owid.yaml")
    assert owid_path.exists(), "datasets/owid.yaml must exist for integration tests"
    return DatasetSchema.from_yaml(owid_path)


@pytest.fixture
def test_config() -> AppConfig:
    return AppConfig(
        provider="api",
        model="gemma-3-27b-it",
        prompt_token_budget=3000,
        rows_to_gemma=50,
        dataset="datasets/owid.yaml",
        snowsight_base_url="https://app.snowflake.com/test-org/account",
        limits={"max_rows": 200, "max_retries": 2, "query_timeout_s": 30},
    )


class MockIntegrationLLM(LLMProvider):
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
        self.call_history.append({"role": role, "prompt": prompt})
        if not self._responses:
            raise RuntimeError(f"Mock provider ran out of responses for role: {role}")
        return self._responses.pop(0), True


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    def test_end_to_end_success_preserves_query_id_and_evidence(
        self,
        test_config: AppConfig,
        owid_dataset_schema: DatasetSchema,
    ) -> None:
        """
        Verify that a valid question correctly traverses:
        Prompt generation -> Gemma SQL -> SQLGlot validation -> Snowflake execution ->
        Gemma explanation -> Evidence verification -> Receipt generation.
        """
        # 1. Gemma responses: (Role 1 & 2: generate) and (Role 4: explain)
        gen_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5;",
            "assumptions": ["Filtered to 2019", "Ranked top 5 descending"],
            "resolved_question": "Top 5 countries by life expectancy in 2019",
        })
        prose_resp = json.dumps({
            "prose": "In 2019, Japan had the highest life expectancy at 84.4 years, followed by Switzerland at 83.8 years.",
        })
        provider = MockIntegrationLLM([gen_resp, prose_resp])

        # 2. Mock Executor (Person A contract)
        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(
                columns=["entity", "life_expectancy"],
                data=[
                    ["Japan", 84.4],
                    ["Switzerland", 83.8],
                    ["Singapore", 83.6],
                    ["Spain", 83.5],
                    ["Italy", 83.3],
                ],
            ),
            query_id="01b3d4f5-0000-1111-0000-222233334444",
            timings_ms={"snowflake": 52},
            flags=[],
        )

        # 3. Mock Verifier (Person A contract)
        mock_verifier = MagicMock()
        mock_verifier.return_value = VerifyResult(
            verified_answer="In 2019, Japan had the highest life expectancy at 84.4 years, followed by Switzerland at 83.8 years.",
            evidence=[
                EvidenceCell(text="Japan", row_index=0, column="entity"),
                EvidenceCell(text="84.4", row_index=0, column="life_expectancy"),
                EvidenceCell(text="Switzerland", row_index=1, column="entity"),
                EvidenceCell(text="83.8", row_index=1, column="life_expectancy"),
            ],
            confidence="verified",
            unverifiable_claims=[],
            status="answered",
        )

        pipeline = DataMindPipeline(
            config=test_config,
            schema=owid_dataset_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp: AskResponse = pipeline.ask("Which 5 countries had highest life expectancy in 2019?")

        # Assertions
        assert resp.status == "answered"
        assert resp.confidence == "verified"
        assert resp.query_id == "01b3d4f5-0000-1111-0000-222233334444"
        assert "01b3d4f5-0000-1111-0000-222233334444" in (resp.snowsight_url or "")
        assert resp.timings_ms["snowflake"] == 52
        assert len(resp.evidence) == 4
        assert resp.rows is not None
        assert resp.rows.row_count() == 5

        # Verify SQL passed to executor was valid and normalized
        executed_sql = mock_executor.call_args[0][0]
        assert "SELECT" in executed_sql
        assert "LIMIT 5" in executed_sql

        # Verify receipt corresponds to the executed query
        assert any("life_expectancy" in line and "Table" in line for line in resp.explanation)
        assert any("year = 2019" in line and "Filter" in line for line in resp.explanation)
        assert any("limit 5" in line.lower() for line in resp.explanation)

    def test_unvalidated_sql_never_reaches_executor(
        self,
        test_config: AppConfig,
        owid_dataset_schema: DatasetSchema,
    ) -> None:
        """
        Verify that unauthorized or malformed SQL queries are blocked by SQLValidator
        and NEVER reach the executor under any circumstances.
        """
        # Model returns unauthorized table and never repairs
        bad_sql = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, password FROM users LIMIT 5;",
        })
        provider = MockIntegrationLLM([bad_sql, bad_sql, bad_sql])

        mock_executor = MagicMock()
        mock_verifier = MagicMock()

        pipeline = DataMindPipeline(
            config=test_config,
            schema=owid_dataset_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("Show user passwords")

        assert resp.status == "error"
        # The executor MUST NEVER be called
        mock_executor.assert_not_called()
        mock_verifier.assert_not_called()
        assert resp.evidence == []
        assert len(resp.retries) > 0

    def test_execution_errors_produce_zero_fabricated_evidence(
        self,
        test_config: AppConfig,
        owid_dataset_schema: DatasetSchema,
    ) -> None:
        """
        Verify that when Snowflake returns an error, no evidence or fake answers are invented.
        """
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy LIMIT 5;",
        })
        provider = MockIntegrationLLM([sql_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=None,
            query_id="ERR_QUERY_001",
            error="Warehouse 'DM_WH' suspended and cannot resume: credit quota exceeded.",
            timings_ms={"snowflake": 10},
            flags=["credit_quota_exceeded"],
        )
        mock_verifier = MagicMock()

        pipeline = DataMindPipeline(
            config=test_config,
            schema=owid_dataset_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("Show data")

        assert resp.status == "error"
        assert "credit quota" in (resp.answer or "")
        assert resp.evidence == []
        assert resp.query_id == "ERR_QUERY_001"
        assert any("quota" in f.lower() for f in resp.flags)
        mock_verifier.assert_not_called()

    def test_verifier_status_accurately_propagated(
        self,
        test_config: AppConfig,
        owid_dataset_schema: DatasetSchema,
    ) -> None:
        """
        Verify that unverifiable claims identified by Person A's verifier
        result in AskResponse.status = 'unverifiable' and are recorded in flags.
        """
        sql_resp = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 1;",
        })
        prose_resp = json.dumps({
            "prose": "Japan has a life expectancy of 84.4 years and 99% health satisfaction.",
        })
        provider = MockIntegrationLLM([sql_resp, prose_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity", "life_expectancy"], data=[["Japan", 84.4]]),
            query_id="Q_UNVERIFIED",
        )

        mock_verifier = MagicMock()
        mock_verifier.return_value = VerifyResult(
            verified_answer="Top result: entity=Japan, life_expectancy=84.4",
            evidence=[],
            confidence="partial",
            unverifiable_claims=["99% health satisfaction not present in data rows"],
            status="unverifiable",
        )

        pipeline = DataMindPipeline(
            config=test_config,
            schema=owid_dataset_schema,
            provider=provider,
            executor=mock_executor,
            verifier=mock_verifier,
        )

        resp = pipeline.ask("Japan stats")

        assert resp.status == "unverifiable"
        assert any("unverifiable" in f.lower() for f in resp.flags)
        assert any("health satisfaction" in f for f in resp.flags)

    def test_sql_receipt_matches_executed_repaired_sql(
        self,
        test_config: AppConfig,
        owid_dataset_schema: DatasetSchema,
    ) -> None:
        """
        Verify that when a query is repaired, the receipt matches the REPAIRED SQL
        that was actually executed, and the failed attempts appear in 'retries'.
        """
        # Attempt 1: missing LIMIT
        bad_sql = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019;",
        })
        # Attempt 2 (Repair): adds LIMIT 5
        repaired_sql = json.dumps({
            "status": "answer",
            "sql": "SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 5;",
            "assumptions": ["Added LIMIT 5 during repair"],
        })
        prose_resp = json.dumps({"prose": "Data for 2019."})

        provider = MockIntegrationLLM([bad_sql, repaired_sql, prose_resp])

        mock_executor = MagicMock()
        mock_executor.return_value = ExecuteResult(
            rows=RowSet(columns=["entity", "life_expectancy"], data=[["Japan", 84.4]]),
            query_id="Q_REPAIR_TEST",
        )

        pipeline = DataMindPipeline(
            config=test_config,
            schema=owid_dataset_schema,
            provider=provider,
            executor=mock_executor,
        )

        resp = pipeline.ask("Life expectancy 2019")

        assert resp.status == "answered"
        # The executed SQL must be the repaired one with LIMIT 5
        executed_sql = mock_executor.call_args[0][0]
        assert "LIMIT 5" in executed_sql

        # The receipt explanation must reflect the executed LIMIT 5
        assert any("limit 5" in line.lower() for line in resp.explanation)

        # The initial failure must be documented in retries
        assert len(resp.retries) == 1
        assert "limit" in resp.retries[0]["error"].lower()
