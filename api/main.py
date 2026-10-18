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

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import AppConfig, load_config
from src.llm import GemmaAPIProvider
from src.models import AskResponse, ModelInfo
from src.pipeline import DataMindPipeline
from src.validate import DatasetSchema

logger = logging.getLogger(__name__)


# ── Request Models ───────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    """Payload for POST /api/ask and POST /ask."""
    question: str = Field(..., description="Natural language question to ask the data.")
    session_id: str | None = Field(default=None, description="Optional conversation session ID.")
    dataset: str | None = Field(default=None, description="Optional dataset identifier override.")


# ── Dependency Provider ──────────────────────────────────────────────────────

def get_default_pipeline() -> DataMindPipeline:
    """
    Default dependency provider: loads configuration, schema, and provider.
    Can be overridden in tests via app.dependency_overrides[get_default_pipeline].
    """
    cfg = load_config()

    # Load dataset schema
    dataset_path = Path(cfg.dataset)
    if not dataset_path.exists():
        dataset_path = Path("datasets/owid.yaml")
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset schema not found at {cfg.dataset} or datasets/owid.yaml")

    schema = DatasetSchema.from_yaml(dataset_path)

    # Instantiate LLM provider
    provider = GemmaAPIProvider(model_name=cfg.model or "gemma-3-27b-it")

    return DataMindPipeline(
        config=cfg,
        schema=schema,
        provider=provider,
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
                available.append(f.stem)
        if not available:
            available = ["owid"]

        return {
            "datasets": available,
            "active": pipeline_dep.schema.name or "owid",
        }

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
                active_pipeline = DataMindPipeline(
                    config=pipeline_dep.config,
                    schema=new_schema,
                    provider=pipeline_dep.provider,
                    executor=pipeline_dep.executor,
                    verifier=pipeline_dep.verifier,
                    validator=None,
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
