"""A2 fallback model: llm-client retries the main model, then asks the fallback; the demo says which one answered.
No network: both models are fake OpenAI clients."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import llm_client.client as llm_client_mod
import openai
import pytest
from fastapi.testclient import TestClient

from app import a2_demo
from app.main import app
from docqa.api import RateLimiter
from extractor.api import get_llm_dep
from extractor.schema import InvoiceExtraction


def rate_limited() -> Exception:
    return openai.RateLimitError("rate limited", response=httpx.Response(429, request=httpx.Request("POST", "http://test")), body=None)


class FakeClient:
    """Stands in for openai.OpenAI: raises `exc` on every call, or answers with `text`."""

    def __init__(self, exc: Exception | None = None, text: str = "{}") -> None:
        self.calls, self.exc, self.text = 0, exc, text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        message = SimpleNamespace(content=self.text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ("A2_FALLBACK_MODEL", "A2_FALLBACK_PROVIDER", "A2_CACHE_TTL_S", "LLM_LOG_PATH", "TURNSTILE_SECRET", "SESSION_SECRET", "GATEWAY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "main-model")
    monkeypatch.setattr(llm_client_mod.time, "sleep", lambda seconds: None)  # no real backoff waits
    a2_demo._llm_for.cache_clear()
    a2_demo._cache.clear()
    yield
    a2_demo._llm_for.cache_clear()
    a2_demo._cache.clear()


def test_without_a_fallback_setting_nothing_changes():
    llm = a2_demo._llm_for("gemini")
    assert llm.fallback is None and llm.max_retries == 3 and llm.model == "main-model"


def test_a_fallback_model_is_built_and_the_main_model_retries_only_once(monkeypatch):
    monkeypatch.setenv("A2_FALLBACK_MODEL", "fallback-model")
    llm = a2_demo._llm_for("gemini")
    assert llm.fallback is not None and llm.fallback.model == "fallback-model" and llm.fallback.provider == "gemini"
    assert llm.max_retries == 1 and llm.model == "main-model"


def test_a_broken_fallback_setting_does_not_take_the_main_model_down(monkeypatch):
    monkeypatch.setenv("A2_FALLBACK_PROVIDER", "no-such-provider")
    llm = a2_demo._llm_for("gemini")
    assert llm.fallback is None and llm.model == "main-model"


def test_when_the_main_model_is_rate_limited_the_fallback_answers(monkeypatch):
    monkeypatch.setenv("A2_FALLBACK_MODEL", "fallback-model")
    llm = a2_demo._llm_for("gemini")
    llm._client, llm.fallback._client = FakeClient(exc=rate_limited()), FakeClient(text="{}")
    a2_demo._used_fallback.set(False)
    result = llm.chat_json([{"role": "user", "content": "x"}], InvoiceExtraction)
    assert isinstance(result, InvoiceExtraction)
    assert llm._client.calls == 2 and llm.fallback._client.calls == 1  # one try and one retry on the main model, then the fallback
    assert a2_demo._used_fallback.get() is True


def test_a_healthy_main_model_never_touches_the_fallback(monkeypatch):
    monkeypatch.setenv("A2_FALLBACK_MODEL", "fallback-model")
    llm = a2_demo._llm_for("gemini")
    llm._client, llm.fallback._client = FakeClient(text="{}"), FakeClient(text="{}")
    a2_demo._used_fallback.set(False)
    llm.chat_json([{"role": "user", "content": "x"}], InvoiceExtraction)
    assert llm.fallback._client.calls == 0 and a2_demo._used_fallback.get() is False


def test_the_demo_route_reports_which_model_answered(monkeypatch):
    monkeypatch.setenv("A2_FALLBACK_MODEL", "fallback-model")
    monkeypatch.setenv("A2_CACHE_TTL_S", "0")
    llm = a2_demo._llm_for("gemini")
    llm._client, llm.fallback._client = FakeClient(exc=rate_limited()), FakeClient(text="{}")
    budget = SimpleNamespace(added=[], exhausted=lambda: False)
    budget.add = budget.added.append
    runtime = SimpleNamespace(budget=budget, config=SimpleNamespace(default_provider="gemini"))
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[a2_demo.get_a2_runtime] = lambda: runtime
    app.dependency_overrides[a2_demo.get_a2_limiter] = lambda: RateLimiter(100)
    app.dependency_overrides[a2_demo.get_llm_factory] = lambda: (lambda provider: llm)
    app.dependency_overrides[get_llm_dep] = lambda: llm
    try:
        r = TestClient(app).post("/a2/demo/extract", json={"sample": "easy_inv_00"})  # a real bundled sample (text invoice)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["fallback_used"] is True and body["llm_calls"] >= 1
        assert llm.fallback._client.calls >= 1
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def test_the_fallback_flag_does_not_leak_from_an_earlier_request(monkeypatch):
    monkeypatch.setenv("A2_FALLBACK_MODEL", "fallback-model")
    monkeypatch.setenv("A2_CACHE_TTL_S", "0")
    llm = a2_demo._llm_for("gemini")
    llm._client, llm.fallback._client = FakeClient(text="{}"), FakeClient(text="{}")  # a healthy main model
    budget = SimpleNamespace(added=[], exhausted=lambda: False)
    budget.add = budget.added.append
    runtime = SimpleNamespace(budget=budget, config=SimpleNamespace(default_provider="gemini"))
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[a2_demo.get_a2_runtime] = lambda: runtime
    app.dependency_overrides[a2_demo.get_a2_limiter] = lambda: RateLimiter(100)
    app.dependency_overrides[a2_demo.get_llm_factory] = lambda: (lambda provider: llm)
    try:
        a2_demo._used_fallback.set(True)  # as if an earlier request on this thread had used the fallback
        r = TestClient(app).post("/a2/demo/extract", json={"sample": "easy_inv_00"})
        assert r.status_code == 200 and r.json()["fallback_used"] is False
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
