"""Agent loop, prompt, config and the Anthropic adapter, all without network access."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from emporio import config
from emporio.agent import (
    FALLBACK_REPLY,
    MAX_ITERATIONS,
    REFUSAL_REPLY,
    Conversation,
    context_window,
    run_turn,
)
from emporio.llm import AnthropicLLM, FakeLLM, LLMError, LLMResponse, get_llm
from emporio.prompts import build_system_prompt
from emporio.store_hours import TIMEZONE
from emporio.tools import ToolContext


@pytest.fixture
def conversation(tmp_path):
    return Conversation(
        session_id="t1",
        tool_context=ToolContext(
            session_id="t1",
            now=datetime(2026, 3, 23, 10, 0, tzinfo=TIMEZONE),
            outbox_path=tmp_path / "outbox.jsonl",
        ),
    )


# --- The loop -----------------------------------------------------------------------------


def test_plain_reply_without_tools(conversation):
    llm = FakeLLM(["Olá! Como posso ajudar?"])
    result = run_turn(conversation, "Oi", llm=llm)
    assert result.reply == "Olá! Como posso ajudar?"
    assert result.tool_calls == [] and result.iterations == 1
    assert conversation.history == [
        {"role": "user", "content": "Oi"},
        {"role": "assistant", "content": "Olá! Como posso ajudar?"},
    ]


def test_tool_call_then_answer(conversation):
    llm = FakeLLM([[("get_product", {"name": "Takamine GD20"})], "Custa R$ 2.199,00."])
    result = run_turn(conversation, "Quanto custa o Takamine GD20?", llm=llm)

    assert [c["name"] for c in result.tool_calls] == ["get_product"]
    assert result.tool_calls[0]["result"]["list_price"] == "R$ 2.199,00"
    assert result.iterations == 2 and result.stop_reason == "end_turn"

    # The second request carries the assistant tool call and the matching tool result.
    second = llm.requests[1]["messages"]
    assert second[-2]["role"] == "assistant"
    tool_result = second[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == second[-2]["content"][0]["id"]
    assert json.loads(tool_result["content"])["product_id"] == 95


def test_parallel_tool_calls_return_in_one_message(conversation):
    llm = FakeLLM(
        [[("get_policy", {"topic": "returns"}), ("get_store_status", {})], "Pronto."]
    )
    run_turn(conversation, "Regras de troca? E vocês estão abertos?", llm=llm)
    results_message = llm.requests[1]["messages"][-1]
    assert results_message["role"] == "user"
    assert len(results_message["content"]) == 2


def test_tool_errors_go_back_to_the_model(conversation):
    llm = FakeLLM([[("get_policy", {"topic": "discounts"})], "Desculpe."])
    result = run_turn(conversation, "x", llm=llm)
    assert result.tool_calls[0]["result"]["error"] == "unknown_topic"
    assert llm.requests[1]["messages"][-1]["content"][0]["is_error"] is True
    assert result.reply == "Desculpe."


def test_loop_stops_after_max_iterations(conversation):
    llm = FakeLLM([[("get_store_status", {})]] * MAX_ITERATIONS)
    result = run_turn(conversation, "x", llm=llm)
    assert result.iterations == MAX_ITERATIONS
    assert result.stop_reason == "max_iterations"
    assert result.reply == FALLBACK_REPLY


def test_llm_failure_gives_fallback_and_keeps_history(conversation):
    llm = FakeLLM([])  # raises LLMError on the first call
    result = run_turn(conversation, "Oi", llm=llm)
    assert result.reply == FALLBACK_REPLY
    assert result.stop_reason.startswith("llm_error")
    assert conversation.history[-1] == {"role": "assistant", "content": FALLBACK_REPLY}


class RefusingLLM:
    def chat(self, system, messages, tools):
        return LLMResponse("", [], "refusal", {}, 0.0, {"role": "assistant", "content": ""})


def test_refusal_gets_a_polite_reply(conversation):
    assert run_turn(conversation, "x", llm=RefusingLLM()).reply == REFUSAL_REPLY


def test_history_carries_text_only_between_turns(conversation):
    llm = FakeLLM([[("get_product", {"name": "Takamine GD20"})], "R$ 2.199,00", "Por nada!"])
    run_turn(conversation, "Quanto custa o GD20?", llm=llm)
    run_turn(conversation, "Obrigado", llm=llm)
    third_request = llm.requests[2]["messages"]
    assert third_request == [
        {"role": "user", "content": "Quanto custa o GD20?"},
        {"role": "assistant", "content": "R$ 2.199,00"},
        {"role": "user", "content": "Obrigado"},
    ]


def test_context_window_keeps_last_messages_starting_with_user():
    history = []
    for i in range(15):
        history += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
    window = context_window(history, limit=5)
    assert window[0] == {"role": "user", "content": "u13"}
    assert len(window) == 4


# --- Prompt and config --------------------------------------------------------------------


def test_system_prompt_has_clock_and_store_facts():
    prompt = build_system_prompt(datetime(2026, 3, 22, 15, 0, tzinfo=TIMEZONE))
    assert "domingo, 22/03/2026, 15:00" in prompt
    assert "Rua 14 de Maio, 3200" in prompt
    assert "(67) 3341-4444" in prompt
    assert "contato@emporiodamusica.com.br" in prompt
    assert "Brazilian Portuguese" in prompt
    # Families come from the data: woodwinds, brass and orchestral strings have no products.
    assert "Violões" in prompt and "Ukuleles" in prompt
    assert "Sopro" not in prompt and "Orquestrais" not in prompt


def test_agent_now_from_environment(monkeypatch):
    monkeypatch.setenv("AGENT_NOW", "2026-03-22T15:00:00")
    now = config.agent_now()
    assert now.tzinfo is not None and now.hour == 15
    assert Conversation().tool_context.current_time() == now


def test_real_time_when_agent_now_is_empty(monkeypatch):
    monkeypatch.setenv("AGENT_NOW", "")
    assert config.agent_now() is None


def test_unsupported_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "cohere")
    with pytest.raises(ValueError):
        get_llm()


# --- Anthropic adapter with a stub client -------------------------------------------------


class StubMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_anthropic_adapter_request_and_parsing():
    content = [
        SimpleNamespace(type="thinking", thinking="", signature="sig"),
        SimpleNamespace(type="text", text="Vou verificar."),
        SimpleNamespace(type="tool_use", id="tu_1", name="get_product", input={"name": "gd20"}),
    ]
    response = SimpleNamespace(
        content=content,
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    stub = StubMessages(response)
    llm = AnthropicLLM(model="claude-haiku-5-5", effort="medium",
                       client=SimpleNamespace(messages=stub))

    result = llm.chat("system", [{"role": "user", "content": "oi"}], tools=[])

    assert stub.kwargs["model"] == "claude-haiku-5-5"
    assert stub.kwargs["output_config"] == {"effort": "medium"}
    assert "temperature" not in stub.kwargs  # Haiku 5.5 rejects sampling parameters
    assert result.text == "Vou verificar."
    assert result.tool_calls[0].name == "get_product"
    assert result.tool_calls[0].arguments == {"name": "gd20"}
    # Thinking blocks are kept, unchanged, in the message appended to the conversation.
    assert result.assistant_message["content"] is content


def test_llm_error_is_raised_for_exhausted_fake():
    with pytest.raises(LLMError):
        FakeLLM([]).chat("s", [], [])
