# Problem Statement: PS1 — DataMind: Talk to Your Data

> **Hacktoberfest Hack Day Dharwad × Vidkarya | ZeroDay OSS, IIIT Dharwad**  
> Team: **Code Blooded** | Submission Deadline: Oct 18, 2026, 8:59 PM IST

---

## Overview

**DataMind Receipts** is a grounded, natural-language interface to structured enterprise databases. Users ask questions in plain English, and the system synthesizes live SQL, validates it through strict Abstract Syntax Tree (AST) guardrails, executes it against Snowflake using read-only credentials, and returns a verified prose answer backed by an explicit SQL execution receipt.

### Core Architecture Principles
- **Gemma 4 does language only**: Question interpretation, SQL generation, SQL repair on dialect errors, and natural-language explanation.
- **Deterministic code does computation and safety**: AST parsing, statement isolation, table allowlisting, LIMIT enforcement, and evidence cell matching are handled entirely by deterministic Python code.
- **Read-only execution**: Queries execute exclusively under Snowflake's `DM_READER` role. Mutation operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `MERGE`, etc.) are blocked at both the AST layer and database privilege layer.
- **Verifiable Receipts**: Every answer is paired with a receipt detailing tables queried, joins, filters applied, row counts, and the Snowflake Query ID.

---

## Tech Stack & Tested Environment

| Layer | Component | Version / Specification |
| :--- | :--- | :--- |
| **LLM Provider** | Google Gemma 4 (instruction-tuned) | Configured dynamically via `config.yaml` |
| **SQL Engine** | Snowflake Data Cloud | Standard `COMPUTE_WH` (XS), `DM_READER` role |
| **SQL Safety** | `sqlglot` | AST parser (no regex shortcuts) |
| **Backend** | Python / FastAPI | Python 3.11+ tested |
| **Frontend** | React 18, Vite, Tailwind CSS | Node.js v20+ tested |
| **Testing** | `pytest` | 71 test cases across guardrails & validation |

### Prerequisites
1. **Google AI Studio API Key**: Required for Gemma 4 query interpretation and SQL generation.
2. **Snowflake Account**: Free 30-day trial or standard account with `ACCOUNTADMIN` (to run setup) and a provisioned `COMPUTE_WH` warehouse.

---

## Team Roles & Ownership

| Team Member | Role | Responsibilities |
| :--- | :--- | :--- |
| **Aryan Verma** (Team Lead) | **Person A** | Snowflake warehouse setup, data ingestion, `sql/setup_snowflake.sql`, SQL execution engine |
| **Kolukuri Ashrith** | **Person B** | Backend API, Gemma 4 provider integration, pipeline orchestration, API contract owner |
| **Kataru Deekshith Reddy** | **Person C** | Evaluation harness (`eval/`), test suites (`tests/`), React frontend (`web/`), documentation |

---

## Project Structure

Tracked repository structure as of Phase 1 baseline:

```text
Code-Blooded/
├── .agent/
│   └── AGENTS.md            # Authoritative developer rules & security constraints
├── datasets/
│   ├── owid.yaml             # Our World in Data schema & indicator declarations
│   └── tpch.yaml             # TPC-H 8-table schema declarations & foreign keys
├── eval/
│   ├── questions.csv         # 30-question ground-truth evaluation harness (draft status)
│   └── run_eval.py           # Independent HTTP-based evaluation runner
├── sql/
│   └── setup_snowflake.sql   # Snowflake RBAC setup (DM_READER, DM_LOADER, DATAMIND schema)
├── src/
│   ├── __init__.py
│   └── validate.py           # AST-based SQL guardrail engine (sqlglot)
├── tests/
│   ├── test_guardrails.py    # DDL/DML rejection & injection test suite (40 tests)
│   ├── test_validate.py      # sqlglot parsing & LIMIT clamping test suite
│   └── test_verify.py        # Cell verification & claim checking test suite
├── web/                      # React + Vite + Tailwind workbench frontend
├── config.yaml               # Runtime configuration (models, limits, temperatures)
├── requirements.txt          # Pinned Python dependencies
├── .env.example              # Clean environment template (no secrets committed)
├── .gitignore                # Excludes secrets, caches, and mock evaluation files
└── LICENSE                   # MIT License
```

---

## Setup & Reproduction

### 1. Clone the Repository
```bash
git clone https://github.com/ZERODAY-2026/Code-Blooded.git
cd Code-Blooded
```

### 2. Configure Environment
Copy `.env.example` to `.env`:

