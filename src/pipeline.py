"""
src/pipeline.py — End-to-end DataMind Receipts Pipeline.

Orchestrates:
  1. Input question interpretation and classification (Understand).
  2. Clarification / Refusal routing without SQL generation or execution.
  3. Text-to-SQL generation with fail-closed SQLGlot AST validation.
  4. Bounded repair loop on validation errors (revalidating every attempt).
  5. Deterministic SQL execution through injectable executor interface.
  6. Empty result detection (status="empty" set by code, never Gemma).
  7. Answer explanation (Gemma prose from result rows only).
  8. Evidence verification through injectable verifier interface.
  9. Deterministic SQL receipt assembly from parsed SQL and assumptions.
  10. Full AskResponse assembly with timing, trace, flags, and query metadata.

Safety and Guardrail Rules:
───────────────────────────
- Never execute SQL that has not passed validation.
- Never bypass validation during repair.
- Never invent rows or evidence when execution fails.
- Do not claim that an answer is verified if the verifier reports otherwise.
- Handle missing executor/verifier dependencies explicitly.
- Do not silently substitute Gemini or another model for Gemma.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from src.config import AppConfig
from src.llm.base import LLMProvider, ProviderError
from src.llm.prompts import build_explain_prompt
from src.llm.trace import GemmaCallTrace
from src.models import (
    AskResponse,
    EvidenceCell,
    ExecuteResult,
    ModelInfo,
    RowSet,
    VerifyResult,
)
from src.receipt import build_receipt_explanation
from src.sqlgen import SQLGenResult, generate_validated_sql
from src.validate import DatasetSchema, SQLValidator

logger = logging.getLogger(__name__)

# Type aliases for injectable dependencies owned by Person A
ExecutorFunc = Callable[[str], ExecuteResult]
VerifierFunc = Callable[..., VerifyResult]


# ── Dependency Invocation Helper ─────────────────────────────────────────────

def _safe_verify(
    verifier: VerifierFunc,
    prose: str,
    rows: RowSet,
    question: str,
    is_simulated: bool = False,
) -> VerifyResult:
    """
    Safely invoke the external verifier, adapting to signature variations
    (e.g. (prose, rows, question), (prose, rows), or keyword arguments).
    """
    sig = inspect.signature(verifier)
    params = list(sig.parameters.keys())

    # Check if keyword argument matching works
    kwargs: dict[str, Any] = {}
    if "prose" in params:
        kwargs["prose"] = prose
    if "rows" in params:
        kwargs["rows"] = rows
    if "question" in params:
        kwargs["question"] = question
    if "is_simulated" in params:
        kwargs["is_simulated"] = is_simulated

    if len(kwargs) == len(params) and len(params) > 0:
        return verifier(**kwargs)

    # Positional attempts
    try:
        return verifier(prose, rows, question)
    except TypeError:
        try:
            return verifier(question, rows, prose)
        except TypeError:
            return verifier(prose, rows)


# ── Pipeline Implementation ──────────────────────────────────────────────────

class DataMindPipeline:
    """
    Orchestrates the question-to-verified-receipt pipeline.
    """

    def __init__(
        self,
        config: AppConfig,
        schema: DatasetSchema,
        provider: LLMProvider,
        *,
        executor: ExecutorFunc | None = None,
        verifier: VerifierFunc | None = None,
        validator: SQLValidator | None = None,
    ) -> None:
        self.config = config
        self.schema = schema
        self.provider = provider
        self.executor = executor
        self.verifier = verifier
        self.validator = validator or SQLValidator(
            schema=schema,
            max_rows=config.limits.max_rows,
        )

    def ask(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        session_id: str | None = None,
    ) -> AskResponse:
        """
        Execute the full question-answering pipeline.
        """
        clean_question = (question or "").strip()
        trace = GemmaCallTrace()
        model_info = ModelInfo(
            provider=self.provider.provider_name,
            name=self.provider.model_name,
        )

        if not clean_question:
            return AskResponse(
                status="error",
                answer="Question cannot be empty.",
                flags=["empty_user_question"],
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": 0, "snowflake": 0},
            )

        # 1. Generate & Validate SQL (Roles 1, 2, and bounded 3)
        sql_gen_result: SQLGenResult = generate_validated_sql(
            question=clean_question,
            provider=self.provider,
            schema=self.schema,
            validator=self.validator,
            trace=trace,
            max_retries=self.config.limits.max_retries,
            max_rows=self.config.limits.max_rows,
            temperature_sql=self.config.temperature.sql,
            temperature_repair=self.config.temperature.repair,
            max_tokens=self.config.limits.max_tokens_sql,
            history=history,
        )

        # 2. Handle Clarification
        if sql_gen_result.status == "clarify":
            return AskResponse(
                status="clarify",
                clarifying_question=sql_gen_result.clarifying_question,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": 0},
            )

        # 3. Handle Refusal
        if sql_gen_result.status == "refuse":
            return AskResponse(
                status="refused",
                refusal_reason=sql_gen_result.refusal_reason,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": 0},
            )

        # 4. Handle SQL Generation / Validation Error
        if sql_gen_result.status == "error" or not sql_gen_result.sql:
            explanation = (
                build_receipt_explanation(sql_gen_result.sql, sql_gen_result.assumptions)
                if sql_gen_result.sql
                else []
            )
            return AskResponse(
                status="error",
                answer=f"Could not construct a valid analytical query: {sql_gen_result.error}",
                sql=sql_gen_result.sql,
                explanation=explanation,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                retries=sql_gen_result.retries,
                flags=[sql_gen_result.error or "SQL validation failed"],
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": 0},
            )

        validated_sql = sql_gen_result.sql
        receipt_lines = build_receipt_explanation(
            validated_sql,
            sql_gen_result.assumptions,
        )

        # 5. Check Executor Dependency
        if self.executor is None:
            return AskResponse(
                status="error",
                answer="Database executor is not configured.",
                sql=validated_sql,
                explanation=receipt_lines,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                retries=sql_gen_result.retries,
                flags=["missing_executor_dependency"],
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": 0},
            )

        # 6. Execute Validated SQL
        try:
            exec_result: ExecuteResult = self.executor(validated_sql)
        except Exception as exec_err:
            logger.error("Exception during SQL execution: %s", exec_err)
            return AskResponse(
                status="error",
                answer="An unexpected error occurred while executing the query.",
                sql=validated_sql,
                explanation=receipt_lines,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                retries=sql_gen_result.retries,
                flags=[f"Executor exception: {exec_err}"],
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": 0},
            )

        sf_ms = exec_result.timings_ms.get("snowflake", 0)
        snowsight_url = exec_result.snowsight_url
        if not snowsight_url and self.config.snowsight_base_url and exec_result.query_id:
            snowsight_url = f"{self.config.snowsight_base_url.rstrip('/')}/#/compute/history/queries/{exec_result.query_id}"

        # 7. Handle Execution Failure
        if exec_result.error:
            return AskResponse(
                status="error",
                answer=f"Database query failed: {exec_result.error}",
                sql=validated_sql,
                explanation=receipt_lines,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                query_id=exec_result.query_id,
                snowsight_url=snowsight_url,
                retries=sql_gen_result.retries,
                flags=exec_result.flags + [f"Snowflake error: {exec_result.error}"],
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": sf_ms},
            )

        rows = exec_result.rows
        flags = list(exec_result.flags)

        # 8. Handle Empty Results (Status "empty" set by code, never Gemma)
        if rows is None or rows.is_empty():
            return AskResponse(
                status="empty",
                answer="No matching data found for your query.",
                sql=validated_sql,
                rows=rows or RowSet(columns=[], data=[]),
                explanation=receipt_lines,
                assumptions=sql_gen_result.assumptions,
                resolved_question=sql_gen_result.resolved_question,
                query_id=exec_result.query_id,
                snowsight_url=snowsight_url,
                retries=sql_gen_result.retries,
                flags=flags + ["empty_result_set"],
                model=model_info,
                gemma_calls=trace.to_list(),
                timings_ms={"gemma": trace.total_ms(), "snowflake": sf_ms},
            )

        # Check if rows hit application or configured row cap
        if rows.row_count() >= self.config.limits.max_rows:
            flags.append(f"results capped at {self.config.limits.max_rows} rows")

        # 9. Explain Step: Call Gemma Role 4 for plain prose
        explain_prompt = build_explain_prompt(
            question=clean_question,
            columns=rows.columns,
            rows=rows.data,
            max_rows=self.config.rows_to_gemma,
        )

        raw_prose = ""
        try:
            if hasattr(self.provider, "explain_answer"):
                prose_res, _ = self.provider.explain_answer(
                    prompt=explain_prompt,
                    temperature=self.config.temperature.prose,
                    max_tokens=self.config.limits.max_tokens_prose,
                    trace=trace,
                )
                raw_prose = prose_res.prose
            else:
                raw_text, first_try = self.provider.raw_generate(
                    role="explain",
                    prompt=explain_prompt,
                    temperature=self.config.temperature.prose,
                    max_tokens=self.config.limits.max_tokens_prose,
                )
                from src.llm.prompts import parse_prose_result
                prose_res = parse_prose_result(raw_text)
                raw_prose = prose_res.prose
                trace.record(
                    role="explain",
                    provider=self.provider.provider_name,
                    model=self.provider.model_name,
                    ms=0,
                    valid_first_try=first_try,
                )
        except Exception as prose_err:
            logger.warning("Gemma prose generation failed: %s. Using code-built fallback.", prose_err)
            flags.append("prose_generation_fallback")
            # Deterministic code-built fallback summary
            top_row = rows.data[0] if rows.data else []
            top_str = ", ".join(f"{col}={val}" for col, val in zip(rows.columns, top_row))
            raw_prose = f"Top result: {top_str}"

        # 10. Evidence Verification
        evidence: list[EvidenceCell] = []
        confidence: Any = None
        verified_answer = raw_prose
        status = "answered"

        is_simulated = "simulated_demo_result" in flags
        if self.verifier is not None:
            try:
                verify_result = _safe_verify(
                    self.verifier,
                    raw_prose,
                    rows,
                    clean_question,
                    is_simulated=is_simulated,
                )
                verified_answer = verify_result.verified_answer
                evidence = verify_result.evidence
                confidence = verify_result.confidence
                if verify_result.status == "unverifiable":
                    status = "unverifiable"
                    flags.append("unverifiable_claims_detected")
                    if verify_result.unverifiable_claims:
                        flags.extend(f"unverifiable: {c}" for c in verify_result.unverifiable_claims)
            except Exception as ver_err:
                logger.warning("Verifier invocation failed: %s", ver_err)
                flags.append(f"verifier_error: {ver_err}")
                confidence = "partial"
        else:
            flags.append("verifier_not_configured")

        # Guard: simulated results must never be marked verified against live Snowflake data
        if is_simulated and confidence == "verified":
            confidence = "partial"

        # 11. Final Response Assembly
        return AskResponse(
            status=status,
            answer=verified_answer,
            evidence=evidence,
            sql=validated_sql,
            explanation=receipt_lines,
            resolved_question=sql_gen_result.resolved_question,
            assumptions=sql_gen_result.assumptions,
            rows=rows,
            query_id=exec_result.query_id,
            snowsight_url=snowsight_url,
            retries=sql_gen_result.retries,
            confidence=confidence,
            flags=flags,
            model=model_info,
            gemma_calls=trace.to_list(),
            timings_ms={"gemma": trace.total_ms(), "snowflake": sf_ms},
        )


def run_pipeline(
    question: str,
    config: AppConfig,
    schema: DatasetSchema,
    provider: LLMProvider,
    *,
    executor: ExecutorFunc | None = None,
    verifier: VerifierFunc | None = None,
    validator: SQLValidator | None = None,
    history: list[dict[str, str]] | None = None,
) -> AskResponse:
    """
    Convenience functional entry point for running the DataMind pipeline.
    """
    pipeline = DataMindPipeline(
        config=config,
        schema=schema,
        provider=provider,
        executor=executor,
        verifier=verifier,
        validator=validator,
    )
    return pipeline.ask(question, history=history)
