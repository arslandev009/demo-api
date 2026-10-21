"""Operator-only diagnostics: no database, no network, no LLM."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import diag
from app.main import app


def test_scrub_hides_keys_and_connection_strings():
    raw = ("connection to postgresql://neondb_owner:npg_secret@ep-x.neon.tech/neondb failed; key=AIzaSyDUMMYDUMMYDUMMYDUMMY12345 "
           "and sk-abcdefghijklmnopqrstu and token=abc123")
    out = diag.scrub(raw)
    for leaked in ("npg_secret", "AIzaSyDUMMY", "sk-abcdef", "abc123"):
        assert leaked not in out
    assert "[hidden]" in out and out.startswith("connection to")
    assert len(diag.scrub("x" * 1000)) == 300


def test_each_check_reports_its_own_error(monkeypatch):
    class RateLimitError(Exception):
        pass

    def broken():
        raise RateLimitError("Error code: 429 - quota exceeded, retry in 3600s (key=AIzaSyDUMMYDUMMYDUMMYDUMMY12345)")

    monkeypatch.setattr(diag, "check_database", lambda: {"chunks": 41})
    monkeypatch.setattr(diag, "check_embedding", broken)
    monkeypatch.setattr(diag, "check_llm", lambda: {"model": "m", "reply": "OK"})
    result = diag.run_checks()
    assert result["database"]["ok"] and result["database"]["chunks"] == 41
    assert result["llm"]["ok"]
    emb = result["embedding"]
    assert emb["ok"] is False and emb["error"] == "RateLimitError" and "429" in emb["message"] and "AIzaSy" not in emb["message"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", "op-token")
    monkeypatch.setattr(diag, "check_database", lambda: {"chunks": 41})
    monkeypatch.setattr(diag, "check_embedding", lambda: {"model": "e", "dims": 3072})
    monkeypatch.setattr(diag, "check_llm", lambda: {"model": "m", "reply": "OK"})
    return TestClient(app)


def test_diag_is_invisible_without_the_operator_token(client):
    assert client.get("/diag").status_code == 404
    assert client.get("/diag", headers={"X-Operator-Token": "wrong"}).status_code == 404


def test_diag_with_the_operator_token_returns_all_three_checks(client):
    r = client.get("/diag", headers={"X-Operator-Token": "op-token"})
    body = r.json()
    assert r.status_code == 200 and set(body) == {"database", "embedding", "llm"} and all(v["ok"] for v in body.values())
    assert r.headers["cache-control"] == "no-store"


def test_diag_is_not_in_the_openapi_or_docs(client):
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
