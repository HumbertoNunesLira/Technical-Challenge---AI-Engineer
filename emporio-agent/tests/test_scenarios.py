"""Scenario suite (marker `llm`) and unit tests for its checks (always run)."""

import importlib.util
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_scenarios.py"
spec = importlib.util.spec_from_file_location("run_scenarios", SCRIPT)
runner = importlib.util.module_from_spec(spec)
sys.modules["run_scenarios"] = runner  # dataclasses look the module up while loading
spec.loader.exec_module(runner)


# --- Checks (no network) ------------------------------------------------------------------


def test_amounts_are_parsed_in_brazilian_format():
    text = "De R$ 2.199,00 por R$2089,05, ou R$ 35 de frete e R$ 1.000."
    assert runner.amounts_in(text) == {
        Decimal("2199.00"), Decimal("2089.05"), Decimal("35.00"), Decimal("1000.00")
    }


def test_amount_from_tool_result_is_grounded():
    reply = "Sai por R$ 439,20 no cartão."
    assert runner.ungrounded_amounts(reply, "", [{"final_price": "R$ 439,20"}]) == set()


def test_invented_amount_is_flagged():
    reply = "Sai por R$ 399,00."
    assert runner.ungrounded_amounts(reply, "", [{"final_price": "R$ 439,20"}]) == {
        Decimal("399.00")
    }


def test_customer_and_policy_amounts_are_allowed():
    reply = "Até R$ 1000 temos opções; frete grátis acima de R$ 500,00."
    assert runner.ungrounded_amounts(reply, "Violões até R$1000?", []) == set()


def test_tracking_code_before_verification_is_a_leak():
    assert runner.leaked_order_fields("Código BRJL5544332BR", verified=False) == {"BRJL5544332BR"}
    assert runner.leaked_order_fields("Código BRJL5544332BR", verified=True) == set()


def test_english_reply_is_flagged():
    assert runner.english_words("No, no saxofone in our catalog right now") == {"our", "right now"}
    assert runner.english_words("Não temos saxofone no catálogo agora.") == set()
    assert runner.english_words("Music Man Bass 1X Electric Bass, Black Friday") == set()


def test_forbid_all_tools():
    turn = {"user": "x", "forbid_tools": ["all"]}
    assert runner.check_turn(turn, "ok", ["get_policy"], [], False) == [
        "forbidden tool get_policy was called"
    ]


def test_every_scenario_is_well_formed():
    scenarios = runner.load_scenarios()
    assert len(scenarios) >= 14
    assert len({s["id"] for s in scenarios}) == len(scenarios)
    for scenario in scenarios:
        assert scenario["turns"] and all("user" in t for t in scenario["turns"])


# --- The suite against the real model -----------------------------------------------------


@pytest.mark.llm
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("scenario", runner.load_scenarios(), ids=lambda s: s["id"])
def test_scenario(scenario):
    result = runner.run_scenario(scenario)
    assert result.passed, runner.format_transcript(result)
