"""A2 (invoice extraction) inside the shared demo service.

Public routes (behind the visitor session): the visitor picks one of the BUNDLED sample invoices. Nothing is uploaded: free LLM
tiers may log their inputs, so real documents are never accepted on the public path.

  GET  /a2/demo/samples    the bundled samples (title, what it shows, expected review flag, image and pdf URLs)
  POST /a2/demo/extract    {"sample": "<id>"} -> the A2 result (fields, checks, needs_review)
  /demo/a2/                the A2 UI (static files), /demo/a2/config.json its public settings

Private routes: A2's own upload router (POST /a2/extract, GET /a2/health) is mounted behind the operator token only.
Protection on the public path: visitor session, per-session rate limit (A2_RATE_LIMIT_PER_MIN, default 6), the SHARED daily
LLM-call budget of the A1 runtime (table api_usage), and a short result cache (A2_CACHE_TTL_S, default 600 s, 0 = off).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import guard
from docqa.api import RateLimiter, get_runtime
from extractor.api import get_llm_dep
from extractor.api import router as upload_router
from extractor.pipeline import Result, process_pdf

log = logging.getLogger("demo.a2")
ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = ROOT / "static" / "a2" / "samples"
router = APIRouter()

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


@lru_cache(maxsize=1)
def load_samples() -> dict[str, dict]:
    """id -> sample record. The id is the file stem; only ids found here can be extracted (no paths from the client)."""
    manifest = SAMPLES_DIR / "samples.json"
    items = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    return {Path(i["file"]).stem: i for i in items}


def get_a2_runtime() -> Any:
    """The A1 runtime: its daily budget (database-backed in production) is shared with A2."""
    return get_runtime()


@lru_cache(maxsize=1)
def get_a2_limiter() -> RateLimiter:
    return RateLimiter(int(os.getenv("A2_RATE_LIMIT_PER_MIN", "6")))


@lru_cache(maxsize=4)
def _llm_for(provider: str) -> Any:
    from llm_client import LLM

    return LLM(provider)


def get_llm_factory() -> Any:
    return _llm_for


def _key(request: Request) -> str:
    """The rate-limit key: app.guard.SessionKeyMiddleware overwrites this header with the verified session id."""
    return request.headers.get("x-real-client-ip", "").strip() or (request.client.host if request.client else "unknown")


def require_operator(request: Request) -> None:
    if not guard.operator_ok(request.headers.get("x-operator-token")):
        raise HTTPException(status_code=404, detail="Not Found")


def llm_calls(res: Result) -> int:
    """How many LLM calls this result used: scans and unreadable PDFs never reach the model."""
    if res.extraction is not None:
        return max(1, res.attempts)
    if res.error and res.error.startswith("LLM extraction failed"):
        return 1
    return 0


class ExtractIn(BaseModel):
    sample: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")


@router.get("/demo/samples")
def samples() -> list[dict]:
    return [{"id": k, "title": v["title"], "shows": v["shows"], "expected_review": bool(v["expected_review"]),
             "image": f"/demo/a2/samples/{k}.png", "pdf": f"/demo/a2/samples/{v['file']}"}
            for k, v in load_samples().items()]


@router.post("/demo/extract")
def extract_sample(body: ExtractIn, request: Request, limiter: RateLimiter = Depends(get_a2_limiter),
                   rt: Any = Depends(get_a2_runtime), llm_for: Any = Depends(get_llm_factory)) -> dict:
    sample = load_samples().get(body.sample)
    if sample is None:
        raise HTTPException(status_code=404, detail="Unknown sample.")
    if not limiter.allow(_key(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a minute.")

    ttl, now = float(os.getenv("A2_CACHE_TTL_S", "600")), time.time()
    with _lock:
        hit = _cache.get(body.sample)
    if hit and ttl > 0 and now - hit[0] < ttl:
        return {**hit[1], "cached": True, "age_s": int(now - hit[0])}

    try:
        data = (SAMPLES_DIR / sample["file"]).read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="Sample file missing.") from None
    if rt.budget.exhausted():
        raise HTTPException(status_code=503, detail="The demo's daily AI budget is used up. Try again tomorrow.")

    provider = rt.config.default_provider
    try:
        res = process_pdf(data, llm_for(provider), max_pages=5, default_currency=os.getenv("DEFAULT_CURRENCY") or None)
    except Exception:  # noqa: BLE001  never leak internals to the visitor
        log.exception("a2 extract failed (sample=%s)", body.sample)
        raise HTTPException(status_code=503, detail="The extraction service is temporarily unavailable.") from None

    calls = llm_calls(res)
    rt.budget.add(calls)
    log.info("a2 sample=%s provider=%s llm_calls=%s review=%s latency=%sms", body.sample, provider, calls, res.needs_review, res.latency_ms)
    # Same rule as A2's own router: only a model failure (error set, nothing else found) is a provider error.
    if res.extraction is None and res.error and res.pages > 0 and not res.issues:
        raise HTTPException(status_code=502, detail="The AI provider failed. Try again in a minute.")

    payload = {"sample": body.sample, "title": sample["title"], "provider": provider, "llm_calls": calls,
               "result": res.model_dump(), "cached": False, "age_s": 0}
    with _lock:
        _cache[body.sample] = (now, payload)
    return payload


def config() -> JSONResponse:
    """Public UI settings from environment variables, so they can change without a new deploy of the code."""
    return JSONResponse({
        "turnstileSiteKey": os.getenv("TURNSTILE_SITE_KEY", "") if guard.enabled() else "",
        "repoUrl": os.getenv("A2_REPO_URL", "https://github.com/arslandev009/a2-extraction-api"),
        "readmeUrl": os.getenv("A2_README_URL", "https://github.com/arslandev009/a2-extraction-api#failure-analysis"),
        "videoUrl": os.getenv("A2_VIDEO_URL", ""),
    }, headers={"Cache-Control": "no-store"})


def _operator_llm() -> Any:
    return _llm_for(get_runtime().config.default_provider)


def mount(app) -> None:
    """Called once from app.main, after the A1 routes (explicit routes first, the static mount last)."""
    app.include_router(router, prefix="/a2", dependencies=[Depends(guard.require_session)])
    app.include_router(upload_router, prefix="/a2", dependencies=[Depends(require_operator)])
    app.dependency_overrides[get_llm_dep] = _operator_llm  # A2's upload router picks the same provider as A1
    app.add_api_route("/demo/a2/config.json", config, methods=["GET"], include_in_schema=False)
    app.mount("/demo/a2", StaticFiles(directory=ROOT / "static" / "a2", html=True), name="a2-ui")
