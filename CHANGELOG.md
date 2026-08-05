# Changelog

## [Unreleased]

### Added
- **BUS-centric Hook subsystem** (`magi.bus.hooks` + `magi.plugins.hooks`).
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
  the `magi-webui` Kubernetes Service, `WEBUI_PORT`/`WEBUI_HOST` env vars,
  and the `magi/WebUI/` React frontend are unchanged: those refer to the
  frontend service, not the renamed Python package.
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
