"""Smoke tests: no database, no network, no LLM."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from docqa.api import get_runtime


def client(monkeypatch, secret: str = "s3cret") -> TestClient:
    monkeypatch.setenv("GATEWAY_SECRET", secret)
    get_runtime.cache_clear()
    return TestClient(app)


def test_healthz_is_open(monkeypatch):
    r = client(monkeypatch).get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_a1_routes_need_the_gateway_secret(monkeypatch):
    c = client(monkeypatch)
    assert c.get("/a1/sources").status_code == 401
    assert c.get("/a1/sources", headers={"X-Gateway-Secret": "wrong"}).status_code == 401
    assert c.post("/a1/chat", json={"question": "How long does a refund take?"}).status_code == 401


def test_a1_router_is_mounted_under_its_prefix(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/chat", json={"question": "hello there"}).status_code == 404  # only /a1/chat exists


def test_no_interactive_docs_on_a_public_api(monkeypatch):
    c = client(monkeypatch)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404
