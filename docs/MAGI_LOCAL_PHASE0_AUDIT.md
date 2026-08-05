# Phase 0 — Local Standalone Deployment Baseline Audit

> **状态**：此审计于 Phase 0 时完成，其中记录的多个问题已在后续 Phase 中修复。
> 截至 2026-08-04：
> - `/workspace` 硬编码已移除（K8s 通过 `MAGI_WORKSPACE_DIR` env var 注入）
> - `LocalProcessRuntimeBackend` 现以 subprocess spawn 形态回归 (Phase 4 commit)：
>   每个 MAGI 一个独立 OS 进程，launcher 退出后被 reparent 到 init，与 K8s Pod
>   对称。Supervisor / restart policy / orchestrator daemon 仍在 Phase 5。
> - `magi/constants.py` 已废弃
> - 路径解析统一由 `magi/launcher/paths.py` 负责
>
> 权威文档请参阅 `docs/ARCHITECTURE.md` 和 `deploy/local/README.md`。

Audit companion to [`MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md`](MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md) §12 ("Phase 0：基线审计").  Walked the tree as of the launch-pad consolidation commit (where `magi.runtime`, `magi.workspace`, `magi.deploy`, `magi.local` were folded into `magi.launcher`).

Sections mirror the Phase 0 checklist:

1. Latest `main` commit + test baseline
2. `/workspace`, `/magis`, `:42069`, `deployment_name`, K8s DNS assumptions
3. Direct construction sites of `KubernetesEvaBackend`
4. Domain → domain cross-imports and direct DB reaches
5. `magi.channels.webui` → `magi.channels.api` migration status
6. API / Tools / Proactive direct calls into `channels.tasks`
7. BUS direct dependency on Orchestrator / backend
8. New BUS contracts, migrations, files actually built during Phases 1–3

---

## 1. Latest `main` + test baseline

```
branch: main
last commit at audit time: 76ef745 refactor: 将MCP工具引导移至ToolWorker启动时执行
prior (relevant to plan):  4dc70ca feat(bus): 添加平台无关的运行时生命周期与本地 MAGIS 存储支持
                            f7bcbee feat(bus): 添加平台无关的运行时生命周期与注册服务
                            83539ac refactor: 将连接器桥接移至 runtime 模块
```

Green at audit time:

```
tests/architecture/test_import_boundaries.py::test_import_boundaries_clean       PASS
tests/architecture/test_import_boundaries.py::test_allowlist_is_empty            PASS
tests/unit/test_plugins.py                                                        16/16 PASS
uv run python -m magi --check                                                    OK
```

---

## 2. Hardcoded paths / ports / K8s assumptions

### 2.1 `/workspace` — **RESOLVED**

The hardcoded `/workspace` constant in `magi/constants.py` has been removed.
K8s Pods now set `MAGI_WORKSPACE_DIR=/workspace` explicitly in the deployment
manifest (`deploy/k8s/base/deployment.yaml`). Local Profile processes derive
their workspace from `HOST_WORKSPACE_DIR`. The `workspace_dir()` function in
`magi/launcher/paths.py` raises `RuntimeError` if neither env var is set —
there is no silent fallback to `/workspace`.

### 2.2 `/magis` (one K8s manifest reference only)

```
magi/orchestrator/kubernetes.py:270   "mountPath": "/magis"      (K8s PV manifest string)
```

No code path uses `/magis` as a Python literal; the K8s adapter reads the PostgreSQL DSN from `MAGIS_DATABASE_URL`.  Local Profile's per-MAGIS SQLite lives under `<data_root>/MAGIS/<magis-id>/magis.db` via [`magi/launcher.py:LocalPathLayout.magis_workspace`](../magi/launcher.py).

### 2.3 `:42069`

