"""LLM providers behind one small interface.

An adapter has two methods:
- chat(system, messages, tools) -> LLMResponse, normalized across providers;
- tool_results_message(results) -> the message that returns tool results to that provider.

The agent loop appends `response.assistant_message` exactly as the provider returned it.
For Anthropic that matters: thinking blocks must be passed back unchanged with tool results.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import anthropic

from emporio import config


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # "end_turn", "tool_use", "max_tokens", "refusal", ...
    usage: dict[str, int]
    latency_s: float
    assistant_message: dict  # append to the conversation as-is


class LLMError(Exception):
    """The provider failed after the SDK's own retries. The message is safe to log."""


class AnthropicLLM:
    """Claude through the official SDK. Default model: Claude Haiku 5.5."""

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int = config.MAX_TOKENS,
        client: anthropic.Anthropic | None = None,
    ):
        self.model = model or config.llm_model() or config.DEFAULT_MODELS["anthropic"]
        self.effort = effort or config.llm_effort()
        self.max_tokens = max_tokens
        # The SDK retries connection errors, 429 and 5xx with backoff (2 retries by default).
        self.client = client or anthropic.Anthropic()

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        start = time.perf_counter()
        try:
            # No temperature: Claude Haiku 5.5 rejects sampling parameters. Effort is the
            # lever for how much the model thinks; adaptive thinking is on by default.
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": self.effort},
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key (check ANTHROPIC_API_KEY).") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached; try again shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc

        # Read blocks by type: a response can start with thinking blocks.
        text = "".join(block.text for block in response.content if block.type == "text")
        tool_calls = [
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
            for block in response.content
            if block.type == "tool_use"
        ]
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            latency_s=time.perf_counter() - start,
            assistant_message={"role": "assistant", "content": response.content},
        )

    def tool_results_message(self, results: Sequence[tuple[ToolCall, dict]]) -> dict:
        return anthropic_tool_results_message(results)


def anthropic_tool_results_message(results: Sequence[tuple[ToolCall, dict]]) -> dict:
    """All results of one turn go back in a single user message."""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
                "is_error": "error" in result,
            }
            for call, result in results
        ],
    }


@dataclass
class FakeLLM:
    """Scripted LLM for tests: no network, deterministic.

    Each script step is either a string (a final text reply) or a list of
    (tool_name, arguments) pairs (a turn that requests those tool calls).
    Every request is recorded in `requests` for assertions.
    """

    script: list[str | list[tuple[str, dict]]]
    requests: list[dict] = field(default_factory=list)
    _step: int = 0

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.requests.append({"system": system, "messages": list(messages), "tools": tools})
        if self._step >= len(self.script):
            raise LLMError("FakeLLM script exhausted")
        step = self.script[self._step]
        self._step += 1
        if isinstance(step, str):
            return LLMResponse(
                text=step,
                tool_calls=[],
                stop_reason="end_turn",
                usage={"input_tokens": 10, "output_tokens": 5},
                latency_s=0.0,
                assistant_message={"role": "assistant", "content": step},
            )
        calls = [
            ToolCall(id=f"call_{self._step}_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(step)
        ]
        return LLMResponse(
            text="",
            tool_calls=calls,
            stop_reason="tool_use",
            usage={"input_tokens": 10, "output_tokens": 5},
            latency_s=0.0,
            assistant_message={
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in calls
                ],
            },
        )

    def tool_results_message(self, results: Sequence[tuple[ToolCall, dict]]) -> dict:
        return anthropic_tool_results_message(results)  # same wire format as Anthropic


def get_llm():
    """The adapter selected by LLM_PROVIDER."""
    provider = config.llm_provider()
    if provider == "anthropic":
        return AnthropicLLM()
    raise ValueError(f"Unsupported LLM_PROVIDER '{provider}'. Supported: anthropic.")
