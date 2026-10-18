# AGENTS.md — Workspace Rules for DataMind Receipts

> Always-on directives, architectural constraints, and compliance guardrails for AI coding agents and developers working on the DataMind Receipts codebase.

---

## Hard Rules (Zero-Tolerance)

1. **Never commit secrets:** Never commit API keys, Snowflake passwords, tokens, or credentials to git. `.env` is ignored and `.env.example` is committed. All commits and git history must remain clean of secrets.
2. **Never add a local-file fallback to the answer path:** Every answer must come from a live Snowflake query. Never fall back to pandas, local CSVs, SQLite, or mocked results to answer a question.
3. **Never use Snowflake Cortex LLM functions for SQL generation:** No `CORTEX.COMPLETE`, Cortex Analyst, or non-Gemma Cortex models in the answer or SQL path. Gemma variants alone perform language tasks.
4. **Strict separation of concerns:** Keep LLM inference exclusively in `src/llm/` and execution in `src/execute.py`. Gemma only does language work (understanding, writing SQL, repairing SQL, and explaining verified rows). Gemma never executes queries, computes/rounds numbers, validates SQL, or selects evidence cells.
5. **Gemma only does language work:** Do not delegate deterministic tasks, computation, aggregation, ranking, or cell verification to Gemma.
6. **Every code change needs a pytest or eval check:** Every feature, validator rule, verifier check, or bugfix must be accompanied by a corresponding unit test (`pytest` in `tests/`) or an evaluation question in `eval/`.
7. **No hardcoded schema in code:** Never hardcode table or column names in Python code or prompts. All schema information must be loaded dynamically from dataset YAML files.

---

## Section 3: Hackathon Rules We Must Obey

*Checked against the official hackathon guide.*

| Rule | What the guide says | How we comply |
| :--- | :--- | :--- |
| **Snowflake (P1, M7)** | Data is stored or queried in Snowflake. Not meaningful: unused connection, answering from local files, mocked results. | Every answer is a live Snowflake query. No pandas or local fallback in the answer path. |
| **Gemma (P2, M7)** | Any Gemma variant does a central task. Non-Gemma Cortex models do not satisfy this. Keep inference separate from data processing. | Gemma does four central jobs: understands the question and states assumptions, writes the SQL (including follow-ups), repairs its own SQL from error text, and explains the verified rows. Every output is schema-validated. Snowflake only runs SQL; code validates and verifies. No Cortex COMPLETE or Analyst in the path. AI Studio and Ollama are our implementation choices; the guide only requires a Gemma variant doing a central task. |
| **Question-time compute (P3, M2)** | Answers come from retrieval or queries run at question time. | No cached answers or pre-baked results in the demo. If development-time prompt/response caching is used, disable it in demo/public mode and never use it to skip the required live Snowflake query. |
| **Open source (G1, G5)** | Public repo with licence; no keys, tokens or passwords. | MIT or Apache-2.0. `.env` ignored, `.env.example` committed, secret-scan pre-commit hook, scan history before the final push. |
| **Reproducible (G2)** | No undisclosed credentials or paid services; otherwise document a feasible evaluation path. | README: judge gets own free Snowflake trial and AI Studio key, runs `setup_snowflake.sql`, then `python run.py` (Windows, macOS, Linux). Fallback evidence: query IDs, eval results, recorded demo. |
| **Real system (G3)** | No mock-ups or hard-coded outputs. | Live typed questions in the demo; mock JSON allowed only during UI development. |
| **Data disclosure (G4)** | Sources and licences disclosed; no non-redistributable data committed. | **TPC-H and TPC-DS sample data: reference only, do not copy into the repo.** OWID: check each indicator licence (CC BY unless stated) and attribute. Do not use the deprecated OpenWeatherMap sample. |
| **Correctness cap** | If criterion 1 is Poor, the PS1 score is capped at 50. | Eval-first development; correctness beats polish. |
| **Scoring** | Final = 0.70 × track score + 0.30 × common score. | Final 2.5 hours before 7:00 reserved for docs and evidence (common rubric is 30%). |

### Mandatory Features and Rubric: Where Each is Handled

| Guide item | Where we handle it |
| :--- | :--- |
| **M1 everyday language, unseen questions** | Generate step with YAML synonyms and few-shot examples; eval has varied phrasing, typos and held-out questions (section 13). |
| **M2 real query at question time** | Live Snowflake execution for every answer (pipeline step 3); no cache in demo mode. |
| **M3 no unsupported claims** | Gemma writes prose only; the verifier matches every number and name to a returned cell, with a code-built fallback sentence. |
| **M4 plain-language method** | Receipt text built from the parsed SQL plus Gemma's stated assumptions. |
| **M5 inspectable evidence** | SQL, rows, query ID and highlighted evidence cells in the receipt. |
| **M6 ambiguity, missing data, unsupported** | Stated assumption or clarify, `empty` and `refused` statuses, tested in section 12. |
| **M7 Snowflake and Gemma integration** | Snowflake runs and stores everything; Gemma has four roles (section 6A); the two are separated. |
| **M8 working, reproducible, open source** | Public repo, licence, pinned dependencies, setup script, `python run.py`, README. |
| **Rubric 1 and 2 (correctness, language to data)** | Eval with independent ground truth, held-out and follow-up questions. |
| **Rubric 3 (evidence)** | Receipt, query ID, claim-to-cell highlighting, retries shown. |
| **Rubric 4 (Snowflake)** | Reader and loader roles, resource monitor, X-Small, timeout, query tag, setup script. |
| **Rubric 5 (Gemma)** | Central task, validated outputs, documented deployment, separation: section 6A. |
| **Rubric 6 (guardrails)** | Section 12 tests and the README red-team table. |
| **Rubric 7 (reuse)** | TPC-H by config only, tested early. |
| **Rubric 8 (relevance, UX)** | Named persona, lean receipt UI. |

