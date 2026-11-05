"""Terminal chat: `python -m emporio chat [SESSION_ID]`.

Commands inside the chat: /reset clears the conversation, /quit (or /sair) exits.
SHOW_TOOLS=1 prints each tool call with a short view of its result.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable

from emporio import config
from emporio.agent import run_turn
from emporio.llm import get_llm
from emporio.session import InvalidSessionId, SessionStore

QUIT_COMMANDS = {"/quit", "/sair", "/exit"}
RESET_COMMAND = "/reset"
MAX_RESULT_PREVIEW = 300


def _preview(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT_PREVIEW else text[:MAX_RESULT_PREVIEW] + "…"


def run_chat(
    session_id: str,
    llm=None,
    store: SessionStore | None = None,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    show_tools: bool | None = None,
) -> None:
    """The REPL. Input and output are injectable so tests can drive it."""
    store = store or SessionStore()
    llm = llm or get_llm()
    show_tools = config.show_tools() if show_tools is None else show_tools
    conversation = store.load(session_id)

    write(f"Empório da Música — atendimento virtual (sessão {session_id})")
    write("Comandos: /reset limpa a conversa, /quit sai.")
    if conversation.history:
        write(f"(Retomando conversa com {len(conversation.history)} mensagens.)")

    while True:
        try:
            message = read("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            break
        if not message:
            continue
        if len(message) > config.MAX_MESSAGE_LENGTH:
            write(f"(Mensagem muito longa: o limite é {config.MAX_MESSAGE_LENGTH} caracteres.)")
            continue
        if message.lower() in QUIT_COMMANDS:
            break
        if message.lower() == RESET_COMMAND:
            store.reset(session_id)
            conversation = store.load(session_id)
            write("(Conversa apagada. Vamos recomeçar!)")
            continue

        result = run_turn(conversation, message, llm=llm)
        store.append_turn(session_id, message, result)
        if show_tools:
            for call in result.tool_calls:
                args = json.dumps(call["arguments"], ensure_ascii=False)
                write(f"  [tool] {call['name']} {args} -> {_preview(call['result'])}")
        write(f"\nEmpório: {result.reply}")

    write("Até logo!")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m emporio")
    commands = parser.add_subparsers(dest="command", required=True)
    chat = commands.add_parser("chat", help="chat with the store assistant")
    chat.add_argument("session_id", nargs="?", help="resume this session (default: new one)")
    args = parser.parse_args(argv)

    # Emoji and accents must print on any Windows console code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    session_id = args.session_id or uuid.uuid4().hex[:8]
    store = SessionStore()
    try:
        store.path(session_id)
    except InvalidSessionId as exc:
        print(exc)
        return 2
    try:
        llm = get_llm()
    except Exception as exc:  # noqa: BLE001 - startup config errors get a friendly message
        print(f"Could not start the LLM client: {exc}")
        print("Check LLM_PROVIDER and the API key in .env.")
        return 1
    run_chat(session_id, llm=llm, store=store)
    return 0
