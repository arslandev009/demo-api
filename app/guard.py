"""Visitor protection for the demo UI that this service serves.

Cloudflare Turnstile proves a human opened the page; the service then issues a signed, short-lived session cookie.
The session id is also the rate-limit key, so no limit depends on client IP headers (a visitor could spoof those
behind Render's proxy). Everything is optional: without TURNSTILE_SECRET and SESSION_SECRET the demo is open.
OPERATOR_TOKEN lets smoke tests and monitors call the API without a browser.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

COOKIE = "demo_session"
SESSION_TTL_S = 30 * 60
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
log = logging.getLogger("demo.guard")


def enabled() -> bool:
    """Protection is on only when both secrets are configured."""
    return bool(os.getenv("TURNSTILE_SECRET") and os.getenv("SESSION_SECRET"))


def _sign(message: str) -> str:
    return hmac.new(os.environ["SESSION_SECRET"].encode(), message.encode(), hashlib.sha256).hexdigest()


def new_session_value(now: float | None = None) -> str:
    sid = secrets.token_hex(8)
    exp = int(now if now is not None else time.time()) + SESSION_TTL_S
    return f"{sid}.{exp}.{_sign(f'{sid}.{exp}')}"


def session_id(value: str | None, now: float | None = None) -> str | None:
    """The session id when the cookie is genuine and not expired, else None."""
    if not value or not enabled():
        return None
    parts = value.split(".")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    sid, exp, sig = parts
    if int(exp) < (now if now is not None else time.time()):
        return None
    return sid if hmac.compare_digest(sig, _sign(f"{sid}.{exp}")) else None


def operator_ok(token: str | None) -> bool:
    configured = os.getenv("OPERATOR_TOKEN", "")
    return bool(configured and token and hmac.compare_digest(token, configured))


def _cookie_from_headers(headers: list[tuple[bytes, bytes]]) -> str | None:
    for k, v in headers:
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
    return None


class SessionKeyMiddleware:
    """Replaces any client-supplied X-Real-Client-IP with a key the client cannot choose: the verified session id
    (or 'operator'). docqa's rate limiter uses that header as its key."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope["headers"] if k != b"x-real-client-ip"]
            key = None
            token = next((v.decode("latin-1") for k, v in headers if k == b"x-operator-token"), None)
            if operator_ok(token):
                key = "operator"
            else:
                sid = session_id(_cookie_from_headers(headers))
                if sid:
                    key = f"session:{sid}"
            if key:
                headers.append((b"x-real-client-ip", key.encode()))
            scope = dict(scope, headers=headers)
        await self.app(scope, receive, send)


def require_session(request: Request) -> None:
    """Dependency for the demo routers: a verified session (or the operator token) is required when protection is on."""
    if not enabled() or request.url.path.endswith("/health"):
        return
    if operator_ok(request.headers.get("x-operator-token")):
        return
    if session_id(request.cookies.get(COOKIE)) is None:
        raise HTTPException(status_code=401, detail="Session expired. Please verify again.")


class SessionIn(BaseModel):
    token: str = Field(min_length=1, max_length=2048)


async def verify_turnstile(token: str) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(TURNSTILE_URL, data={"secret": os.environ["TURNSTILE_SECRET"], "response": token})
        return r.json().get("success") is True


async def create_session(body: SessionIn, request: Request) -> JSONResponse:
    if not enabled():
        return JSONResponse({"ok": True, "protected": False})
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="Cross-origin requests are not allowed.")
    try:
        ok = await verify_turnstile(body.token)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="Could not reach the verification service. Please try again.") from None
    if not ok:
        raise HTTPException(status_code=403, detail="Verification failed. Please reload the page.")
    resp = JSONResponse({"ok": True, "protected": True, "ttl": SESSION_TTL_S})
    local = (request.url.hostname or "") in ("localhost", "127.0.0.1")
    resp.set_cookie(COOKIE, new_session_value(), max_age=SESSION_TTL_S, httponly=True, samesite="lax", secure=not local, path="/")
    return resp
