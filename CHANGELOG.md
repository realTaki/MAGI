# Changelog

## [Unreleased]

### Added
- **BUS-centric Hook subsystem** (planned plugin/hook surface).
  11 first-version hook points: `agent.input.pending`,
  `llm.request.prepared`, `llm.response.received`, `tool.call.pending`,
  `tool.result.received`, `a2a.invocation.pending`, `a2a.result.received`,
  `delivery.pending`, `run.transition.committed`, `operation.failed`,
  `operation.dead_lettered`. Hooks declare required `HookDataScope`s at
  registration; BUS materializes a frozen `HookEnvelope` with only the
  declared scopes. Handlers NEVER receive a `Bus` reference — the
  envelope is the only input. Two new persistent tables
  (`hook_evaluations`, `hook_plugin_configs`) + Alembic revisions 0003
  and 0004. Architecture tests (`test_hook_import_boundaries.py`,
  `test_hook_envelope_purity.py`) enforce the boundary; the legacy
  fire-and-forget `magi.plugins.bus` is removed. Tool worker now
  gates on `TOOL_CALL_PENDING` before invoking executors; agent step
  gates on `LLM_REQUEST_PREPARED` before provider calls.
- Unified `contacts` table (merges `employees` + `contact_entries` + `user_im_bindings`)
- `magics` + `magis` tables replacing the old `departments` tree
- Full CRUD APIs for MAGIC teams and Magi agents
- Knowledge → Contacts pane with dual-mode view (directory + notes)
- Multi-language landing page (zh / en / ja)
- Icon set for MAGIC teams, Magis, Contacts

### Changed
- "Organization" tab → "智群" (Swarm) tab with MAGI teams + Magis management
- `Employee` → `Contact` across the entire codebase
- `departments` concept fully removed
- All route imports now use `auth_gates` instead of `departments`
- Renamed Python package `magi.channels.webui` → `magi.channels.api` and
  flattened its inner `api/` subpackage into the parent. The FastAPI app,
  every router, and the `magi/channels/api/` module now serve the generic
  MAGI HTTP API (browsers, the A2A peer ingress, and future non-web
  clients) — not only the WebUI frontend. The `magi webui` CLI subcommand,
  the `magi-webui` Kubernetes Service, `MAGI_WEBUI_PORT`/`MAGI_WEBUI_HOST`
  env vars, and the `WebUI/` React frontend are unchanged: those
  refer to the frontend service, not the renamed Python package.
- **Eve → Eva rename (full sweep)**: every remaining `Eve`/`EVE`/`eve`
  identifier, file, directory, and runtime token is now `Eva`/`EVA`/`eva`.
  - `EveRuntime` → `EvaRuntime`; `magi/bus/models/magis/eve_runtime.py` → `eva_runtime.py`
  - `KubernetesEveBackend` → `KubernetesEvaBackend`
  - `eve_runtimes` table → `eva_runtimes` (Alembic 0001 baseline edited in place; recreate dev DBs)
  - `eve-example` overlay dir → `eva-example`; matching secrets example file renamed
  - `EVE_IMAGE` / `MAGI_EVE_IMAGE` env var removed (was misleading — both
    ADAM and EVA pods run the same `magi` image). Renamed to `MAGI_IMAGE`
    in `magi/orchestrator/kubernetes.py`, `deploy/k8s/bootstrap-k8s.sh`,
    `deploy/k8s-dev/bootstrap-k8s-dev.sh`, and `deploy/k8s/control/configmap.yaml`
  - `MAGI_NODE_ROLE=eve` → `eva`; validation set `{"adam","eva"}`
  - `source='eve'` default → `eva` (alembic baseline + model defaults)
  - `[eve]` pyproject extra → `[eva]`
  - i18n keys `positionEve`/`startEve`/`stopEve` → `positionEva`/`startEva`/`stopEva`
  - `docs/terms.md` rationale updated to record the token flip
- **MAGI creation-flow refactor** — split create-time identity from
  runtime configuration. Provider / API key / model no longer ride in
  on `POST /api/magic`; they live in a per-MAGI `runtime_settings.toml`
  written by the new `PATCH /api/magic/self/provider` endpoint after
  the runtime starts. Highlights:
  - Bootstrap seeds the root MAGIS (`Genesis`) and its ADAM (`EVA-000`)
    with explicit `id=0`; both DB engines (SQLite + Postgres) accept
    the explicit id and the Postgres sequence is reset to advance from
    `max(id)`. Application-layer `_next_id` is the single source of
    truth for new rows on either backend.
  - `MAGICCreate` now requires `magis_id`; the service auto-creates
    the direct MAGIS membership (defaulting to the target MAGIS's
    reserved `EVA` role when `role_id` is omitted). New `magic_name_duplicate`
    error code on collision.
  - New Alembic 0005 `magic.name` UNIQUE index (de-dupes pre-existing
    duplicates by suffixing with id, then builds the index).
  - New `magi/bus/runtime_settings.py` — JSON file read/write with
    atomic temp-file + rename; `load_runtime_settings` returns
    defaults on missing / corrupt files. `MagicService.provider_configuration`
    reads from the file, with a legacy fallback to the `magic.provider` /
    `magic.api_key` columns for pre-refactor rows.
  - New `runtime_provider` router mounted on every MAGI runtime:
    `GET / PATCH / DELETE /api/magic/self/provider`. WebUI proxy
    (`/api/runtime/{magic_id}/magic/self/provider`) lets an admin edit
    a non-session MAGI's settings without first switching sessions;
    non-admin sessions stay pinned to their selected MAGI.
  - `MagicPane.tsx` create form now has MAGIS + role selectors with
    `EVA-NNN` default name; the per-row edit panel disables the
    Provider / API key / model inputs until the runtime reaches
    `running` (the file lives next to the runtime's workspace).
  - Bug fix: `MagicService._runtime_magic` (and the matching
    `MagisService.current_runtime_magic_id`) used `if root and root.adam_id`
    which silently treated `adam_id == 0` as falsy. Replaced with
    `root.adam_id is not None` so the seeded ADAM resolves correctly.
  - 10 new integration tests in
    `tests/integration/test_magic_creation.py` covering bootstrap ids,
    auto-bind membership, ADAM role uniqueness, name duplicates,
    missing MAGIS, runtime-settings round-trip / missing / corrupt
    files, and `provider_configuration` reading the file.

### Removed
- `departments` table and all related code
- `user_im_bindings` table
- `contact_entries` table
- `EmployeesPane` and `DepartmentsPane` frontend components

---

## v0.1.0 (Initial)

- C0–C2: WebUI + Telegram channel, SQLite ORM, session memory
- Agent loop with interrupt-aware message handling
- Auto-compaction and FTS5 search
- Contact and memory subsystems with LLM-callable tools
- Proactive task scheduler (APScheduler)
- Multi-LLM provider support (Anthropic, OpenAI, Minimax)
