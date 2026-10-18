"""
src/sqlgen.py — SQL Generation and bounded repair loop for DataMind Receipts.

Orchestrates:
  1. Question interpretation + candidate SQL generation via Gemma (Role 1 & 2).
  2. Fail-closed SQL validation via SQLGlot AST validator (src/validate.py).
  3. Controlled, bounded SQL repair loop (Role 3, up to max_retries).
  4. Tracing and structured result return without secret leakage.

Design rules
────────────
- Zero connections to Snowflake, zero network calls in this module.
- Never generates or executes SQL on 'clarify' or 'refuse' outcomes.
- Every candidate SQL (original or repaired) must pass SQLValidator before return.
- Never creates an unbounded retry loop.
- Records all attempts in 'retries' list for receipt transparency.
- Records all model calls in GemmaCallTrace when provided.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.llm.base import LLMProvider, ProviderError
from src.llm.prompts import (
    build_generate_prompt,
    build_repair_prompt,
    parse_generate_result,
)
from src.llm.trace import GemmaCallTrace, Timer
from src.models import GenerateResult
from src.validate import DatasetSchema, SQLValidator

logger = logging.getLogger(__name__)


# ── Structured Output Model ──────────────────────────────────────────────────

class SQLGenResult(BaseModel):
    """
    Output of generate_validated_sql().
    Contains the validated SQL query or explicit clarify/refuse/error outcome.
    """
    status: Literal["answer", "clarify", "refuse", "error"]
    sql: str | None = None  # Normalised SQL from validator when status == 'answer'
    assumptions: list[str] = Field(default_factory=list)
    resolved_question: str | None = None
    clarifying_question: str | None = None  # populated when status == 'clarify'
    refusal_reason: str | None = None       # populated when status == 'refuse'
    error: str | None = None                # populated when status == 'error'
    error_code: str | None = None           # e.g. "VALIDATION_FAILED", "PROVIDER_ERROR"
    retries: list[dict[str, str]] = Field(default_factory=list)
    # [{"sql": ..., "error": ...}] recording failed attempts before successful repair or exhaustion


# ── Helper to invoke provider ────────────────────────────────────────────────

def _call_provider_generate(
    provider: LLMProvider,
    role: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    trace: GemmaCallTrace | None,
) -> tuple[GenerateResult, bool]:
    """Invoke provider for structured GenerateResult, updating trace if provided."""
    # Use provider's typed convenience method if available
    if role == "generate" and hasattr(provider, "generate_plan_and_sql"):
        return provider.generate_plan_and_sql(  # type: ignore
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            trace=trace,
        )
    if role == "repair" and hasattr(provider, "repair_sql"):
        return provider.repair_sql(  # type: ignore
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            trace=trace,
        )

    # Generic LLMProvider fallback (e.g. for mock providers in unit tests)
    with Timer() as timer:
        raw_text, first_try = provider.raw_generate(
            role=role,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    parsed = parse_generate_result(raw_text)

    if trace is not None:
        trace.record(
            role=role,
            provider=provider.provider_name,
            model=provider.model_name,
            ms=timer.elapsed_ms,
            valid_first_try=first_try,
        )

    return parsed, first_try


# ── Main Orchestration Function ──────────────────────────────────────────────

def generate_validated_sql(
    question: str,
    provider: LLMProvider,
    schema: DatasetSchema,
    *,
    validator: SQLValidator | None = None,
    trace: GemmaCallTrace | None = None,
    max_retries: int = 2,
    max_rows: int = 200,
    temperature_sql: float = 0.1,
    temperature_repair: float = 0.1,
    max_tokens: int = 1024,
    history: list[dict[str, str]] | None = None,
    few_shot_examples: list[dict[str, str]] | None = None,
) -> SQLGenResult:
    """
    Generate and validate a Snowflake SQL query from a user question.

    Steps:
      1. Build understand + text-to-SQL prompt with schema allowlist context.
      2. Call Gemma (Role 1 & 2).
      3. Handle 'clarify' and 'refuse' outcomes without generating/executing SQL.
      4. Validate candidate SQL with fail-closed SQLValidator.
      5. If invalid, enter bounded repair loop (up to max_retries) with sanitized error.
      6. Revalidate repaired SQL from scratch on each attempt.
      7. Return structured SQLGenResult with retry history.
    """
    active_validator = validator or SQLValidator(schema=schema, max_rows=max_rows)
    schema_context = schema.format_context()
    examples = few_shot_examples or schema.examples

    # 1. Build prompt
    prompt = build_generate_prompt(
        question=question,
        schema_context=schema_context,
        history=history,
        few_shot_examples=examples,
    )

    # 2. Initial generate call
    try:
        gen_result, _ = _call_provider_generate(
            provider=provider,
            role="generate",
            prompt=prompt,
            temperature=temperature_sql,
            max_tokens=max_tokens,
            trace=trace,
        )
    except ProviderError as p_err:
        logger.error("Provider error during SQL generation: %s", p_err)
        return SQLGenResult(
            status="error",
            error=f"LLM provider error: {p_err}",
            error_code="PROVIDER_ERROR",
        )
    except Exception as exc:
        logger.error("Unexpected error during SQL generation: %s", exc)
        return SQLGenResult(
            status="error",
            error=f"Failed to generate query: {exc}",
            error_code="GENERATE_FAILED",
        )

    # 3. Handle non-SQL outcomes
    if gen_result.status == "clarify":
        return SQLGenResult(
            status="clarify",
            clarifying_question=gen_result.clarifying_question
            or "Could you please clarify your question?",
            assumptions=gen_result.assumptions,
            resolved_question=gen_result.resolved_question,
        )

    if gen_result.status == "refuse":
        return SQLGenResult(
            status="refuse",
            refusal_reason=gen_result.refusal_reason
            or "Question is outside dataset scope or requests unsupported operations.",
            assumptions=gen_result.assumptions,
            resolved_question=gen_result.resolved_question,
        )

    # 4. Validate initial candidate SQL
    candidate_sql = (gen_result.sql or "").strip()
    val_result = active_validator.validate(candidate_sql)

    if val_result.valid:
        return SQLGenResult(
            status="answer",
            sql=val_result.sql,
            assumptions=gen_result.assumptions,
            resolved_question=gen_result.resolved_question,
            retries=[],
        )

    # 5. Enter bounded repair loop
    retries: list[dict[str, str]] = []
    current_sql = candidate_sql
    current_assumptions = list(gen_result.assumptions)
    attempts = 0

    while not val_result.valid and attempts < max_retries:
        # Record the failed attempt
        retries.append({
            "sql": current_sql,
            "error": val_result.error or val_result.error_code,
        })
        attempts += 1

        logger.info(
            "SQL validation failed (attempt %d/%d): %s. Requesting repair.",
            attempts,
            max_retries,
            val_result.error_code,
        )

        repair_prompt = build_repair_prompt(
            question=question,
            schema_context=schema_context,
            failed_sql=current_sql,
            error_text=val_result.error or val_result.error_code,
        )

        try:
            repair_result, _ = _call_provider_generate(
                provider=provider,
                role="repair",
                prompt=repair_prompt,
                temperature=temperature_repair,
                max_tokens=max_tokens,
                trace=trace,
            )
        except ProviderError as p_err:
            logger.error("Provider error during SQL repair: %s", p_err)
            return SQLGenResult(
                status="error",
                error=f"LLM provider error during repair: {p_err}",
                error_code="PROVIDER_ERROR",
                retries=retries,
                assumptions=current_assumptions,
                resolved_question=gen_result.resolved_question,
            )
        except Exception as exc:
            logger.error("Unexpected error during SQL repair: %s", exc)
            return SQLGenResult(
                status="error",
                error=f"Repair call failed: {exc}",
                error_code="REPAIR_FAILED",
                retries=retries,
                assumptions=current_assumptions,
                resolved_question=gen_result.resolved_question,
            )

        # If repair determined question must be refused or clarified
        if repair_result.status == "refuse":
            return SQLGenResult(
                status="refuse",
                refusal_reason=repair_result.refusal_reason,
                assumptions=current_assumptions + repair_result.assumptions,
                resolved_question=gen_result.resolved_question,
                retries=retries,
            )
        if repair_result.status == "clarify":
            return SQLGenResult(
                status="clarify",
                clarifying_question=repair_result.clarifying_question,
                assumptions=current_assumptions + repair_result.assumptions,
                resolved_question=gen_result.resolved_question,
                retries=retries,
            )

        current_sql = (repair_result.sql or "").strip()
        current_assumptions.extend(repair_result.assumptions)

        # Revalidate repaired SQL from scratch
        val_result = active_validator.validate(current_sql)

        if val_result.valid:
            logger.info("SQL successfully repaired on attempt %d.", attempts)
            return SQLGenResult(
                status="answer",
                sql=val_result.sql,
                assumptions=current_assumptions,
                resolved_question=gen_result.resolved_question,
                retries=retries,
            )

    # 6. Repair budget exhausted without valid SQL
    retries.append({
        "sql": current_sql,
        "error": val_result.error or val_result.error_code,
    })

    return SQLGenResult(
        status="error",
        error=f"SQL validation failed after {attempts} repair attempts: {val_result.error}",
        error_code=val_result.error_code or "VALIDATION_FAILED",
        retries=retries,
        assumptions=current_assumptions,
        resolved_question=gen_result.resolved_question,
    )
