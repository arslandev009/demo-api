"""Session protection, UI hosting and the rate-limit key: no database, no network, no LLM."""
from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import guard
from app.main import app
from docqa.api import ApiConfig, DailyBudget, RateLimiter, Runtime, get_runtime
from docqa.generate import AnswerResult, Citation

BASE = "http://localhost"  # a non-secure host, so the test client sends the (otherwise Secure) cookie back
ENV_KEYS = ("TURNSTILE_SECRET", "SESSION_SECRET", "OPERATOR_TOKEN", "GATEWAY_SECRET", "TURNSTILE_SITE_KEY", "DEMO_VIDEO_URL")


class FakeService:
    def ping(self) -> bool:
        return True

    def ask(self, provider: str, question: str) -> AnswerResult:
        return AnswerResult(question, "Refunds take 5 to 10 business days [S1].", False, "ok", llm_calls=1,
                            citations=[Citation("S1", "c1", "refund-policy-v2", "Refund Policy (current)", "How refunds are paid", "https://x.test")])

    def sources(self) -> list[dict]:
        return [{"doc_id": "refund-policy-v2", "title": "Refund Policy (current)", "source_url": "https://x.test", "version": "v2", "effective_date": ""}]


@pytest.fixture()
def make(monkeypatch):
    def factory(*, protected: bool = True, rate: int = 100) -> TestClient:
        for k in ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("OPERATOR_TOKEN", "op-token")
        if protected:
            monkeypatch.setenv("TURNSTILE_SECRET", "ts-secret")
            monkeypatch.setenv("SESSION_SECRET", "s" * 32)
            monkeypatch.setenv("TURNSTILE_SITE_KEY", "site-key-123")

        async def fake_verify(token: str) -> bool:
            return token == "good"

        monkeypatch.setattr(guard, "verify_turnstile", fake_verify)
        rt = Runtime(FakeService(), ApiConfig(rate_per_minute=rate, daily_llm_calls=1000), RateLimiter(rate), DailyBudget(1000))
        app.dependency_overrides[get_runtime] = lambda: rt
        return TestClient(app, base_url=BASE)

    yield factory
    app.dependency_overrides.clear()
    get_runtime.cache_clear()


def verified(client: TestClient) -> TestClient:
    assert client.post("/session", json={"token": "good"}).status_code == 200
    return client


Q = {"question": "How long does a refund take?"}


def test_protected_routes_need_a_session_but_health_stays_open(make):
    c = make()
    assert c.post("/a1/chat", json=Q).status_code == 401
    assert c.get("/a1/sources").status_code == 401
    assert c.get("/a1/health").status_code == 200


def test_session_is_created_only_after_a_good_turnstile_token(make):
    c = make()
    bad = c.post("/session", json={"token": "bad"})
    assert bad.status_code == 403 and "set-cookie" not in bad.headers
    good = c.post("/session", json={"token": "good"})
    cookie = good.headers["set-cookie"]
    assert good.status_code == 200 and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert c.get("/a1/sources").status_code == 200
    assert c.post("/a1/chat", json=Q).status_code == 200


def test_turnstile_outage_is_a_502_and_cross_origin_session_requests_are_refused(make, monkeypatch):
    c = make()

    async def down(token: str) -> bool:
        raise httpx.ConnectError("network")

    monkeypatch.setattr(guard, "verify_turnstile", down)
    assert c.post("/session", json={"token": "good"}).status_code == 502
    assert c.post("/session", json={"token": "good"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_tampered_and_expired_cookies_are_rejected(make):
    c = make()
    good = guard.new_session_value()
    c.cookies.set("demo_session", good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert c.get("/a1/sources").status_code == 401
    c.cookies.set("demo_session", guard.new_session_value(now=time.time() - 7200), domain="localhost")
    assert c.get("/a1/sources").status_code == 401


def test_operator_token_works_without_a_browser_session(make):
    c = make()
    assert c.get("/a1/sources", headers={"X-Operator-Token": "op-token"}).status_code == 200
    assert c.get("/a1/sources", headers={"X-Operator-Token": "wrong"}).status_code == 401


def test_spoofed_client_ip_header_cannot_dodge_the_rate_limit_and_sessions_are_limited_separately(make):
    c = verified(make(rate=2))
    codes = [c.post("/a1/chat", json=Q, headers={"X-Real-Client-IP": f"203.0.113.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    other = verified(TestClient(app, base_url=BASE))  # a different visitor has their own allowance
    assert other.post("/a1/chat", json=Q).status_code == 200


def test_open_mode_without_turnstile_secrets(make):
    c = make(protected=False)
    assert c.get("/a1/sources").status_code == 200
    assert c.post("/session", json={"token": "x"}).json() == {"ok": True, "protected": False}
    assert c.get("/demo/a1/config.json").json()["turnstileSiteKey"] == ""


def test_config_json_comes_from_the_environment(make, monkeypatch):
    c = make()
    monkeypatch.setenv("DEMO_VIDEO_URL", "https://video.example/a1")
    cfg = c.get("/demo/a1/config.json").json()
    assert cfg["turnstileSiteKey"] == "site-key-123" and cfg["videoUrl"] == "https://video.example/a1"
    assert "arslandev009/a1-rag-docqa" in cfg["repoUrl"]


def test_ui_is_served_with_security_headers_and_root_redirects(make):
    c = make()
    page = c.get("/demo/a1/")
    assert page.status_code == 200 and "Ledgerly Support Assistant" in page.text
    assert "challenges.cloudflare.com" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert c.get("/demo/a1/app.js").status_code == 200 and c.get("/demo/a1/style.css").status_code == 200
    root = c.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/demo/a1/"


def test_without_turnstile_nothing_points_at_cloudflare(make):
    c = make(protected=False)
    page = c.get("/demo/a1/")
    assert "cloudflare" not in page.text.lower()
    assert "cloudflare" not in page.headers["content-security-policy"].lower()


def test_with_turnstile_the_csp_allows_cloudflare_and_the_page_still_has_no_script_tag_for_it(make):
    c = make(protected=True)
    page = c.get("/demo/a1/")
    assert "challenges.cloudflare.com" in page.headers["content-security-policy"]
    assert "<script src=\"https://challenges.cloudflare.com" not in page.text  # injected by app.js only when configured