### Mistakes to Avoid (Seen in Other Plans; Never Reintroduce)

1. **Do not insert into `SNOWFLAKE_SAMPLE_DATA`.** It is shared and read-only. The data-change demo runs on tables in our own database.
2. **No regex "guard" for safety.** Use `sqlglot` (SELECT only, one statement, table allowlist) plus the read-only role. Block `INSERT`, `MERGE`, `COPY`, `CALL`, `GRANT` too, not just `DROP`, `DELETE` and `ALTER`.
3. **The data-change demo never uses the LLM path.** It is an operator-invoked local script with fixed, reviewed statements and separate loader credentials. Do not expose a loader-backed mutation endpoint in FastAPI, even behind a UI toggle.
4. **No invented numbers or scores in the docs or UI** (for example "95+ points", "15/15", "100% live"). Say "numbers verified against the returned rows".
5. **Gemma does not validate SQL, compute numbers or decide what counts as evidence.** Snowflake `EXPLAIN`, the validator and the verifier do. Do not hard-code a Gemma model name; read it from `config.yaml`.
6. **Do not claim "claimed" credits as done or required.** Credits are optional extras (section 1).

---

## Section 4: Locked Decisions

| Topic | Decision |
| :--- | :--- |
| **Product** | **DataMind Receipts:** every answer ships with a receipt (how I got this, executed SQL, rows, Snowflake query ID, data-quality flags). |
| **Persona** | A policy or public-health analyst who knows the questions but not SQL. |
| **Datasets** | Primary: 3 or 4 OWID indicators (for example life expectancy, GDP per capita, CO2, population) loaded into **our own** Snowflake database. Second, config-only: TPC-H from `SNOWFLAKE_SAMPLE_DATA`. Fallback: UCI. |
| **Gemma access** | Gemini API (key created in Google AI Studio) with a hosted Gemma model actually listed for the account. Google's current Gemma API documentation lists `gemma-4-31b-it` and `gemma-4-26b-a4b-it`; verify access and quota with a smoke test. Fallback: Ollama using an exact Gemma tag that is available locally and passes the same tests; do not assume a Gemma 4 local tag exists. Use a tested Gemma 3 tag if needed. Switch by `provider` in `config.yaml`, no code change. AI Studio and Ollama are our choices, not organizer requirements. The question, schema text, limited prior-turn context and result rows are sent to the configured hosted provider when using the API. Use public/non-sensitive data for this project, disclose this clearly in the README, and never send Snowflake credentials or secrets. Local Ollama inference stays on the machine, subject to any separately enabled network features. Section 6A lists exactly what Gemma does. |
| **Extra prizes** | The hackathon site (our screenshots) also lists "Best Use of Gemma 4" and a DigitalOcean "Best Open-Source AI Project" (open-weight AI important, public repo, open licence). A Gemma 4 variant and an open licence target both. They are extras, never gates. |
| **Snowflake** | Read-only role (SELECT only), X-SMALL warehouse, 60 s auto-suspend, resource monitor, `QUERY_TAG` on every query. The application uses only DM_READER credentials. Any optional data-change demo is run by a separate, operator-invoked local script using DM_LOADER credentials; those credentials are never available to the web app or public deployment. |
| **SQL safety** | `sqlglot`: SELECT only, table allowlist, enforced LIMIT, statement timeout, `EXPLAIN` before run, at most 2 retries feeding the error back to Gemma. |
| **Receipts** | The "how I got this" text is generated **from the parsed SQL**, not by Gemma, so it always matches what ran. |
| **Verifier** | Gemma writes prose only. Code verifies claim-level entity/value pairs against the same result row. Relational claims (largest, increased, doubled, rankings, differences) are allowed only when computed deterministically from the returned result set or encoded in the SQL semantics and independently checked. A number merely appearing somewhere in the rows is not sufficient. Unmatched/unsupported claim: regenerate once, then return a conservative code-built sentence or an explicit unverifiable status. |
| **Frontend** | React + Vite + Tailwind, FastAPI backend. One origin in production; Vite proxy in development. The UI is a thin renderer of the API JSON. **FastAPI also serves the prebuilt `web/dist`** (commit the build), so a judge needs only Python and one command; Node is needed only to rebuild. |
| **Scope** | No role-based access, no charts unless everything else passes the eval. Visual polish alone earns little (criterion 8). |
