import json
from datetime import datetime

import pytest

from emporio.agent import run_turn
from emporio.cli import main, run_chat
from emporio.llm import FakeLLM
from emporio.session import InvalidSessionId, SessionStore
from emporio.store_hours import TIMEZONE
from emporio.tools import ToolContext


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions")


def context(tmp_path):
    return ToolContext(
        now=datetime(2026, 3, 23, 10, 0, tzinfo=TIMEZONE), outbox_path=tmp_path / "out.jsonl"
    )


def chat_turn(store, session_id, message, llm, tmp_path):
    conversation = store.load(session_id, tool_context=context(tmp_path))
    result = run_turn(conversation, message, llm=llm)
    store.append_turn(session_id, message, result)
    return result


# --- SessionStore -------------------------------------------------------------------------


def test_history_survives_a_restart(store, tmp_path):
    llm = FakeLLM(["Prazer, Ana!", "Temos sim.", "Você se chama Ana."])
    chat_turn(store, "s1", "Oi, sou a Ana", llm, tmp_path)
    chat_turn(store, "s1", "Vocês têm violões?", llm, tmp_path)

    # "Restart": a brand-new store object reading the same directory.
    restarted = SessionStore(store.directory)
    chat_turn(restarted, "s1", "Qual é o meu nome?", llm, tmp_path)

    sent = llm.requests[2]["messages"]
    assert [m["content"] for m in sent] == [
        "Oi, sou a Ana",
        "Prazer, Ana!",
        "Vocês têm violões?",
        "Temos sim.",
        "Qual é o meu nome?",
    ]


def test_jsonl_records_log_tool_calls(store, tmp_path):
    llm = FakeLLM([[("get_product", {"name": "Takamine GD20"})], "R$ 2.199,00"])
    chat_turn(store, "s2", "Preço do GD20?", llm, tmp_path)
    lines = store.path("s2").read_text(encoding="utf-8").splitlines()
    user, assistant = (json.loads(line) for line in lines)
    assert user["role"] == "user" and user["content"] == "Preço do GD20?"
    assert assistant["content"] == "R$ 2.199,00"
    assert assistant["tool_calls"][0]["name"] == "get_product"
    assert {"timestamp", "usage", "stop_reason"} <= assistant.keys()


def test_only_role_and_content_reach_the_model(store, tmp_path):
    llm = FakeLLM([[("get_store_status", {})], "Aberta.", "Ok"])
    chat_turn(store, "s3", "Abertos?", llm, tmp_path)
    history = store.load("s3", tool_context=context(tmp_path)).history
    assert all(set(message) == {"role", "content"} for message in history)


def test_reset_clears_the_session(store, tmp_path):
    chat_turn(store, "s4", "Oi", FakeLLM(["Olá!"]), tmp_path)
    store.reset("s4")
    assert not store.path("s4").exists()
    assert store.load("s4").history == []


def test_verification_lockout_survives_a_restart(store, tmp_path):
    wrong = [("get_order_status", {"order_id": 5, "verification": "x@y.com"})]
    llm = FakeLLM([wrong, "Não confere.", wrong, "Não confere.", wrong, "Não confere."])
    for _ in range(3):
        chat_turn(store, "s5", "Pedido 5, x@y.com", llm, tmp_path)
    reloaded = store.load("s5", tool_context=context(tmp_path))
    assert reloaded.tool_context.failed_verifications == 3


@pytest.mark.parametrize("bad_id", ["../etc", "a/b", "", "x" * 65, "nome com espaço"])
def test_unsafe_session_ids_are_rejected(store, bad_id):
    with pytest.raises(InvalidSessionId):
        store.path(bad_id)


# --- CLI ----------------------------------------------------------------------------------


def scripted_input(lines):
    iterator = iter(lines)

    def read(prompt):
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError from None

    return read


def test_cli_chat_reset_and_quit(store, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_NOW", "2026-03-23T10:00:00")
    output = []
    llm = FakeLLM([[("get_store_status", {})], "Estamos abertos!", "Olá de novo!"])
    run_chat(
        "cli1",
        llm=llm,
        store=store,
        read=scripted_input(["Vocês estão abertos?", "/reset", "Oi", "/quit", "nunca lido"]),
        write=output.append,
        show_tools=True,
    )
    text = "\n".join(output)
    assert "Empório: Estamos abertos!" in text
    assert "[tool] get_store_status" in text
    assert "Conversa apagada" in text
    assert "Empório: Olá de novo!" in text
    # After /reset only the last exchange is stored.
    assert [r["content"] for r in store.records("cli1")] == ["Oi", "Olá de novo!"]
    assert text.endswith("Até logo!")


def test_cli_resumes_and_says_so(store, tmp_path):
    chat_turn(store, "cli2", "Oi", FakeLLM(["Olá!"]), tmp_path)
    output = []
    run_chat("cli2", llm=FakeLLM([]), store=store, read=scripted_input([]), write=output.append)
    assert any("Retomando conversa com 2 mensagens" in line for line in output)


def test_main_rejects_unsafe_session_id(capsys):
    assert main(["chat", "../hack"]) == 2
    assert "Session id" in capsys.readouterr().out
