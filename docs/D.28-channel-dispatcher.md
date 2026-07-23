# D.28 — Channel dispatcher: keep tgid inside the TG adapter

## Why

Identity model after D.27:

  - **UID** = person. Used everywhere outside the channel
    adapters. Cookie identity. `_super_admins()` returns uid set.
    `ToolContext.uid`. `SessionStore.create(uid, ...)`. `Task.uid`.
  - **Session ID** = conversation. `chat_sessions.session_id`.
  - **Channel + per-channel IM id** = where to push the next
    message. Today: `chat_sessions.channel = "tg" | "webui" | "task"`
    and the per-channel IM id is the TG chat id ("tgid"). When
    Slack ships, it adds `channel = "slack"` and the per-channel
    id is a Slack mid.

Current state: 522 references to the token `tgid` across the
repo, of which ~219 are in domain code (`agent/`, `db/`,
`tools/`, `prompts/`). Each one is a leak of the TG wire
format into places that shouldn't care.

The leak has costs:

  - **Adding Slack / Teams / WeChat** = grep-replace one more
    identifier. Domain code becomes a four-way `if` ladder:
    `if tgid: ... elif slack_id: ...`. The branch is the
    opposite of what the architecture wants.
  - **Tests** must mock TG specifics to exercise generic flows
    (e.g. `send_message` tool currently reads tgid from the
    session row to find its target; a Slack-only test has to
    fabricate a fake tgid-shaped string).
  - **Wire format bakes into the public API**: the WebUI's
    `/api/auth/allowed-accounts` exposes `telegram_id`; the
    onboarding wizard inputs `tgids`. None of that is generic.

## Architecture

```
                              ┌──────────────────────────────────────────┐
                              │  domain code                              │
                              │   agent loop / tools / webui api / runner │
                              │                                          │
                              │   works in: uid + channel + session_id    │
                              └─────────────────────┬────────────────────┘
                                                    │
                                                    ▼
                              ┌──────────────────────────────────────────┐
                              │  magi/channels/dispatcher.py               │
                              │                                          │
                              │  send_to_session(session_id, text)        │
                              │  send_to_uid(uid, channel, text)          │
                              │  channel_for_session(session_id) -> str   │
                              │  lookup_im_id(uid, channel) -> str|None   │
                              │  bind_im_id(uid, channel, im_id) -> None   │
                              │  list_bindings(uid) -> [(channel, im_id)]│
                              └─────┬────────────────┬───────────────┬───┘
                                    │                │               │
                                    ▼                ▼               ▼
                          ┌─────────────┐  ┌────────────┐  ┌────────────┐
                          │ channels/   │  │ channels/  │  │ channels/  │
                          │ telegram    │  │ slack      │  │ wechat     │
                          │             │  │            │  │            │
                          │ owns tgid   │  │ owns mid   │  │ owns wid   │
                          │ owns the    │  │            │  │            │
                          │ bot token,  │  │            │  │            │
                          │ the binding │  │            │  │            │
                          │ store path  │  │            │  │            │
                          └─────────────┘  └────────────┘  └────────────┘
```

Each adapter implements a uniform interface (a `Protocol`):

```python
class ChannelAdapter(Protocol):
    name: str                                  # "telegram" / "slack"
    async def send(self, uid: int, text: str) -> None: ...
    def lookup_im_id(self, uid: int) -> str | None: ...
    def bind_im_id(self, uid: int, im_id: str) -> None: ...
```

The dispatcher holds a `{name: adapter}` registry. Adding a
channel = writing one adapter + registering it.

## Schema changes

### `chat_sessions.tgid` → `chat_sessions.delivery_address`

The column's role is "the per-channel delivery address on this
session row". Today that's a TG chat id. After rename it's
still a TG chat id (no data migration needed at the value
level), but the column name no longer leaks "this is a TG
concept". Domain code reads/writes `session.delivery_address`
opaquely.

The `_RENAME_COLUMN_MIGRATIONS` entry is added; existing
data survives (rename is metadata-only on SQLite).

### `Employee.telegram_id` stays (for now)

This column is the TG channel's binding for the user. Other
channels will eventually get sibling columns OR a
`user_im_bindings(uid, channel, im_id)` table. For D.28 we
keep the column and move all WRITES into
`magi/channels/telegram/binding.py` (the TG adapter's binding
facade). Reads from outside the TG adapter drop to zero.

### Future: `user_im_bindings` table (D.29+)

