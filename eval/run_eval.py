"""
eval/run_eval.py

Independent Evaluation Harness for DataMind Receipts.
Tests the running system via HTTP (POST /ask, POST /switch-dataset).
Maintains an independent Snowflake connection as DM_READER for ground-truth execution.
Never writes or modifies expected values.

Handbook & Reviewer Specification:
- Dataset grouping: calls POST /switch-dataset once per group.
- Session isolation: fresh session_id per row; shared session_id for follow-up pairs.
- Independent ground truth: own snowflake-connector connection as DM_READER.
- Grading: checks expected_status first; evaluates result sets by column name with normalisation.
- Behaviour checks: rows with no SQL and expected_status=answered require non-empty assumptions.
- Headline accuracy: strictly counts verified rows (draft rows reported as unverified).
- Mock mode isolation: writes only to eval/results.mock.json, never eval/results.json.
- Non-zero exit code on any request or query failure.
"""

import argparse
import csv
import datetime
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Try loading .env for Snowflake credentials
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Try importing snowflake.connector
try:
    import snowflake.connector
    SNOWFLAKE_AVAILABLE = True
except ImportError:
    SNOWFLAKE_AVAILABLE = False

# Try importing httpx
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.csv"
RESULTS_JSON_PATH = PROJECT_ROOT / "eval" / "results.json"
RESULTS_MD_PATH = PROJECT_ROOT / "eval" / "results.md"
MOCK_RESULTS_JSON_PATH = PROJECT_ROOT / "eval" / "results.mock.json"
MOCK_RESULTS_MD_PATH = PROJECT_ROOT / "eval" / "results.mock.md"


@dataclass
class QuestionRow:
    id: str
    dataset: str
    question: str
    category: str
    context_question: str
    ground_truth_sql: str
    expected_status: str
    expected: str
    tolerance: str
    status: str
    held_out: bool
    order_matters: bool


@dataclass
class RowEvalResult:
    id: str
    dataset: str
    question: str
    category: str
    held_out: bool
    status: str                 # "draft" | "verified"
    outcome: str                # "pass" | "fail" | "error" | "behaviour_pass" | "behaviour_fail"
    api_status: str = ""
    expected_status: str = ""
    generated_sql: str = ""
    error_detail: str = ""
    first_try_json: bool = True
    first_try_sql: bool = True
    repaired: bool = False
    duration_ms: float = 0.0


def parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes")


def load_questions(csv_path: Path) -> List[QuestionRow]:
    if not csv_path.exists():
        print(f"Error: {csv_path} not found.", file=sys.stderr)
        sys.exit(1)

    rows: List[QuestionRow] = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(QuestionRow(
                id=r["id"].strip(),
                dataset=r["dataset"].strip(),
                question=r["question"].strip(),
                category=r["category"].strip(),
                context_question=(r.get("context_question") or "").strip(),
                ground_truth_sql=(r.get("ground_truth_sql") or "").strip(),
                expected_status=(r.get("expected_status") or "answered").strip(),
                expected=(r.get("expected") or "").strip(),
                tolerance=(r.get("tolerance") or "").strip(),
                status=(r.get("status") or "draft").strip(),
                held_out=parse_bool(r.get("held_out", False)),
                order_matters=parse_bool(r.get("order_matters", False))
            ))
    return rows


class IndependentSnowflakeRunner:
    """Independent Snowflake executor running strictly as DM_READER."""

    def __init__(self):
        self.conn = None
        if not SNOWFLAKE_AVAILABLE:
            return

        account = os.getenv("SNOWFLAKE_ACCOUNT")
        user = os.getenv("SNOWFLAKE_USER")
        password = os.getenv("SNOWFLAKE_PASSWORD")
        warehouse = os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
        role = os.getenv("SNOWFLAKE_ROLE", "DM_READER")

        if account and user and password:
            try:
                self.conn = snowflake.connector.connect(
                    account=account,
                    user=user,
                    password=password,
                    warehouse=warehouse,
                    role=role,
                    autocommit=True
                )
            except Exception as e:
                print(f"[Warning] Could not initialize independent Snowflake connection: {e}", file=sys.stderr)
                self.conn = None

    def execute(self, sql: str) -> Tuple[List[str], List[List[Any]]]:
        if not self.conn:
            raise RuntimeError("Snowflake connection unavailable (check credentials in .env).")
        # Strip trailing semicolon for connector
        clean_sql = sql.strip().rstrip(";")
        with self.conn.cursor() as cur:
            cur.execute(clean_sql)
            columns = [col[0].upper() for col in cur.description] if cur.description else []
            rows = cur.fetchall()
            return columns, [list(r) for r in rows]

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass


