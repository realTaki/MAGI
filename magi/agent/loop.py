"""The agent loop — the spine every channel plugs into.

v0 (LLM minimum-viable): one chat turn. Read SOUL.md for
persona, build a one-message history, call the LLM, return
the reply. No skills, no memory, no proactive triggers —
those land in C4/C5. The audit row for each turn (inbound +
outbound, with thinking block captured) is the contract that
later checkpoints build on.

Why this is a function and not a class: the agent loop
doesn't have per-instance state in v0. Channels call
``handle_message`` with everything the call needs
(credentials + text) and get a string back. C4 will move
per-conversation state (history, scratchpad) onto a class so
multi-turn calls can pass it in; the function signature will
gain a ``conversation_id`` arg without breaking callers.

Persistence side: ``handle_message`` records one row per
successful LLM call in the ``token_usage`` table (D.15).
Session history lives in the SQLite ``chat_sessions`` table
under ``<workspace>/memories/<state-dir>.db`` (D.18, replacing
the pre-D.18 JSON layout). No separate audit log — operator-
facing ``/api/contacts/{uid}/token-usage`` + ``GET
/api/chat/sessions/{id}`` cover the same questions
("what was said", "what was spent") that an audit
view would.

Interrupt-aware loop (D.21)
--------------------------
Channels append the user's message to the session store
**before** invoking ``handle_message`` — the agent loop
itself does not own the inbound queue. Inside the tool-
iteration loop, every iteration polls the store for fresh
user messages:

  - If the store has grown since the loop's last poll,
    new user messages are spliced into the in-memory
    ``messages`` list **after** truncating any trailing
    assistant+tool_use/tool_result blocks (Anthropic's
    API rejects role messages interleaved with
    tool blocks).
  - The iteration counter resets to zero so the model
    gets a fresh ``max_iter`` budget to respond to the
    new message.
  - One log line (``agent.interrupt``) is emitted so an
    operator can see "the user interrupted the agent
    halfway through tool execution".

The contract is: a user can send more messages while the
agent is mid-tool-chain; those messages land in the
session store (TG / WebUI already does this), and the
loop picks them up on the next iteration. The channel
side does NOT need any extra wiring — the polling
happens entirely inside :func:`handle_message`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from magi.agent.compaction import maybe_compact
from magi.agent.llm import (
    ChatMessage,
    ChatResult,
    LLMError,
    LLMProvider,
    get_provider,
)
from magi.agent.memory.session import SessionStore
from magi.agent.system_prompt import build_system_prompt, read_soul
from magi.agent.token_usage import record_token_usage

# Note: prompt-block helpers (memory / contacts / skills)
# live in :mod:`magi.agent.system_prompt` — not imported
# here so this module stays focused on the chat loop.
from magi.agent.tools.base import ToolContext
from magi.agent.tools.registry import get_tool, get_tool_schemas

logger = logging.getLogger("magi.agent.agent")

# Default cap on a single LLM reply. 1024 is enough for chat
# turns and well under the 8K most Anthropic-compatible APIs
# advertise. Callers can override per-call.
DEFAULT_MAX_TOKENS = 1024

# Two friendly strings the agent loop can return when
# something is wrong: ``agent_fallback`` for an LLM call
# that failed mid-stream (network / rate-limit / context-
# length), and ``agent_no_credentials`` for the strict-mode
# rejection when the chat caller never supplied per-
# contact credentials. Both live in
# ``magi/agent/prompts/bot_replies.yaml`` — see that file
# to tweak. Resolved lazily and cached so a single YAML
# read serves every fallback for the rest of the process.
from magi.agent.prompts import load_bot_replies  # noqa: E402

_FALLBACK_REPLY_CACHE: dict[str, str] = {}


def _truncate_at_safe_boundary(messages: list[ChatMessage]) -> None:
    """Truncate ``messages`` so the last entry is a plain
    text message (no ``content_blocks``).

    Anthropic's Messages API rejects request payloads where
    a plain ``user`` text message is interleaved with the
    tool_use → tool_result chain from a prior assistant
    turn — the API expects tool_use blocks to be answered
    by an immediately-following tool_result block. When an
    interrupt splices a new user message in, the tail of
    ``messages`` may look like:

        [..., assistant(tool_use), user(tool_result, ...), user("hi")]

    and the last ``user("hi")`` breaks the chain. We drop
    any trailing message that carries ``content_blocks`` so
    the next LLM call sees a clean boundary.

    The truncation only touches the in-memory list; the
    session store's history is left intact (it's the audit
    trail, not the LLM-facing view).
    """
    while messages and messages[-1].content_blocks:
        messages.pop()


def _build_messages_from_session(
    state_dir: str,
    uid: int,
    session_id: str | None,
    new_user_text: str,
) -> tuple[list[ChatMessage], set[str]]:
    """Load the prior-turn history into the LLM-facing
    message list (D.17). Returns ``(messages, seen_ids)``
    where ``messages`` is the existing ``sess.messages``
    followed by the brand-new user text, and ``seen_ids``
    is the set of message_ids the loop has already seen
    (so subsequent :func:`_drain_pending_user_messages`
    polls don't re-read them). ``sess.archive`` is
    intentionally NOT read; it's the forensic record and
    the LLM never sees it.

    D.23: ``uid`` is the cross-channel session
    key. The previous per-channel delivery-address argument
    is gone — the session is identified by who owns it, not
    by which channel they happened to be on.
    """
    if not session_id:
        # No session: the inbound is its own message; no
        # history to track.
        return [ChatMessage(role="user", content=new_user_text)], set()
    sess = SessionStore(state_dir).get(uid, session_id)
    if sess is None:
        return [ChatMessage(role="user", content=new_user_text)], set()
    out: list[ChatMessage] = []
    seen: set[str] = set()
    for m in sess.messages:
        llm_role = "user" if m.role in ("user", "system") else "assistant"
        out.append(ChatMessage(role=llm_role, content=m.text))
        seen.add(m.message_id)
    # ``new_user_text`` was already appended to the store
    # by the channel-side caller before invoking
    # ``handle_message``; the loop tracks that id so it
    # doesn't re-drain it on the first poll.
    out.append(ChatMessage(role="user", content=new_user_text))
    return out, seen


def _drain_pending_user_messages(
    state_dir: str,
    uid: int,
    session_id: str | None,
    messages: list[ChatMessage],
    seen_message_ids: set[str],
) -> bool:
    """Pull fresh user messages from the session store into
    ``messages`` (D.21 — interrupt-aware loop).

    Returns ``True`` when at least one new user message
    was spliced in, ``False`` otherwise. The caller uses
    the return value to decide whether to reset the
    iteration counter.

    Algorithm:

      1. ``SessionStore.get`` is the source of truth.
         Anything not in ``seen_message_ids`` is "new"
         since the loop's last poll (or since the loop
         started, on the first call).
      2. New messages with ``role="user"`` are spliced
         in chronological order. New ``assistant`` rows
         are skipped — the channel-side writer appends
         the assistant's final reply **after**
         ``handle_message`` returns, so any assistant
         row in the store at this point is from a
         prior turn and is already in our
         in-memory list.
      3. Before splicing, call
         :func:`_truncate_at_safe_boundary` so the new
         user message lands at a legal point in the
         tool_use / tool_result chain.
      4. Every new id (regardless of role) is added to
         ``seen_message_ids`` so the next poll doesn't
         re-read it. ``seen_message_ids`` is mutated
         in-place by the caller.

    Failures inside ``SessionStore.get`` are logged and
    treated as "no new messages" — a transient store
    hiccup must not crash the in-flight agent loop.
    """
    if not session_id:
        return False
    try:
        sess = SessionStore(state_dir).get(uid, session_id)
    except Exception:
        logger.exception(
            "agent: store read failed during interrupt poll "
            "(contact=%s, session=%s); continuing without drain",
            uid, session_id,
        )
        return False
    if sess is None:
        return False

    new_user_texts: list[str] = []
    for m in sess.messages:
        # Track every new id we see, regardless of role, so
        # we don't re-poll the same row on the next
        # iteration.
        if m.message_id in seen_message_ids:
            continue
        seen_message_ids.add(m.message_id)
        if m.role == "user":
            new_user_texts.append(m.text)

    if not new_user_texts:
        return False

    # Truncate the tool-use / tool-result chain so the new
    # user message lands at a legal boundary.
    _truncate_at_safe_boundary(messages)
    for text in new_user_texts:
        messages.append(ChatMessage(role="user", content=text))

    logger.info(
        "agent.interrupt: spliced %d new user message(s) into "
        "in-flight loop (contact=%s, session=%s)",
        len(new_user_texts), uid, session_id,
    )
    return True


def _fallback_reply(key: str = "agent_fallback") -> str:
    """Resolve a friendly fallback string from the
    bot_replies prompt table. Cached so a single YAML read
    serves every fallback for the rest of the process.

    ``key`` selects which template: ``agent_fallback`` for
    LLM-call failures (the legacy single-purpose template),
    ``agent_no_credentials`` for the strict-mode rejection
    that tells the user where to fix the missing config.
    """
    cached = _FALLBACK_REPLY_CACHE.get(key)
    if cached is None:
        cached = load_bot_replies()[key]
        _FALLBACK_REPLY_CACHE[key] = cached
    return cached

# Where SOUL.md lives. C4 will move this to a per-contact
# path (each EVE can have its own persona); for v0 we read
# the single workspace file at startup.
_SOUL_FILENAME = "SOUL.md"

@dataclass
class _AgentContext:
    """All immutable and mutable state needed for one agent turn."""

    provider: LLMProvider
    soul: str
    tool_ctx: ToolContext
    tool_schemas: list[dict]
    messages: list[ChatMessage]
    seen_message_ids: set[str]
    max_iter: int


@dataclass
class _ToolLoopOutcome:
    """The last provider result plus the reply-loop counters."""

    final_text: str
    result: ChatResult | None
    iterations_run: int


def _validate_credentials(
    *,
    contact_provider: str | None,
    contact_api_key: str | None,
    uid: int | None,
    channel: str,
) -> tuple[str, str] | None:
    """Validate strict per-contact LLM credentials.

    Returns the provider/key pair for the happy path and ``None`` when the
    caller should receive the no-credentials fallback. Keeping this gate
    separate makes the billing invariant independently testable.
    """
    if contact_provider and contact_api_key:
        return contact_provider, contact_api_key

    reason = (
        "no per-contact credentials configured"
        if contact_provider is None and contact_api_key is None
        else "per-contact credentials partially configured "
             "(provider or key missing)"
    )
    logger.warning(
        "chat rejected (contact=%s channel=%s): %s",
        uid, channel, reason,
    )
    return None


def _build_context(
    state_dir: str,
    *,
    text: str,
    channel: str,
    uid: int | None,
    session_id: str | None,
    contact_provider: str,
    contact_api_key: str,
    contact_model: str | None,
    caller_role: str | None,
    max_tool_iterations: int | None,
) -> _AgentContext | None:
    """Build the provider, tool context, and LLM-facing message state."""
    try:
        provider = get_provider(
            contact_provider,
            contact_api_key,
            contact_model,
        )
    except Exception as e:
        # ``get_provider`` can fail for an unknown provider or malformed key;
        # treat it like an LLM failure rather than leaking into the channel.
        logger.warning(
            "agent: get_provider failed (contact=%s provider=%s): %s",
            uid, contact_provider, e,
        )
        return None

    from magi.agent.workspace import workspace_root

    workspace = Path(workspace_root(state_dir))
    if max_tool_iterations is None:
        # Lazy import keeps provider-only unit tests from importing the
        # settings API and its ORM stack at module load time.
        from magi.channels.webui.api.system_settings import (
            get_tool_max_iterations,
        )
        max_iter = get_tool_max_iterations(state_dir)
    else:
        max_iter = max_tool_iterations

    tool_ctx = ToolContext(
        state_dir=state_dir,
        workspace=workspace,
        uid=uid if uid is not None else 0,
        channel=channel,
        session_id=session_id or "",
    )
    messages, seen_message_ids = _build_messages_from_session(
        state_dir,
        uid,
        session_id,
        text,
    )
    return _AgentContext(
        provider=provider,
        soul=read_soul(state_dir),
        tool_ctx=tool_ctx,
        tool_schemas=get_tool_schemas(caller_role=caller_role),
        messages=messages,
        seen_message_ids=seen_message_ids,
        max_iter=max_iter,
    )


async def _run_tool_calls(
    result: ChatResult,
    *,
    tool_ctx: ToolContext,
    uid: int | None,
    session_id: str | None,
) -> list[dict]:
    """Execute one provider turn's tool calls and serialize their results."""
    tool_results: list[dict] = []
    for tool_use in result.tool_uses:
        tool = get_tool(tool_use["name"])
        if tool is None:
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": f"unknown tool: {tool_use['name']!r}",
                "is_error": True,
            })
            continue

        try:
            # The tool itself owns channel dispatch; the loop remains
            # independent of Telegram or any other transport client.
            tool_result = await tool.run(
                tool_ctx,
                **dict(tool_use.get("input") or {}),
            )
        except Exception as e:
            logger.exception(
                "agent: tool %s crashed (contact=%s, session=%s)",
                tool_use["name"], uid, session_id,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": f"tool {tool_use['name']!r} crashed: {e}"[:8000],
                "is_error": True,
            })
            continue

        content = tool_result.content
        if len(content) > 8000:
            content = content[:8000] + "\n…[truncated at 8000 chars]"
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": content,
            "is_error": tool_result.is_error,
        })
    return tool_results


