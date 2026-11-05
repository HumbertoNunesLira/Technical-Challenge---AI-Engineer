"""Optional HTTP API over the same agent and session store as the CLI.

Run: uvicorn emporio.api:app --reload

This is the shape a WhatsApp Business webhook would call: the sender's number becomes the
session id, the message text is the body, and the reply goes back through the WhatsApp API.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Response
from pydantic import BaseModel, Field, field_validator

from emporio.agent import run_turn
from emporio.llm import get_llm
from emporio.session import SessionStore

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MAX_MESSAGE_LENGTH = 1000

app = FastAPI(title="Empório da Música Support Agent", version="0.1.0")

# One lock per session: two requests for the same conversation run one after the other,
# so turns are appended in order. Different sessions run in parallel.
_session_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _lock_for(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _session_locks[session_id]


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN, examples=["5567999990000"])
    message: str = Field(max_length=MAX_MESSAGE_LENGTH, examples=["Quanto custa o Takamine GD20?"])

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty")
        return value.strip()


class ToolCallSummary(BaseModel):
    name: str
    arguments: dict


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[ToolCallSummary]  # names and arguments only, no result data


def get_store() -> SessionStore:
    return SessionStore()


def get_agent_llm():
    return get_llm()


Store = Annotated[SessionStore, Depends(get_store)]
AgentLLM = Annotated[object, Depends(get_agent_llm)]
SessionIdPath = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, store: Store, llm: AgentLLM) -> ChatResponse:
    with _lock_for(request.session_id):
        conversation = store.load(request.session_id)
        result = run_turn(conversation, request.message, llm=llm)
        if result.stop_reason.startswith("llm_error"):
            # Do not store a failed turn; the client can retry the same message.
            raise HTTPException(
                status_code=502,
                detail="The language model provider is unavailable. Try again shortly.",
            )
        store.append_turn(request.session_id, request.message, result)
    return ChatResponse(
        session_id=request.session_id,
        reply=result.reply,
        tool_calls=[
            ToolCallSummary(name=c["name"], arguments=c["arguments"]) for c in result.tool_calls
        ],
    )


@app.delete("/sessions/{session_id}", status_code=204)
def reset_session(session_id: SessionIdPath, store: Store) -> Response:
    with _lock_for(session_id):
        store.reset(session_id)
    return Response(status_code=204)