```python
class UserImBinding(Base):
    __tablename__ = "user_im_bindings"
    uid: int                  # FK -> employees.id
    channel: str               # "telegram", "slack", ...
    im_id: str                 # the per-channel IM identifier
    __table_args__ = (UniqueConstraint("uid", "channel"),)
```

When this lands, `Employee.telegram_id` becomes a denormalised
read-cache for the legacy `tg` channel, kept in sync by the
TG adapter.

## What changes file-by-file

### New files

  - `magi/channels/dispatcher.py` — the dispatcher singleton.
    Reads `chat_sessions.channel` to route. `lookup_im_id`
    delegates to the per-channel adapter. The dispatcher's
    `send_to_session(session_id, text)` is the ONE place that
    joins session + adapter; nobody else does that join.

  - `magi/channels/telegram/__init__.py` — exports the TG
    adapter. The adapter wraps `bot.send_message`, the
    `tg_bindings.py` writes, and the wizard's `bind_uid` step.

### Files that lose `tgid` reads

  - `magi/agent/tools/send_message.py` — replace
    `_resolve_tg_target(ctx.session_id)` with
    `dispatcher.send_to_session(ctx.session_id, text)`. The
    tool no longer reads `session.tgid`.

  - `magi/agent/tools/schedule_task.py` — `_session_tgid_str`
    → `_session_delivery_address`. Reads `session.delivery_address`
    (which may be a tgid-shaped string today; the field name
    is opaque to the tool).

  - `magi/agent/proactive/runner.py` — drop the
    `_tg_send_callback` closure construction. The runner calls
    `dispatcher.send_to_session(task.session_id, reply)` and
    the dispatcher handles channel routing. The runner no
    longer needs to know which channel the task fires into.

  - `magi/agent/loop.py` — replace the few `tgid`-named
    variables (`tgid` parameter, log fields) with `session_id`
    + `uid`. The agent loop is channel-agnostic.

  - `magi/channels/webui/api/auth.py` — drop the
    `_tgid_for_uid` helper. The auth flow calls
    `dispatcher.send_to_uid(uid, channel="telegram", text=code)`
    where `channel="telegram"` is the *user's default channel*
    (the dispatcher picks the right one in future; today it's
    the only one).

  - `magi/channels/webui/api/chat.py` — `_telegram_id_str_for_uid`
    removed. The cookie-init / session-create flow stamps
    `delivery_address` on the new session row by calling
    `dispatcher.lookup_im_id(uid, "telegram")` (channel
    pinned to TG by the chat-send channel argument).

  - `magi/channels/webui/api/onboarding.py` — the wizard's
    "bind TG chat id to this UID" step moves to
    `magi/channels/telegram/binding.py::bind_uid(uid, tgid, code)`.
    The wizard's HTTP endpoint stays (operator-facing) but its
    body is now `{uid, channel: "telegram", im_id, code}` and
    it delegates to the adapter.

  - `magi/channels/webui/api/tg_bindings.py` — moves to
    `magi/channels/telegram/binding.py` and shrinks to just
    the API surface the wizard uses. Reads/writes
    `Employee.telegram_id` happen only inside this file.

  - `magi/channels/webui/api/chat_sessions.py` — the
    `SessionSummary` and `SessionDetail` schemas drop the
    `tgid` field. If a UI surface needs the per-channel
    delivery address, it calls `/api/channels/<channel>/bindings`
    (one endpoint per channel; the dispatcher routes).

  - `magi/agent/memory/session/store.py` — the `Session`
    dataclass's `tgid` field renames to `delivery_address`.
    Reads outside `channels/telegram/` get the value as an
    opaque string; the only caller that interprets it is the
    dispatcher.

  - `magi/agent/memory/session/tables.py` — column rename.

  - `magi/agent/memory/session/auto_title.py` — `TitleJob.tgid`
    drops (the worker doesn't need it; logs use `uid` +
    `session_id` instead).