async def _run_tool_loop(
    state_dir: str,
    *,
    context: _AgentContext,
    uid: int | None,
    session_id: str | None,
    contact_provider: str,
    contact_api_key: str,
    contact_model: str | None,
    max_tokens: int,
) -> _ToolLoopOutcome:
    """Run compaction, provider calls, interrupts, and tool execution."""
    final_text = ""
    last_result: ChatResult | None = None
    iterations_run = 0

    while iterations_run < context.max_iter:
        iterations_run += 1
        if _drain_pending_user_messages(
            state_dir,
            uid,
            session_id,
            context.messages,
            context.seen_message_ids,
        ):
            iterations_run = 0
            continue

        await maybe_compact(
            state_dir,
            uid,
            session_id,
            context.messages,
            contact_provider=contact_provider,
            contact_api_key=contact_api_key,
            contact_model=contact_model,
        )

        result = await context.provider.chat(
            system=build_system_prompt(
                state_dir,
                uid=uid,
                soul=context.soul,
            ),
            messages=context.messages,
            max_tokens=max_tokens,
            tools=context.tool_schemas,
        )
        last_result = result
        final_text = result.text
        context.messages.append(ChatMessage(
            role="assistant",
            content=result.text or "",
            content_blocks=result.raw_blocks or None,
        ))

        if not result.tool_uses or result.stop_reason == "end_turn":
            break

        tool_results = await _run_tool_calls(
            result,
            tool_ctx=context.tool_ctx,
            uid=uid,
            session_id=session_id,
        )
        context.messages.append(ChatMessage(
            role="user",
            content="",
            content_blocks=tool_results,
        ))
    else:
        logger.warning(
            "agent: tool loop hit max_iter=%d for session=%s "
            "(contact=%s); model still wanted tools",
            context.max_iter,
            session_id,
            uid,
        )

    return _ToolLoopOutcome(
        final_text=final_text,
        result=last_result,
        iterations_run=iterations_run,
    )


