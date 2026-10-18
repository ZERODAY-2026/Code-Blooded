"""
tests/test_config.py — Unit tests for src/config.py, src/models.py,
src/llm/base.py, and src/llm/trace.py.

Rules
─────
- No API calls, no Snowflake connections, no network access.
- No real API keys; env vars are monkey-patched in tests that need them.
- All config is written to tmp_path fixtures; no global state.

Run with:
    python -m pytest tests/test_config.py -v
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import yaml

from src.config import (
    AppConfig,
    LimitsConfig,
    TemperatureConfig,
    api_key_env_var_name,
    get_api_key,
    load_config,
)
from src.llm.base import LLMProvider, ModelIdentityError, ProviderError
from src.llm.trace import GemmaCallTrace, Timer
from src.models import (
    AskResponse,
    EvidenceCell,
    ExecuteResult,
    GemmaCallEntry,
    GenerateResult,
    ModelInfo,
    ProseResult,
    RepairInput,
    RowSet,
    ValidationResult,
    VerifyResult,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    """Write a minimal valid config.yaml to tmp_path and return its path."""
    base: dict = {
        "provider": "api",
        "model": "gemma-3-27b-it",  # valid: contains "gemma"
        "local_model": "",
        "temperature": {"sql": 0.1, "repair": 0.1, "prose": 0.2, "second_opinion": 0.7},
        "prompt_token_budget": 3000,
        "rows_to_gemma": 50,
        "dataset": "datasets/owid.yaml",
        "limits": {"max_rows": 200, "max_retries": 2, "query_timeout_s": 30},
        "cache_llm_responses": False,
        "self_check": False,
        "public_mode": False,
        "snowsight_base_url": "",
    }
    if overrides:
        base.update(overrides)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(base), encoding="utf-8")
    return cfg_path


# ══════════════════════════════════════════════════════════════════════════════
# 1. load_config — valid cases
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadConfigValid:
    def test_valid_config_loads(self, tmp_path: Path) -> None:
        cfg_path = _write_config(tmp_path)
        cfg = load_config(cfg_path)
        assert isinstance(cfg, AppConfig)

    def test_provider_is_api(self, tmp_path: Path) -> None:
        cfg = load_config(_write_config(tmp_path))
        assert cfg.provider == "api"

    def test_model_preserved(self, tmp_path: Path) -> None:
        cfg = load_config(_write_config(tmp_path))
        assert cfg.model == "gemma-3-27b-it"

    def test_limits_loaded(self, tmp_path: Path) -> None:
        cfg = load_config(_write_config(tmp_path))
        assert cfg.limits.max_rows == 200
        assert cfg.limits.max_retries == 2
        assert cfg.limits.query_timeout_s == 30

    def test_temperatures_loaded(self, tmp_path: Path) -> None:
        cfg = load_config(_write_config(tmp_path))
        assert cfg.temperature.sql == pytest.approx(0.1)
        assert cfg.temperature.repair == pytest.approx(0.1)
        assert cfg.temperature.prose == pytest.approx(0.2)
        assert cfg.temperature.second_opinion == pytest.approx(0.7)

    def test_defaults_applied_for_missing_optional_fields(self, tmp_path: Path) -> None:
        # Minimal config: only required fields
        minimal = {"provider": "api", "model": "gemma-3-27b-it"}
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(minimal), encoding="utf-8")
        cfg = load_config(cfg_path)
        assert cfg.cache_llm_responses is False
        assert cfg.self_check is False
        assert cfg.public_mode is False
        assert cfg.rows_to_gemma == 50
        assert cfg.prompt_token_budget == 3000

    def test_gemma4_model_accepted(self, tmp_path: Path) -> None:
        cfg = load_config(_write_config(tmp_path, {"model": "gemma-4-31b-it"}))
        assert "gemma" in cfg.model.lower()

    def test_model_string_stripped_of_whitespace(self, tmp_path: Path) -> None:
        cfg = load_config(_write_config(tmp_path, {"model": "  gemma-3-27b-it  "}))
        assert cfg.model == "gemma-3-27b-it"

    def test_ollama_provider_with_local_model(self, tmp_path: Path) -> None:
        cfg = load_config(
            _write_config(tmp_path, {"provider": "ollama", "local_model": "gemma3:27b"})
        )
        assert cfg.provider == "ollama"
        assert cfg.local_model == "gemma3:27b"


# ══════════════════════════════════════════════════════════════════════════════
# 2. load_config — invalid cases
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadConfigInvalid:
    def test_missing_config_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_empty_model_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"model": ""}))

    def test_blank_model_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"model": "   "}))

    def test_non_gemma_model_raises_value_error(self, tmp_path: Path) -> None:
        """
        A model string that doesn't contain 'gemma' must be rejected.

        This is a config-level check — it does not prove runtime identity.
        """
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"model": "gemini-pro"}))

    def test_gpt_model_rejected(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"model": "gpt-4o"}))

    def test_invalid_provider_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"provider": "openai"}))

    def test_ollama_without_local_model_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(
                _write_config(tmp_path, {"provider": "ollama", "local_model": ""})
            )

    def test_ollama_with_blank_local_model_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(
                _write_config(tmp_path, {"provider": "ollama", "local_model": "   "})
            )

    def test_token_budget_too_small_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"prompt_token_budget": 100}))

    def test_rows_to_gemma_zero_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, Exception)):
            load_config(_write_config(tmp_path, {"rows_to_gemma": 0}))

    def test_secret_in_config_file_rejected(self, tmp_path: Path) -> None:
        """API keys must never appear in config.yaml."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump({
                "provider": "api",
                "model": "gemma-3-27b-it",
                "api_key": "AIzaSy_FAKE_KEY",  # must be rejected
            }),
            encoding="utf-8",
        )
        with pytest.raises((ValueError, Exception)):
            load_config(cfg_path)

    def test_password_in_config_file_rejected(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump({"provider": "api", "model": "gemma-3-27b-it", "password": "secret"}),
            encoding="utf-8",
        )
        with pytest.raises((ValueError, Exception)):
            load_config(cfg_path)


