"""Shared demo service: backends and their small web UIs, one free Render instance for all demos.

  /demo/a1/   the A1 UI (static files)        /a1/...    the A1 API (docqa router)
  /session    Turnstile check -> session cookie   /healthz   Render's health check
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# docqa derives the prices.json location from its own install path, which is wrong for a pip-installed package.
os.environ.setdefault("LLM_PRICES_FILE", str(Path(__file__).resolve().parents[1] / "prices.json"))

from app import diag, guard  # noqa: E402
from docqa.api import router as a1_router  # noqa: E402  (must come after the environment is prepared)
from app import a2_demo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def csp() -> str:
    """Content-Security-Policy. Cloudflare Turnstile is allowed only while it is switched on; otherwise only this origin."""
    cf = " https://challenges.cloudflare.com" if guard.enabled() else ""
    return ("default-src 'self'; script-src 'self'" + cf + "; frame-src " + (cf.strip() or "'none'") + "; connect-src 'self'" + cf +
            "; img-src 'self' data:; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


# No interactive docs or OpenAPI schema on a public API.
app = FastAPI(title="demo-api", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(guard.SessionKeyMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", csp())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.get("/healthz")
def healthz() -> dict:
    """Render's health check: answers without touching the database or any LLM."""
    return {"status": "ok"}


@app.get("/diag", include_in_schema=False)
def diagnose(request: Request) -> JSONResponse:
    """Operator-only: checks database, embedding and LLM one by one and reports the real error of each."""
    if not guard.operator_ok(request.headers.get("x-operator-token")):
        raise HTTPException(status_code=404, detail="Not Found")  # look like nothing is here
    return JSONResponse(diag.run_checks(), headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/demo/a1/")


app.add_api_route("/session", guard.create_session, methods=["POST"])


@app.get("/demo/a1/config.json", include_in_schema=False)
def a1_config() -> JSONResponse:
    """Public UI settings from environment variables, so they can change without a new deploy of the code."""
    return JSONResponse({
        "turnstileSiteKey": os.getenv("TURNSTILE_SITE_KEY", "") if guard.enabled() else "",
        "repoUrl": os.getenv("A1_REPO_URL", "https://github.com/arslandev009/a1-rag-docqa"),
        "readmeUrl": os.getenv("A1_README_URL", "https://github.com/arslandev009/a1-rag-docqa#failure-analysis"),
        "videoUrl": os.getenv("DEMO_VIDEO_URL", ""),
    }, headers={"Cache-Control": "no-store"})


app.include_router(a1_router, prefix="/a1", dependencies=[Depends(guard.require_session)])
app.mount("/demo/a1", StaticFiles(directory=ROOT / "static" / "a1", html=True), name="a1-ui")  # after the explicit routes
a2_demo.mount(app)  # A2 (invoice extraction): sample demo, operator-only upload router, UI at /demo/a2/