def _audit_and_return(
    state_dir: str,
    *,
    outcome: _ToolLoopOutcome,
    provider: LLMProvider,
    uid: int | None,
    channel: str,
    session_id: str | None,
    messages: list[ChatMessage],
) -> str:
    """Record usage, emit the reply log, and return a safe reply string."""
    result = outcome.result
    if result is None:
        return _fallback_reply()

    try:
        # v0 records the last provider call as the token-cost proxy. The
        # provider layer can expose per-iteration usage in a future revision.
        record_token_usage(
            state_dir,
            uid=uid,
            channel=channel,
            provider=provider.name,
            model=result.model,
            usage=result.usage or {},
        )
    except Exception:
        logger.exception(
            "agent: token_usage insert failed (contact=%s, channel=%s); "
            "chat reply already succeeded",
            uid,
            channel,
        )

    logger.info(
        "llm reply",
        extra={
            "uid": uid,
            "channel": channel,
            "provider": provider.name,
            "model": result.model,
            "text_len": len(outcome.final_text),
            "thinking_len": len(result.thinking) if result.thinking else 0,
            "iterations": outcome.iterations_run,
            "tool_calls": sum(
                len(message.content_blocks)
                for message in messages
                if message.role == "assistant" and message.content_blocks
            ),
            "session": session_id,
        },
    )
    return outcome.final_text or _fallback_reply()


