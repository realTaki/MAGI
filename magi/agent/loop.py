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
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from magi.agent.llm import ChatMessage, LLMError, get_provider
from magi.agent.tools.skill_loader import format_skills_block, get_skill_loader
from magi.agent.tools.base import ToolContext
from magi.agent.tools.registry import get_tool, get_tool_schemas
from magi.agent.llm.tokens import estimate_messages_tokens
from magi.agent.memory.session import (
    SessionStore,
    SessionMessage,
    new_session_id,
    utcnow_iso as _sessions_utcnow_iso,
)
from magi.agent.prompts import load_compaction_prompt

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

def _read_soul(state_dir: str) -> str:
    """Load the persona text from the workspace's ``SOUL.md``.

    Path resolution goes through :func:`magi.agent.workspace.workspace_root`
    so a deployer that sets ``MAGI_WORKSPACE_DIR`` (state lives
    outside the workspace tree) still gets the right file.

    This is a **read** function — it does not bootstrap or write
    to disk. :func:`magi.agent.workspace.bootstrap_workspace`
    runs once at boot from ``magi.node`` and is responsible
    for keeping ``SOUL.md`` in place. If the file is still
    missing (e.g. operator wiped the workspace mid-run, or the
    bundled prompt is absent from the install), we fall back to
    the bundled ``prompts/fallback_persona.md`` rather than
    write anything — the agent loop should never silently
    mutate on-disk state.
    """
    from magi.agent.prompts import load_fallback_persona
    from magi.agent.workspace import workspace_root

    soul_path = workspace_root(state_dir) / _SOUL_FILENAME
    try:
        text = soul_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return load_fallback_persona()
    text = text.strip()
    return text or load_fallback_persona()


