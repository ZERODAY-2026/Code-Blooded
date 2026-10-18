"""
run.py — Consolidated Single Entrypoint for DataMind Receipts.

Starts the FastAPI server (which automatically serves the prebuilt React UI
from web/dist on port 8000).

Usage:
    python run.py
"""

from __future__ import annotations

import sys
import uvicorn


def main() -> None:
    print("\n========================================================")
    print("  DataMind Receipts — Deterministic Data Receipts Engine")
    print("  Serving API & Web Interface on http://localhost:8000")
    print("========================================================\n")

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
