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
Session history lives in JSON files under
``<workspace>/memories/sessions/<chat_id>/<sid>.json``
(D.6). No separate audit log — operator-facing
``/api/employees/{id}/token-usage`` + ``GET
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
from datetime import datetime, timezone
from pathlib import Path

from magi.agent.llm import ChatMessage, LLMError, get_provider
# Note: prompt-block helpers (memory / contacts / skills)
# live in :mod:`magi.agent.system_prompt` — not imported
# here so this module stays focused on the chat loop.
from magi.agent.tools.base import ToolContext
from magi.agent.compaction import call_llm_for_summary, maybe_compact
from magi.agent.system_prompt import build_system_prompt, read_soul
from magi.agent.token_usage import record_token_usage
from magi.agent.tools.registry import get_tool, get_tool_schemas
from magi.agent.llm.tokens import estimate_messages_tokens
from magi.agent.memory.session import SessionStore

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
# employee credentials. Both live in
# ``magi/agent/prompts/bot_replies.yaml`` — see that file
# to tweak. Resolved lazily and cached so a single YAML
# read serves every fallback for the rest of the process.
from magi.agent.prompts import load_bot_replies  # noqa: E402

_FALLBACK_REPLY_CACHE: dict[str, str] = {}


def _truncate_at_safe_boundary(messages: list["ChatMessage"]) -> None:
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
    employee_id: int,
    session_id: str | None,
    new_user_text: str,
) -> tuple[list["ChatMessage"], set[str]]:
    """Load the prior-turn history into the LLM-facing
    message list (D.17). Returns ``(messages, seen_ids)``
    where ``messages`` is the existing ``sess.messages``
    followed by the brand-new user text, and ``seen_ids``
    is the set of message_ids the loop has already seen
    (so subsequent :func:`_drain_pending_user_messages`
    polls don't re-read them). ``sess.archive`` is
    intentionally NOT read; it's the forensic record and
    the LLM never sees it.

    D.23: ``employee_id`` is the cross-channel session
    key. The previous ``chat_id`` argument is gone — the
    session is identified by who owns it, not by which
    channel they happened to be on.
    """
    if not session_id:
        # No session: the inbound is its own message; no
        # history to track.
        return [ChatMessage(role="user", content=new_user_text)], set()
    sess = SessionStore(state_dir).get(employee_id, session_id)
    if sess is None:
        return [ChatMessage(role="user", content=new_user_text)], set()
    out: list["ChatMessage"] = []
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
    employee_id: int,
    session_id: str | None,
    messages: list["ChatMessage"],
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
        sess = SessionStore(state_dir).get(employee_id, session_id)
    except Exception:
        logger.exception(
            "agent: store read failed during interrupt poll "
            "(employee=%s, session=%s); continuing without drain",
            employee_id, session_id,
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
        "in-flight loop (employee=%s, session=%s)",
        len(new_user_texts), employee_id, session_id,
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

# Where SOUL.md lives. C4 will move this to a per-employee
# path (each EVE can have its own persona); for v0 we read
# the single workspace file at startup.
_SOUL_FILENAME = "SOUL.md"

async def handle_message(
    state_dir: str,
    *,
    text: str,
    channel: str,
    employee_id: int | None = None,
    # D.6: optional session id. Persisted alongside the
    # message in the session JSON file
    # (``<workspace>/memories/sessions/<chat_id>/<id>.json``);
    # v0 also echoes it into the ``token_usage`` row so the
    # audit-style question "which session burned these
    # tokens?" can be answered later.
    session_id: str | None = None,
    # D.16: chat_id is needed by the tool context (the
    # ``send_message`` tool uses it as the TG target). The
    # WebUI cookie is a stringified int; TG passes its
    # ``effective_chat.id`` as a string too.
    chat_id: str = "",
    # Per-employee credentials — the chat path is strict by
    # default (no fall-back to a system default) so every LLM
    # call can be billed to a specific employee. Both must be
    # set together or the call is rejected with the
    # ``agent_fallback`` friendly reply.
    employee_provider: str | None = None,
    employee_api_key: str | None = None,
    employee_model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    # D.16: optional override for the agent loop's
    # tool-iteration cap. ``None`` (the default) means
    # "read from settings / fall back to default". Tests
    # pass an explicit small number to keep the suite fast.
    max_tool_iterations: int | None = None,
    # D.16: callback the ``send_message`` tool invokes to
    # deliver an out-of-band TG message. ``None`` means the
    # tool is effectively disabled for this channel — which
    # is fine on webui, where the tool itself rejects with
    # ``is_error=true``.
    tg_send_callback=None,
    # Calling operator's role. Used to filter which tools
    # the LLM sees (see ``get_tool_schemas(caller_role=...)``
    # in :mod:`magi.agent.tools.registry`): admin-only tools
    # like ``schedule_task`` and the action-item trio
    # (``add_todo`` / ``complete_todo`` / ``list_todo``) are
    # stripped from the menu when the caller isn't
    # ``admin`` or ``assigned``. ``None`` skips the filter —
    # tests, headless callers, or contexts where the role
    # hasn't been plumbed yet. Chat handlers always pass an
    # explicit role.
    caller_role: str | None = None,
) -> str:
    """One chat turn. Returns the LLM's reply text.

    On any LLM failure (including missing per-employee
    credentials), returns the ``agent_fallback`` template
    (see ``magi/agent/prompts/bot_replies.yaml``) and
    audits the real error. The caller (TG bot / WebUI chat)
    is responsible for delivering the string; we don't raise
    into the transport layer because the user already pressed
    send, and a transport-level exception would just confuse
    the UI.

    No default-LLM fallback. Every LLM call must carry the
    employee credentials that pay for it — the design is
    "every message is billed to a person", so silent fall-back
    to a house-LLM (which would mis-route the reply and hide
    the configuration mistake) is deliberately not supported.
    The pre-flight rejection is loud enough that the user
    can fix it from the dashboard / config panel.

    Parameters
    ----------
    state_dir
        The on-disk state directory (``MAGI_STATE_DIR``).
    text
        The inbound message text.
    channel
        Tag for the ``token_usage`` row (``"tg"`` / ``"webui"`` /
        ``"scheduled"``). Free-form string; no enum yet.
    employee_id
        Optional employee id, for the ``token_usage`` row.
        ``None`` is accepted (FK NOT NULL on the SQL column
        will surface any caller that drops the ball), but
        v0 never sends ``None`` — both channel paths
        resolve ``chat_id`` → ``Employee`` before this
        function runs.
    session_id
        D.6: optional chat session id. Echoed into the
        ``token_usage`` row so the question "which session
        burned these tokens?" can be answered later by
        joining against the session JSON files under
        ``<workspace>/memories/sessions/<chat_id>/<id>.json``.
    employee_provider / employee_api_key / employee_model
        Per-call LLM credentials. Either all three are set
        (employee chooses model optionally) or the call is
        rejected. The TG channel fetches these from the
        employee row; the WebUI chat API does the same via
        the ``magi_session`` admin cookie.
    """

    # Strict-mode pre-flight: per-employee credentials must
    # be present in full. We treat empty strings as "not set"
    # so a half-cleared row doesn't accidentally route to
    # a broken provider. The user-friendly reply points the
    # user at the panel that fixes the config (TG users will
    # see this; WebUI users hit a 403 one layer up before
    # getting here).
    if not employee_provider or not employee_api_key:
        reason = (
            "no per-employee credentials configured"
            if employee_provider is None and employee_api_key is None
            else "per-employee credentials partially configured "
                 "(provider or key missing)"
        )
        logger.warning(
            "chat rejected (employee=%s channel=%s): %s",
            employee_id, channel, reason,
        )
        return _fallback_reply("agent_no_credentials")

    provider_name = employee_provider
    api_key = employee_api_key
    model = employee_model

    soul = read_soul(state_dir)

    # D.16: agent tool-use loop. The runtime now sends every
    # registered tool's schema to the LLM, runs the loop
    # until the model produces a text reply (stop_reason
    # ``end_turn``) or hits the iteration cap. See
    # ``magi/agent/agent.py:handle_message`` docstring
    # for the failure-mode rationale; the loop continues
    # to swallow LLMError as before — the user already
    # pressed send, and a transport-level exception would
    # only confuse the UI.
    from magi.agent.workspace import workspace_root

    workspace = Path(workspace_root(state_dir))
    if max_tool_iterations is None:
        # Lazy import to avoid pulling settings.py at agent
        # module load (keeps unit tests that mock the
        # provider from triggering the SQLAlchemy path).
        from magi.channels.webui.api.system_settings import (
            get_tool_max_iterations,
        )
        max_iter = get_tool_max_iterations(state_dir)
    else:
        max_iter = max_tool_iterations

    tool_ctx = ToolContext(
        state_dir=state_dir,
        workspace=workspace,
        chat_id=chat_id,
        employee_id=employee_id if employee_id is not None else 0,
        channel=channel,
        # Populate ``session_id`` so tools (notably
        # ``schedule_task``) can default ``delivery_to`` to
        # the current chat when called mid-conversation.
        # ``""`` means there's no chat session to anchor to
        # — fine for cron-driven rows or admin tool calls
        # that don't have a chat thread.
        session_id=session_id or "",
    )
    tool_schemas = get_tool_schemas(caller_role=caller_role)

    try:
        provider = get_provider(provider_name, api_key, model)
    except Exception as e:
        # ``get_provider`` itself can fail (unknown provider
        # name, malformed key) — those don't come through as
        # LLMError. Treat the same as an LLMError: log +
        # return fallback, no exception to the caller.
        logger.warning(
            "agent: get_provider failed (employee=%s provider=%s): %s",
            employee_id, provider_name, e,
        )
        return _fallback_reply()

    # D.17 — load session history. ``_build_messages_from_session``
    # returns ``(messages, seen_ids)``: the prior-turn messages
    # (in ``sess.messages``, which is exactly the LLM-facing
    # view: summary at index 0 if a compaction has happened,
    # else the most recent K verbatim turns) plus the brand-
    # new user text, and the set of message_ids the loop has
    # already seen so subsequent interrupt polls don't re-read
    # them. ``archive`` is NOT included — it's the forensic
    # record only.
    messages, seen_message_ids = _build_messages_from_session(
        state_dir, employee_id, session_id, text,
    )

    final_text = ""
    iterations_run = 0
    try:
        # ``while ... else``: ``else`` fires when the
        # condition becomes False without a ``break`` —
        # i.e. the model kept requesting tools past
        # ``max_iter``. A plain ``for _iteration in
        # range(max_iter)`` would have the same shape,
        # but we need the loop body to be able to
        # **reset** ``iterations_run`` (D.21 interrupt
        # path) without leaving the loop body. A
        # ``while`` lets us track the iteration count
        # explicitly; ``max_iter`` is still the hard cap.
        while iterations_run < max_iter:
            iterations_run += 1
            # D.21 — interrupt poll. Runs **before** compaction
            # so the user's fresh input lands in ``messages``
            # and is included in the next LLM call (and, on
            # compaction, in the next compaction pass too).
            # Returns ``True`` when new user messages were
            # spliced in; we reset ``iterations_run`` so the
            # model gets a fresh ``max_iter`` budget to react
            # to the new input rather than dying on the budget
            # of the previous turn.
            if _drain_pending_user_messages(
                state_dir, employee_id, session_id,
                messages, seen_message_ids,
            ):
                iterations_run = 0
                continue  # re-enter the loop with the new input
            # D.17 — compact the in-memory message list if it
            # has grown past the configured threshold. Runs
            # before EVERY LLM call so a long tool chain
            # can't push the next request over the model's
            # context window. ``_maybe_compact`` mutates
            # ``messages`` in-place; on failure (no summary
            # produced, persistence error) the list is left
            # unchanged and the next call sees the full
            # history.
            await maybe_compact(
                state_dir,
                employee_id,
                session_id,
                messages,
                employee_provider=employee_provider or "",
                employee_api_key=employee_api_key or "",
                employee_model=employee_model,
            )

            result = await provider.chat(
                # System prompt = SOUL + MAGI's long-term
                # memory + current chatter contact +
                # available skills. Each block short-circuits
                # when empty (no memory rows / no contact
                # for this chat / no SKILL.md), so a fresh
                # deploy still gets a sensible prompt.
                # Built once per turn (not cached) so the
                # operator can drop a SKILL.md or add a
                # memory row and the next inbound sees it
                # without a restart.
                system=build_system_prompt(
                    state_dir,
                    employee_id=employee_id,
                    chat_id=chat_id,
                    soul=soul,
                ),
                messages=messages,
                max_tokens=max_tokens,
                tools=tool_schemas,
            )
            final_text = result.text

            # Append the assistant turn (text + raw_blocks).
            # ``content_blocks`` carries the full assistant
            # content-block dump so tool_use IDs round-trip
            # when we send the next tool_result block.
            messages.append(ChatMessage(
                role="assistant",
                content=result.text or "",
                content_blocks=result.raw_blocks or None,
            ))

            # No tool calls → done. ``stop_reason`` is the
            # canonical signal but a model that returns a
            # plain text reply without ``end_turn`` still
            # terminates the loop (defensive — some
            # Anthropic-compatible providers omit it).
            if not result.tool_uses or result.stop_reason == "end_turn":
                break

            # Execute every tool_use in this turn. The SDK
            # allows multiple tool_use blocks in one
            # assistant message; we run them all and feed
            # the results back as a single ``user`` message
            # with one ``tool_result`` block per tool id.
            tool_results: list[dict] = []
            for tu in result.tool_uses:
                tool = get_tool(tu["name"])
                if tool is None:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": f"unknown tool: {tu['name']!r}",
                        "is_error": True,
                    })
                    continue
                try:
                    # ``_safe_path`` already validates; if
                    # the tool raises any unexpected
                    # exception we still want the LLM to
                    # see the failure, not the caller.
                    kwargs = dict(tu.get("input") or {})
                    if tool.name == "send_message":
                        # Special-case the TG callback —
                        # ``Tool.run`` only sees kwargs; the
                        # callback is injected here so the
                        # tool stays SDK-agnostic.
                        kwargs["_tg_send_callback"] = tg_send_callback
                    tr = await tool.run(tool_ctx, **kwargs)
                except Exception as e:
                    logger.exception(
                        "agent: tool %s crashed (employee=%s, "
                        "chat=%s)", tu["name"], employee_id, chat_id,
                    )
                    tr_content = f"tool {tu['name']!r} crashed: {e}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": tr_content[:8000],
                        "is_error": True,
                    })
                    continue

                # Truncate tool result content so a 50 MB
                # log file or shell output can't blow up
                # the next LLM call. The model gets a
                # notice appended when truncation kicks in.
                truncated = tr.content
                if len(truncated) > 8000:
                    truncated = truncated[:8000] + "\n…[truncated at 8000 chars]"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": truncated,
                    "is_error": tr.is_error,
                })

            messages.append(ChatMessage(
                role="user",
                content="",
                content_blocks=tool_results,
            ))

        else:
            # ``for ... else`` fires when we exit the loop
            # without ``break`` — i.e. the model kept
            # requesting tools past ``max_iter``. Log a
            # warning and return whatever text was
            # produced on the last iteration (may be empty).
            logger.warning(
                "agent: tool loop hit max_iter=%d for chat=%s "
                "(employee=%s); model still wanted tools",
                max_iter, chat_id, employee_id,
            )
    except LLMError as e:
        logger.warning(
            "llm call failed (employee=%s provider=%s): %s",
            employee_id, provider_name, e,
        )
        return _fallback_reply()

    # D.15 — per-employee token accounting. We don't have
    # # usage per-iteration in v0 (only the last response
    # is preserved); v0 records the last call's usage as a
    # proxy. Aggregating across iterations is a future
    # improvement once the provider layer supports it.
    try:
        record_token_usage(
            state_dir,
            employee_id=employee_id,
            channel=channel,
            provider=provider.name,
            model=result.model,
            usage=result.usage or {},
        )
    except Exception:
        logger.exception(
            "agent: token_usage insert failed (employee=%s, "
            "channel=%s); chat reply already succeeded",
            employee_id, channel,
        )
    logger.info(
        "llm reply",
        extra={
            "employee_id": employee_id,
            "channel": channel,
            "provider": provider.name,
            "model": result.model,
            "text_len": len(final_text),
            "thinking_len": len(result.thinking) if result.thinking else 0,
            "iterations": iterations_run,
            "tool_calls": sum(
                len(m.content_blocks) for m in messages
                if m.role == "assistant" and m.content_blocks
            ),
        },
    )
    return final_text or _fallback_reply()