def _record_token_usage(
    state_dir: str,
    *,
    employee_id: int,
    channel: str,
    provider: str,
    model: str | None,
    usage: dict,
) -> None:
    """Insert one ``token_usage`` row for a successful LLM call.

    Synchronous because we're already past the async boundary
    (the LLM returned). The SQL insert is one row in a
    dedicated table; latency is bounded by SQLite WAL commit
    (~ms). Pushing it onto the asyncio event loop would add
    bookkeeping for no measurable gain.

    ``usage`` keys follow the Anthropic SDK's ``Usage`` shape
    (see :class:`magi.agent.llm.provider.ChatResult.usage`).
    Unknown keys are ignored; missing keys default to 0 so
    a provider that returned no usage metadata still gets a
    row (call count stays honest).

    Raises whatever the ORM raises — caller is responsible
    for swallowing (we don't want a transient DB hiccup to
    break a chat that already succeeded).
    """
    from magi.agent.db import TokenUsage, open_session

    in_t = int(usage.get("input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    cc_t = int(usage.get("cache_creation_input_tokens") or 0)
    cr_t = int(usage.get("cache_read_input_tokens") or 0)

    with open_session() as session:
        session.add(TokenUsage(
            employee_id=employee_id,
            channel=channel,
            provider=provider,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cache_creation_tokens=cc_t,
            cache_read_tokens=cr_t,
        ))
        session.commit()



def _build_messages_from_session(
    state_dir: str,
    chat_id: str,
    session_id: str | None,
    new_user_text: str,
) -> list["ChatMessage"]:
    """Load the prior-turn history into the LLM-facing
    message list (D.17). Returns the existing
    ``sess.messages`` followed by the brand-new user
    text. ``sess.archive`` is intentionally NOT read;
    it's the forensic record and the LLM never sees it.
    """
    if not session_id:
        return [ChatMessage(role="user", content=new_user_text)]
    sess = SessionStore(state_dir).get(chat_id, session_id)
    if sess is None:
        return [ChatMessage(role="user", content=new_user_text)]
    out: list[ChatMessage] = []
    for m in sess.messages:
        llm_role = "user" if m.role in ("user", "system") else "assistant"
        out.append(ChatMessage(role=llm_role, content=m.text))
    out.append(ChatMessage(role="user", content=new_user_text))
    return out


async def _call_llm_for_summary(
    *,
    state_dir: str,
    employee_provider: str,
    employee_api_key: str,
    employee_model: str | None,
    to_compress: list["ChatMessage"],
) -> str | None:
    """One LLM call to compress ``to_compress`` into a
    structured summary. Uses the same provider + creds
    as the main chat (the employee is paying for it).
    Returns the summary text, or ``None`` on any failure
    so the caller can fall back to "no compaction
    happened".
    """
    from magi.agent.prompts import load_compaction_prompt

    system = load_compaction_prompt()
    # Serialise the messages as plain text. Format mirrors
    # the standard Anthropic Messages API ``role: text``
    # lines so the LLM sees familiar structure.
    user_lines: list[str] = []
    for m in to_compress:
        who = m.role.upper()
        user_lines.append(f"[{who}]\n{m.content}")
    user_content = "\n\n".join(user_lines)
    if len(user_content) > 6000:
        # Hard cap: if the input to the compaction call
        # itself exceeds ~24 K tokens (the heuristic gives
        # 24 K at 4 chars/token), the call is too expensive
        # to make sense for v0. Skip and log.
        return None
    try:
        provider = get_provider(employee_provider, employee_api_key, employee_model)
        result = await provider.chat(
            system=system,
            messages=[ChatMessage(role="user", content=user_content)],
            max_tokens=1024,
        )
        text = (result.text or "").strip()
        return text or None
    except Exception as e:
        logger.exception(
            "compact: LLM call failed (employee=%s); skipping",
            employee_id_for_log(state_dir),
        )
        return None


def _chat_to_session_message(m: "ChatMessage") -> SessionMessage:
    """Translate a runtime ChatMessage to the storage
    SessionMessage shape. v0 archive doesn't carry tool
    blocks (those are in-loop only); ``text`` is enough.
    The timestamp is "now" (we're rewriting history, the
    original timestamps would be misleading).
    """
    role = m.role if m.role in ("user", "assistant", "system") else "user"
    return SessionMessage(
        role=role,
        text=m.content,
        ts=_sessions_utcnow_iso(),
        message_id=new_session_id(),
    )


def _employee_id_for_log(state_dir: str) -> int | None:
    """Best-effort employee_id for log lines from the
    compact helper. Reads the cookie-side admin gate is
    not available here (we're inside the agent loop, not
    the API layer), so this just returns None; the
    caller has access to employee_id directly and prefers
    passing it. We keep the symbol so the failure-path
    log line in ``_call_llm_for_summary`` doesn't
    NameError.
    """
    return None


async def _maybe_compact(
    state_dir: str,
    chat_id: str,
    session_id: str | None,
    messages: list["ChatMessage"],
    *,
    employee_provider: str,
    employee_api_key: str,
    employee_model: str | None,
) -> None:
    """Estimate token cost of ``messages``. If over the
    configured threshold, run one compaction pass: move
    the older entries into ``sess.archive``, prepend a
    summary at ``messages[0]``, and shrink ``messages``
    in-place so the next LLM call sees
    ``[summary, ...recent K]``.

    No-op when there's no session yet (first turn of a
    brand-new conversation; nothing to compact).
    """
    if not session_id:
        return

    # Lazy import to avoid pulling settings.py at agent
    # module load (the SQLAlchemy dependency inside
    # settings.py would otherwise leak into tests that
    # only want handle_message).
    from magi.channels.webui.api.system_settings import (
        get_compact_context_window,
        get_compact_threshold_pct,
        get_compact_keep_recent,
    )

    keep = get_compact_keep_recent(state_dir)
    # Already short enough: nothing to compact.
    if len(messages) <= keep:
        return

    total = estimate_messages_tokens(messages)
    threshold = get_compact_context_window(state_dir) *         get_compact_threshold_pct(state_dir) // 100
    if total <= threshold:
        return

    # Slice: oldest entries (everything before the last K)
    # are about to be archived; the last K stays verbatim.
    to_archive = messages[:-keep]

    # Call LLM for summary. Failure → no compaction this
    # turn; the next turn will try again (the in-memory
    # ``messages`` list is unchanged so the operator sees
    # the full context this turn at least).
    summary_text = await _call_llm_for_summary(
        state_dir=state_dir,
        employee_provider=employee_provider,
        employee_api_key=employee_api_key,
        employee_model=employee_model,
        to_compress=to_archive,
    )
    if not summary_text:
        logger.warning(
            "compact: no summary produced; session will retry "
            "next turn (messages=%d, total_tokens~%d)",
            len(messages), total,
        )
        return

    # Build summary system message. Lives at messages[0]
    # going forward.
    summary_msg = ChatMessage(
        role="user",  # later mapped to "user" via _build_messages_from_session
        content=f"[Prior conversation summary]\n{summary_text}",
    )

    # Persist: append old messages to archive, prepend
    # summary to active, update active_tail_count and
    # last_compaction_at. Atomic write via _write().
    store = SessionStore(state_dir)
    sess = store.get(chat_id, session_id)
    if sess is None:
        return  # session disappeared mid-call; skip
    sess.archive.extend(_chat_to_session_message(m) for m in to_archive)
    sess.last_compaction_at = _sessions_utcnow_iso()
    sess.active_tail_count = keep
    sess.messages = [_chat_to_session_message(summary_msg)] + [
        _chat_to_session_message(m) for m in messages[-keep:]
    ]
    try:
        store._write(sess, bump_updated=False)
    except Exception:
        logger.exception(
            "compact: persist failed (session=%s); in-memory "
            "messages already shrunk, on-disk archive NOT "
            "written. Next chat will re-compact.", session_id,
        )

    # Shrink in-memory list. The caller (agent loop)
    # passes its own ``messages`` list in; we mutate it
    # in-place so the next LLM call sees the compacted
    # view.
    summary_msg_for_llm = ChatMessage(
        role="user",  # ChatMessage Literal doesn't allow "system" here
        content=f"[Prior conversation summary]\n{summary_text}",
    )
    messages[:] = [summary_msg_for_llm] + messages[-keep:]


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

    soul = _read_soul(state_dir)

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
    )
    tool_schemas = get_tool_schemas()

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
    # returns the prior-turn messages (in ``sess.messages``,
    # which is exactly the LLM-facing view: summary at index
    # 0 if a compaction has happened, else the most recent K
    # verbatim turns) plus the brand-new user text. ``archive``
    # is NOT included — it's the forensic record only.
    messages: list[ChatMessage] = _build_messages_from_session(
        state_dir, chat_id, session_id, text,
    )

    final_text = ""
    iterations_run = 0
    try:
        for _iteration in range(max_iter):
            iterations_run += 1
            # D.17 — compact the in-memory message list if it
            # has grown past the configured threshold. Runs
            # before EVERY LLM call so a long tool chain
            # can't push the next request over the model's
            # context window. ``_maybe_compact`` mutates
            # ``messages`` in-place; on failure (no summary
            # produced, persistence error) the list is left
            # unchanged and the next call sees the full
            # history.
            await _maybe_compact(
                state_dir,
                chat_id,
                session_id,
                messages,
                employee_provider=employee_provider or "",
                employee_api_key=employee_api_key or "",
                employee_model=employee_model,
            )

            result = await provider.chat(
                # Skills: frontmatter list appended to the
                # SOUL system prompt. ``format_skills_block``
                # returns ``""`` when no skills are registered,
                # so we short-circuit to ``soul`` verbatim and
                # save the per-turn tokens. Read every turn
                # rather than caching: an operator may drop a
                # SKILL.md into the workspace and the next
                # restart picks it up; the per-turn cost is
                # negligible (a couple dozen lines of text).
                system=(soul + format_skills_block(get_skill_loader().list())).strip() or soul,
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
        _record_token_usage(
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

    # D.15 — per-employee token accounting. Every chat
    # call in v0 has a concrete ``employee_id`` — both
    # channel paths (WebUI cookie admin + TG bound
    # employee) resolve to a row before reaching the LLM.
    # The FK NOT NULL on ``token_usage.employee_id``
    # surfaces any future channel that arrives without a
    # ``chat_id`` → ``Employee`` mapping. Failure here is
    # logged but does NOT raise: a missing token_usage
    # row is a statistical gap, not a user-visible
    # failure mode.
    try:
        _record_token_usage(
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
            "text_len": len(result.text),
            "thinking_len": len(result.thinking) if result.thinking else 0,
        },
    )
    return result.text