async def handle_message(
    state_dir: str,
    *,
    text: str,
    channel: str,
    uid: int | None = None,
    session_id: str | None = None,
    contact_provider: str | None = None,
    contact_api_key: str | None = None,
    contact_model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_tool_iterations: int | None = None,
    caller_role: str | None = None,
) -> str:
    """Handle one channel message through validation, context, tools, and audit."""
    credentials = _validate_credentials(
        contact_provider=contact_provider,
        contact_api_key=contact_api_key,
        uid=uid,
        channel=channel,
    )
    if credentials is None:
        return _fallback_reply("agent_no_credentials")
    provider_name, api_key = credentials

    context = _build_context(
        state_dir,
        text=text,
        channel=channel,
        uid=uid,
        session_id=session_id,
        contact_provider=provider_name,
        contact_api_key=api_key,
        contact_model=contact_model,
        caller_role=caller_role,
        max_tool_iterations=max_tool_iterations,
    )
    if context is None:
        return _fallback_reply()

    try:
        outcome = await _run_tool_loop(
            state_dir,
            context=context,
            uid=uid,
            session_id=session_id,
            contact_provider=provider_name,
            contact_api_key=api_key,
            contact_model=contact_model,
            max_tokens=max_tokens,
        )
    except LLMError as e:
        logger.warning(
            "llm call failed (contact=%s provider=%s): %s",
            uid,
            provider_name,
            e,
        )
        return _fallback_reply()

    return _audit_and_return(
        state_dir,
        outcome=outcome,
        provider=context.provider,
        uid=uid,
        channel=channel,
        session_id=session_id,
        messages=context.messages,
    )
