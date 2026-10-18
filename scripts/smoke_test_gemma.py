"""
scripts/smoke_test_gemma.py — Opt-in live smoke test for Google Gemma API.

IMPORTANT NOTICE
────────────────
This script makes REAL live API calls to Google AI Studio.
It requires:
  1. A valid GOOGLE_GEMMA_API_KEY set in your environment or .env file.
  2. Model access enabled in your Google AI Studio account for the target model.
  3. Sufficient API quota (real API calls may consume free tier or billable quota).

DO NOT run this in automated CI without an explicit opt-in flag.

Usage
─────
  # Using the model configured in config.yaml:
  python scripts/smoke_test_gemma.py

  # Specifying a model explicitly:
  python scripts/smoke_test_gemma.py --model gemma-3-27b-it
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root to sys.path so scripts can import src
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Optional dotenv loading if .env file exists
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

from src.config import _API_KEY_ENV_VAR, load_config
from src.llm import (
    GemmaAPIProvider,
    GemmaCallTrace,
    ModelIdentityError,
    ProviderError,
    build_generate_prompt,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in live smoke test for Google Gemma API via google-genai SDK."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Gemma model ID to test (overrides model in config.yaml)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  DataMind Receipts -- Google Gemma API Live Smoke Test")
    print("=" * 70)
    print("NOTICE: This test sends a REAL API request to Google AI Studio.")
    print("Applicable quota or charges may apply to your account.")
    print("-" * 70)

    # 1. Check API Key
    api_key = os.environ.get(_API_KEY_ENV_VAR, "").strip()
    if not api_key:
        print(f"SKIPPED: Environment variable '{_API_KEY_ENV_VAR}' is not set.")
        print("To run this live smoke test:")
        print(f"  1. Obtain an API key from https://aistudio.google.com/apikey")
        print(f"  2. Set {_API_KEY_ENV_VAR} in your environment or local .env file")
        print("  3. Re-run this script.")
        return 0

    # Mask key for display
    masked_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
    print(f"[OK] Found API key in environment: {masked_key}")

    # 2. Determine target model
    target_model = args.model
    if not target_model:
        try:
            cfg = load_config()
            target_model = cfg.model
        except Exception:
            target_model = "gemma-3-27b-it"

    if not target_model:
        target_model = "gemma-3-27b-it"

    print(f"[*] Target model: {target_model}")

    # 3. Instantiate provider
    try:
        provider = GemmaAPIProvider(model_name=target_model)
    except ModelIdentityError as err:
        print(f"[FAIL] Model configuration error: {err}")
        return 1
    except Exception as err:
        print(f"[FAIL] Provider initialisation error: {err}")
        return 1

    print(f"[OK] Provider initialized: {provider}")

    # 4. Prepare test prompt
    test_question = "Which 5 countries had the highest life expectancy in 2019?"
    test_schema = (
        "table: life_expectancy\n"
        "columns:\n"
        "  - entity: country name (text)\n"
        "  - code: ISO3 code (text)\n"
        "  - year: calendar year (int)\n"
        "  - life_expectancy: years (float)\n"
    )
    few_shot = [
        {
            "q": "What was the life expectancy in France in 2010?",
            "sql": "SELECT life_expectancy FROM life_expectancy WHERE entity = 'France' AND year = 2010 LIMIT 1;",
        }
    ]

    prompt = build_generate_prompt(
        question=test_question,
        schema_context=test_schema,
        few_shot_examples=few_shot,
    )

    trace = GemmaCallTrace()

    print("[*] Sending test question to Gemma...")
    try:
        result, first_try = provider.generate_plan_and_sql(
            prompt=prompt,
            temperature=0.1,
            max_tokens=1024,
            trace=trace,
        )
    except ProviderError as err:
        print(f"[FAIL] Provider error: {err}")
        if err.is_rate_limit:
            print("Hint: You hit a rate limit / quota exhaustion. Wait and retry.")
        return 1
    except Exception as err:
        print(f"[FAIL] Unexpected error during call: {err}")
        return 1

    # 5. Display results and trace
    print("-" * 70)
    print("[SUCCESS] Live Gemma call completed successfully!")
    print(f"  Status:             {result.status}")
    print(f"  Generated SQL:      {result.sql}")
    print(f"  Assumptions:        {result.assumptions}")
    print(f"  Resolved Question:  {result.resolved_question}")
    print(f"  Valid on First Try: {first_try}")
    print(f"  Elapsed Time:       {trace.total_ms()} ms")
    print(f"  Trace Summary:      {trace.to_list()}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
