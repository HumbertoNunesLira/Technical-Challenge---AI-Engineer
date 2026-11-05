import pytest
from fastapi.testclient import TestClient

from emporio.api import RateLimiter, app, get_agent_llm, get_rate_limiter, get_store
from emporio.llm import FakeLLM
from emporio.session import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_NOW", "2026-03-23T10:00:00")
    store = SessionStore(tmp_path / "sessions")
    llm = FakeLLM([])
    limiter = RateLimiter(limit=3, window_s=60)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_agent_llm] = lambda: llm
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as test_client:
        test_client.store, test_client.llm = store, llm
        yield test_client
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_chat_runs_the_agent_and_persists(client):
    client.llm.script = [[("get_product", {"name": "Takamine GD20"})], "Custa R$ 2.199,00."]
    response = client.post("/chat", json={"session_id": "5567999990000", "message": "GD20?"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Custa R$ 2.199,00."
    assert body["tool_calls"] == [{"name": "get_product", "arguments": {"name": "Takamine GD20"}}]
    assert "result" not in body["tool_calls"][0]  # no tool result data leaves the API
    assert [r["role"] for r in client.store.records("5567999990000")] == ["user", "assistant"]


def test_second_request_sees_history(client):
    client.llm.script = ["Olá!", "Você disse oi."]
    client.post("/chat", json={"session_id": "s1", "message": "Oi"})
    client.post("/chat", json={"session_id": "s1", "message": "O que eu disse?"})
    assert len(client.llm.requests[1]["messages"]) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s1", "message": ""},
        {"session_id": "s1", "message": "   "},
        {"session_id": "s1", "message": "x" * 1001},
        {"session_id": "../etc", "message": "oi"},
        {"message": "oi"},
    ],
)
def test_invalid_input_is_rejected(client, payload):
    assert client.post("/chat", json=payload).status_code == 422


def test_llm_failure_returns_502_and_stores_nothing(client):
    client.llm.script = []  # FakeLLM raises LLMError
    response = client.post("/chat", json={"session_id": "s2", "message": "Oi"})
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]
    assert client.store.records("s2") == []


def test_flood_is_rejected_before_calling_the_model(client):
    client.llm.script = ["1", "2", "3"]
    codes = [
        client.post("/chat", json={"session_id": "flood", "message": "oi"}).status_code
        for _ in range(5)
    ]
    assert codes == [200, 200, 200, 429, 429]
    assert len(client.llm.requests) == 3  # the rejected requests never reached the LLM
    rejected = client.post("/chat", json={"session_id": "flood", "message": "oi"})
    assert int(rejected.headers["Retry-After"]) >= 1
    # Another session is not affected.
    client.llm.script.append("ok")
    assert client.post("/chat", json={"session_id": "other", "message": "oi"}).status_code == 200


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = RateLimiter(limit=2, window_s=60, clock=lambda: now[0])
    assert [limiter.retry_after("a") for _ in range(3)] == [0, 0, 61]
    now[0] = 60.0
    assert limiter.retry_after("a") == 0  # the oldest hits left the window


def test_delete_session_resets_it(client):
    client.llm.script = ["Olá!"]
    client.post("/chat", json={"session_id": "s3", "message": "Oi"})
    assert client.delete("/sessions/s3").status_code == 204
    assert client.store.records("s3") == []
    assert client.delete("/sessions/..%2Fetc").status_code in (404, 422)