### Files that keep `tgid` (legitimate, inside the adapter)

  - `magi/channels/telegram/bot.py` — the bot's update handler
    reads `update.effective_chat.id` (= tgid). Calls
    `bot.send_message(chat_id=..., text=...)` (vendor kwarg).
    This is the ONE place that touches the TG client API.

  - `magi/channels/telegram/binding.py` (new, replacing
    `tg_bindings.py` + `onboarding.py`'s TG-binding step) —
    owns the `Employee.telegram_id` writes and the verification
    code path. Imports `bot.py`'s send helpers.

  - Tests under `magi/tests/unit/` that exercise TG-specific
    flows — they keep using tgid via the dispatcher or
    directly call the TG adapter's helpers.

## Migration order

  1. **Add `chat_sessions.delivery_address`** (rename column
     via `_RENAME_COLUMN_MIGRATIONS`). All references to
     `chat_sessions.tgid` become `chat_sessions.delivery_address`.
     `Employee.telegram_id` stays put for now.

  2. **Add `magi/channels/dispatcher.py`** with the `Protocol`
     interface + a registry. Initially the registry contains
     exactly one adapter: the TG adapter. The dispatcher
     delegates `lookup_im_id` / `send` / `bind_im_id` to the
     right adapter.

  3. **Move TG binding write-path** into
     `magi/channels/telegram/binding.py`. The wizard's
     `/api/onboarding/save-admin` body becomes
     `{uid, channel: "telegram", im_id}` and delegates to
     the adapter. `Employee.telegram_id` writes are now an
     implementation detail of the adapter.

  4. **Replace `_resolve_tg_target` and `_tg_send_callback`**
     in tools/runner with `dispatcher.send_to_session`. The
     agent loop stops reading `chat_sessions.tgid`.

  5. **Refactor auth's "send verification code"** to call
     `dispatcher.send_to_uid`. The auth endpoint no longer
     knows what channel the user is bound to.

  6. **Audit + drop stragglers.** After steps 1-5, any
     remaining `tgid` reference outside the TG adapter is a
     leak. `grep -rn tgid magi/ --exclude-dir=.venv` should
     return only TG-adapter + tests.

  7. **(D.29, optional)** Introduce the `user_im_bindings`
     table. Move the data, drop `Employee.telegram_id`. Each
     new channel adapter adds its own rows.

## What does NOT change

  - **UID stays the cookie identity.** All auth/admin gating
    remains UID-based.
  - **Session row model stays.** `chat_sessions.session_id`,
    `chat_sessions.uid`, `chat_sessions.channel`,
    `chat_sessions.messages`. The change is purely on the
    `tgid` column → `delivery_address` rename and the
    contract that callers treat it as opaque.
  - **The TG vendor wire format stays.** `bot.send_message(
    chat_id=<tgid>, text=...)` is what python-telegram-bot
    requires; nothing upstream of the adapter changes this.

## Out of scope (deliberately)

  - Real Slack / Teams / WeChat adapters. D.28 is the
    structural change that makes them cheap; landing each
    adapter is its own milestone.
  - Per-IM-type feature parity (read-receipt emoji, typing
    indicator, etc.). Each adapter implements what its
    channel supports; the dispatcher surface is uniform.
  - Renaming the public cookie name `magi_session` or any
    HTTP wire changes beyond what's strictly required by
    `chat_sessions.delivery_address` and the wizard's
    payload.
  - Multi-IM-per-UID dispatch policy. Today: the dispatcher
    picks the user's only bound channel. Future: prefer the
    channel the user last used; fall back to default. Out of
    scope for D.28.

## Acceptance criteria

  - `grep -rn "\btgid\b" magi/ --exclude-dir=.venv --exclude-dir=node_modules`
    shows ONLY:
      - `magi/channels/telegram/` files (the adapter)
      - test files (which can keep using tgid because they
        exercise the adapter directly)
      - migration entries (the column rename record)
    - 0 hits in `magi/agent/`, `magi/channels/webui/api/`,
      `magi/agent/memory/session/`, `magi/agent/db/`.
  - `pytest magi/tests/` passes.
  - `tsc --noEmit` passes.
  - The wizard can still bind a TG chat to a UID and that
    UID can log in.
  - The runner can still deliver a reply to a task session
    via TG.
  - The `send_message` tool can still push a side-channel
    message to the user's TG.

## Approximate scope

  - New files: ~3 (`dispatcher.py`, `channels/telegram/__init__.py`,
    `channels/telegram/binding.py`)
  - Refactored files: ~10 (agent loop, tools/send_message,
    tools/schedule_task, runner, webui api/auth, chat,
    chat_sessions, onboarding, tg_bindings, session/tables,
    session/store, session/auto_title)
  - Migration entries: 1 (`chat_sessions.tgid` →
    `chat_sessions.delivery_address`)
  - Tests to update: most unit tests under `magi/tests/unit/`
    (rename `tgid` → `delivery_address` in seeds/asserts).

The work is mechanical once the architecture is locked in.
