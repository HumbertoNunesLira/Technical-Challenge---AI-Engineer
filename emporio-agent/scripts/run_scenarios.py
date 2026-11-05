"""Run the scripted scenarios in tests/scenarios.yaml against the real model.

Usage: python scripts/run_scenarios.py [SCENARIO_ID ...]

Prints a pass/fail table and, for each failure, the full transcript with tool calls.
Exit code 1 if any scenario fails. Also used by tests/test_scenarios.py (marker `llm`).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml

from emporio.agent import Conversation, run_turn
from emporio.data import get_data
from emporio.store_hours import to_local
from emporio.tools import TOOL_FUNCTIONS, ToolContext

SCENARIOS_PATH = Path(__file__).resolve().parents[1] / "tests" / "scenarios.yaml"
DEFAULT_NOW = "2026-03-23T10:00:00"

# Policy amounts the agent may state without a product/order tool (shipping fee and
# threshold, installment minimums, payment-combination threshold).
ALLOWED_AMOUNTS = {Decimal(v) for v in ("35", "50", "80", "100", "500", "2000")}
AMOUNT = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)")


def amounts_in(text: str) -> set[Decimal]:
    """All R$ amounts in a text, as Decimals: 'R$ 2.199,00' -> Decimal('2199.00')."""
    return {
        Decimal(raw.replace(".", "").replace(",", ".")).quantize(Decimal("0.01"))
        for raw in AMOUNT.findall(text)
    }


def ungrounded_amounts(reply: str, user_message: str, tool_results: list[dict]) -> set[Decimal]:
    """Invariant 1: amounts in the reply that no tool result, customer text or policy explains."""
    grounded = amounts_in(user_message) | {a.quantize(Decimal("0.01")) for a in ALLOWED_AMOUNTS}
    for result in tool_results:
        grounded |= amounts_in(json.dumps(result, ensure_ascii=False))
    return amounts_in(reply) - grounded


def tracking_codes() -> set[str]:
    return set(get_data().orders["tracking_code"].dropna())


def leaked_order_fields(reply: str, verified: bool) -> set[str]:
    """Invariant 2: before a successful verification, no tracking code may appear."""
    if verified:
        return set()
    return {code for code in tracking_codes() if code in reply}


# Invariant 3: common English words that never appear in a Portuguese reply or a product name.
ENGLISH_WORDS = re.compile(r"\b(the|our|we|you|your|right now|please|sorry)\b", re.IGNORECASE)


def english_words(reply: str) -> set[str]:
    return {match.lower() for match in ENGLISH_WORDS.findall(reply)}


def _matches(pattern: str, text: str) -> bool:
    if pattern.startswith("re:"):
        return re.search(pattern[3:], text, re.IGNORECASE) is not None
    return pattern.lower() in text.lower()


@dataclass
class ScenarioResult:
    id: str
    failures: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def check_turn(turn: dict, reply: str, called: list[str], results: list[dict],
               verified: bool) -> list[str]:
    failures = []
    for tool in turn.get("expect_tools", []):
        if tool not in called:
            failures.append(f"expected tool {tool}, called {called}")
    any_tools = turn.get("expect_any_tools")
    if any_tools and not set(any_tools) & set(called):
        failures.append(f"expected one of {any_tools}, called {called}")
    forbidden = turn.get("forbid_tools", [])
    if "all" in forbidden:
        forbidden = list(TOOL_FUNCTIONS)
    for tool in forbidden:
        if tool in called:
            failures.append(f"forbidden tool {tool} was called")
    for pattern in turn.get("must_include", []):
        if not _matches(pattern, reply):
            failures.append(f"missing {pattern!r}")
    for pattern in turn.get("must_not_include", []):
        if _matches(pattern, reply):
            failures.append(f"must not include {pattern!r}")
    ungrounded = ungrounded_amounts(reply, turn["user"], results)
    if ungrounded:
        failures.append(f"ungrounded R$ amounts {sorted(map(str, ungrounded))}")
    leaked = leaked_order_fields(reply, verified)
    if leaked:
        failures.append(f"order data before verification {sorted(leaked)}")
    english = english_words(reply)
    if english:
        failures.append(f"reply not in Portuguese, found {sorted(english)}")
    return failures


def run_scenario(scenario: dict, llm=None) -> ScenarioResult:
    result = ScenarioResult(scenario["id"])
    now = to_local(datetime.fromisoformat(scenario.get("now", DEFAULT_NOW)))
    with tempfile.TemporaryDirectory() as tmp:
        context = ToolContext(
            session_id=f"scenario-{scenario['id']}",
            now=now,
            outbox_path=Path(tmp) / "escalations.jsonl",
        )
        conversation = Conversation(session_id=context.session_id, tool_context=context)
        verified = False
        for number, turn in enumerate(scenario["turns"], start=1):
            turn_result = run_turn(conversation, turn["user"], llm=llm)
            called = [call["name"] for call in turn_result.tool_calls]
            results = [call["result"] for call in turn_result.tool_calls]
            verified = verified or any(r.get("verified") for r in results)
            failures = check_turn(turn, turn_result.reply, called, results, verified)
            result.failures += [f"turn {number}: {f}" for f in failures]
            result.transcript.append(
                {"user": turn["user"], "reply": turn_result.reply, "tools": turn_result.tool_calls}
            )
    return result


def load_scenarios(path: Path = SCENARIOS_PATH, only: list[str] | None = None) -> list[dict]:
    scenarios = yaml.safe_load(path.read_text(encoding="utf-8"))
    if only:
        scenarios = [s for s in scenarios if s["id"] in only]
    return scenarios


def format_transcript(result: ScenarioResult) -> str:
    lines = [f"--- {result.id} ---"]
    for turn in result.transcript:
        lines.append(f"Cliente: {turn['user']}")
        for call in turn["tools"]:
            args = json.dumps(call["arguments"], ensure_ascii=False)
            lines.append(f"  [tool] {call['name']} {args}")
        lines.append(f"Empório: {turn['reply']}")
    lines += [f"  FAIL: {failure}" for failure in result.failures]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = [run_scenario(s) for s in load_scenarios(only=argv or None)]
    width = max(len(r.id) for r in results)
    print(f"\n{'scenario'.ljust(width)}  result")
    for r in results:
        print(f"{r.id.ljust(width)}  {'PASS' if r.passed else 'FAIL'}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    for r in results:
        if not r.passed:
            print("\n" + format_transcript(r))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