# ── Normalisation & Data Comparison ──────────────────────────────────────────

def normalise_scalar(val: Any) -> Any:
    if val is None:
        return None
    # String normalisation
    if isinstance(val, str):
        v = val.strip()
        # ISO Date check (e.g. 1995-01-01 or 1995-01-01 00:00:00)
        date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", v)
        if date_match:
            return date_match.group(1)
        # Check if float/int encoded as string
        try:
            return float(v)
        except ValueError:
            return v.lower()
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.strftime("%Y-%m-%d")
    return str(val).strip().lower()


def values_match(val_api: Any, val_gt: Any, tolerance_str: str) -> bool:
    n_api = normalise_scalar(val_api)
    n_gt = normalise_scalar(val_gt)

    if n_api == n_gt:
        return True

    # Numeric tolerance comparison
    if isinstance(n_api, float) and isinstance(n_gt, float):
        tol = 0.0
        if tolerance_str:
            try:
                tol = float(tolerance_str)
            except ValueError:
                tol = 0.0

        # Absolute or relative tolerance
        diff = abs(n_api - n_gt)
        if tol > 0.0:
            if diff <= tol:
                return True
        # Default float rounding tolerance (e.g. 84.36 vs 84.4)
        if diff <= 0.1:
            return True
        if abs(n_gt) > 0 and (diff / abs(n_gt)) <= 0.01:
            return True

    return False


def compare_result_sets(
    api_cols: List[str],
    api_rows: List[List[Any]],
    gt_cols: List[str],
    gt_rows: List[List[Any]],
    order_matters: bool,
    tolerance: str
) -> Tuple[bool, str]:
    """Compares API query results to Ground Truth results matched by column name."""
    if len(api_rows) != len(gt_rows):
        return False, f"Row count mismatch: API returned {len(api_rows)}, Ground Truth expected {len(gt_rows)}."

    # Match columns case-insensitively
    api_col_map = {col.upper(): idx for idx, col in enumerate(api_cols)}
    gt_col_indices = []

    for gt_c in gt_cols:
        gt_c_upper = gt_c.upper()
        if gt_c_upper in api_col_map:
            gt_col_indices.append((gt_c_upper, api_col_map[gt_c_upper]))

    # If exact column names didn't match, match by column position if column counts match
    if len(gt_col_indices) == 0:
        if len(api_cols) == len(gt_cols):
            gt_col_indices = [(gt_cols[i].upper(), i) for i in range(len(gt_cols))]
        else:
            return False, f"Column mismatch: Ground truth expects {gt_cols}, API provided {api_cols}."

    if order_matters:
        for r_idx in range(len(gt_rows)):
            for gt_c_idx, (_, api_c_idx) in enumerate(gt_col_indices):
                gt_val = gt_rows[r_idx][gt_c_idx]
                api_val = api_rows[r_idx][api_c_idx]
                if not values_match(api_val, gt_val, tolerance):
                    return False, f"Value mismatch at row {r_idx+1}, col '{gt_cols[gt_c_idx]}': API got '{api_val}', expected '{gt_val}'."
        return True, ""
    else:
        # Unordered multiset comparison
        matched_gt_indices = set()
        for api_r in api_rows:
            found_match = False
            for gt_idx, gt_r in enumerate(gt_rows):
                if gt_idx in matched_gt_indices:
                    continue
                all_cols_match = True
                for gt_c_idx, (_, api_c_idx) in enumerate(gt_col_indices):
                    if not values_match(api_r[api_c_idx], gt_r[gt_c_idx], tolerance):
                        all_cols_match = False
                        break
                if all_cols_match:
                    matched_gt_indices.add(gt_idx)
                    found_match = True
                    break
            if not found_match:
                return False, f"API row {api_r} has no matching row in Ground Truth."
        return True, ""


