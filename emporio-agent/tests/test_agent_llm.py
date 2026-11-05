"""Smoke checks against the real model (Step 5 "Done when").

Marked `llm`: deselected by default. Run with `pytest -m llm` (needs ANTHROPIC_API_KEY).
The full scenario suite with grounding checks comes in Step 7.
"""

import os
from datetime import datetime

import pytest

from emporio.agent import Conversation, run_turn
from emporio.store_hours import TIMEZONE
from emporio.tools import ToolContext

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]

PRODUCT_AND_ORDER_TOOLS = {"search_products", "get_product", "get_order_status"}


def ask(message, now=datetime(2026, 3, 23, 10, 0), tmp_path=None):
    conversation = Conversation(
        tool_context=ToolContext(
            now=now.replace(tzinfo=TIMEZONE),
            outbox_path=(tmp_path / "outbox.jsonl") if tmp_path else ToolContext().outbox_path,
        )
    )
    result = run_turn(conversation, message)
    print(f"\n> {message}\n< {result.reply}\n  tools: {[c['name'] for c in result.tool_calls]}")
    return result


def tool_names(result):
    return [call["name"] for call in result.tool_calls]


def test_price_question_uses_get_product(tmp_path):
    result = ask("Quanto custa o Takamine GD20?", tmp_path=tmp_path)
    assert "get_product" in tool_names(result)
    assert "2.199,00" in result.reply


def test_returns_question_uses_get_policy(tmp_path):
    result = ask("Como funciona a troca ou devolução de um produto?", tmp_path=tmp_path)
    assert any(
        c["name"] == "get_policy" and c["arguments"].get("topic") == "returns"
        for c in result.tool_calls
    )


def test_sunday_question_uses_store_status(tmp_path):
    result = ask("Vocês estão abertos agora?", now=datetime(2026, 3, 22, 15, 0), tmp_path=tmp_path)
    assert "get_store_status" in tool_names(result)
    assert "fechad" in result.reply.lower()


def test_strings_are_redirected_without_tools(tmp_path):
    result = ask("Vocês vendem corda de violão?", tmp_path=tmp_path)
    assert tool_names(result) == []
    assert "instrumento" in result.reply.lower()


def test_off_topic_is_declined_without_product_tools(tmp_path):
    result = ask("Me ajuda a escrever um código em Python para ler um CSV?", tmp_path=tmp_path)
    assert not PRODUCT_AND_ORDER_TOOLS & set(tool_names(result))
    assert "import" not in result.reply and "def " not in result.reply