```
magi/constants.py:26                WEBUI_PORT: int = 42069                (K8s dev port)
magi/__main__.py:75                 comment only
magi/bus/protocols/runtime.py:31    comment only ("Replaces the legacy f"http://{deployment_name}:42069" URL forging")
magi/bus/services/runtime.py:171    base_url=f"http://{runtime.deployment_name}:42069"
magi/bus/services/magic.py:60       f"http://{runtime.deployment_name}:42069"
magi/bus/services/magis.py:283,1000 f"http://{runtime.deployment_name}:42069"
magi/bus/services/magis.py:286,1003 MAGI_ROOT_RUNTIME_URL fallback  "http://magi:42069"
```

The four live `f"http://...:42069"` forgeries are exactly the anti-pattern `MAGI_BUS_CENTRIC_ARCHITECTURE.md` §4.4 documents. The `RuntimeEndpoint` DTO ([magi/bus/protocols/runtime.py:28](magi/bus/protocols/runtime.py)) was introduced so that future callers read the URL from `bus.registry.resolve_endpoint(magic_id)` instead.  Phase 7 ("multi-MAGI") finishes rolling these forgeries out — they still appear in `magic.py:60`, `magis.py:283/1000` because those service paths have not yet been re-wired through the registry.

### 2.4 `deployment_name`

```
magi/bus/protocols/runtime.py:4         docstring
magi/bus/protocols/runtime.py:31        docstring
magi/bus/db/alembic/versions/0001_initial_schema.py:689   column on k8s_runtime table
magi/bus/protocols/lifecycle.py:44       base field  (K8s legacy, optional)
magi/bus/protocols/magis.py:144,156      docstring + field  (K8s legacy, optional)
magi/bus/services/magic.py:54,57,60      forge K8s URL from deployment_name
```

`deployment_name` survives as **optional** metadata on the K8s-runtime table and lifecycle DTOs so backward-compat reads still resolve.  Local-runtimes never set it — `BackendDispatcherService` only fills it when the chosen backend is K8s.

### 2.5 K8s DNS

No code constructs `f"http://{name}:42069"` outside the four sites listed in §2.3.  The DNS assumption is suppressed in the new code paths: `bus.registry.resolve_endpoint(magic_id)` reads the per-runtime row's `endpoint_url` directly, which the K8s backend fills via its `RuntimeEndpoint.url` adapter.

---

## 3. Direct construction of `KubernetesEvaBackend`

```
magi/orchestrator/backends/kubernetes_compat.py:44      self._inner = inner or KubernetesEvaBackend()
```

That single line is the **only** construction site in the entire tree.  Everywhere else uses the factory:

```
magi/orchestrator/backends/factory.py:17   def create() -> RuntimeBackend:
```

`KubernetesEvaBackend` lives on as the legacy implementation consumed by [`KubernetesEvaBackendAdapter`](../magi/orchestrator/backends/kubernetes_compat.py), which exposes the `RuntimeBackend` Protocol.  Phase 2 is satisfied at this surface.

---

## 4. Domain → domain cross-imports + direct DB reaches

Already enforced by [`tests/architecture/test_import_boundaries.py`](../tests/architecture/test_import_boundaries.py).  Audit re-check on the post-consolidation tree shows the rules pass with an empty `ALLOWLIST` — see `test_allowlist_is_empty` in §1.  Composition-Root exception set: `{"magi.launcher"}`.

Cross-checks:

