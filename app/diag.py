"""Operator-only dependency check: which of database, embedding and LLM is failing, and with what error.

Reached through GET /diag with the X-Operator-Token header (everyone else gets a plain 404). Error messages are
trimmed and scrubbed of anything that looks like a key or a connection string before they leave the service.
"""
from __future__ import annotations

import re
import time

_SCRUB = [
    (re.compile(r"postgres(ql)?://\S+", re.I), "postgresql://[hidden]"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "[key]"),
    (re.compile(r"gsk_[A-Za-z0-9]{16,}"), "[key]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[key]"),
    (re.compile(r"(?i)(key|token|password|secret)=\S+"), r"\1=[hidden]"),
]


def scrub(text: str, limit: int = 300) -> str:
    for pattern, repl in _SCRUB:
        text = pattern.sub(repl, text)
    return text[:limit]


def check_database() -> dict:
    import psycopg
    from psycopg import sql

    from docqa.config import load_settings

    s = load_settings()
    with psycopg.connect(s.database_url, connect_timeout=10) as conn:
        row = conn.execute(sql.SQL("SELECT count(*) FROM {}.chunks").format(sql.Identifier(s.db_schema))).fetchone()
    return {"chunks": int(row[0])}


def check_embedding() -> dict:
    from docqa.config import load_settings
    from docqa.embedder import Embedder

    s = load_settings()
    vec = Embedder(s.embed_provider, s.embed_model, batch_size=s.embed_batch_size).embed_query("refund")
    return {"model": s.embed_model, "dims": len(vec)}


def check_llm() -> dict:
    from llm_client import LLM

    llm = LLM("gemini")
    out = llm.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=8)
    return {"model": getattr(llm, "model", None), "reply": str(getattr(out, "text", ""))[:20]}


def _timed(fn) -> dict:
    t0 = time.perf_counter()
    try:
        result = fn()
        return {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), **result}
    except Exception as exc:  # noqa: BLE001  the whole point is to report any failure
        return {"ok": False, "ms": int((time.perf_counter() - t0) * 1000), "error": type(exc).__name__, "message": scrub(str(exc))}


def run_checks() -> dict:
    return {"database": _timed(check_database), "embedding": _timed(check_embedding), "llm": _timed(check_llm)}
