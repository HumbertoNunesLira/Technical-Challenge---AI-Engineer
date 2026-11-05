"""The agent loop: one customer message in, one reply out.

1. Build the system prompt with the agent clock and the recent text history.
2. Call the model with the tool schemas.
3. While it asks for tools: run them, send all results back in one message, call again.
4. Stop at a text reply, or after MAX_ITERATIONS with a safe fallback.

History design: only the customer's messages and the final replies are kept between turns.
Tool calls, tool results and thinking blocks live only inside the turn that produced them.
- Prices, stock and order data are re-fetched every turn, so a reply never quotes a stale
  tool result, and verified order data does not linger in the context.
- Thinking blocks are only valid while everything before them is unchanged. The system
  prompt carries the current time and the history window slides, so replaying old thinking
  blocks would break; within one turn the conversation is append-only, so they stay valid.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from emporio import config
from emporio.llm import LLMError, get_llm
from emporio.prompts import build_system_prompt
from emporio.tools import TOOL_SCHEMAS, ToolContext, run_tool

MAX_ITERATIONS = 6
MAX_HISTORY_MESSAGES = 20

FALLBACK_REPLY = (
    "Desculpe, tive um problema para concluir seu atendimento agora. Pode tentar de novo em "
    "instantes? Se preferir, fale com a nossa equipe pelo (67) 3341-4444."
)
REFUSAL_REPLY = (
    "Desculpe, não consigo ajudar com esse pedido. Posso te ajudar com instrumentos, pedidos "
    "ou dúvidas sobre a loja?"
)


@dataclass
class Conversation:
    """One customer conversation: text history plus the tools' per-session state."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    history: list[dict] = field(default_factory=list)  # [{"role", "content": str}]
    tool_context: ToolContext | None = None

    def __post_init__(self):
        if self.tool_context is None:
            self.tool_context = ToolContext(session_id=self.session_id, now=config.agent_now())


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict]  # [{"name", "arguments", "result"}] in call order
    usage: dict[str, int]
    latency_s: float
    iterations: int
    stop_reason: str  # model stop reason, or "max_iterations" / "llm_error"


def context_window(history: list[dict], limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """The last `limit` messages, starting with a customer message."""
    window = history[-limit:]
    while window and window[0]["role"] != "user":
        window = window[1:]
    return window


def run_turn(conversation: Conversation, user_message: str, llm=None) -> TurnResult:
    llm = llm or get_llm()
    system = build_system_prompt(conversation.tool_context.current_time())
    messages = context_window(conversation.history) + [{"role": "user", "content": user_message}]

    tool_log: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    start = time.perf_counter()
    reply, stop_reason, iterations = FALLBACK_REPLY, "max_iterations", 0

    while iterations < MAX_ITERATIONS:
        iterations += 1
        try:
            response = llm.chat(system, messages, TOOL_SCHEMAS)
        except LLMError as exc:
            reply, stop_reason = FALLBACK_REPLY, f"llm_error: {exc}"
            break
        for key in usage:
            usage[key] += response.usage.get(key, 0)

        if response.stop_reason == "tool_use" and response.tool_calls:
            messages.append(response.assistant_message)
            # Tools are local and fast, so they run in order; all results still go back
            # in a single message, as the API expects for parallel tool calls.
            results = []
            for call in response.tool_calls:
                result = run_tool(call.name, call.arguments, conversation.tool_context)
                tool_log.append({"name": call.name, "arguments": call.arguments, "result": result})
                results.append((call, result))
            messages.append(llm.tool_results_message(results))
            continue

        stop_reason = response.stop_reason
        if stop_reason == "refusal":
            reply = REFUSAL_REPLY
        else:
            reply = response.text.strip() or FALLBACK_REPLY
        break

    conversation.history.append({"role": "user", "content": user_message})
    conversation.history.append({"role": "assistant", "content": reply})
    return TurnResult(
        reply=reply,
        tool_calls=tool_log,
        usage=usage,
        latency_s=time.perf_counter() - start,
        iterations=iterations,
        stop_reason=stop_reason,
    )
