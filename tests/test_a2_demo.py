"""A2 demo routes (/a2/demo/*, /demo/a2/, operator-only upload router): no network, no database, no real LLM."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import a2_demo, guard
from app.main import app
from docqa.api import RateLimiter
from extractor.api import get_llm_dep
from extractor.schema import LineItem

# Two tiny PDFs (a one-page text invoice, a blank page = a "scan"), embedded so CI needs no PDF library.
INVOICE_PDF = base64.b64decode(
    ""
    "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBvYmoKPDwK"
    "L0YxIDIgMCBSCj4+CmVuZG9iagoyIDAgb2JqCjw8Ci9CYXNlRm9udCAvSGVsdmV0aWNhIC9FbmNvZGluZyAvV2luQW5zaUVu"
    "Y29kaW5nIC9OYW1lIC9GMSAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRl"
    "bnRzIDcgMCBSIC9NZWRpYUJveCBbIDAgMCAzMDAgMjAwIF0gL1BhcmVudCA2IDAgUiAvUmVzb3VyY2VzIDw8Ci9Gb250IDEg"
    "MCBSIC9Qcm9jU2V0IFsgL1BERiAvVGV4dCAvSW1hZ2VCIC9JbWFnZUMgL0ltYWdlSSBdCj4+IC9Sb3RhdGUgMCAvVHJhbnMg"
    "PDwKCj4+IAogIC9UeXBlIC9QYWdlCj4+CmVuZG9iago0IDAgb2JqCjw8Ci9QYWdlTW9kZSAvVXNlTm9uZSAvUGFnZXMgNiAw"
    "IFIgL1R5cGUgL0NhdGFsb2cKPj4KZW5kb2JqCjUgMCBvYmoKPDwKL0F1dGhvciAoYW5vbnltb3VzKSAvQ3JlYXRpb25EYXRl"
    "IChEOjIwMDAwMTAxMDAwMDAwKzAwJzAwJykgL0NyZWF0b3IgKGFub255bW91cykgL0tleXdvcmRzICgpIC9Nb2REYXRlIChE"
    "OjIwMDAwMTAxMDAwMDAwKzAwJzAwJykgL1Byb2R1Y2VyIChSZXBvcnRMYWIgUERGIExpYnJhcnkgLSBcKG9wZW5zb3VyY2Vc"
    "KSkgCiAgL1N1YmplY3QgKHVuc3BlY2lmaWVkKSAvVGl0bGUgKHVudGl0bGVkKSAvVHJhcHBlZCAvRmFsc2UKPj4KZW5kb2Jq"
    "CjYgMCBvYmoKPDwKL0NvdW50IDEgL0tpZHMgWyAzIDAgUiBdIC9UeXBlIC9QYWdlcwo+PgplbmRvYmoKNyAwIG9iago8PAov"
    "TGVuZ3RoIDQwMQo+PgpzdHJlYW0KMSAwIDAgMSAwIDAgY20gIEJUIC9GMSAxMiBUZiAxNC40IFRMIEVUCkJUIDEgMCAwIDEg"
    "MTAgMTgwIFRtIChBY21lIFRlc3QgQ28pIFRqIFQqIEVUCkJUIDEgMCAwIDEgMTAgMTU4IFRtIChJbnZvaWNlIE5vIFQtMTAw"
    "MSkgVGogVCogRVQKQlQgMSAwIDAgMSAxMCAxMzYgVG0gKERhdGUgMjAyNi0wMy0wMSkgVGogVCogRVQKQlQgMSAwIDAgMSAx"
    "MCAxMTQgVG0gKFdpZGdldCAyIHggMTAuMDAgMjAuMDApIFRqIFQqIEVUCkJUIDEgMCAwIDEgMTAgOTIgVG0gKFN1YnRvdGFs"
    "IDIwLjAwKSBUaiBUKiBFVApCVCAxIDAgMCAxIDEwIDcwIFRtIChUb3RhbCBVU0QgMjAuMDApIFRqIFQqIEVUCkJUIDEgMCAw"
    "IDEgMTAgNDggVG0gKFRoYW5rIHlvdSBmb3IgeW91ciBidXNpbmVzcyB3aXRoIHVzLikgVGogVCogRVQKIAplbmRzdHJlYW0K"
    "ZW5kb2JqCnhyZWYKMCA4CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDA2MSAwMDAwMCBuIAowMDAwMDAwMDkyIDAwMDAw"
    "IG4gCjAwMDAwMDAxOTkgMDAwMDAgbiAKMDAwMDAwMDM5MiAwMDAwMCBuIAowMDAwMDAwNDYwIDAwMDAwIG4gCjAwMDAwMDA3"
    "MjEgMDAwMDAgbiAKMDAwMDAwMDc4MCAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9JRCAKWzwxYzE3ODE5OGZiZGZhNTFiMjU5OTVk"
    "ODlkNDEwMjA0Mz48MWMxNzgxOThmYmRmYTUxYjI1OTk1ZDg5ZDQxMDIwNDM+XQolIFJlcG9ydExhYiBnZW5lcmF0ZWQgUERG"
    "IGRvY3VtZW50IC0tIGRpZ2VzdCAob3BlbnNvdXJjZSkKCi9JbmZvIDUgMCBSCi9Sb290IDQgMCBSCi9TaXplIDgKPj4Kc3Rh"
    "cnR4cmVmCjEyMzEKJSVFT0YK"
)

BLANK_PDF = base64.b64decode(
    ""
    "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBvYmoKPDwK"
    "L0YxIDIgMCBSCj4+CmVuZG9iagoyIDAgb2JqCjw8Ci9CYXNlRm9udCAvSGVsdmV0aWNhIC9FbmNvZGluZyAvV2luQW5zaUVu"
    "Y29kaW5nIC9OYW1lIC9GMSAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2JqCjMgMCBvYmoKPDwKL0NvbnRl"
    "bnRzIDcgMCBSIC9NZWRpYUJveCBbIDAgMCAzMDAgMjAwIF0gL1BhcmVudCA2IDAgUiAvUmVzb3VyY2VzIDw8Ci9Gb250IDEg"
    "MCBSIC9Qcm9jU2V0IFsgL1BERiAvVGV4dCAvSW1hZ2VCIC9JbWFnZUMgL0ltYWdlSSBdCj4+IC9Sb3RhdGUgMCAvVHJhbnMg"
    "PDwKCj4+IAogIC9UeXBlIC9QYWdlCj4+CmVuZG9iago0IDAgb2JqCjw8Ci9QYWdlTW9kZSAvVXNlTm9uZSAvUGFnZXMgNiAw"
    "IFIgL1R5cGUgL0NhdGFsb2cKPj4KZW5kb2JqCjUgMCBvYmoKPDwKL0F1dGhvciAoYW5vbnltb3VzKSAvQ3JlYXRpb25EYXRl"
    "IChEOjIwMDAwMTAxMDAwMDAwKzAwJzAwJykgL0NyZWF0b3IgKGFub255bW91cykgL0tleXdvcmRzICgpIC9Nb2REYXRlIChE"
    "OjIwMDAwMTAxMDAwMDAwKzAwJzAwJykgL1Byb2R1Y2VyIChSZXBvcnRMYWIgUERGIExpYnJhcnkgLSBcKG9wZW5zb3VyY2Vc"
    "KSkgCiAgL1N1YmplY3QgKHVuc3BlY2lmaWVkKSAvVGl0bGUgKHVudGl0bGVkKSAvVHJhcHBlZCAvRmFsc2UKPj4KZW5kb2Jq"
    "CjYgMCBvYmoKPDwKL0NvdW50IDEgL0tpZHMgWyAzIDAgUiBdIC9UeXBlIC9QYWdlcwo+PgplbmRvYmoKNyAwIG9iago8PAov"
    "TGVuZ3RoIDQyCj4+CnN0cmVhbQoxIDAgMCAxIDAgMCBjbSAgQlQgL0YxIDEyIFRmIDE0LjQgVEwgRVQKIAplbmRzdHJlYW0K"
    "ZW5kb2JqCnhyZWYKMCA4CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDA2MSAwMDAwMCBuIAowMDAwMDAwMDkyIDAwMDAw"
    "IG4gCjAwMDAwMDAxOTkgMDAwMDAgbiAKMDAwMDAwMDM5MiAwMDAwMCBuIAowMDAwMDAwNDYwIDAwMDAwIG4gCjAwMDAwMDA3"
    "MjEgMDAwMDAgbiAKMDAwMDAwMDc4MCAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9JRCAKWzwxYzE3ODE5OGZiZGZhNTFiMjU5OTVk"
    "ODlkNDEwMjA0Mz48MWMxNzgxOThmYmRmYTUxYjI1OTk1ZDg5ZDQxMDIwNDM+XQolIFJlcG9ydExhYiBnZW5lcmF0ZWQgUERG"
    "IGRvY3VtZW50IC0tIGRpZ2VzdCAob3BlbnNvdXJjZSkKCi9JbmZvIDUgMCBSCi9Sb290IDQgMCBSCi9TaXplIDgKPj4Kc3Rh"
    "cnR4cmVmCjg3MQolJUVPRgo="
)


GOOD = dict(vendor_name="Acme Test Co", invoice_number="T-1001", invoice_date="2026-03-01", currency="USD",
            line_items=[LineItem(description="Widget", quantity=2, unit_price=10, amount=20)],
            subtotal=20, tax_amount=0, total=20)


class FakeLLM:
    def __init__(self) -> None:
        self.calls, self.data, self.fail = 0, dict(GOOD), False

    def chat_json(self, messages, model, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("secret internal detail")
        return model(**self.data)


class FakeBudget:
    def __init__(self) -> None:
        self.added, self.full = [], False

    def exhausted(self) -> bool:
        return self.full

    def add(self, n: int) -> None:
        self.added.append(n)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("TURNSTILE_SECRET", "SESSION_SECRET", "OPERATOR_TOKEN", "GATEWAY_SECRET", "A2_CACHE_TTL_S",
                 "A2_RATE_LIMIT_PER_MIN", "DEFAULT_CURRENCY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "invoice_t1.pdf").write_bytes(INVOICE_PDF)
    (tmp_path / "scan_t1.pdf").write_bytes(BLANK_PDF)
    (tmp_path / "samples.json").write_text(json.dumps([
        {"file": "invoice_t1.pdf", "title": "Test invoice", "shows": "a text invoice", "expected_review": False},
        {"file": "scan_t1.pdf", "title": "Test scan", "shows": "no text layer", "expected_review": True},
    ]), encoding="utf-8")
    monkeypatch.setattr(a2_demo, "SAMPLES_DIR", tmp_path)
    a2_demo.load_samples.cache_clear()
    a2_demo._cache.clear()

    llm, budget = FakeLLM(), FakeBudget()
    state = SimpleNamespace(limiter=RateLimiter(100))
    runtime = SimpleNamespace(budget=budget, config=SimpleNamespace(default_provider="fake"))
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[a2_demo.get_a2_runtime] = lambda: runtime
    app.dependency_overrides[a2_demo.get_a2_limiter] = lambda: state.limiter
    app.dependency_overrides[a2_demo.get_llm_factory] = lambda: (lambda provider: llm)
    app.dependency_overrides[get_llm_dep] = lambda: llm
    yield SimpleNamespace(c=TestClient(app), llm=llm, budget=budget, state=state, mp=monkeypatch)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)
    a2_demo.load_samples.cache_clear()
    a2_demo._cache.clear()


def extract(env, sample="invoice_t1", **kwargs):
    return env.c.post("/a2/demo/extract", json={"sample": sample}, **kwargs)


def test_samples_are_listed(env):
    r = env.c.get("/a2/demo/samples")
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()}
    assert set(items) == {"invoice_t1", "scan_t1"}
    assert items["scan_t1"]["expected_review"] is True and items["invoice_t1"]["expected_review"] is False
    assert items["invoice_t1"]["image"] == "/demo/a2/samples/invoice_t1.png"
    assert items["invoice_t1"]["pdf"] == "/demo/a2/samples/invoice_t1.pdf"


def test_extract_returns_the_checked_result_and_caches_it(env):
    r = extract(env)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is False and body["llm_calls"] == 1 and body["provider"] == "fake"
    assert body["result"]["extraction"]["total"] == 20 and body["result"]["needs_review"] is False
    assert env.llm.calls == 1 and env.budget.added == [1]
    again = extract(env).json()
    assert again["cached"] is True and again["result"] == body["result"]
    assert env.llm.calls == 1 and env.budget.added == [1]  # the cache costs nothing


def test_a_wrong_model_answer_is_flagged_not_passed_on(env):
    env.llm.data["total"] = 25  # the model "reads" a total that does not match the printed lines
    body = extract(env).json()
    assert body["result"]["needs_review"] is True
    assert any(i["code"] == "total_mismatch" for i in body["result"]["issues"])


def test_a_scan_never_reaches_the_model_or_the_budget(env):
    r = extract(env, "scan_t1")
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["needs_review"] is True and body["result"]["extraction"] is None
    assert any(i["code"] == "scanned_pdf" for i in body["result"]["issues"])
    assert body["llm_calls"] == 0 and env.llm.calls == 0 and env.budget.added == [0]


def test_unknown_and_malformed_sample_ids_are_rejected(env):
    assert extract(env, "nope_t1").status_code == 404
    for bad in ("../x", "UPPER", "a b", "", "x" * 65):
        assert extract(env, bad).status_code == 422, bad
    assert env.llm.calls == 0


def test_per_session_rate_limit(env):
    env.state.limiter = RateLimiter(2)
    assert extract(env).status_code == 200
    assert extract(env).status_code == 200
    assert extract(env).status_code == 429


def test_budget_exhausted_gives_503_but_cached_results_are_still_served(env):
    assert extract(env).status_code == 200
    env.budget.full = True
    assert extract(env).json()["cached"] is True
    r = extract(env, "scan_t1")  # not cached yet
    assert r.status_code == 503 and "budget" in r.json()["detail"]
    assert env.budget.added == [1]


def test_llm_failure_is_a_502_without_internal_text_and_is_not_cached(env):
    env.llm.fail = True
    r = extract(env)
    assert r.status_code == 502
    assert "secret" not in r.text and "RuntimeError" not in r.text
    assert env.budget.added == [1] and not a2_demo._cache
    env.llm.fail = False
    ok = extract(env)
    assert ok.status_code == 200 and ok.json()["cached"] is False


def test_unexpected_exception_is_a_generic_503(env):
    def boom(*args, **kwargs):
        raise ValueError("secret path /etc/passwd")

    env.mp.setattr(a2_demo, "process_pdf", boom)
    r = extract(env)
    assert r.status_code == 503 and "secret" not in r.text and "passwd" not in r.text
    assert not a2_demo._cache


def test_cache_can_be_switched_off(env):
    env.mp.setenv("A2_CACHE_TTL_S", "0")
    extract(env)
    assert extract(env).json()["cached"] is False
    assert env.llm.calls == 2


def test_upload_router_is_operator_only(env):
    files = {"file": ("invoice.pdf", INVOICE_PDF, "application/pdf")}
    assert env.c.post("/a2/extract", files=files).status_code == 404
    assert env.c.get("/a2/health").status_code == 404
    env.mp.setenv("OPERATOR_TOKEN", "op-token")
    assert env.c.post("/a2/extract", files=files, headers={"X-Operator-Token": "wrong"}).status_code == 404
    r = env.c.post("/a2/extract", files=files, headers={"X-Operator-Token": "op-token"})
    assert r.status_code == 200 and r.json()["extraction"]["vendor_name"] == "Acme Test Co"
    assert env.c.get("/a2/health", headers={"X-Operator-Token": "op-token"}).status_code == 200


def test_visitor_session_is_required_when_protection_is_on(env):
    env.mp.setenv("TURNSTILE_SECRET", "t-secret")
    env.mp.setenv("SESSION_SECRET", "s-secret")
    env.mp.setenv("OPERATOR_TOKEN", "op-token")
    assert env.c.get("/a2/demo/samples").status_code == 401
    assert extract(env).status_code == 401 and env.llm.calls == 0
    env.c.cookies.set(guard.COOKIE, guard.new_session_value())
    assert env.c.get("/a2/demo/samples").status_code == 200
    assert extract(env).status_code == 200
    env.c.cookies.clear()
    assert env.c.get("/a2/demo/samples", headers={"X-Operator-Token": "op-token"}).status_code == 200


def test_ui_and_config_are_served_with_the_security_headers(env):
    page = env.c.get("/demo/a2/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "default-src 'self'" in page.headers["content-security-policy"]
    cfg = env.c.get("/demo/a2/config.json")
    assert cfg.status_code == 200 and {"turnstileSiteKey", "repoUrl", "readmeUrl", "videoUrl"} <= cfg.json().keys()
    assert env.c.get("/demo/a2/app.js").status_code == 200
    assert env.c.get("/demo/a1/config.json").status_code == 200  # A1 is untouched