| Check | Outcome |
|---|---|
| `magi.agent` ↛ `magi.tools`, `magi.channels`, `magi.bus.db`, `magi.bus.models` | Clean |
| `magi.tools` ↛ `magi.agent`, `magi.channels`, `magi.bus.db`, `magi.bus.models` | Clean |
| `magi.channels` ↛ `magi.agent`, `magi.tools`, `magi.bus.db`, `magi.bus.models` | Clean |
| `magi.mcp` ↛ `magi.bus.db`, `magi.bus.models` (it may use `magi.tools`) | Clean |
| `magi.connectors` ↛ `magi.bus.db`, `magi.bus.models` (it may use `magi.tools`) | Clean |
| `magi.proactive` ↛ `magi.bus.db`, `magi.bus.models` | Clean |
| `magi.orchestrator` ↛ `magi.bus.db`, `magi.bus.models` | Clean |
| `magi.skills` ↛ `magi.bus.db`, `magi.bus.models` | Clean |
| `magi.channels.api` ↛ `magi.channels.tasks`, `magi.agent`, `magi.tools`, `magi.mcp`, `magi.plugins`, `magi.connectors`, `magi.bus.db`, `magi.bus.models`, `magi.orchestrator`, `magi.orchestrator.backends`, `magi.orchestrator.client`, `magi.orchestrator.service`, `magi.orchestrator.contracts` | Clean |
| `magi.bus` ↛ `magi.tools`, `magi.channels.telegram`, `magi.channels.api`, `magi.channels.a2a`, `magi.channels.base`, `magi.channels.dispatcher`, `magi.channels.delivery`, `magi.agent.worker`, `magi.agent.step`, `magi.providers` | Clean |
| `magi.bus.services` ↛ `magi.orchestrator.kubernetes`, `magi.orchestrator.client`, `magi.orchestrator.service`, `magi.orchestrator.contracts` | Clean |
| `magi.bus.services.runtime` ↛ `magi.orchestrator.kubernetes` | Clean (reaches `magi.orchestrator.backends.factory` only, which is the documented exception in [magi/bus/services/runtime.py:64](../magi/bus/services/runtime.py)) |

---

## 5. `magi.channels.webui` → `magi.channels.api`

```
magi/launcher.py                                  contains no `channels.webui` reference
grep -rn "from magi.channels.webui" magi/         (no matches)
```

