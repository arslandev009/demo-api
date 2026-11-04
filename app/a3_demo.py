"""A3 (dental clinic booking agent) inside the shared demo service.

Public routes (behind the visitor session). Every visitor gets a PRIVATE copy of the clinic: own calendar, own conversation,
own "front desk" queue. Nobody can see or change another visitor's bookings, and no staff key exists on the public path.

  POST /a3/demo/chat                   {"message": "..."} -> reply, debug (tool and safety-rule names only), turns_left
  GET  /a3/demo/info                   sample patients to try, message limit
  GET  /a3/demo/approvals              this visitor's cancellation requests waiting for the "front desk"
  POST /a3/demo/approvals/{id}/approve | /reject
  POST /a3/demo/reset                  forget this visitor's conversation and calendar
  GET  /a3/demo/stats                  operator only (X-Operator-Token): sessions, memory, calls used today
  /demo/a3/                            the A3 UI (static files), /demo/a3/config.json its public settings

Protection on the public path: visitor session (Turnstile, when switched on), per-visitor and global rate limits, a
per-message and per-conversation size limit, an A3 daily model-call cap (A3_DAILY_CALLS) AND the SHARED daily budget of the
A1 runtime (table api_usage). Every model call (agent step or safety judge) is charged before it is made.

Environment (all optional): A3_PROVIDERS ("gemini" or "groq,gemini" for failover; default = the A1 default provider),
A3_DAILY_CALLS (default 300, 0 = off), A3_RATE_LIMIT_PER_MIN (default 8 per visitor), A3_GLOBAL_RATE_PER_MIN (default 40),
A3_MAX_SESSIONS (default 80), A3_MAX_MESSAGES (default 40 per conversation), A3_REPO_URL, A3_README_URL, A3_VIDEO_URL.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import guard
from clinicagent.demo_core import MAX_MESSAGE_CHARS, DemoSessions
from docqa.api import RateLimiter, get_runtime

log = logging.getLogger("demo.a3")
ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()

COOKIE = "a3_sid"
_SID_RE = re.compile(r"^[0-9a-f]{32}$")
BUSY_MESSAGE = "The assistant is busy right now. Please try again in a minute."
BUDGET_MESSAGE = "The demo's daily AI budget is used up. Try again tomorrow."


class DemoBudgetUsedUp(Exception):
    """Raised by the charge callback before a model call when a daily cap is reached."""


class _DailyCalls:
    """A3's own cap on model calls per UTC day (in memory: a restart gives a fresh allowance, the shared budget still applies)."""

    def __init__(self) -> None:
        self.day, self.used, self._lock = "", 0, threading.Lock()

    def take(self, limit: int) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if today != self.day:
                self.day, self.used = today, 0
            if limit > 0 and self.used >= limit:
                return False
            self.used += 1
            return True


_calls = _DailyCalls()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def make_charge(budget: Any):
    """The callback DemoSessions runs before every model call: A3's daily cap first, then the shared budget."""

    def charge() -> None:
        if not _calls.take(_int_env("A3_DAILY_CALLS", 300)):
            raise DemoBudgetUsedUp("A3 daily call cap reached")
        if budget.exhausted():
            raise DemoBudgetUsedUp("shared daily budget used up")
        budget.add(1)

    return charge


@lru_cache(maxsize=4)
def _llm_for(provider: str) -> Any:
    from llm_client import LLM

    return LLM(provider)


def build_llm(default_provider: str) -> Any:
    providers = [p.strip() for p in os.getenv("A3_PROVIDERS", "").split(",") if p.strip()] or [default_provider]
    if len(providers) == 1:
        return _llm_for(providers[0])
    from clinicagent.failover import FailoverLLM

    return FailoverLLM([_llm_for(p) for p in providers])


@lru_cache(maxsize=1)
def get_a3_sessions() -> DemoSessions:
    rt = get_runtime()
    return DemoSessions(build_llm(rt.config.default_provider), charge=make_charge(rt.budget),
                        max_sessions=_int_env("A3_MAX_SESSIONS", 80),
                        max_messages_per_session=_int_env("A3_MAX_MESSAGES", 40))


@lru_cache(maxsize=1)
def get_a3_limiter() -> RateLimiter:
    return RateLimiter(_int_env("A3_RATE_LIMIT_PER_MIN", 8))


@lru_cache(maxsize=1)
def get_a3_global_limiter() -> RateLimiter:
    return RateLimiter(_int_env("A3_GLOBAL_RATE_PER_MIN", 40))


def visitor_key(request: Request, response: Response) -> str:
    """Who this visitor is. With protection on it is the verified session id (set by app.guard.SessionKeyMiddleware).
    Without it, a random cookie of our own: the proxy's client address cannot tell visitors apart."""
    header = request.headers.get("x-real-client-ip", "").strip()
    if header.startswith("session:") or header == "operator":
        return header
    sid = request.cookies.get(COOKIE, "")
    if not _SID_RE.match(sid):
        sid = secrets.token_hex(16)
        local = (request.url.hostname or "") in ("localhost", "127.0.0.1", "testserver")
        response.set_cookie(COOKIE, sid, max_age=1800, httponly=True, samesite="lax", secure=not local, path="/")
    return f"cookie:{sid}"