# ══════════════════════════════════════════════════════════════════════════════
# 3. get_api_key — env var access
# ══════════════════════════════════════════════════════════════════════════════

class TestGetApiKey:
    def test_reads_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMMA_API_KEY", "fake-test-key-abc123")
        key = get_api_key()
        assert key == "fake-test-key-abc123"

    def test_missing_env_var_raises_environment_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GOOGLE_GEMMA_API_KEY", raising=False)
        with pytest.raises(EnvironmentError):
            get_api_key()

    def test_empty_env_var_raises_environment_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_GEMMA_API_KEY", "")
        with pytest.raises(EnvironmentError):
            get_api_key()

    def test_whitespace_only_env_var_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_GEMMA_API_KEY", "   ")
        with pytest.raises(EnvironmentError):
            get_api_key()

    def test_api_key_env_var_name_correct(self) -> None:
        assert api_key_env_var_name() == "GOOGLE_GEMMA_API_KEY"

    def test_appconfig_has_no_api_key_field(self) -> None:
        """AppConfig must not have a field that stores an API key."""
        field_names = set(AppConfig.model_fields.keys())
        forbidden = {"api_key", "apikey", "google_api_key", "key", "secret", "token"}
        overlap = field_names & forbidden
        assert not overlap, f"AppConfig should not have these fields: {overlap}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. LLMProvider ABC and ModelIdentityError
# ══════════════════════════════════════════════════════════════════════════════