# ── HTTP API Client ──────────────────────────────────────────────────────────

class EvalApiClient:
    def __init__(self, base_url: str = "http://localhost:8000", mock: bool = False):
        self.base_url = base_url.rstrip("/")
        self.mock = mock

    def switch_dataset(self, dataset: str) -> None:
        if self.mock:
            return
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx package is required to run live evaluation against FastAPI.")
        resp = httpx.post(f"{self.base_url}/switch-dataset", json={"dataset": dataset}, timeout=10.0)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to switch dataset to '{dataset}': {resp.status_code} {resp.text}")

    def ask(self, question: str, session_id: str) -> Dict[str, Any]:
        if self.mock:
            # Deterministic simulation for mock test mode
            time.sleep(0.01)
            # Ambiguous with clarification
            if "what about 2015?" in question and not session_id.endswith("-followup"):
                return {
                    "status": "clarify",
                    "model": "gemma-4-26b-a4b-it",
                    "retries": 0,
                    "sql": "",
                    "assumptions": [],
                    "rows": {"columns": [], "data": []}
                }
            # Ambiguous with assumed defaults (behaviour check)
            if "best country" in question or "improved the most" in question:
                return {
                    "status": "answered",
                    "model": "gemma-4-26b-a4b-it",
                    "retries": 0,
                    "sql": "SELECT entity, life_expectancy FROM DATAMIND.OWID.life_expectancy LIMIT 1;",
                    "assumptions": ["Assumed latest available year 2019", "Ranked by life expectancy"],
                    "rows": {"columns": ["ENTITY", "LIFE_EXPECTANCY"], "data": [["Japan", 84.36]]}
                }
            # Empty rows
            if "year 2100" in question or "Atlantis" in question:
                return {
                    "status": "empty",
                    "model": "gemma-4-26b-a4b-it",
                    "retries": 0,
                    "sql": "SELECT entity FROM DATAMIND.OWID.life_expectancy WHERE year = 2100;",
                    "assumptions": [],
                    "rows": {"columns": ["ENTITY"], "data": []}
                }
            # Refused (out of domain)
            if "stock prices" in question:
                return {
                    "status": "refused",
                    "model": "gemma-4-26b-a4b-it",
                    "retries": 0,
                    "sql": "",
                    "assumptions": [],
                    "rows": {"columns": [], "data": []}
                }
            # Default answered mock query
            return {
                "status": "answered",
                "model": "gemma-4-26b-a4b-it",
                "retries": 0,
                "sql": "SELECT * FROM mock LIMIT 5;",
                "assumptions": ["Sample assumption"],
                "rows": {"columns": ["COL1"], "data": [["MOCK_VAL"]]}
            }

        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx package is required to run live evaluation.")

        resp = httpx.post(
            f"{self.base_url}/ask",
            json={"question": question, "session_id": session_id},
            timeout=45.0
        )
        if resp.status_code != 200:
            raise RuntimeError(f"POST /ask error {resp.status_code}: {resp.text}")
        return resp.json()


# ── Core Runner ──────────────────────────────────────────────────────────────

