"""
src/models.py — Shared Pydantic models for DataMind Receipts.

─────────────────────────────────────────────────────────────────────────────
COORDINATION NOTE (Person A):
  ExecuteResult, RowSet, and VerifyResult defined here are the PROVISIONAL
  canonical definitions that Person B's pipeline will import.  Person A must
  review and confirm (or request changes to) these three models before
  src/pipeline.py is written.  All other models in this file are owned by
  Person B and do not require cross-team sign-off.

  Open questions for Person A:
    1. Is `error` in ExecuteResult a short sanitised string (recommended) or
       an exception type?  Never include raw Snowflake error text that may
       reveal schema information.
    2. Does execute_query() run EXPLAIN internally, or should pipeline.py
       request it separately?
    3. Is flags: list[str] the right shape for row-cap and null warnings, or
       would a structured type be cleaner?
─────────────────────────────────────────────────────────────────────────────

No API calls, no Snowflake connections, no file I/O in this module.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Shared data transport ─────────────────────────────────────────────────────

class RowSet(BaseModel):
    """A bounded result set returned by Snowflake execution."""
    columns: list[str]
    data: list[list[Any]]  # row-major; each inner list aligns with columns

    def is_empty(self) -> bool:
        return len(self.data) == 0

    def row_count(self) -> int:
        return len(self.data)


# ── Person A's contracts (provisional — awaiting confirmation) ─────────────

class ExecuteResult(BaseModel):
    """
    PROVISIONAL — awaiting Person A confirmation.
    Returned by src/execute.py after a Snowflake query.
    """
    rows: RowSet | None = None          # None only on execution error
    query_id: str | None = None         # Snowflake query ID; always present on success
    snowsight_url: str | None = None    # set when config.snowsight_base_url is configured
    error: str | None = None            # None on success; short sanitised message on failure
                                        # MUST NOT include raw Snowflake error text that
                                        # reveals schema details or credentials
    timings_ms: dict[str, int] = Field(default_factory=dict)   # {"snowflake": <ms>}
    flags: list[str] = Field(default_factory=list)
    # e.g. ["results truncated to 200 rows", "query_timeout exceeded"]


class EvidenceCell(BaseModel):
    """A single cell in the result set that supports a claim in the answer prose."""
    text: str          # string representation of the cell value
    row_index: int     # 0-based index into RowSet.data
    column: str        # column name from RowSet.columns


class VerifyResult(BaseModel):
    """
    PROVISIONAL — awaiting Person A confirmation.
    Returned by src/verify.py after claim verification.
    """
    verified_answer: str                    # possibly rewritten for safety
    evidence: list[EvidenceCell] = Field(default_factory=list)
    confidence: Literal["verified", "partial"]
    unverifiable_claims: list[str] = Field(default_factory=list)
    status: Literal["answered", "unverifiable"]


# ── SQL validation ────────────────────────────────────────────────────────────

class ValidationResult(BaseModel):
    """Returned by src/validate.py. No Snowflake calls are made here."""
    valid: bool
    sql: str | None = None      # normalised SQL when valid; None when invalid
    error: str = ""             # empty when valid; sanitised message when invalid
    error_code: str = ""        # e.g. "MULTI_STATEMENT", "FORBIDDEN_TABLE", "MISSING_LIMIT"

    def __iter__(self):
        yield self.valid
        yield self.sql if self.valid else self.error

    def __getitem__(self, item: int | str):
        if isinstance(item, int):
            return (self.valid, self.sql if self.valid else self.error)[item]
        return getattr(self, item)



# ── LLM internal models ───────────────────────────────────────────────────────

class GenerateResult(BaseModel):
    """
    Internal Pydantic model for the LLM's structured output from the generate
    (understand + write SQL) step.

    This is NOT the public API response format.  The pipeline maps this to
    the public API statuses: answered | clarify | refused | empty | error |
    unverifiable.  The `empty` status is always set by pipeline code after
    execute() returns 0 rows — Gemma never sets it.
    """
    status: Literal["answer", "clarify", "refuse"]
    sql: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    resolved_question: str | None = None    # how a follow-up was interpreted
    clarifying_question: str | None = None  # populated when status == "clarify"
    refusal_reason: str | None = None       # populated when status == "refuse"


class ProseResult(BaseModel):
    """Internal model for the prose explanation step (Gemma role 4)."""
    prose: str    # 1–3 plain sentences; Gemma does not embed numbers or evidence pointers


class RepairInput(BaseModel):
    """Inputs forwarded to Gemma for a SQL repair attempt."""
    failed_sql: str
    error_text: str     # sanitised validator/Snowflake error; must not reveal credentials


# ── Tracing ───────────────────────────────────────────────────────────────────

class GemmaCallEntry(BaseModel):
    """One model call recorded in gemma_calls on the API response."""
    role: str           # "generate" | "repair" | "explain"
    provider: str       # "api" | "ollama"
    model: str          # exact configured model identifier
    ms: int             # elapsed milliseconds
    valid_first_try: bool


# ── Public API response ───────────────────────────────────────────────────────

class ModelInfo(BaseModel):
    provider: str   # "api" | "ollama"
    name: str       # exact configured model identifier


class AskResponse(BaseModel):
    """
    Canonical public API response for POST /ask.
    Owned by Person B; consumed by Person C's React frontend.
    Do not alter field names or types without coordinating with Person C.
    """
    status: Literal["answered", "clarify", "refused", "empty", "error", "unverifiable"]
    answer: str | None = None
    evidence: list[EvidenceCell] = Field(default_factory=list)
    clarifying_question: str | None = None
    refusal_reason: str | None = None
    sql: str | None = None
    explanation: list[str] = Field(default_factory=list)   # from receipt.py (parsed SQL)
    resolved_question: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    rows: RowSet | None = None
    query_id: str | None = None
    snowsight_url: str | None = None
    retries: list[dict] = Field(default_factory=list)       # [{"sql": ..., "error": ...}]
    confidence: Literal["verified", "partial"] | None = None
    flags: list[str] = Field(default_factory=list)
    model: ModelInfo | None = None
    gemma_calls: list[GemmaCallEntry] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
