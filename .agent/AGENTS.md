# AGENTS.md — Always-on rules for every coding agent working on DataMind Receipts
# Source: DataMind Receipts Developer Handbook, sections 2, 3, 4
# Every rule below is locked. Do not override them without a handbook change.

## Project identity
- Product: DataMind Receipts (ZeroDay OSS Hacktoberfest 2026, PS1)
- Persona: a policy or public-health analyst who knows questions but not SQL.
- Internal deadline: 7:00 PM IST. Feature freeze: 4:30 PM IST.

## Hackathon compliance (gates — violating any = disqualification)
- Every answer must come from a live Snowflake query. No pandas fallback, no local files, no cached results in demo mode.
- Gemma does language work only: understand the question, write SQL, repair SQL, explain verified rows. Nothing else.
- Gemma never computes numbers, rounds values, validates SQL, picks evidence cells, or writes the receipt explanation.
- The receipt explanation is built from the parsed SQL by code, not by Gemma.
- Evidence cells are found by the verifier (code), never supplied by Gemma.
- Never use Snowflake Cortex LLM functions (CORTEX.COMPLETE, Cortex Analyst) in the answer path.
- No mock-ups or hard-coded outputs anywhere (gate G3).
- Never commit secrets. .env is in .gitignore. Only .env.example is committed.

## Architecture rules
- All Gemma calls go through one LLMProvider.generate(role, prompt, schema) in src/llm/.
- Model names come from config.yaml only. Never hard-code a model name in Python.
- No table or column names in Python code or prompts. They come from YAML only (datasets/owid.yaml, datasets/tpch.yaml).
- Inference lives in src/llm/. Execution lives in src/execute.py. They are separate.
- The app connects with DM_READER credentials only. DM_LOADER credentials are never in FastAPI or any deployed environment.
- The data-change demo runs as a local operator-invoked script (sql/admin_update_demo.sql) with DM_LOADER. It is not a web endpoint.
- No mutation endpoint (/demo/mutate, /demo/reset) in FastAPI, even behind a toggle.
- cache_llm_responses must be false in demo/public mode.

## SQL safety rules (Section 4, locked)
- Use sqlglot (Snowflake dialect) for all SQL validation.
- Allow SELECT only. Single statement only. Table allowlist enforced. LIMIT enforced. EXPLAIN before run.
- Block: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, MERGE, COPY, CALL, GRANT, CREATE, multi-statements, comments hiding a second statement.
- Max 2 retries: on SQL error, feed the failed SQL and error text back to Gemma for repair.
- Every retry is shown in the receipt (failed SQL + error text).

## Gemma output rules
- Every Gemma output is validated by Pydantic before use.
- On invalid JSON: one repair retry, then return a clean error status.
- Default to a stated assumption rather than asking for clarification. Clarify only when no sensible default exists.
- Statuses from the generate step: answer | clarify | refuse (Pydantic enum). The pipeline maps to API statuses: answered | clarify | refused | empty | error | unverifiable. The empty status is set by code after a valid query returns no rows — never by Gemma.

## Evidence and verifier rules
- Verifier extracts numbers and entity names from the Gemma prose and matches them to cells in the result rows.
- Relational claims (largest, increased, doubled, rankings, differences) are allowed only when computed deterministically from the result set or encoded in the SQL semantics and independently checked.
- A number merely appearing somewhere in the rows is not sufficient for a relational claim.
- Unmatched claim: regenerate once, then fall back to a code-built sentence or explicit unverifiable status.

## Mistakes the agent must not reintroduce (Section 3 of handbook)
1. Do not INSERT into SNOWFLAKE_SAMPLE_DATA. It is shared and read-only. Data-change demo uses our own DATAMIND tables.
2. No regex guard for safety. Use sqlglot + read-only role. Block INSERT, MERGE, COPY, CALL, GRANT too.
3. The data-change demo never uses the LLM path. It is a fixed local admin script.
4. No invented numbers or scores in docs or UI (e.g., "95+ points", "15/15", "100% live"). Say "numbers verified against returned rows".
5. Gemma does not validate SQL, compute numbers, or decide what counts as evidence.
6. Do not claim credits as done or required. Credits are optional extras.

## Per-task working style
- One small task per prompt. Run the code, check the result, then commit.
- Every change needs a test or an eval question.
- After each task: tell the human how to run and check it.
