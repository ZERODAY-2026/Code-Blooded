"""
tests/test_api.py — Unit and integration tests for FastAPI endpoints.

Rules enforced:
───────────────
- Zero real API keys or external network calls.
- Zero live Snowflake connections.
- Uses FastAPI TestClient with dependency injection.
- Tests:
  - Health check (/health and /api/health)
  - Datasets listing (/datasets and /api/datasets)
  - Successful /ask and /api/ask execution
  - Clarification routing
  - Refusal routing
  - Invalid request payloads (validation errors, 422)
  - Empty question handling
  - Pipeline error handling
  - Complete response serialization against AskResponse schema
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from api.main import AskRequest, app, create_app, get_default_pipeline
from src.config import AppConfig
from src.llm.base import LLMProvider
from src.models import (
    AskResponse,
    EvidenceCell,
    GemmaCallEntry,
    ModelInfo,
    RowSet,
)
from src.pipeline import DataMindPipeline
from src.validate import DatasetSchema


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_pipeline() -> MagicMock:
    """Mock DataMindPipeline instance with default properties."""
    pipe = MagicMock(spec=DataMindPipeline)
    pipe.config = AppConfig(
        provider="api",
        model="gemma-3-27b-it",
        dataset="datasets/owid.yaml",
    )
    pipe.schema = DatasetSchema(name="owid")
    pipe.provider = MagicMock(spec=LLMProvider)
    pipe.provider.provider_name = "api"
    pipe.provider.model_name = "gemma-3-27b-it"
    return pipe


@pytest.fixture
def client(mock_pipeline: MagicMock) -> TestClient:
    """TestClient with injected mock pipeline."""
    test_app = create_app(pipeline=mock_pipeline)
    return TestClient(test_app)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Health and System Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    def test_health_check_root(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["app"] == "DataMind Receipts"
        assert data["model"] == "gemma-3-27b-it"
        assert data["provider"] == "api"

    def test_health_check_api_prefix(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_datasets_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/datasets")
        assert resp.status_code == 200
        data = resp.json()
        assert "datasets" in data
        assert "active" in data
        assert "owid" in data["datasets"] or "owid" == data["active"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. Query Endpoints (/ask and /api/ask)
# ══════════════════════════════════════════════════════════════════════════════

class TestAskEndpoints:
    def test_ask_successful_response(
        self,
        client: TestClient,
        mock_pipeline: MagicMock,
    ) -> None:
        mock_pipeline.ask.return_value = AskResponse(
            status="answered",
            answer="In 2019, Japan had the highest life expectancy at 84.4 years.",
            evidence=[
                EvidenceCell(text="Japan", row_index=0, column="entity"),
                EvidenceCell(text="84.4", row_index=0, column="life_expectancy"),
            ],
            sql="SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 LIMIT 5;",
            explanation=["Table: life_expectancy", "Filter: year = 2019", "Limit: 5"],
            resolved_question="Top country in 2019",
            assumptions=["Filtered to 2019"],
            rows=RowSet(columns=["entity", "life_expectancy"], data=[["Japan", 84.4]]),
            query_id="01b3d4f5-0000-1111-0000-222233334444",
            snowsight_url="https://app.snowflake.com/test/#/compute/history/queries/01b3d4f5",
            confidence="verified",
            flags=[],
            model=ModelInfo(provider="api", name="gemma-3-27b-it"),
            gemma_calls=[
                GemmaCallEntry(role="generate", provider="api", model="gemma-3-27b-it", ms=120, valid_first_try=True),
                GemmaCallEntry(role="explain", provider="api", model="gemma-3-27b-it", ms=95, valid_first_try=True),
            ],
            timings_ms={"gemma": 215, "snowflake": 45},
        )

        payload = {"question": "Which country had highest life expectancy in 2019?"}
        resp = client.post("/api/ask", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "answered"
        assert "Japan" in data["answer"]
        assert data["confidence"] == "verified"
        assert len(data["evidence"]) == 2
        assert data["sql"] is not None
        assert data["query_id"] == "01b3d4f5-0000-1111-0000-222233334444"
        assert len(data["explanation"]) == 3
        assert data["timings_ms"]["snowflake"] == 45
        assert len(data["gemma_calls"]) == 2

    def test_ask_both_routes_supported(
        self,
        client: TestClient,
        mock_pipeline: MagicMock,
    ) -> None:
        mock_pipeline.ask.return_value = AskResponse(
            status="answered",
            answer="Answer text",
        )
        # Check /ask without /api prefix
        resp1 = client.post("/ask", json={"question": "Test question"})
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "answered"

        # Check /api/ask with /api prefix
        resp2 = client.post("/api/ask", json={"question": "Test question"})
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "answered"

    def test_ask_clarification(
        self,
        client: TestClient,
        mock_pipeline: MagicMock,
    ) -> None:
        mock_pipeline.ask.return_value = AskResponse(
            status="clarify",
            clarifying_question="Did you want data for 2019 or 2020?",
            sql=None,
        )

        resp = client.post("/api/ask", json={"question": "Tell me about life expectancy"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "clarify"
        assert data["clarifying_question"] == "Did you want data for 2019 or 2020?"
        assert data["sql"] is None

    def test_ask_refusal(
        self,
        client: TestClient,
        mock_pipeline: MagicMock,
    ) -> None:
        mock_pipeline.ask.return_value = AskResponse(
            status="refused",
            refusal_reason="Deleting data is strictly prohibited.",
            sql=None,
        )

        resp = client.post("/api/ask", json={"question": "DROP TABLE life_expectancy"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "refused"
        assert "prohibited" in data["refusal_reason"]
        assert data["sql"] is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Input Validation and Error Handling
# ══════════════════════════════════════════════════════════════════════════════

class TestValidationAndErrors:
    def test_missing_question_field_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/ask", json={})
        assert resp.status_code == 422
        errors = resp.json().get("detail", [])
        assert any("question" in str(err) for err in errors)

    def test_empty_string_question_returns_structured_error(
        self,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/ask", json={"question": "   "})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "empty_user_question" in data["flags"]

    def test_unhandled_exception_returns_500_without_secret_leak(
        self,
        client: TestClient,
        mock_pipeline: MagicMock,
    ) -> None:
        mock_pipeline.ask.side_effect = RuntimeError("Sensitive db_pass: XYZ123 occurred!")

        resp = client.post("/api/ask", json={"question": "Trigger error"})

        assert resp.status_code == 500
        data = resp.json()
        assert data["status"] == "error"
        # Secret must NOT be present in response
        assert "XYZ123" not in resp.text
        assert "internal_server_error" in data["flags"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. Serialization Check (Person C Contract)
# ══════════════════════════════════════════════════════════════════════════════

class TestResponseSerialization:
    def test_all_contract_fields_present_in_serialized_json(
        self,
        client: TestClient,
        mock_pipeline: MagicMock,
    ) -> None:
        mock_pipeline.ask.return_value = AskResponse(
            status="answered",
            answer="Complete response test",
            evidence=[],
            clarifying_question=None,
            refusal_reason=None,
            sql="SELECT 1;",
            explanation=["Table: test"],
            resolved_question="Resolved",
            assumptions=["Assumption 1"],
            rows=RowSet(columns=["a"], data=[[1]]),
            query_id="Q100",
            snowsight_url="https://snowsight.example.com/Q100",
            retries=[{"sql": "bad sql", "error": "syntax error"}],
            confidence="verified",
            flags=["flag1"],
            model=ModelInfo(provider="api", name="gemma-3-27b-it"),
            gemma_calls=[GemmaCallEntry(role="generate", provider="api", model="gemma-3-27b-it", ms=10, valid_first_try=True)],
            timings_ms={"gemma": 10, "snowflake": 20},
        )

        resp = client.post("/api/ask", json={"question": "Test"})
        assert resp.status_code == 200
        json_data = resp.json()

        # Check all fields expected by Person C
        expected_fields = [
            "status",
            "answer",
            "evidence",
            "clarifying_question",
            "refusal_reason",
            "sql",
            "explanation",
            "resolved_question",
            "assumptions",
            "rows",
            "query_id",
            "snowsight_url",
            "retries",
            "confidence",
            "flags",
            "model",
            "gemma_calls",
            "timings_ms",
        ]
        for field in expected_fields:
            assert field in json_data, f"Missing field in serialized response: {field}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Dataset Override and Configuration Error Regression Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestDatasetOverrideAndConfigErrors:
    def test_unknown_dataset_override_returns_clean_error(
        self,
        client: TestClient,
    ) -> None:
        resp = client.post(
            "/api/ask",
            json={"question": "Test question?", "dataset": "non_existent_xyz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "not found" in data["answer"].lower()
        assert any("unknown_dataset" in f for f in data["flags"])

    def test_configuration_error_reported_clearly(self) -> None:
        def failing_pipeline() -> DataMindPipeline:
            raise EnvironmentError("Environment variable 'GOOGLE_GEMMA_API_KEY' is not set or is empty.")

        test_app = create_app()
        test_app.dependency_overrides[get_default_pipeline] = failing_pipeline
        c = TestClient(test_app, raise_server_exceptions=False)

        resp = c.get("/health")
        assert resp.status_code == 500
        data = resp.json()
        assert data["status"] == "error"
        assert "configuration_error" in data["flags"]
        assert "GOOGLE_GEMMA_API_KEY" in data["answer"]