class _FakeProvider(LLMProvider):
    """Minimal concrete implementation for testing the ABC contract."""

    def __init__(self, model_id: str) -> None:
        self._assert_gemma_model(model_id)
        self._model = model_id

    @property
    def provider_name(self) -> str:
        return "api"

    @property
    def model_name(self) -> str:
        return self._model

    def raw_generate(
        self, role: str, prompt: str, temperature: float, max_tokens: int
    ) -> tuple[str, bool]:
        return ('{"status": "answer", "sql": "SELECT 1"}', True)


class TestLLMProviderABC:
    def test_gemma_model_accepted(self) -> None:
        provider = _FakeProvider("gemma-3-27b-it")
        assert provider.model_name == "gemma-3-27b-it"

    def test_non_gemma_model_raises_model_identity_error(self) -> None:
        with pytest.raises(ModelIdentityError):
            _FakeProvider("gemini-pro")

    def test_empty_model_raises_model_identity_error(self) -> None:
        with pytest.raises(ModelIdentityError):
            _FakeProvider("")

    def test_gpt_model_raises(self) -> None:
        with pytest.raises(ModelIdentityError):
            _FakeProvider("gpt-4o")

    def test_gemma4_variant_accepted(self) -> None:
        provider = _FakeProvider("gemma-4-31b-it")
        assert provider.model_name == "gemma-4-31b-it"

    def test_identity_dict_contains_provider_and_name(self) -> None:
        provider = _FakeProvider("gemma-3-27b-it")
        ident = provider.identity()
        assert ident["provider"] == "api"
        assert ident["name"] == "gemma-3-27b-it"

    def test_model_identity_error_is_value_error(self) -> None:
        assert issubclass(ModelIdentityError, ValueError)

    def test_provider_error_carries_rate_limit_flag(self) -> None:
        err = ProviderError("rate limited", is_rate_limit=True)
        assert err.is_rate_limit is True

    def test_provider_error_default_not_rate_limit(self) -> None:
        err = ProviderError("timeout")
        assert err.is_rate_limit is False


# ══════════════════════════════════════════════════════════════════════════════
# 5. GemmaCallTrace
# ══════════════════════════════════════════════════════════════════════════════

class TestGemmaCallTrace:
    def test_empty_trace_total_ms_is_zero(self) -> None:
        trace = GemmaCallTrace()
        assert trace.total_ms() == 0

    def test_empty_trace_call_count_is_zero(self) -> None:
        trace = GemmaCallTrace()
        assert trace.call_count() == 0

    def test_empty_trace_first_try_rate_is_one(self) -> None:
        trace = GemmaCallTrace()
        assert trace.first_try_rate() == 1.0

    def test_record_single_call(self) -> None:
        trace = GemmaCallTrace()
        trace.record(
            role="generate",
            provider="api",
            model="gemma-3-27b-it",
            ms=250,
            valid_first_try=True,
        )
        assert trace.call_count() == 1
        assert trace.total_ms() == 250

    def test_record_multiple_calls_sums_ms(self) -> None:
        trace = GemmaCallTrace()
        trace.record(role="generate", provider="api", model="gemma-3-27b-it", ms=300, valid_first_try=True)
        trace.record(role="repair", provider="api", model="gemma-3-27b-it", ms=200, valid_first_try=False)
        trace.record(role="explain", provider="api", model="gemma-3-27b-it", ms=150, valid_first_try=True)
        assert trace.total_ms() == 650
        assert trace.call_count() == 3

    def test_first_try_rate_partial(self) -> None:
        trace = GemmaCallTrace()
        trace.record(role="generate", provider="api", model="gemma-3-27b-it", ms=100, valid_first_try=True)
        trace.record(role="repair", provider="api", model="gemma-3-27b-it", ms=100, valid_first_try=False)
        assert trace.first_try_rate() == pytest.approx(0.5)

    def test_to_list_returns_correct_structure(self) -> None:
        trace = GemmaCallTrace()
        trace.record(role="generate", provider="api", model="gemma-3-27b-it", ms=123, valid_first_try=True)
        entries = trace.to_list()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["role"] == "generate"
        assert entry["provider"] == "api"
        assert entry["model"] == "gemma-3-27b-it"
        assert entry["ms"] == 123
        assert entry["valid_first_try"] is True

    def test_to_list_is_serialisable(self) -> None:
        import json
        trace = GemmaCallTrace()
        trace.record(role="explain", provider="ollama", model="gemma3:27b", ms=50, valid_first_try=True)
        # Must not raise
        json.dumps(trace.to_list())

    def test_trace_is_per_request(self) -> None:
        """Each request must create a fresh trace; they must not share state."""
        t1 = GemmaCallTrace()
        t2 = GemmaCallTrace()
        t1.record(role="generate", provider="api", model="gemma-3-27b-it", ms=100, valid_first_try=True)
        assert t2.call_count() == 0


