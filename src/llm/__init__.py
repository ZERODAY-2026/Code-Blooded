"""
src/llm — Gemma model provider and prompt engineering package.
"""

from src.llm.base import LLMProvider, ModelIdentityError, ProviderError
from src.llm.gemma_api import GemmaAPIProvider
from src.llm.prompts import (
    build_explain_prompt,
    build_generate_prompt,
    build_repair_prompt,
    extract_json,
    parse_generate_result,
    parse_prose_result,
)
from src.llm.trace import GemmaCallTrace, Timer

__all__ = [
    "LLMProvider",
    "ProviderError",
    "ModelIdentityError",
    "GemmaAPIProvider",
    "GemmaCallTrace",
    "Timer",
    "build_generate_prompt",
    "build_repair_prompt",
    "build_explain_prompt",
    "extract_json",
    "parse_generate_result",
    "parse_prose_result",
]