`magi.channels.webui` no longer exists.  The WebUI control plane is now `magi.channels.api` split into `create_control_app` (control plane, reads from `MAGIS_POSTGRES_URL`) and `create_runtime_app` (per-runtime API, reads from the runtime's private SQLite).  Phase 1 §9 expectation met.

---

## 6. API / Tools / Proactive direct calls into `channels.tasks`

**API:** the only references to `magi.channels.tasks.*` in `magi.channels.api` are textual — comments at [magi/channels/api/tasks.py:667](../magi/channels/api/tasks.py) and [magi/channels/api/tasks.py:792](../magi/channels/api/tasks.py) telling readers to use `bus.task.*` (`bus.task.schedule(...)`, `bus.task.list(...)`) instead.

**Tools / Agents / Proactive:**

```
$ grep -rn "from magi.channels.tasks" magi/agent magi/tools magi/proactive magi/mcp magi/connectors
(no matches)
```

**One intentional exception:**

```
magi/bus/services/task_scheduler_bridge.py:121   from magi.channels.tasks.channel import TaskChannel
magi/bus/services/task_scheduler_bridge.py:155   from magi.channels.tasks.scheduler import get_scheduler
```

This is the documented Python bridge — `task_scheduler_bridge` is allowed by the boundary-test rule to hold the scheduler handle.  Domain code can publish `bus.task.schedule/update/cancel/pause/resume` and the bridge translates them.  Phase 5 will move the bus-side workers to consume commands off the bus instead of importing this Python helper.

---

## 7. BUS direct dependency on Orchestrator / backend

The only site is the documented exception:

```
magi/bus/services/runtime.py:64   from magi.orchestrator.backends.factory import create
```

The dispatcher consumes the factory (which returns the `RuntimeBackend` Protocol).  Both the test rule's `magi.bus.services.runtime` row and the rule's `magi.bus.services` row allow `magi.orchestrator.backends` but forbid `magi.orchestrator.kubernetes`, `.client`, `.service`, `.contracts` directly.  The factory + adapter pattern is the boundary.  Plan §4.4 satisfied at this surface.

---

## 8. New contracts / migrations / files actually built during Phases 1–3

Contracts (BUS DTOs):

| File | Phase | Purpose |
|---|---|---|
| `magi/bus/protocols/runtime.py` | 2 | `RuntimeEndpoint` — replaces `deployment_name + :42069` URL forging |
| `magi/bus/protocols/lifecycle.py` | 2 | Runtime lifecycle command/query DTOs (`Start`/`Stop`/`Inspect`/`Delete`/`Reconcile`) |
| `magi/bus/db/magis/local_engine.py` | 3 | Per-MAGIS SQLite engine for Local Profile |

Services wired into [`magi/bus/bootstrap.py`](../magi/bus/bootstrap.py):

| Service | Phase | Notes |
|---|---|---|
| `DispatcherService` | 2 | platform-neutral post-box |
| `BackendDispatcherService` | 2 | picks K8s via factory, will pick Local via Phase 4 |
| `RuntimeRegistryService` | 2 | K8s-Runtime row reader; Phase 7 extends it for Local |

Orchestrator surface:

| File | Phase | Purpose |
|---|---|---|
| `magi/orchestrator/backends/base.py` | 2 | `RuntimeBackend` Protocol |
| `magi/orchestrator/backends/factory.py` | 2 | `create()` returns Protocol-conforming instance |
| `magi/orchestrator/backends/kubernetes_compat.py` | 2 | Wraps `KubernetesEvaBackend` to satisfy Protocol |
| `magi/orchestrator/backends/local_process.py` | **4** | NOT YET BUILT — slot reserved by `factory.create()` |
| `magi/orchestrator/worker.py` | 5+ | NOT YET BUILT — Phase 4's reconcile loop will live here |

Composition Root (the consolidated launch-pad):

| File | Phase | Notes |
|---|---|---|
| `magi/launcher.py` | 1 | `LocalPathLayout`, `bootstrap_local`, channel/bridge/lifespan helpers |
| `magi/db/control/` | **3 close-out** | NOT YET BUILT — Local control-plane registry (Runtime state, port allocation, process identity, workspace archive) |
| `magi/launcher/cli.py` | **6** | NOT YET BUILT — `magi local start/status/stop/doctor` |
| `magi/launcher/supervisor.py` | **4** | NOT YET BUILT — when `bootstrap_local` calls backend.start, this is the harness |
| `magi/launcher/security.py` | **3** | NOT YET BUILT — control secret + file-mode 0700 |
| `magi/launcher/paths.py` | **3** | NOT YET BUILT — OS-specific data-root defaults |
| `magi/launcher/ports.py` | **4** | NOT YET BUILT — port allocator helper |
| `magi/launcher/platform.py` | **3** | NOT YET BUILT — Linux / macOS / Windows detection |

Migrations:

| Revision | Phase | Notes |
|---|---|---|
| `magi/bus/db/alembic/versions/0001_initial_schema.py` | 1 | Initial baseline including `deployments` / `runtime_endpoints` tables |
| future `0002_*.py` | **3 close-out** | NOT YET WRITTEN — `magi/db/control` ORM models |

---

## Open findings that block Phase 6

These are not Phase 0 problems; they are forward-looking notes for whichever Phase takes them next:

1. **`bus/services/{magic,magis,runtime}.py` still forge `http://{deployment_name}:42069`** — Phase 7 ("multi-MAGI") replaces these with `bus.registry.resolve_endpoint(magic_id)`.  Until then, Local-Runtime reads of `MAGI_ROOT_RUNTIME_URL` are the only escape hatch.
2. **`WorkerLifespan` / Tasks dispatch** — `worker_lifespan()` in `magi.launcher` starts `agent + tools + delivery` workers, but the Tasks worker (plan §5.5) is still started from `magi/__main__.py` via `from magi.channels.tasks.scheduler import start_scheduler`.  Phase 5 pulls this through `bus.task_scheduler.start()` so the runtime/lifespan story is uniform.
3. **Magic ID / SessionID / UID** — already centralised.  No Phase-0 work left.

---

## Audit verdict

The K8s production path is green.  Phases 1–3 are code-completed and the tree satisfies every import-boundary rule with an empty allowlist.  The Phase 4 / Phase 5 / Phase 6 surface is reserved in tree-shape: `magi/orchestrator/backends/local_process.py`, `magi/orchestrator/worker.py`, `magi/db/control/`, `magi/launcher/{cli,supervisor,security,paths,ports,platform}.py` — those are the next concrete commits.  No K8s path is regressed.