# ══════════════════════════════════════════════════════════════════════════════
# 6. Timer context manager
# ══════════════════════════════════════════════════════════════════════════════

class TestTimer:
    def test_timer_records_positive_elapsed(self) -> None:
        import time
        with Timer() as t:
            time.sleep(0.01)
        assert t.elapsed_ms >= 10

    def test_timer_returns_self(self) -> None:
        timer = Timer()
        result = timer.__enter__()
        timer.__exit__(None, None, None)
        assert result is timer

    def test_timer_zero_elapsed_for_no_op(self) -> None:
        with Timer() as t:
            pass
        # Even a no-op takes some microseconds; just check it's non-negative
        assert t.elapsed_ms >= 0


# ══════════════════════════════════════════════════════════════════════════════
# 7. Shared Pydantic models (src/models.py)
# ══════════════════════════════════════════════════════════════════════════════

class TestSharedModels:
    # ── RowSet ────────────────────────────────────────────────────────────────
    def test_rowset_is_empty(self) -> None:
        rs = RowSet(columns=["a"], data=[])
        assert rs.is_empty() is True

    def test_rowset_not_empty(self) -> None:
        rs = RowSet(columns=["a"], data=[[1]])
        assert rs.is_empty() is False

    def test_rowset_row_count(self) -> None:
        rs = RowSet(columns=["a", "b"], data=[[1, 2], [3, 4], [5, 6]])
        assert rs.row_count() == 3

    # ── ExecuteResult (provisional — for coordination with Person A) ──────────
    def test_execute_result_success(self) -> None:
        rs = RowSet(columns=["x"], data=[[42]])
        er = ExecuteResult(rows=rs, query_id="abc123", error=None)
        assert er.query_id == "abc123"
        assert er.error is None

    def test_execute_result_error(self) -> None:
        er = ExecuteResult(rows=None, query_id=None, error="Query timed out")
        assert er.rows is None
        assert er.error == "Query timed out"

    def test_execute_result_flags_default_empty(self) -> None:
        er = ExecuteResult()
        assert er.flags == []

    # ── ValidationResult ──────────────────────────────────────────────────────
    def test_validation_result_valid(self) -> None:
        vr = ValidationResult(valid=True, sql="SELECT 1", error="", error_code="")
        assert vr.valid is True
        assert vr.error == ""

    def test_validation_result_invalid(self) -> None:
        vr = ValidationResult(valid=False, sql=None, error="Forbidden table", error_code="FORBIDDEN_TABLE")
        assert vr.valid is False
        assert vr.sql is None
        assert vr.error_code == "FORBIDDEN_TABLE"

    # ── GenerateResult ────────────────────────────────────────────────────────
    def test_generate_result_answer(self) -> None:
        gr = GenerateResult(status="answer", sql="SELECT 1", assumptions=["latest year"])
        assert gr.status == "answer"
        assert gr.sql == "SELECT 1"

    def test_generate_result_clarify(self) -> None:
        gr = GenerateResult(status="clarify", clarifying_question="Which year?")
        assert gr.status == "clarify"
        assert gr.sql is None

    def test_generate_result_refuse(self) -> None:
        gr = GenerateResult(status="refuse", refusal_reason="Write operations are not supported.")
        assert gr.status == "refuse"
        assert gr.sql is None

    def test_generate_result_invalid_status_raises(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GenerateResult(status="answered")  # "answered" is the API status; model uses "answer"

    # ── AskResponse ───────────────────────────────────────────────────────────
    def test_ask_response_answered(self) -> None:
        rs = RowSet(columns=["entity", "life_expectancy"], data=[["Japan", 84.4]])
        resp = AskResponse(
            status="answered",
            answer="Japan had the highest life expectancy in 2019.",
            rows=rs,
            sql="SELECT entity, life_expectancy FROM life_expectancy WHERE year = 2019 ORDER BY life_expectancy DESC LIMIT 5",
            query_id="qid_001",
            confidence="verified",
        )
        assert resp.status == "answered"
        assert resp.query_id == "qid_001"
        assert resp.confidence == "verified"

    def test_ask_response_clarify(self) -> None:
        resp = AskResponse(status="clarify", clarifying_question="Which indicator do you mean?")
        assert resp.status == "clarify"

    def test_ask_response_refused(self) -> None:
        resp = AskResponse(status="refused", refusal_reason="Write operations are not supported.")
        assert resp.status == "refused"

    def test_ask_response_empty(self) -> None:
        resp = AskResponse(status="empty", answer="No data found for the requested year.")
        assert resp.status == "empty"

    def test_ask_response_error(self) -> None:
        resp = AskResponse(status="error")
        assert resp.status == "error"
        assert resp.answer is None

    def test_ask_response_invalid_status_raises(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AskResponse(status="unknown_status")

    def test_ask_response_serialisable(self) -> None:
        import json
        resp = AskResponse(
            status="answered",
            answer="Test answer.",
            model=ModelInfo(provider="api", name="gemma-3-27b-it"),
        )
        # model_dump and JSON serialisation must not raise
        data = resp.model_dump()
        json.dumps(data)

    def test_evidence_cell_structure(self) -> None:
        cell = EvidenceCell(text="84.4", row_index=0, column="life_expectancy")
        assert cell.text == "84.4"
        assert cell.row_index == 0
        assert cell.column == "life_expectancy"

    def test_gemma_call_entry_structure(self) -> None:
        entry = GemmaCallEntry(
            role="generate",
            provider="api",
            model="gemma-3-27b-it",
            ms=312,
            valid_first_try=True,
        )
        assert entry.ms == 312
        assert entry.valid_first_try is True

    def test_model_info_structure(self) -> None:
        mi = ModelInfo(provider="api", name="gemma-3-27b-it")
        assert mi.provider == "api"

    # ── VerifyResult (provisional) ────────────────────────────────────────────
    def test_verify_result_answered(self) -> None:
        vr = VerifyResult(
            verified_answer="Japan had life expectancy 84.4 in 2019.",
            evidence=[EvidenceCell(text="84.4", row_index=0, column="life_expectancy")],
            confidence="verified",
            status="answered",
        )
        assert vr.status == "answered"
        assert len(vr.evidence) == 1

    def test_verify_result_unverifiable(self) -> None:
        vr = VerifyResult(
            verified_answer="The data does not support this claim.",
            confidence="partial",
            unverifiable_claims=["The increase was 3%"],
            status="unverifiable",
        )
        assert vr.status == "unverifiable"
        assert "The increase was 3%" in vr.unverifiable_claims

    # ── ProseResult / RepairInput ─────────────────────────────────────────────
    def test_prose_result(self) -> None:
        pr = ProseResult(prose="Japan had the highest life expectancy.")
        assert pr.prose.startswith("Japan")

    def test_repair_input(self) -> None:
        ri = RepairInput(failed_sql="SELECT * FROM forbidden", error_text="Table not in allowlist")
        assert ri.failed_sql is not None
        assert ri.error_text is not None