def require_operator(request: Request) -> None:
    if not guard.operator_ok(request.headers.get("x-operator-token")):
        raise HTTPException(status_code=404, detail="Not Found")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


@router.get("/demo/info")
def info(sessions: DemoSessions = Depends(get_a3_sessions)) -> dict:
    return {"samples": sessions.sample_hint(), "max_messages": sessions.max_messages, "max_chars": MAX_MESSAGE_CHARS}


@router.post("/demo/chat")
def chat(body: ChatIn, request: Request, response: Response, sessions: DemoSessions = Depends(get_a3_sessions),
         limiter: RateLimiter = Depends(get_a3_limiter), glimiter: RateLimiter = Depends(get_a3_global_limiter)) -> dict:
    key = visitor_key(request, response)
    if not limiter.allow(key) or not glimiter.allow("all"):
        raise HTTPException(status_code=429, detail="Too many messages in a minute. Please wait a moment.")
    t0 = time.perf_counter()
    try:
        out = sessions.chat(key, body.message)
    except DemoBudgetUsedUp:
        raise HTTPException(status_code=503, detail=BUDGET_MESSAGE) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Message must be 1 to 1000 characters.") from None
    except Exception as exc:  # noqa: BLE001  never leak internals; message text is never logged (it may hold names and phones)
        log.warning("a3 chat failed: %s", type(exc).__name__)
        if getattr(exc, "status_code", None) in (429, 500, 502, 503, 504) or "Timeout" in type(exc).__name__:
            raise HTTPException(status_code=503, detail=BUSY_MESSAGE) from None
        raise HTTPException(status_code=503, detail="The assistant is temporarily unavailable.") from None
    log.info("a3 chat guards=%s tools=%s ms=%d", out["debug"]["guards"], [c["name"] for c in out["debug"]["tool_calls"]],
             int((time.perf_counter() - t0) * 1000))
    return out


@router.get("/demo/approvals")
def approvals(request: Request, response: Response, sessions: DemoSessions = Depends(get_a3_sessions)) -> dict:
    return {"pending": sessions.pending(visitor_key(request, response))}


def _decide(appointment_id: int, approve: bool, request: Request, response: Response, sessions: DemoSessions) -> dict:
    result = sessions.decide(visitor_key(request, response), appointment_id, approve)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 409, result.get("message", "Could not save the decision."))
    return result


@router.post("/demo/approvals/{appointment_id}/approve")
def approve(appointment_id: int, request: Request, response: Response, sessions: DemoSessions = Depends(get_a3_sessions)) -> dict:
    return _decide(appointment_id, True, request, response, sessions)


@router.post("/demo/approvals/{appointment_id}/reject")
def reject(appointment_id: int, request: Request, response: Response, sessions: DemoSessions = Depends(get_a3_sessions)) -> dict:
    return _decide(appointment_id, False, request, response, sessions)


@router.post("/demo/reset")
def reset(request: Request, response: Response, sessions: DemoSessions = Depends(get_a3_sessions)) -> dict:
    sessions.reset(visitor_key(request, response))
    return {"ok": True}


def memory_mb() -> dict:
    """Current and peak resident memory of this process in MB (Linux; empty elsewhere). Render's free plan has 512 MB."""
    out: dict[str, float] = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                name, value = line.split(":", 1)
                out["rss_mb" if name == "VmRSS" else "peak_mb"] = round(int(value.split()[0]) / 1024, 1)
    except (OSError, ValueError):
        pass
    return out


@router.get("/demo/stats", dependencies=[Depends(require_operator)])
def stats(sessions: DemoSessions = Depends(get_a3_sessions)) -> dict:
    llm_status = getattr(sessions.llm, "llm", sessions.llm)
    return {"sessions": len(sessions), "max_sessions": sessions.max_sessions, "a3_calls_today": _calls.used,
            "a3_daily_calls": _int_env("A3_DAILY_CALLS", 300), **memory_mb(),
            "providers": llm_status.status() if hasattr(llm_status, "status") else str(getattr(llm_status, "provider", "?"))}


def config() -> JSONResponse:
    """Public UI settings from environment variables, so they can change without a new deploy of the code."""
    return JSONResponse({
        "turnstileSiteKey": os.getenv("TURNSTILE_SITE_KEY", "") if guard.enabled() else "",
        "repoUrl": os.getenv("A3_REPO_URL", "https://github.com/arslandev009/a3-agent-booking"),
        "readmeUrl": os.getenv("A3_README_URL", "https://github.com/arslandev009/a3-agent-booking#readme"),
        "videoUrl": os.getenv("A3_VIDEO_URL", ""),
    }, headers={"Cache-Control": "no-store"})


def mount(app) -> None:
    """Called once from app.main, after the A2 routes (explicit routes first, the static mount last)."""
    app.include_router(router, prefix="/a3", dependencies=[Depends(guard.require_session)])
    app.add_api_route("/demo/a3/config.json", config, methods=["GET"], include_in_schema=False)
    app.mount("/demo/a3", StaticFiles(directory=ROOT / "static" / "a3", html=True), name="a3-ui")
