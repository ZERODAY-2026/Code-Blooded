"""
src/llm/trace.py — Per-answer Gemma call accumulator.

GemmaCallTrace collects one entry per model call (generate, repair, explain)
and produces the gemma_calls list that appears in the public API response.

Design rules
────────────
- Entries are appended by the call site after each raw_generate() call.
- The trace is per-request: create a new GemmaCallTrace for each /ask request.
- The trace records the configured model name and provider, not any
  response-level model identifier.  If the API returns a model field in its
  response, the provider layer (M2) may record a mismatch warning separately.
- No secrets, prompts, or full responses are stored in the trace.

No network calls, no file I/O, no Snowflake in this module.
"""

from __future__ import annotations

import time
from typing import Any

from src.models import GemmaCallEntry


class GemmaCallTrace:
    """
    Accumulates Gemma call records for a single /ask request.

    Usage
    ─────
        trace = GemmaCallTrace()

        t0 = time.monotonic()
        raw_text, first_try = provider.raw_generate(...)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        trace.record(
            role="generate",
            provider=provider.provider_name,
            model=provider.model_name,
            ms=elapsed_ms,
            valid_first_try=first_try,
        )

        # At response time:
        response.gemma_calls = trace.to_list()
        response.timings_ms["gemma"] = trace.total_ms()
    """

    def __init__(self) -> None:
        self._entries: list[GemmaCallEntry] = []

    def record(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        ms: int,
        valid_first_try: bool,
    ) -> None:
        """Append one call entry to the trace."""
        self._entries.append(
            GemmaCallEntry(
                role=role,
                provider=provider,
                model=model,
                ms=ms,
                valid_first_try=valid_first_try,
            )
        )

    def to_list(self) -> list[dict[str, Any]]:
        """Return the trace as a list of dicts for JSON serialisation."""
        return [entry.model_dump() for entry in self._entries]

    def total_ms(self) -> int:
        """Sum of elapsed milliseconds across all recorded calls."""
        return sum(e.ms for e in self._entries)

    def call_count(self) -> int:
        return len(self._entries)

    def first_try_rate(self) -> float:
        """
        Fraction of calls that succeeded on the first provider attempt.
        Returns 1.0 when no calls have been recorded.
        """
        if not self._entries:
            return 1.0
        return sum(1 for e in self._entries if e.valid_first_try) / len(self._entries)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"GemmaCallTrace(calls={self.call_count()}, "
            f"total_ms={self.total_ms()}, "
            f"first_try_rate={self.first_try_rate():.0%})"
        )


# ── Timing helper ─────────────────────────────────────────────────────────────

class Timer:
    """
    Simple context-manager timer.  Used by the pipeline to measure each
    provider call without scattering time.monotonic() calls everywhere.

    Usage
    ─────
        with Timer() as t:
            raw_text, first_try = provider.raw_generate(...)
        elapsed_ms = t.elapsed_ms
    """

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed_ms: int = 0

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = int((time.monotonic() - self._start) * 1000)