def run_evaluation(
    api_url: str = "http://localhost:8000",
    mock: bool = False,
    dataset_filter: Optional[str] = None,
    only_held_out: bool = False
) -> int:
    start_time = datetime.datetime.now(datetime.timezone.utc)
    date_str = start_time.strftime("%Y-%m-%d %H:%M:%S UTC")

    print("\n==================================================================")
    print(" DataMind Receipts — Automated Ground-Truth Evaluation Runner")
    print(f" Mode: {'MOCK SIMULATION' if mock else 'LIVE FASTAPI TEST'} | Time: {date_str}")
    print("==================================================================\n")

    questions = load_questions(QUESTIONS_PATH)

    # Apply filters
    if dataset_filter:
        questions = [q for q in questions if q.dataset.lower() == dataset_filter.lower()]
    if only_held_out:
        questions = [q for q in questions if q.held_out]

    if not questions:
        print("No questions match the specified filters.", file=sys.stderr)
        return 0

    api_client = EvalApiClient(base_url=api_url, mock=mock)
    sf_runner = IndependentSnowflakeRunner() if not mock else None

    # Group questions by dataset to invoke /switch-dataset once per dataset
    grouped_questions: Dict[str, List[QuestionRow]] = {}
    for q in questions:
        grouped_questions.setdefault(q.dataset, []).append(q)

    results: List[RowEvalResult] = []
    has_errors = False
    detected_model = "unknown"

    for dataset_name, dataset_rows in grouped_questions.items():
        print(f"\n[Dataset Switch] Activating dataset: '{dataset_name.upper()}' ({len(dataset_rows)} questions)")
        try:
            api_client.switch_dataset(dataset_name)
        except Exception as e:
            print(f"Error switching dataset to '{dataset_name}': {e}", file=sys.stderr)
            for q in dataset_rows:
                results.append(RowEvalResult(
                    id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                    held_out=q.held_out, status=q.status, outcome="error",
                    expected_status=q.expected_status, error_detail=f"Dataset switch failed: {e}"
                ))
            has_errors = True
            continue

        for q in dataset_rows:
            t0 = time.time()
            session_id = f"eval-{uuid.uuid4()}"
            is_followup = bool(q.context_question)

            # Handle follow-up by calling context_question first in the same session
            if is_followup:
                session_id = f"eval-followup-{uuid.uuid4()}"
                try:
                    api_client.ask(q.context_question, session_id=session_id)
                except Exception as e:
                    elapsed = (time.time() - t0) * 1000
                    results.append(RowEvalResult(
                        id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                        held_out=q.held_out, status=q.status, outcome="error",
                        expected_status=q.expected_status, error_detail=f"Context question failed: {e}",
                        duration_ms=elapsed
                    ))
                    has_errors = True
                    print(f"#{q.id.zfill(2)} [ERROR] [{q.category:<20}] {q.question[:45]} -> Context turn error")
                    continue

            # Query API
            try:
                api_resp = api_client.ask(q.question, session_id=session_id)
                elapsed = (time.time() - t0) * 1000
            except Exception as e:
                elapsed = (time.time() - t0) * 1000
                results.append(RowEvalResult(
                    id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                    held_out=q.held_out, status=q.status, outcome="error",
                    expected_status=q.expected_status, error_detail=str(e),
                    duration_ms=elapsed
                ))
                has_errors = True
                print(f"#{q.id.zfill(2)} [ERROR] [{q.category:<20}] {q.question[:45]} -> {e}")
                continue

            api_status = api_resp.get("status", "")
            gen_sql = api_resp.get("sql", "")
            detected_model = api_resp.get("model", detected_model)
            retries = api_resp.get("retries", 0)

            # ── Grading ──

            # 1. Status Check
            if api_status != q.expected_status:
                results.append(RowEvalResult(
                    id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                    held_out=q.held_out, status=q.status, outcome="fail",
                    api_status=api_status, expected_status=q.expected_status,
                    generated_sql=gen_sql, error_detail=f"Expected status '{q.expected_status}', got '{api_status}'",
                    first_try_json=retries == 0, first_try_sql=bool(gen_sql),
                    repaired=retries > 0, duration_ms=elapsed
                ))
                print(f"#{q.id.zfill(2)} [FAIL ] [{q.category:<20}] Status mismatch ({api_status} != {q.expected_status})")
                continue

            # 2. Behaviour Checks (Rows with no SQL and expected_status=answered)
            if q.expected_status == "answered" and not q.ground_truth_sql:
                assumptions = api_resp.get("assumptions", [])
                if assumptions and len(assumptions) > 0:
                    results.append(RowEvalResult(
                        id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                        held_out=q.held_out, status=q.status, outcome="behaviour_pass",
                        api_status=api_status, expected_status=q.expected_status,
                        generated_sql=gen_sql, first_try_json=retries == 0,
                        first_try_sql=bool(gen_sql), repaired=retries > 0, duration_ms=elapsed
                    ))
                    print(f"#{q.id.zfill(2)} [BEHAV] [{q.category:<20}] Answered with {len(assumptions)} stated assumptions")
                else:
                    results.append(RowEvalResult(
                        id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                        held_out=q.held_out, status=q.status, outcome="behaviour_fail",
                        api_status=api_status, expected_status=q.expected_status,
                        generated_sql=gen_sql, error_detail="Expected non-empty stated assumptions for ambiguous query.",
                        first_try_json=retries == 0, first_try_sql=bool(gen_sql),
                        repaired=retries > 0, duration_ms=elapsed
                    ))
                    print(f"#{q.id.zfill(2)} [B_FAIL] [{q.category:<20}] Missing stated assumptions")
                continue

            # 3. Non-answered status checks (clarify / empty / refused)
            if q.expected_status in ("clarify", "empty", "refused"):
                results.append(RowEvalResult(
                    id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                    held_out=q.held_out, status=q.status, outcome="pass",
                    api_status=api_status, expected_status=q.expected_status,
                    generated_sql=gen_sql, first_try_json=retries == 0,
                    first_try_sql=bool(gen_sql), repaired=retries > 0, duration_ms=elapsed
                ))
                print(f"#{q.id.zfill(2)} [PASS ] [{q.category:<20}] Correctly triggered {q.expected_status}")
                continue

            # 4. Result Set Comparison against Ground Truth (for answered queries)
            gt_cols: List[str] = []
            gt_rows: List[List[Any]] = []

            # Check if expected snapshot exists
            if q.expected.strip():
                try:
                    snapshot = json.loads(q.expected)
                    gt_cols = [c.upper() for c in snapshot.get("columns", [])]
                    gt_rows = snapshot.get("data", [])
                except Exception as e:
                    results.append(RowEvalResult(
                        id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                        held_out=q.held_out, status=q.status, outcome="error",
                        error_detail=f"Malformed expected snapshot in CSV: {e}",
                        duration_ms=elapsed
                    ))
                    has_errors = True
                    continue
            elif not mock and sf_runner:
                try:
                    gt_cols, gt_rows = sf_runner.execute(q.ground_truth_sql)
                except Exception as e:
                    results.append(RowEvalResult(
                        id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                        held_out=q.held_out, status=q.status, outcome="error",
                        error_detail=f"Ground truth execution error on Snowflake: {e}",
                        duration_ms=elapsed
                    ))
                    has_errors = True
                    print(f"#{q.id.zfill(2)} [ERROR] [{q.category:<20}] Ground truth SQL failed on Snowflake: {e}")
                    continue
            else:
                # In mock mode with no snapshot, pass through
                gt_cols = ["COL1"]
                gt_rows = [["MOCK_VAL"]]

            api_rows_obj = api_resp.get("rows", {})
            api_cols = api_rows_obj.get("columns", [])
            api_data = api_rows_obj.get("data", [])

            matches, mismatch_reason = compare_result_sets(
                api_cols=api_cols,
                api_rows=api_data,
                gt_cols=gt_cols,
                gt_rows=gt_rows,
                order_matters=q.order_matters,
                tolerance=q.tolerance
            )

            if matches:
                results.append(RowEvalResult(
                    id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                    held_out=q.held_out, status=q.status, outcome="pass",
                    api_status=api_status, expected_status=q.expected_status,
                    generated_sql=gen_sql, first_try_json=retries == 0,
                    first_try_sql=bool(gen_sql), repaired=retries > 0, duration_ms=elapsed
                ))
                print(f"#{q.id.zfill(2)} [PASS ] [{q.category:<20}] {q.question[:45]} ({elapsed:.1f}ms)")
            else:
                results.append(RowEvalResult(
                    id=q.id, dataset=q.dataset, question=q.question, category=q.category,
                    held_out=q.held_out, status=q.status, outcome="fail",
                    api_status=api_status, expected_status=q.expected_status,
                    generated_sql=gen_sql, error_detail=mismatch_reason,
                    first_try_json=retries == 0, first_try_sql=bool(gen_sql),
                    repaired=retries > 0, duration_ms=elapsed
                ))
                print(f"#{q.id.zfill(2)} [FAIL ] [{q.category:<20}] {mismatch_reason}")

    if sf_runner:
        sf_runner.close()

    # ── Summary Aggregations ─────────────────────────────────────────────────

    total_count = len(results)
    verified_rows = [r for r in results if r.status == "verified"]
    draft_rows = [r for r in results if r.status == "draft"]
    error_rows = [r for r in results if r.outcome == "error"]

    behaviour_rows = [r for r in results if r.outcome in ("behaviour_pass", "behaviour_fail")]
    behaviour_passes = sum(1 for r in behaviour_rows if r.outcome == "behaviour_pass")

    # Headline accuracy applies strictly to verified rows
    if len(verified_rows) > 0:
        verified_passes = sum(1 for r in verified_rows if r.outcome == "pass")
        headline_accuracy_str = f"{(verified_passes / len(verified_rows) * 100.0):.1f}% ({verified_passes}/{len(verified_rows)})"
    else:
        headline_accuracy_str = "0 verified rows (all rows currently in draft)"

    # Held out counts
    held_out_total = sum(1 for r in results if r.held_out)
    held_out_passes = sum(1 for r in results if r.held_out and r.outcome in ("pass", "behaviour_pass"))

    # Categories breakdown
    cat_stats: Dict[str, Dict[str, Any]] = {}
    for r in results:
        if r.category not in cat_stats:
            cat_stats[r.category] = {"total": 0, "passed": 0, "verified": 0, "draft": 0}
        cat_stats[r.category]["total"] += 1
        if r.status == "verified":
            cat_stats[r.category]["verified"] += 1
        else:
            cat_stats[r.category]["draft"] += 1

        if r.outcome in ("pass", "behaviour_pass"):
            cat_stats[r.category]["passed"] += 1

    # Gemma Metrics
    valid_res_count = len(results) - len(error_rows)
    if valid_res_count > 0:
        first_try_json_pct = round(sum(1 for r in results if r.outcome != "error" and r.first_try_json) / valid_res_count * 100.0, 1)
        first_try_sql_pct = round(sum(1 for r in results if r.outcome != "error" and r.first_try_sql) / valid_res_count * 100.0, 1)
        repaired_pct = round(sum(1 for r in results if r.outcome != "error" and r.repaired) / valid_res_count * 100.0, 1)
    else:
        first_try_json_pct = 0.0
        first_try_sql_pct = 0.0
        repaired_pct = 0.0

    failing_rows = [
        {
            "id": r.id,
            "dataset": r.dataset,
            "question": r.question,
            "category": r.category,
            "error": r.error_detail,
            "sql": r.generated_sql
        }
        for r in results if r.outcome in ("fail", "error", "behaviour_fail")
    ]

    # Console Report
    print("\n------------------------------------------------------------------")
    print(" EVALUATION REPORT")
    print("------------------------------------------------------------------")
    print(f" Timestamp           : {date_str}")
    print(f" Model (from API)    : {detected_model}")
    print(f" Headline Accuracy   : {headline_accuracy_str}")
    print(f" Status Breakdown    : {len(verified_rows)} verified, {len(draft_rows)} unverified (draft), {len(error_rows)} errors")
    print(f" Behaviour Checks    : {behaviour_passes}/{len(behaviour_rows)} passed (reported separately)")
    print(f" Held-Out Questions  : {held_out_passes} of {held_out_total} passed")
    print("\n Category Breakdown:")
    for cat, d in cat_stats.items():
        pct = round(d["passed"] / d["total"] * 100.0, 1) if d["total"] > 0 else 0.0
        print(f"  - {cat:<22}: {d['passed']}/{d['total']} ({pct}%) [{d['verified']} verified, {d['draft']} draft]")

    print("\n Gemma 4 Metrics (from API provenance):")
    print(f"  - First-try valid JSON : {first_try_json_pct}%")
    print(f"  - First-try valid SQL  : {first_try_sql_pct}%")
    print(f"  - Repaired after retry : {repaired_pct}%")

    if failing_rows:
        print(f"\n Failing / Error Questions ({len(failing_rows)}):")
        for f in failing_rows:
            print(f"  - #{f['id']} [{f['category']}]: {f['question']} -> {f['error']}")

    # ── Write Output Files ───────────────────────────────────────────────────

    summary_dict = {
        "timestamp": date_str,
        "mock_mode": mock,
        "model": detected_model,
        "headline_accuracy": headline_accuracy_str,
        "total_questions": total_count,
        "verified_count": len(verified_rows),
        "draft_unverified_count": len(draft_rows),
        "error_count": len(error_rows),
        "behaviour_checks": {
            "total": len(behaviour_rows),
            "passed": behaviour_passes
        },
        "held_out": {
            "total": held_out_total,
            "passed": held_out_passes
        },
        "category_accuracy": {
            cat: round(d["passed"] / d["total"] * 100.0, 1) if d["total"] > 0 else 0.0
            for cat, d in cat_stats.items()
        },
        "category_counts": cat_stats,
        "gemma_metrics": {
            "first_try_valid_json_pct": first_try_json_pct,
            "first_try_valid_sql_pct": first_try_sql_pct,
            "repaired_after_retry_pct": repaired_pct
        },
        "failing_rows": failing_rows
    }

    # Decide target paths based on mock flag
    target_json_path = MOCK_RESULTS_JSON_PATH if mock else RESULTS_JSON_PATH
    target_md_path = MOCK_RESULTS_MD_PATH if mock else RESULTS_MD_PATH

    with open(target_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_dict, f, indent=2)

    # Markdown Report Generation
    md_content = f"""# DataMind Receipts — Evaluation Results

> Generated: `{date_str}`  
> Model: `{detected_model}`  
> Mode: `{'Mock Simulation' if mock else 'Live System'}`

---

## Headline Accuracy (Verified Rows Only)
**{headline_accuracy_str}**

- **Verified Rows Evaluated**: {len(verified_rows)}
- **Draft / Unverified Rows**: {len(draft_rows)} (excluded from headline accuracy)
- **Request / Execution Errors**: {len(error_rows)}
- **Behaviour Checks**: {behaviour_passes} of {len(behaviour_rows)} passed (reported separately from data accuracy)
- **Held-Out Score**: {held_out_passes} of {held_out_total} passed

---

## Category Accuracy

| Category | Passed | Total | Rate | Verified | Draft |
| :--- | :---: | :---: | :---: | :---: | :---: |
"""
    for cat, d in cat_stats.items():
        pct = round(d["passed"] / d["total"] * 100.0, 1) if d["total"] > 0 else 0.0
        md_content += f"| `{cat}` | {d['passed']} | {d['total']} | {pct}% | {d['verified']} | {d['draft']} |\n"

    md_content += f"""
---

## Gemma 4 Metrics
- **First-try valid JSON**: `{first_try_json_pct}%`
- **First-try valid SQL**: `{first_try_sql_pct}%`
- **Repaired after retry**: `{repaired_pct}%`

---

## Failing / Error Questions
"""
    if failing_rows:
        for f in failing_rows:
            md_content += f"- **#{f['id']}** `[{f['category']}]`: *{f['question']}*\n"
            md_content += f"  - Reason: `{f['error']}`\n"
            if f.get("sql"):
                md_content += f"  - Generated SQL: `{f['sql']}`\n"
    else:
        md_content += "None. All evaluated questions passed successfully.\n"

    with open(target_md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\nWrote summary JSON to : {target_json_path}")
    print(f"Wrote summary Report to: {target_md_path}\n")

    return 1 if has_errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DataMind Receipts Ground-Truth Evaluator")
    parser.add_argument("--mock", action="store_true", help="Run with internal mock API (writes to eval/results.mock.json only)")
    parser.add_argument("--dataset", type=str, default=None, help="Filter questions by dataset ('owid' or 'tpch')")
    parser.add_argument("--only-held-out", action="store_true", help="Evaluate only held-out questions")
    parser.add_argument("--api-url", type=str, default="http://localhost:8000", help="FastAPI backend URL")
    args = parser.parse_args()

    exit_code = run_evaluation(
        api_url=args.api_url,
        mock=args.mock,
        dataset_filter=args.dataset,
        only_held_out=args.only_held_out
    )
    sys.exit(exit_code)
