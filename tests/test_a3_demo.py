"""A3 demo routes (/a3/demo/*, /demo/a3/): fake model, no network, no database, no real LLM."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import a3_demo, guard
from app.main import app
from clinicagent.demo_core import DemoSessions
from docqa.api import RateLimiter

WED = datetime(2026, 10, 14, 8, 0)


def tc(name, args, cid="c1"):
    return NS(id=cid, function=NS(name=name, arguments=json.dumps(args)))


def resp(content="", calls=None):
    msg = NS(content=content, tool_calls=calls or None)
    return NS(text=content, raw=NS(choices=[NS(message=msg)]))


class FakeLLM:
    provider, model = "fake", "fake-1"

    def __init__(self) -> None:
        self.script: list = []
        self.calls = 0
        self.error: Exception | None = None

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.script.pop(0) if self.script else resp("Hello! How can I help?")


class FakeBudget:
    def __init__(self) -> None:
        self.added, self.full = 0, False

    def exhausted(self) -> bool:
        return self.full

    def add(self, n: int) -> None:
        self.added += n


@pytest.fixture
def env(monkeypatch):
    for name in ("TURNSTILE_SECRET", "SESSION_SECRET", "OPERATOR_TOKEN", "GATEWAY_SECRET", "A3_DAILY_CALLS", "A3_PROVIDERS"):
        monkeypatch.delenv(name, raising=False)
    a3_demo._calls.day, a3_demo._calls.used = "", 0
    llm, budget = FakeLLM(), FakeBudget()
    sessions = DemoSessions(llm, charge=a3_demo.make_charge(budget), now=lambda: WED, use_judge=False, retries=0)
    state = NS(limiter=RateLimiter(100), glimiter=RateLimiter(1000))
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[a3_demo.get_a3_sessions] = lambda: sessions
    app.dependency_overrides[a3_demo.get_a3_limiter] = lambda: state.limiter
    app.dependency_overrides[a3_demo.get_a3_global_limiter] = lambda: state.glimiter
    yield NS(c=TestClient(app), llm=llm, budget=budget, sessions=sessions, state=state, mp=monkeypatch)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def chat(c, text="hi"):
    return c.post("/a3/demo/chat", json={"message": text})


def key_of(c) -> str:
    return "cookie:" + c.cookies.get(a3_demo.COOKIE)


def test_info_lists_the_fictional_sample_patients(env):
    r = env.c.get("/a3/demo/info")
    assert r.status_code == 200
    body = r.json()
    assert {p["name"] for p in body["samples"]} == {"Maria Lopez", "Ben Cole"} and body["max_messages"] == 40


def test_chat_returns_reply_and_a_trace_with_names_only(env):
    env.llm.script = [resp(None, [tc("get_clinic_info", {"query": "parking"})]), resp("Yes, there is parking.")]
    r = chat(env.c, "Do you have parking?")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Yes, there is parking." and body["turns_left"] == 39
    assert body["debug"]["tool_calls"] == [{"name": "get_clinic_info", "ok": True}]
    assert "parking" not in json.dumps(body["debug"])
    assert env.budget.added == 2  # every model call is charged to the shared budget


def test_visitors_are_isolated_without_turnstile(env):
    other = TestClient(app)
    chat(env.c)
    chat(other)
    assert key_of(env.c) != key_of(other)
    env.sessions._get(key_of(env.c)).tools.conn.execute("UPDATE appointments SET status='cancel_pending' WHERE id=1")
    env.sessions._get(key_of(env.c)).tools.conn.commit()
    assert [p["appointment_id"] for p in env.c.get("/a3/demo/approvals").json()["pending"]] == [1]
    assert other.get("/a3/demo/approvals").json()["pending"] == []
    assert other.post("/a3/demo/approvals/1/approve").status_code == 409  # their own #1 is a plain booking, not a request
    assert env.sessions._get(key_of(other)).tools.state()[0]["status"] == "booked"


def test_front_desk_approve_and_reject_work_on_own_session_only(env):
    chat(env.c)
    tools = env.sessions._get(key_of(env.c)).tools
    tools.conn.execute("UPDATE appointments SET status='cancel_pending' WHERE id IN (1, 2)")
    tools.conn.commit()
    assert env.c.post("/a3/demo/approvals/1/approve").status_code == 200
    assert env.c.post("/a3/demo/approvals/2/reject").status_code == 200
    assert env.c.get("/a3/demo/approvals").json()["pending"] == []
    assert env.c.post("/a3/demo/approvals/1/approve").status_code in (404, 409)  # already decided


def test_reset_forgets_the_conversation_and_the_calendar(env):
    chat(env.c)
    assert len(env.sessions) == 1
    assert env.c.post("/a3/demo/reset").json() == {"ok": True}
    assert len(env.sessions) == 0


@pytest.mark.parametrize("bad", ["", "x" * 1001])
def test_bad_messages_never_reach_the_model(env, bad):
    assert chat(env.c, bad).status_code == 422
    assert env.llm.calls == 0


def test_per_visitor_rate_limit_and_global_rate_limit(env):
    env.state.limiter = RateLimiter(2)
    assert chat(env.c).status_code == 200 and chat(env.c).status_code == 200
    assert chat(env.c).status_code == 429
    env.state.limiter, env.state.glimiter = RateLimiter(100), RateLimiter(1)
    assert chat(TestClient(app)).status_code == 200
    assert chat(TestClient(app)).status_code == 429


def test_shared_budget_used_up_gives_503_before_any_model_call(env):
    env.budget.full = True
    r = chat(env.c)
    assert r.status_code == 503 and "budget" in r.json()["detail"]
    assert env.llm.calls == 0 and env.budget.added == 0


def test_a3_own_daily_cap_stops_the_demo(env):
    env.mp.setenv("A3_DAILY_CALLS", "1")
    assert chat(env.c).status_code == 200
    r = chat(env.c)
    assert r.status_code == 503 and "budget" in r.json()["detail"]
    assert env.llm.calls == 1


def test_provider_quota_error_is_a_friendly_503_without_internal_text(env):
    class Quota(Exception):
        status_code = 429

    env.llm.error = Quota("secret key AIzaSyXXXX quota exceeded")
    r = chat(env.c)
    assert r.status_code == 503 and "busy" in r.json()["detail"]
    assert "secret" not in r.text and "AIza" not in r.text


def test_unexpected_failure_is_a_generic_503(env):
    env.llm.error = RuntimeError("secret path /etc/passwd")
    r = chat(env.c)
    assert r.status_code == 503 and "passwd" not in r.text and "secret" not in r.text


def test_stats_are_operator_only_and_show_memory_fields(env):
    assert env.c.get("/a3/demo/stats").status_code == 404
    env.mp.setenv("OPERATOR_TOKEN", "op-token")
    assert env.c.get("/a3/demo/stats", headers={"X-Operator-Token": "wrong"}).status_code == 404
    r = env.c.get("/a3/demo/stats", headers={"X-Operator-Token": "op-token"})
    assert r.status_code == 200 and {"sessions", "max_sessions", "a3_calls_today"} <= r.json().keys()


def test_visitor_session_is_required_when_protection_is_on_and_is_the_visitor_key(env):
    env.mp.setenv("TURNSTILE_SECRET", "t-secret")
    env.mp.setenv("SESSION_SECRET", "s-secret")
    env.mp.setenv("OPERATOR_TOKEN", "op-token")
    assert env.c.get("/a3/demo/info").status_code == 401
    assert chat(env.c).status_code == 401 and env.llm.calls == 0
    value = guard.new_session_value()
    env.c.cookies.set(guard.COOKIE, value)
    assert chat(env.c).status_code == 200
    assert a3_demo.COOKIE not in env.c.cookies  # the verified session id is the key; no second cookie
    assert env.sessions._sessions.keys() == {f"session:{value.split('.')[0]}"}
    env.c.cookies.clear()
    assert env.c.get("/a3/demo/info", headers={"X-Operator-Token": "op-token"}).status_code == 200


def test_a_client_cannot_choose_its_own_key_with_a_header(env):
    chat(env.c)
    r = env.c.post("/a3/demo/chat", json={"message": "hi"}, headers={"X-Real-Client-IP": "session:someone-else"})
    assert r.status_code == 200
    assert all(not k.startswith("session:") for k in env.sessions._sessions)


def test_ui_and_config_are_served_with_the_security_headers(env):
    page = env.c.get("/demo/a3/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert "<script>" not in page.text and "fonts.googleapis" not in page.text  # the strict CSP allows neither
    cfg = env.c.get("/demo/a3/config.json")
    assert cfg.status_code == 200 and {"turnstileSiteKey", "repoUrl", "readmeUrl", "videoUrl"} <= cfg.json().keys()
    assert env.c.get("/demo/a3/app.js").status_code == 200 and env.c.get("/demo/a3/style.css").status_code == 200
    assert env.c.get("/demo/a1/config.json").status_code == 200 and env.c.get("/demo/a2/config.json").status_code == 200


def test_the_faq_is_packaged_with_the_installed_clinicagent():
    from clinicagent.tools import DEFAULT_FAQ

    assert DEFAULT_FAQ.exists(), "pip-installed clinicagent must ship data/clinic_faq.md"


def test_the_agent_uses_the_installed_llm_client_result_shape():
    """The real llm-client ChatResult must carry .text and .raw.choices[0].message, which the A3 agent reads."""
    import dataclasses

    from llm_client.client import ChatResult

    assert {"text", "raw"} <= {f.name for f in dataclasses.fields(ChatResult)}


def test_provider_selection_single_and_failover(monkeypatch):
    from clinicagent.failover import FailoverLLM

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "m-gemini")
    monkeypatch.setenv("GROQ_MODEL", "m-groq")
    a3_demo._llm_for.cache_clear()
    monkeypatch.delenv("A3_PROVIDERS", raising=False)
    assert a3_demo.build_llm("gemini").provider == "gemini"
    monkeypatch.setenv("A3_PROVIDERS", "groq,gemini")
    llm = a3_demo.build_llm("gemini")
    assert isinstance(llm, FailoverLLM) and [x.provider for x in llm.llms] == ["groq", "gemini"]
    a3_demo._llm_for.cache_clear()