**Windows PowerShell:**
```powershell
Copy-Item .env.example .env
```
**Linux / macOS / Bash:**
```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:
```ini
GOOGLE_API_KEY=your_gemini_api_key
SNOWFLAKE_ACCOUNT=your_account_identifier
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=DATAMIND
SNOWFLAKE_SCHEMA=OWID
SNOWFLAKE_ROLE=DM_READER
```

### 3. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 4. Snowflake RBAC Initialization (Person A)
Execute `sql/setup_snowflake.sql` in a Snowflake Worksheet or via SnowSQL as `ACCOUNTADMIN` to create the `DATAMIND` database, `OWID` schema, and `DM_READER` / `DM_LOADER` roles.

### 5. Run the Test Suites
```bash
python -m pytest tests/ -v
```

### 6. Run the Frontend (Development)
```bash
cd web
npm install
npm run dev
```
Open `http://localhost:3000` in your browser.  
*(Note: Person B will provide the consolidated `python run.py` single-command entrypoint once FastAPI serves prebuilt `web/dist`).*

---

## Datasets

| Dataset | Schema / Table | Source | License / Access | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Our World in Data (OWID)** | `DATAMIND.OWID.life_expectancy` | Our World in Data | CC BY 4.0 | Global life expectancy indicators across countries and years (1950–2021). |
| **TPC-H Benchmark (SF1)** | `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.*` | TPC-H Benchmark | Snowflake Shared Sample Data | 8 standard relational tables (`CUSTOMER`, `ORDERS`, `LINEITEM`, `NATION`, `REGION`, `SUPPLIER`, `PART`, `PARTSUPP`) referenced live. Not redistributed. |

---

## SQL Safety & Red-Team Guardrail Matrix

All SQL generated by Gemma 4 passes through `src/validate.py` before execution. The engine parses queries into a `sqlglot` AST and enforces the following policy:

| Test Case | Attempted SQL Syntax | Guardrail Action | Policy Rationale |
| :--- | :--- | :---: | :--- |
| **Drop Table** | `DROP TABLE CUSTOMER;` | ❌ BLOCKED | DDL operations strictly rejected |
| **Delete Rows** | `DELETE FROM ORDERS WHERE 1=1;` | ❌ BLOCKED | DML mutations strictly rejected |
| **Data Insertion** | `INSERT INTO CUSTOMER VALUES (...);` | ❌ BLOCKED | DML mutations strictly rejected |
| **Table Truncation**| `TRUNCATE TABLE LINEITEM;` | ❌ BLOCKED | Table wipes strictly rejected |
| **Schema Alteration**| `ALTER TABLE CUSTOMER ADD COLUMN x;` | ❌ BLOCKED | Schema changes strictly rejected |
| **Semicolon Chaining**| `SELECT 1; DROP TABLE CUSTOMER;` | ❌ BLOCKED | Multi-statement queries strictly blocked |
| **Information Schema**| `SELECT * FROM INFORMATION_SCHEMA.TABLES;` | ❌ BLOCKED | Unlisted metadata tables blocked |
| **Missing LIMIT** | `SELECT * FROM CUSTOMER;` | ⚠️ CLAMPED | Automatically appends `LIMIT 200` |
| **Excessive LIMIT** | `SELECT * FROM ORDERS LIMIT 50000;` | ⚠️ CLAMPED | Automatically clamped to `LIMIT 200` |
| **Safe SELECT** | `SELECT c.C_NAME, SUM(...) FROM CUSTOMER c...` | ✅ PASSED | Read-only queries on allowlisted tables permitted |

---

## Evaluation Harness (`eval/`)

The evaluation framework consists of:
1. **`eval/questions.csv`**: A 30-question ground-truth benchmark covering 6 distinct categories:
   - `single_table` (8 questions)
   - `join` (8 questions, including 5 multi-table joins)
   - `cte_multistep` (5 questions)
   - `ambiguous` (3 questions: 2 answered with stated assumptions, 1 true `clarify`)
   - `empty_or_unsupported` (3 questions: tested for `empty` rowsets and out-of-domain `refused`)
   - `followup` (3 multi-turn contextual questions)
   - **Held-out split**: Exactly 10 questions tagged `held_out=true` (balanced across OWID and TPC-H).

2. **`eval/run_eval.py`**: An independent evaluation runner that calls the API over HTTP, maintains its own separate Snowflake connection as `DM_READER`, and grades results by column-name matching, date/number normalisation, and tolerance thresholds.

### Running Evaluation
```bash
# Offline simulation mode (tests runner logic, writes to git-ignored eval/results.mock.json)
python eval/run_eval.py --mock

# Live evaluation mode (against running FastAPI service)
python eval/run_eval.py --api-url http://localhost:8000
```

> **Evaluation Transparency Notice**: All 30 ground-truth questions currently hold `status=draft`. Per handbook gate G3, headline accuracy strictly reports `0 verified rows` until queries are run live against Snowflake by the team and snapshot values are verified. No speculative numbers or unverified accuracy scores are published.

---

## Known Limitations & Planned Enhancements
- **OWID Multi-Table Ingestion**: In Phase 1, only `life_expectancy` is loaded in `DATAMIND.OWID`. Multi-table join questions are currently evaluated against `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` until Person A loads secondary OWID indicators (GDP per capita, CO₂).
- **Read-Only Scope**: The system deliberately rejects all state modifications. Queries requiring temporary table creation or stored procedures are out of scope by design.

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
