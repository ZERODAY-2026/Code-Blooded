# Problem Statement: PS1 — DataMind: Talk to Your Data

> **Hacktoberfest Hack Day Dharwad × Vidkarya | ZeroDay OSS, IIIT Dharwad**  
> Team: **Code Blooded** | Person A: Aryan Verma | Person B: Kolukuri Ashrith | Person C: Kataru Deekshith Reddy  
> Repository: `https://github.com/ZERODAY-2026/Code-Blooded.git`

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![Snowflake: Live Execution](https://img.shields.io/badge/Snowflake-Live_Execution_(DM__READER)-29B5E8.svg)](https://www.snowflake.com/)
[![Google Gemma 4](https://img.shields.io/badge/Google_Gemma-Gemma_4-4285F4.svg)](https://ai.google.dev/gemma)
[![Guardrail: sqlglot AST](https://img.shields.io/badge/Guardrails-sqlglot_AST_Parser-orange.svg)](src/validate.py)

---

## 📖 Table of Contents

- [1. Executive Summary](#1-executive-summary)
- [2. System Architecture & Visual Data Flow](#2-system-architecture--visual-data-flow)
  - [Turn-by-Turn Query Lifecycle](#turn-by-turn-query-lifecycle)
  - [DataMind Receipt Anatomy (Illustrative Example)](#datamind-receipt-anatomy-illustrative-example)
- [3. Core System Design: Language vs. Determinism](#3-core-system-design-language-vs-determinism)
- [4. Technical Differentiator: AST Guardrails vs. Naive Regex](#4-technical-differentiator-ast-guardrails-vs-naive-regex)
- [5. Intended Behaviour: The 6 Rubric Archetypes (Design Targets, Pipeline Pending)](#5-intended-behaviour-the-6-rubric-archetypes-design-targets-pipeline-pending)
- [6. Live Snowflake Execution & Verification Design](#6-live-snowflake-execution--verification-design)
- [7. Dynamic Dataset Reusability (Design Goal, Pipeline Pending)](#7-dynamic-dataset-reusability-design-goal-pipeline-pending)
- [8. Datasets & Licences](#8-datasets--licences)
- [9. Team Roles & Code Ownership](#9-team-roles--code-ownership)
- [10. Project Structure](#10-project-structure)
- [11. Step-by-Step Setup & Reproduction](#11-step-by-step-setup--reproduction)
  - [Current Implementation Status](#current-implementation-status)
  - [Prerequisites](#prerequisites)
  - [Setup Walkthrough](#setup-walkthrough)
- [12. Red-Team Guardrail Matrix](#12-red-team-guardrail-matrix)
- [13. Evaluation Harness (`eval/`)](#13-evaluation-harness-eval)
- [14. Known Limitations](#14-known-limitations)
- [15. Technical Boundaries & Trade-Offs](#15-technical-boundaries--trade-offs)
- [16. Troubleshooting & Operational Notes](#16-troubleshooting--operational-notes)
- [17. License](#17-license)

---

## 1. Executive Summary

**DataMind Receipts** is a verifiable natural-language interface to Snowflake cloud data warehouses.

Existing Text-to-SQL solutions frequently suffer from hallucinated data values, unconstrained SQL generation, silent schema drift, and security risks from unchecked DDL/DML execution. DataMind solves these failure modes by enforcing a strict **Language vs. Determinism Boundary**:
1. **Google Gemma 4** is utilized exclusively for language intelligence: intent parsing, candidate SQL synthesis, ambiguity identification, and explanatory prose generation.
2. **Deterministic Code** handles all critical safety invariants: AST-level SQL validation via `sqlglot`, table allowlist enforcement, automatic `LIMIT` clamping, cell-level claim verification, and machine-auditable query receipts.

---

## 2. System Architecture & Visual Data Flow

The end-to-end processing pipeline ensures that no model output reaches Snowflake without AST validation, query errors trigger an automatic EXPLAIN repair loop (up to 2 retries), and no generated prose reaches the user without cell-level claim matching.

```mermaid
flowchart TD
    User["User Question<br/>(or Follow-up in last 2 turns)"] --> GemmaSQL["Gemma 4<br/>(Intent Parsing & Candidate SQL)"]
    GemmaSQL --> ASTGuard{"sqlglot AST Guardrail<br/>(src/validate.py)"}
    ASTGuard -- "Prohibited DML/DDL or Multi-stmt" --> Rejected["Rejected / Refused Status"]
    ASTGuard -- "Valid Read-Only SELECT" --> LimitClamp["Deterministic LIMIT Clamping<br/>(Max 200 Rows)"]
    LimitClamp --> ExplainCheck{"Snowflake EXPLAIN Validation"}
    ExplainCheck -- "Syntax/Semantic Error" --> RetryCheck{"Retry Count < 2?"}
    RetryCheck -- "Yes" --> GemmaRepair["Gemma 4 SQL Repair<br/>(Feed Error into Prompt)"]
    GemmaRepair --> ASTGuard
    RetryCheck -- "No" --> ErrorStatus["Error Status Returned"]
    ExplainCheck -- "Valid Query Plan" --> Snowflake[("Snowflake Data Warehouse<br/>(Role: DM_READER)")]
    Snowflake --> RowFlags["Code Flag Evaluation<br/>(Empty, Nulls, Row-cap hit)"]
    RowFlags --> GemmaProse["Gemma 4<br/>(Prose Synthesis from Rows)"]
    GemmaProse --> Verifier{"Code Verifier Engine<br/>(Cell & Relational Match)"}
    Verifier -- "Claim Mismatch" --> CodeFallback["Deterministic Fallback Sentence"]
    Verifier -- "Verified" --> Receipt["Receipt Builder<br/>(AST Parsed: Tables, Joins, Filters, Query ID)"]
    Receipt --> FinalOutput["Final Verified Answer + Monospace Receipt"]
```

### Turn-by-Turn Query Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Gemma as Gemma 4
    participant AST as sqlglot AST Guardrail
    participant SF as Snowflake (DM_READER)
    participant Verify as Verification Engine
    participant UI as Workbench UI

    User->>Gemma: Natural language query (context: last two turns)
    Gemma-->>AST: Synthesizes candidate SQL
    Note over AST: AST Inspection:<br/>SELECT-only, Single Stmt,<br/>Allowlist, LIMIT Clamp
    AST->>SF: Validate via EXPLAIN (repair loop on error, max 2 retries)
    SF-->>Verify: Return raw rowset + Query ID (sfqid)
    SF-->>Gemma: Pass rowset (capped to 50 rows)
    Gemma-->>Verify: Natural language explanation
    Note over Verify: Deterministic Claim Verifier:<br/>Matches numeric claims & entities
    Verify-->>UI: Verified Answer + Monospace Receipt
    UI-->>User: Render auditable response
```

### DataMind Receipt Anatomy (Illustrative Example, Not Real Output)

*(Design specification: illustrates the planned receipt format generated once pipeline integration is complete. Not real execution output.)*

```text
┌────────────────────────────────────────────────────────────────────────┐
│ DATAMIND QUERY RECEIPT (ILLUSTRATIVE EXAMPLE)                          │
├────────────────────────────────────────────────────────────────────────┤
│ Snowflake Query ID : 01b4b20a-0001-294b-0000-0001f3e6205e              │
│ Active Dataset     : TPCH (SF1)                                        │
│ Tables Referenced  : CUSTOMER, ORDERS                                  │
│ Join Equivalence   : CUSTOMER.C_CUSTKEY = ORDERS.O_CUSTKEY             │
│ Applied Filters    : ORDERS.O_ORDERDATE >= '1995-01-01'                │
│ LIMIT Enforcement  : 200 (clamped by src/validate.py)                  │
│ Rows Returned      : 10                                                │
│ Cell Verification  : PASSED (all numeric & entity claims verified)     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core System Design: Language vs. Determinism

| Responsibility Domain | Executed By | Rationale |
| :--- | :--- | :--- |
| **Natural Language Understanding** | Google Gemma 4 | Translates ambiguous user prompts into structured relational semantics. |
| **Candidate SQL Generation** | Google Gemma 4 | Translates domain requests into standard Snowflake SQL dialects. |
| **AST Security & Safety** | Python (`src/validate.py`) | Rejects non-SELECT commands, comment bypasses, and unauthorized tables before execution. |
| **Execution Resource Bounds** | Python (`src/validate.py`) | Deterministically injects or clamps `LIMIT 200` to prevent unbounded query execution. |
| **Database Execution** | Snowflake (`DM_READER`) | Read-only role prevents database mutation even if an application-layer check were bypassed. |
| **Claim Verification** | Python (`src/verify.py` *(planned)*) | Inspects prose output against result sets; catches and replaces hallucinated numbers. |
| **Audit Receipt Construction** | Python (`src/receipt.py` *(planned)*) | Extracts table names, joins, filters, and Snowflake Query ID (`sfqid`) directly from the AST. |

---

## 4. Technical Differentiator: AST Guardrails vs. Naive Regex

Many Text-to-SQL systems rely on regular expressions or keyword blocklists for safety. Naive regex approaches introduce serious vulnerabilities and false rejections:

| Safety Challenge | Naive Regex Approach | DataMind AST Engine (`sqlglot`) |
| :--- | :--- | :--- |
| **Safe Column / Value Names** | Rejects harmless queries containing words like `DROP` (e.g. `WHERE status = 'DROP'`). | Parses string literals and identifiers into distinct AST nodes; recognizes literals as safe data values. |
| **Comment-Hidden Statements** | Fails or gets confused by trailing comments (`--` or `/* ... */`) wrapping secondary statements. | AST parser evaluates the entire statement tree and rejects multi-statement structures regardless of comments. |
| **Nested Subqueries & CTEs** | Regex cannot reliably distinguish between CTE table aliases and unauthorized external tables. | Recursively walks CTE definitions and verifies all referenced tables against the active dataset allowlist. |
| **LIMIT Clause Clamping** | Regex string substitution can corrupt complex queries with `UNION` or subqueries. | Modifies AST node attributes directly via `.limit(200)` and re-serializes clean, valid Snowflake SQL. |

---

## 5. Intended Behaviour: The 6 Rubric Archetypes (Design Targets, Pipeline Pending)

The evaluation benchmark (`eval/questions.csv`) exercises six query archetypes. The target system behaviours below represent design specifications pending pipeline integration:

### Archetype 1: Aggregation & Analytical Metric
- **Eval Prompt (Row 10):** *"what is the total discounted line-item revenue by customers' nation?"*
- **Target SQL:** Aggregates `SUM(l.L_EXTENDEDPRICE * (1 - l.L_DISCOUNT))` across `NATION`, `CUSTOMER`, `ORDERS`, and `LINEITEM`.
- **Intended Behaviour:** Snowflake executes the analytical sum; deterministic verifier validates that the prose mentions match returned cell values.

### Archetype 2: Multi-Table Relational JOIN
- **Eval Prompt (Row 15):** *"what is the total discounted line-item revenue by customers' region?"*
- **Target SQL:** Multi-table join across 5 tables (`REGION`, `NATION`, `CUSTOMER`, `ORDERS`, `LINEITEM`).
- **Intended Behaviour:** Walks foreign key joins; receipt builder logs all 5 tables and join predicates.

### Archetype 3: Follow-Up Conversational Context
- **Eval Context (Row 1):** *"Which 5 nations had the longest life expectancy in 2018?"*
- **Eval Follow-Up (Row 28):** *"what about in 2015 instead?"*
- **Intended Behaviour:** Conversation state tracks the last two turns, applying the previous ranking query pattern while swapping the year filter to 2015 without re-prompting.

### Archetype 4: Zero-Row / Empty Result Handling
- **Eval Prompt (Row 27):** *"what is the life expectancy of Atlantis?"*
- **Intended Behaviour:** Query executes against Snowflake, returning 0 rows. Code catches the zero-row flag and honestly reports `empty` status without fabricating statistics.

### Archetype 5: Ambiguity & Out-of-Domain Requests
- **Sensible Default (Row 22):** *"what is the best country for health?"* -> Answers with a stated assumption (highest life expectancy in latest year).
- **Unresolvable Ambiguity (Row 23):** *"what about 2015?"* (with no context) -> Returns `clarify` status requesting the missing indicator.
- **Out-of-Domain Request (Row 26):** *"what are the current stock prices today for tech companies?"* -> Returns `refused` status because stocks are outside the active schemas.

### Archetype 6: Guardrail-Blocked Mutation & Injection
- **Attempted SQL:** `"SELECT * FROM CUSTOMER; DROP TABLE ORDERS;"`
- **Intended Behaviour:** Blocked by `src/validate.py` AST validation before reaching the database. Status set to `refused`.

---

## 6. Live Snowflake Execution & Verification Design

Design goals for proving real-time cloud data warehouse execution:
1. **Live Query Execution**: Queries execute via `snowflake-connector-python` under role `DM_READER`.
2. **Native Snowflake Query ID**: Successful queries capture Snowflake Query IDs (`cursor.sfqid`) for auditability in the Snowflake console.
3. **Execution Latency Tracking**: Logs warehouse query response latency in milliseconds.
4. **Independent Evaluation Connection**: The evaluation runner (`eval/run_eval.py`) maintains its own separate Snowflake connection to evaluate ground-truth queries independently of the application service.

---

## 7. Dynamic Dataset Reusability (Design Goal, Pipeline Pending)

DataMind's target architecture is designed for dataset switching via configuration files and API endpoints without modifying core validation logic:

```yaml
# config.yaml (schema declarations)
active_dataset: owid

datasets:
  owid:
    database: DATAMIND
    schema: OWID
    config_file: datasets/owid.yaml

  tpch:
    database: SNOWFLAKE_SAMPLE_DATA
    schema: TPCH_SF1
    config_file: datasets/tpch.yaml
```

- **Runtime Switch Endpoint (Design Specification)**: Client requests `POST /switch-dataset {"dataset": "tpch"}`. The backend resets schema context and loads the corresponding table allowlist dynamically.
- **Status**: The schema definitions (`datasets/owid.yaml`, `datasets/tpch.yaml`) and eval runner switching logic are written; full live endpoint integration is pending Person B's API implementation.

---

## 8. Datasets & Licences

| Dataset | Schema / Table | Source & Attribution | Licence |
| :--- | :--- | :--- | :--- |
| **Our World in Data (OWID)** | `DATAMIND.OWID.life_expectancy` | [TODO: Upstream source URL and commit SHA] | [TODO: Confirm indicator-specific upstream licence terms] |
| **TPC-H Benchmark (SF1)** | `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.*` | Referenced live from Snowflake Shared Sample Data (`SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`) | Standard Snowflake Sample Data (referenced live, not redistributed) |

---

## 9. Team Roles & Code Ownership

| Team Member | Role | Ownership & Responsibilities |
| :--- | :--- | :--- |
| **Aryan Verma** (Team Lead) | **Person A** | Snowflake warehouse initialization (`sql/setup_snowflake.sql`), data loader scripts, SQL execution engine. |
| **Kolukuri Ashrith** | **Person B** | Backend FastAPI server (`api/`), Gemma 4 inference pipeline (`pipeline.py`), API contract definition. |
| **Kataru Deekshith Reddy** | **Person C** | Evaluation harness (`eval/questions.csv`, `eval/run_eval.py`), test suites (`tests/`), React frontend (`web/`), documentation. |

---

## 10. Project Structure

```text
Code-Blooded/
├── .agent/
│   └── AGENTS.md            # Authoritative developer rules & security constraints
├── datasets/
│   ├── owid.yaml             # Our World in Data schema & indicator declarations
│   └── tpch.yaml             # TPC-H 8-table schema declarations & foreign keys
├── eval/
│   ├── questions.csv         # 30-question ground-truth evaluation benchmark (draft status)
│   └── run_eval.py           # Independent HTTP-based evaluation runner
├── sql/
│   └── setup_snowflake.sql   # Snowflake RBAC setup (DM_READER, DM_LOADER, DM_WH)
├── src/
│   ├── __init__.py
│   └── validate.py           # AST-based SQL guardrail engine (sqlglot)
├── tests/
│   ├── test_guardrails.py    # DDL/DML rejection & injection test suite
│   ├── test_validate.py      # sqlglot parsing & LIMIT clamping test suite
│   └── test_verify.py        # Cell verification & claim checking test suite (skips until verify.py)
├── web/                      # React + Vite + Tailwind workbench frontend
├── config.yaml               # Runtime configuration (models, limits, temperatures)
├── requirements.txt          # Pinned Python dependencies
├── .env.example              # Clean environment template (no secrets committed)
├── .gitignore                # Excludes secrets, caches, and mock evaluation files
└── LICENSE                   # MIT License
```

---

## 11. Step-by-Step Setup & Reproduction

### Current Implementation Status
- **Implemented & Present in Repository**:
  - AST-based SQL safety validator (`src/validate.py`)
  - Automated test suites (`tests/test_guardrails.py` & `tests/test_validate.py` passing; `tests/test_verify.py` written, skipped until `src/verify.py` ships — run `pytest` to execute)
  - Ground-truth evaluation harness (`eval/questions.csv`)
  - Evaluation runner with mock isolation and DM_READER connector (`eval/run_eval.py`)
  - React workbench UI scaffold (`web/`)
- **Pending Integration (Person A & Person B)**:
  - FastAPI server endpoints (`api/`)
  - LLM pipeline integration (`pipeline.py`)
  - Secondary OWID indicator ingestion (`scripts/load_data.py`)
  - Consolidated single-entrypoint runner (`run.py`)

### Prerequisites
1. **Google AI Studio API Key**: Configured in `.env` for Gemma 4 query interpretation.
2. **Snowflake Account**: Trial or standard account with a provisioned `DM_WH` warehouse (created via `sql/setup_snowflake.sql`).
3. **Environment**: Python 3.11+, Node.js (for frontend build).

### Setup Walkthrough

#### 1. Clone the Repository
```bash
git clone https://github.com/ZERODAY-2026/Code-Blooded.git
cd Code-Blooded
```

#### 2. Configure Environment
Copy `.env.example` to `.env`:

**Windows PowerShell:**
```powershell
Copy-Item .env.example .env
```
**Linux / macOS / Bash:**
```bash
cp .env.example .env
```

Configure connection parameters in `.env`:
```ini
GOOGLE_API_KEY=your_gemini_api_key
SNOWFLAKE_ACCOUNT=your_account_identifier
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=DM_WH
SNOWFLAKE_DATABASE=DATAMIND
SNOWFLAKE_SCHEMA=OWID
SNOWFLAKE_ROLE=DM_READER
```

#### 3. Install Python Dependencies
```bash
pip install -r requirements.txt
```

#### 4. Initialize Snowflake RBAC (Person A)
Run `sql/setup_snowflake.sql` in a Snowflake Worksheet as `ACCOUNTADMIN` to create the `DATAMIND` database, `OWID` schema, and `DM_WH` warehouse grants.

#### 5. Run the Test Suites
Execute the test suites with `pytest`:
```bash
python -m pytest tests/ -v
```

#### 6. Run the Frontend Workbench
```bash
cd web
npm install
npm run dev
```
Open `http://localhost:3000` (port configured in `web/vite.config.js`) in your browser.  
*(Note: `python run.py` single-command entrypoint is scheduled once FastAPI serves prebuilt `web/dist`).*

---

## 12. Red-Team Guardrail Matrix

All SQL generated by the language model is validated by `src/validate.py` before execution. The table below lists active guardrail checks and pipeline-dependent safety targets:

| Policy Case | Attempted SQL Syntax | Guardrail Action | Test Function / Status |
| :--- | :--- | :---: | :--- |
| **DROP TABLE** | `DROP TABLE CUSTOMER;` | ❌ BLOCKED | `test_drop_table_rejected` |
| **DELETE** | `DELETE FROM CUSTOMER WHERE C_CUSTKEY = 1;` | ❌ BLOCKED | `test_delete_rejected` |
| **INSERT** | `INSERT INTO CUSTOMER (C_NAME) VALUES ('x');` | ❌ BLOCKED | `test_insert_rejected` |
| **UPDATE** | `UPDATE CUSTOMER SET C_ACCTBAL = 0;` | ❌ BLOCKED | `test_update_rejected` |
| **TRUNCATE** | `TRUNCATE TABLE CUSTOMER;` | ❌ BLOCKED | `test_truncate_rejected` |
| **ALTER TABLE** | `ALTER TABLE CUSTOMER ADD COLUMN x VARCHAR;` | ❌ BLOCKED | `test_alter_table_rejected` |
| **MERGE** | `MERGE INTO CUSTOMER USING ...;` | ❌ BLOCKED | `test_merge_rejected` |
| **COPY INTO** | `COPY INTO CUSTOMER FROM @my_stage;` | ❌ BLOCKED | `test_copy_into_rejected` |
| **CALL Procedure** | `CALL my_stored_procedure();` | ❌ BLOCKED | `test_call_rejected` |
| **GRANT / REVOKE** | `GRANT ALL PRIVILEGES ON TABLE CUSTOMER...;` | ❌ BLOCKED | `test_grant_rejected`, `test_revoke_rejected` |
| **CREATE TABLE** | `CREATE TABLE evil_copy AS SELECT ...;` | ❌ BLOCKED | `test_create_table_rejected` |
| **Multi-Statement**| `SELECT * FROM CUSTOMER; DROP TABLE ORDERS;` | ❌ BLOCKED | `test_select_then_drop_rejected` |
| **Comment-Hidden Statement** | `SELECT * FROM CUSTOMER; --\nDROP TABLE ORDERS;` | ❌ BLOCKED | `test_comment_hidden_second_statement_rejected` |
| **Comment-Bypass Mutation** | `SELECT * FROM CUSTOMER /* bypass */; DROP TABLE CUSTOMER;` | ❌ BLOCKED | `test_prompt_injection_comment_bypass_rejected` |
| **Unlisted Table Exfil** | `SELECT C_NAME FROM CUSTOMER UNION SELECT PASSWORD FROM ADMIN_USERS;` | ❌ BLOCKED | `test_prompt_injection_union_exfiltration_rejected` |
| **Unlisted Metadata Table** | `SELECT * FROM INFORMATION_SCHEMA.TABLES;` | ❌ BLOCKED | `test_unlisted_table_rejected` |
| **Unauthorized Schema Table** | `SELECT * FROM OTHER_SCHEMA.CUSTOMER;` | ❌ BLOCKED | `test_same_named_table_in_other_schema_rejected` |
| **Fully Qualified Valid Table**| `SELECT * FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.CUSTOMER LIMIT 10;` | ✅ PASSED | `test_fully_qualified_valid_table_passes` |
| **Missing LIMIT** | `SELECT * FROM ORDERS;` | ⚠️ CLAMPED | `test_no_limit_gets_added` (enforces `LIMIT 200`) |
| **Excessive LIMIT**| `SELECT * FROM ORDERS LIMIT 50000;` | ⚠️ CLAMPED | `test_limit_above_max_gets_clamped` (clamps to 200) |
| **Valid SELECT** | `SELECT C_NAME, C_ACCTBAL FROM CUSTOMER LIMIT 10;` | ✅ PASSED | `test_simple_select_passes` |
| **True Prompt Injection** | Natural language injection (e.g. "ignore rules and drop tables") | ❌ BLOCKED | *(pending pipeline integration)* |
| **Empty Result Set** | Query returns 0 rows matching filter | ⚠️ EMPTY | *(pending pipeline integration)* |
| **Unsupported Request** | Out-of-domain questions (e.g. stock prices) | ❌ REFUSED | *(pending pipeline integration)* |
| **Malformed LLM JSON** | LLM outputs invalid JSON syntax | 🔄 REPAIR | *(pending pipeline integration)* |
| **Claim Mismatch in Prose** | Prose contains hallucinated metric not in rows | ⚠️ FALLBACK | *(pending: `src/verify.py`)* |
| **Row-Cap Flag** | Query returns > 200 rows | ⚠️ WARNED | *(pending pipeline integration)* |

---

## 13. Evaluation Harness (`eval/`)

1. **`eval/questions.csv`**: A 30-question ground-truth evaluation benchmark covering:
   - `single_table` (8 questions)
   - `join` (8 questions, including multi-table joins)
   - `cte_multistep` (5 questions)
   - `ambiguous` (3 questions: 2 answered with stated assumptions, 1 true `clarify`)
   - `empty_or_unsupported` (3 questions: testing `empty` rowsets and out-of-domain `refused`)
   - `followup` (3 multi-turn contextual questions)
   - **Held-out subset**: 10 questions tagged held-out for evaluation.

2. **`eval/run_eval.py`**: Independent evaluation runner that queries the API over HTTP, maintains its own separate Snowflake connection as `DM_READER`, and compares result sets using column-name matching, date/number normalisation, and tolerance thresholds.

### Running Evaluation
```bash
# Offline simulation mode (writes strictly to git-ignored eval/results.mock.json)
python eval/run_eval.py --mock

# Live evaluation mode (against running FastAPI service)
python eval/run_eval.py --api-url http://localhost:8000
```

> **Evaluation Notice**: Results are produced by `eval/run_eval.py`. All 30 ground-truth questions currently hold `status=draft`. Rows count toward headline accuracy only once their ground-truth data snapshots have been verified against live data by the team.

---

## 14. Known Limitations

1. **Single OWID Table Loaded**: Only `DATAMIND.OWID.life_expectancy` is currently provisioned in Snowflake; secondary tables (`gdp_per_capita`, `co2`, `population`) await loader script execution by Person A.
2. **Global Dataset Switching**: Active dataset state is currently toggled globally across the service rather than scoped per user session.
3. **Evaluation Pending Live Verification**: All 30 evaluation benchmark questions hold `status=draft`. Headline accuracy remains 0 verified rows until snapshots are verified against live data.
4. **Pipeline & API Pending Integration**: End-to-end LLM orchestration (`pipeline.py`) and FastAPI endpoints (`api/`) are pending implementation by Person B.

---

## 15. Technical Boundaries & Trade-Offs

1. **SELECT-Only Enforcement**: To prevent data corruption, only read-only `SELECT` statements are permitted. Mutations cannot be initiated via natural language.
2. **Mandatory LIMIT Clamping (Max 200 Rows)**: Unbounded queries (`SELECT * FROM LINEITEM`) can rapidly exhaust warehouse compute credits. Every query is clamped to a maximum of 200 rows by the AST engine.
3. **Prose Row Capping (50 Rows)**: When passing query results to Gemma 4 for natural-language synthesis, only the top 50 rows are provided to prevent context bloat while preserving analytical fidelity.
4. **Conversational Memory Window**: Follow-up sessions track the last two turns to maintain conversational context without exceeding model token budgets.

---

## 16. Troubleshooting & Operational Notes

- **Snowflake MFA / Key-Pair Authentication**: If your Snowflake user enforces Duo MFA or SSO, password authentication will time out. Configure key-pair authentication or set `authenticator='externalbrowser'` in your local configuration.
- **Model Configuration**: Model names and parameters are read directly from `config.yaml`. To switch models or providers, update `config.yaml` rather than editing source code.
- **`[TODO]` Technical Specifications**:
  - *LLM Tier*: `[TODO: Gemma model runtime specifications, exact parameter tier, and benchmark latency to be confirmed post-pipeline integration]`
  - *Data Attribution*: `[TODO: Add exact Our World in Data download timestamp, commit SHA, and upstream indicator URL]`

---

## 17. License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
