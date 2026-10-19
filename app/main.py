"""Shared demo API: every portfolio demo mounts its own router under its own prefix (A1 lives at /a1).

One service for all demos keeps the free Render instance hours (750 per month) for the demos that are actually used.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

# docqa derives the prices.json location from its own install path, which is wrong for a pip-installed package.
os.environ.setdefault("LLM_PRICES_FILE", str(Path(__file__).resolve().parents[1] / "prices.json"))

from docqa.api import router as a1_router  # noqa: E402  (must come after the environment is prepared)

# No interactive docs or OpenAPI schema on a public API.
app = FastAPI(title="demo-api", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(a1_router, prefix="/a1")


@app.get("/healthz")
def healthz() -> dict:
    """Render's health check: answers without touching the database or any LLM."""
    return {"status": "ok"}
