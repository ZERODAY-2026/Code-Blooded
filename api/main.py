"""
api/main.py — FastAPI Application for DataMind Receipts.

Exposes:
  - POST /api/ask and POST /ask: Primary question-answering endpoint
  - GET /api/health and GET /health: Health check endpoint
  - GET /api/datasets and GET /datasets: Available datasets listing
  - Static file serving from web/dist (when present)

Design rules:
─────────────
- Validates requests with Pydantic (AskRequest).
- Reuses the canonical AskResponse model from src/models.py without alteration.
- Does not duplicate pipeline business logic.
- Injectable pipeline dependency for deterministic unit testing.
- Never prints or exposes credentials, stack traces, or secrets.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os
import time

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import AppConfig, load_config
from src.llm import GemmaAPIProvider
from src.models import (
    AskResponse,
    EvidenceCell,
    ExecuteResult,
    ModelInfo,
    RowSet,
    VerifyResult,
)
from src.pipeline import DataMindPipeline
from src.validate import DatasetSchema, SQLValidator

logger = logging.getLogger(__name__)


# ── Request Models ───────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    """Payload for POST /api/ask and POST /ask."""
    question: str = Field(..., description="Natural language question to ask the data.")
    session_id: str | None = Field(default=None, description="Optional conversation session ID.")
    dataset: str | None = Field(default=None, description="Optional dataset identifier override.")


# ── Dependency Provider ──────────────────────────────────────────────────────

def get_default_pipeline(request: Request = None) -> DataMindPipeline:
    """
    Default dependency provider: loads configuration, schema, and provider.
    Can be overridden in tests via app.dependency_overrides[get_default_pipeline].
    """
    cfg = load_config()

    # Determine active dataset: either from request app state or cfg.dataset
    active_ds = None
    if request is not None and hasattr(request, "app") and hasattr(request.app, "state"):
        active_ds = getattr(request.app.state, "active_dataset", None)

    if active_ds:
        dataset_path = Path(f"datasets/{active_ds}.yaml")
    else:
        dataset_path = Path(cfg.dataset)
        if not dataset_path.exists():
            dataset_path = Path("datasets/owid.yaml")

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset schema not found at {dataset_path}")

    schema = DatasetSchema.from_yaml(dataset_path)

    # Instantiate LLM provider
    provider = GemmaAPIProvider(model_name=cfg.model or "gemma-4-26b-a4b-it")

    # Check for live Snowflake credentials
    sf_account = os.environ.get("SNOWFLAKE_ACCOUNT", "").strip()
    sf_user = os.environ.get("SNOWFLAKE_USER", "").strip()
    sf_password = os.environ.get("SNOWFLAKE_PASSWORD", "").strip()
    sf_private_key = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "").strip()

    executor = None
    verifier = None

    has_snowflake_creds = bool(sf_account and sf_user and (sf_password or sf_private_key))
    if has_snowflake_creds:
        try:
            from src.execute import execute_snowflake

            def build_live_executor(ds_schema: DatasetSchema):
                target_db = getattr(ds_schema, "database", None) or os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND").strip()
                target_schema = getattr(ds_schema, "schema_name", None) or getattr(ds_schema, "schema", None) or os.environ.get("SNOWFLAKE_SCHEMA", "OWID").strip()

                def _live_exec(sql: str) -> ExecuteResult:
                    res = execute_snowflake(
                        sql,
                        timeout_s=cfg.limits.query_timeout_s,
                        database=target_db,
                        schema=target_schema,
                    )
                    if res.error:
                        if os.environ.get("DEMO_MODE", "").lower() in ("true", "1", "yes"):
                            return ExecuteResult(
                                rows=RowSet(
                                    columns=["entity", "life_expectancy"],
                                    data=[
                                        ["Japan", 84.4],
                                        ["Switzerland", 83.8],
                                        ["Singapore", 83.6],
                                        ["Spain", 83.5],
                                        ["Italy", 83.3],
                                    ],
                                ),
                                query_id="DEMO-SIMULATED-0001",
                                timings_ms=res.timings_ms,
                                flags=["simulated_demo_result", f"snowflake_unavailable: {res.error}"],
                            )
                    return res

                return _live_exec

            executor = build_live_executor(schema)
            logger.info("Configured Snowflake executor.")
        except Exception as err:
            logger.warning("Could not initialize Snowflake executor: %s", err)

    try:
        from src.verify import verify_answer

        def _pipeline_verify(prose: str, rows: RowSet, question: str = "", is_simulated: bool = False, sql: str = "") -> VerifyResult:
            effective_sql = f"{sql} {question}".strip()
            v_res = verify_answer(prose, rows.columns, rows.data, sql=effective_sql)
            ev_cells = [
                EvidenceCell(text=str(item["text"]), row_index=int(item["row_index"]), column=str(item["column"]))
                for item in getattr(v_res, "evidence", [])
            ]
            conf = "verified" if getattr(v_res, "confidence", "partial") == "verified" else "partial"
            st = "answered" if getattr(v_res, "is_verified", True) else "unverifiable"
            return VerifyResult(
                verified_answer=getattr(v_res, "verified_prose", prose),
                evidence=ev_cells,
                confidence=conf,
                unverifiable_claims=getattr(v_res, "unmatched_claims", []),
                status=st,
            )

        verifier = _pipeline_verify
        logger.info("Configured evidence verifier.")
    except Exception as err:
        logger.warning("Could not initialize verifier: %s", err)

    if executor is None:
        def _demo_exec(sql: str) -> ExecuteResult:
            return ExecuteResult(
                rows=RowSet(
                    columns=["entity", "life_expectancy"],
                    data=[
                        ["Japan", 84.4],
                        ["Switzerland", 83.8],
                        ["Singapore", 83.6],
                        ["Spain", 83.5],
                        ["Italy", 83.3],
                    ],
                ),
                query_id="DEMO-SIMULATED-0001",
                timings_ms={"snowflake": 15},
                flags=["simulated_demo_result", "snowflake_unconfigured"],
            )

        executor = _demo_exec

    if verifier is None:
        def _demo_verify(prose: str, rows: RowSet, question: str = "") -> VerifyResult:
            evidence: list[EvidenceCell] = []
            for r_idx, row in enumerate(rows.data):
                for c_idx, col_name in enumerate(rows.columns):
                    val_str = str(row[c_idx])
                    if val_str and val_str in prose:
                        evidence.append(EvidenceCell(text=val_str, row_index=r_idx, column=col_name))
            confidence = "verified" if evidence else "partial"
            return VerifyResult(
                verified_answer=prose,
                evidence=evidence,
                confidence=confidence,
                status="answered",
            )

        verifier = _demo_verify

    return DataMindPipeline(
        config=cfg,
        schema=schema,
        provider=provider,
        executor=executor,
        verifier=verifier,
    )


# ── Application Factory ──────────────────────────────────────────────────────

def create_app(
    pipeline: DataMindPipeline | None = None,
    config: AppConfig | None = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.
    """
    app = FastAPI(
        title="DataMind Receipts API",
        description="Talk to your data with cryptographic-like receipts and evidence verification.",
        version="1.0.0",
    )

    # Enable CORS for local Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # If an explicit pipeline is provided, register it in dependency overrides
    if pipeline is not None:
        app.dependency_overrides[get_default_pipeline] = lambda: pipeline

    # ── Exception Handler ────────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled exception processing request: %s", exc)
        # Clearly report configuration and environment failures without leaking secrets
        if isinstance(exc, (EnvironmentError, FileNotFoundError, ValueError)):
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": "error",
                    "answer": f"Configuration error: {exc}",
                    "flags": ["configuration_error"],
                },
            )
        # Never leak raw stack traces or internal secrets to the client
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "answer": "An unexpected server error occurred.",
                "flags": ["internal_server_error"],
            },
        )

    # ── Health Endpoints ─────────────────────────────────────────────────────
    @app.get("/health", tags=["System"])
    @app.get("/api/health", tags=["System"])
    async def health_check(
        pipeline_dep: DataMindPipeline = Depends(get_default_pipeline),
    ) -> dict[str, Any]:
        """System health and active model check."""
        return {
            "status": "ok",
            "app": "DataMind Receipts",
            "model": pipeline_dep.provider.model_name,
            "provider": pipeline_dep.provider.provider_name,
            "dataset": pipeline_dep.schema.name,
        }

    # ── Dataset Endpoints ────────────────────────────────────────────────────
    @app.get("/datasets", tags=["Datasets"])
    @app.get("/api/datasets", tags=["Datasets"])
    async def list_datasets(
        pipeline_dep: DataMindPipeline = Depends(get_default_pipeline),
    ) -> dict[str, Any]:
        """List available datasets and active selection."""
        datasets_dir = Path("datasets")
        available = []
        if datasets_dir.exists():
            for f in datasets_dir.glob("*.yaml"):
                available.append({
                    "id": f.stem,
                    "dataset_id": f.stem,
                    "name": "Our World in Data (OWID)" if f.stem == "owid" else f.stem.upper(),
                })
        if not available:
            available = [{"id": "owid", "dataset_id": "owid", "name": "Our World in Data (OWID)"}]

        return {
            "datasets": available,
            "active": pipeline_dep.schema.name or "owid",
        }

    @app.post("/switch-dataset", tags=["Datasets"])
    @app.post("/api/switch-dataset", tags=["Datasets"])
    async def switch_dataset_route(
        request: Request,
        payload: dict[str, Any],
        pipeline_dep: DataMindPipeline = Depends(get_default_pipeline),
    ) -> dict[str, Any]:
        """Switch active dataset."""
        if pipeline_dep.config.public_mode:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Dataset switching is disabled in public mode.",
            )

        target = str(payload.get("dataset") or payload.get("dataset_id") or "").strip().lower()
        if not target:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dataset identifier is required.",
            )

        ds_path = Path(f"datasets/{target}.yaml")
        if not ds_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Requested dataset '{target}' was not found.",
            )

        try:
            new_schema = DatasetSchema.from_yaml(ds_path)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to load dataset schema '{target}': {e}",
            )

        request.app.state.active_dataset = target
        return {
            "status": "ok",
            "active": target,
            "name": new_schema.name or target,
            "tables": list(new_schema.tables.keys()),
        }

    # ── Evaluation Summary Endpoint ──────────────────────────────────────────
    @app.get("/eval-summary", tags=["Evaluation"])
    @app.get("/api/eval-summary", tags=["Evaluation"])
    async def get_eval_summary() -> JSONResponse:
        """Return evaluation benchmark results if available."""
        eval_path = Path("eval/summary.json")
        if eval_path.exists():
            import json
            try:
                with eval_path.open("r", encoding="utf-8") as f:
                    return JSONResponse(content=json.load(f))
            except Exception as e:
                logger.warning("Could not parse eval summary: %s", e)
        return JSONResponse(status_code=404, content={"detail": "No evaluation summary available yet"})

    # ── Ask Endpoints ────────────────────────────────────────────────────────
    @app.post("/ask", response_model=AskResponse, tags=["Query"])
    @app.post("/api/ask", response_model=AskResponse, tags=["Query"])
    async def ask_question(
        payload: AskRequest,
        pipeline_dep: DataMindPipeline = Depends(get_default_pipeline),
    ) -> AskResponse:
        """
        Submit a natural language question to the DataMind Receipts pipeline.
        Returns validated SQL, evidence cells, execution metadata, and full receipt.
        """
        # Validate question is not empty or whitespace
        clean_q = payload.question.strip() if payload.question else ""
        if not clean_q:
            return AskResponse(
                status="error",
                answer="Question cannot be empty or whitespace.",
                flags=["empty_user_question"],
                model=ModelInfo(
                    provider=pipeline_dep.provider.provider_name,
                    name=pipeline_dep.provider.model_name,
                ),
            )

        # Check public_mode guard for dataset override
        if pipeline_dep.config.public_mode and payload.dataset and payload.dataset != pipeline_dep.schema.name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Dataset switching is disabled in public mode.",
            )

        # Dataset override handling if requested
        active_pipeline = pipeline_dep
        if payload.dataset and payload.dataset != pipeline_dep.schema.name:
            ds_path = Path(f"datasets/{payload.dataset}.yaml")
            if not ds_path.exists():
                return AskResponse(
                    status="error",
                    answer=f"Requested dataset '{payload.dataset}' was not found.",
                    flags=[f"unknown_dataset: {payload.dataset}"],
                    model=ModelInfo(
                        provider=pipeline_dep.provider.provider_name,
                        name=pipeline_dep.provider.model_name,
                    ),
                )
            try:
                new_schema = DatasetSchema.from_yaml(ds_path)
                target_db = getattr(new_schema, "database", None) or os.environ.get("SNOWFLAKE_DATABASE", "DATAMIND").strip()
                target_schema = getattr(new_schema, "schema_name", None) or getattr(new_schema, "schema", None) or os.environ.get("SNOWFLAKE_SCHEMA", "OWID").strip()

                from src.execute import execute_snowflake
                def _override_exec(sql: str) -> ExecuteResult:
                    return execute_snowflake(
                        sql,
                        timeout_s=pipeline_dep.config.limits.query_timeout_s,
                        database=target_db,
                        schema=target_schema,
                    )

                active_pipeline = DataMindPipeline(
                    config=pipeline_dep.config,
                    schema=new_schema,
                    provider=pipeline_dep.provider,
                    executor=_override_exec,
                    verifier=pipeline_dep.verifier,
                    validator=SQLValidator(schema=new_schema, max_rows=pipeline_dep.config.limits.max_rows),
                )
            except Exception as ds_err:
                logger.warning("Could not switch to dataset '%s': %s", payload.dataset, ds_err)
                return AskResponse(
                    status="error",
                    answer=f"Failed to load dataset schema '{payload.dataset}': {ds_err}",
                    flags=["dataset_schema_load_error"],
                    model=ModelInfo(
                        provider=pipeline_dep.provider.provider_name,
                        name=pipeline_dep.provider.model_name,
                    ),
                )

        try:
            return active_pipeline.ask(
                question=clean_q,
                session_id=payload.session_id,
            )
        except Exception as exc:
            logger.error("Error executing pipeline: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": "error",
                    "answer": "An unexpected server error occurred.",
                    "flags": ["internal_server_error"],
                },
            )

    # ── Static File Serving (web/dist for judges) ────────────────────────────
    dist_dir = Path("web/dist")
    if dist_dir.exists() and dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")

    return app


# Module-level default application instance
app = create_app()
