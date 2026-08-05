# 名词解释 (Terminology)

> 本仓库每个名词都对一种**实体**或**身份**做了严格区分;混淆时容易把 onboarding
> 默认值、生产部署假设、SQL seed 字符串改错。读源码前先扫一遍这张表。

> Each term below maps to a single, carefully scoped entity or identity in
> this repo. Mixing them up leads to broken onboarding defaults, wrong
> production deployment assumptions, and seed-string mismatches.

---

## 三元体系:产品 / 社会 / 公民 (Product / Society / Citizen)

| 名词           | 含义 |
|----------------|------|
| **MAGI**       | 整个产品 — *M*odular *A*gentic *G*roup *I*ntelligence。也可泛指系统里任意一个 agent。|
| **MAGIS**      | **MAGI Society** — 一群 MAGI 组成的**组织**,可以有树形父子关系。MAGI Container 实际上挂在一个 MAGIS 下,持有该 MAGIS 的 PostgreSQL PVC。|
| **MAGIC**      | **MAGI Citizen** — 一个**具体**的 agent runtime 进程,属于且仅属于一个 MAGIS。一个 MAGIC = 一个独立容器 + 一个 runtime 进程 + 一份私有 SQLite。|

| Term      | Meaning |
|-----------|---------|
| **MAGI**  | The product — *M*odular *A*gentic *G*roup *I*ntelligence. Also used as the umbrella term for "any agent in the system". |
| **MAGIS** | **MAGI Society** — an *organisation* of multiple MAGIs; a tree of MAGISes. Each MAGIS owns its own PostgreSQL cluster and public PVC. A MAGIC always belongs to exactly one MAGIS. |
| **MAGIC** | **MAGI Citizen** — a *specific* agent runtime process. One MAGIC = one container + one runtime process + one private SQLite file. |

---

## 两种 archetype:ADAM / EVA

| 名词    | 含义 |
|---------|------|
| **ADAM** | *A*utonomous *D*ispatch *A*gent *M*anager — 一个 Society 的**领导**型 MAGIC。负责控制面 (WebUI)、员工管理、MAGIC 派发 / 回收、provider 与 skill 全局配置。`magic_position='adam'`。每个 Society 恰好一个。|
| **EVA**  | *E*xtended *V*irtual *A*gent — Society 的**工作**型 MAGIC。Society 可创建 / 配置 / 启动 / 停止 / 退役**多个** EVA,每个 EVA 绑定一名已指派的 employee (Telegram ID)。|

**同构原则:** ADAM 与 EVA 跑**同一份 `magi` 二进制**;archetype 仅由环境变量
`MAGI_NODE_ROLE` 在 boot 时选出,代码不动。它们不是两个独立产品。

| Term    | Meaning |
|---------|---------|
| **ADAM** | *A*utonomous *D*ispatch *A*gent *M*anager — the **manager-archetype** MAGIC of a Society. Owns the control plane (WebUI), staff management, dispatch/recycle, global provider + skill config. Stored as `magic_position='adam'`. Exactly one per Society. |
| **EVA**  | *E*xtended *V*irtual *A*gent — a **worker-archetype** MAGIC. A Society can create / configure / start / stop / retire **multiple** EVAs, each bound to one assigned employee (Telegram ID). |

**Same-binary principle:** ADAM and EVA run the **same `magi` binary**;
the archetype is picked at boot via the `MAGI_NODE_ROLE` env var, with no
code branches at deploy time. They are not two separate products.

---

## 历史命名(已废弃,仅在迁移日志里出现)

| 旧名 | 含义 |
|------|------|
| **Adam** (PascalCase) | 已被 **ADAM** 替代。|
| **EVA** (= *Everyday Virtual Employee*) | 单数 EVA 在 2026-07 命名刷新前代表"整天干活的虚拟员工";新语境下"EVA"仅是 EVA 的前身拼写,**不再是 acronym**,所有正式文档、role 名称、soul prompt 都用 EVA。|

| Old name | Meaning |
|----------|---------|
| **Adam** (PascalCase) | Superseded by **ADAM** (all caps). |
| **EVA** (formerly *Everyday Virtual Employee*) | In the pre-2026-07 framing, "EVA" was a single employee-facing agent. The 2026-07 rename reframed it as a role inside a Society; **EVA is no longer an acronym**, all product copy now uses EVA. |

> **Renamed in 2026-08:** *Adam* → *ADAM* (case + new backronym),
> *EVA* → *EVA* (different word + new backronym).
>
> **2026-08 follow-up:** the lowercase internal token was **also** flipped
> from `eve` → `eva`. `MAGI_NODE_ROLE=eva`, `source='eva'`, and
> `magic.position='eva'` now all share the spelling with the display name.
> Validation set in `magi/__main__.py` is `{"adam", "eva"}`.

---

## 运行时节点 (Runtime node)

| 名词                       | 含义 |
|----------------------------|------|
| **MAGI 节点**                | 一个正在跑的 MAGI 进程 (容器)。可能是 ADAM,也可能是 EVA。节点凭 `MAGI_NODE_ROLE` 选择 archetype,凭 `MAGI_RUNTIME_ID` 标识自己的身份。|
| **`MAGI_NODE_ROLE`**         | 环境变量,值为 `adam` 或 `eva`(小写,在代码 `magi/__main__.py` 内做 `{"adam","eva"}` 校验)。值是**内部 identifier**,不参与 UI 显示;**不要**和角色显示名 ADAM/EVA 混为一谈。|

| Term                      | Meaning |
|---------------------------|---------|
| **MAGI node**             | A running MAGI process (container). May be ADAM or EVA. Identified by its `MAGI_NODE_ROLE` (archetype) + `MAGI_RUNTIME_ID` (identity). |
| **`MAGI_NODE_ROLE`**      | Env var, value `adam` or `eva` (lowercase — internal token, **not** a display name). Validated in `magi/__main__.py` against `{"adam","eva"}`. |

---

## 通道 (Channels)

| 名词                | 含义 |
|---------------------|------|
| **TG channel**      | EVA 端的 Telegram 入口。bot 处理已绑定 TG ID 的员工消息。|
| **WebUI channel**   | ADAM 端的浏览器入口。React + FastAPI;管理员唯一管理界面。|
| **A2A channel**      | Agent-to-Agent — MAGI peer 之间经 HMAC 签名的 HTTP 内部通道。默认 scope 限同 MAGIS 内的 peer。|
| **Scheduled channel** | 内部调度任务通道;触发持久化的 cron / interval 任务。|

| Term                | Meaning |
|---------------------|---------|
| **TG channel**      | EVA-side Telegram entry point. Bot processes messages from employees whose TG ID is already bound. |
| **WebUI channel**   | ADAM-side browser entry. React + FastAPI; the single operator console. |
| **A2A channel**      | Agent-to-Agent — HMAC-signed HTTP channel between MAGI peers. Default scope: same MAGIS. |
| **Scheduled channel** | Internal scheduled-task channel; fires persisted tasks. |

---

## 存储分层 (Storage layers)

| 名词                            | 含义 |
|---------------------------------|------|
| **MAGI 私有 SQLite**              | `<workspace>/memories/magi.db`。每个 MAGIC 一份。存私人记忆、会话、联系人、本地设置、token usage、action items、runtime 状态、MCP server 配置。路径由 `MAGI_WORKSPACE_DIR`（K8s）或 `HOST_WORKSPACE_DIR`（CLI）解析。|
| **MAGIS 公共数据库**              | `MAGIS_DATABASE_URL` 指向。K8s 为 PostgreSQL，CLI / k8s-dev 为独立 SQLite。存 MAGIS 树、MAGIC 注册表、`magis_memberships`、`magis_roles`、ADAM/EVA roles、team/role instruction、provider 配置。|
| **Genesis**                      | 启动时播种的 root MAGIS,是组织树的根节点;**不依赖**名字字面量,而是 `parent_id IS NULL` 判定。|

| Term                          | Meaning |
|-------------------------------|---------|
| **MAGI private SQLite**       | `<workspace>/memories/magi.db` — one per MAGIC. Holds private memory, sessions, contacts, local settings, token usage, action items, runtime state, MCP server config. Path resolved from `MAGI_WORKSPACE_DIR` (K8s) or `HOST_WORKSPACE_DIR` (CLI). |
| **MAGIS public database**     | Reached via `MAGIS_DATABASE_URL`. PostgreSQL in K8s, separate SQLite in CLI / k8s-dev mode. Holds the MAGIS tree, MAGIC registry, `magis_memberships`, `magis_roles` (incl. ADAM/EVA), team + role instructions, provider config. |
| **Genesis**                   | The root MAGIS seeded on first boot. Identified by `parent_id IS NULL`, **not** by literal name. |

### 关键不变量

1. **一个 MAGIC 一个直接 MAGIS membership** (`uq_magis_memberships_magic`) — 不能跨 Society 兼任。
2. **一个 MAGIS 一个直接 ADAM** — `MAGIS.adam_id` 唯一;`MAGISMembership` 中把角色命名为 `ADAM` 的 MAGIC 写入 `adam_id` 列。
3. **`ADAM` / `EVA` 是 magis_roles 表里的两条**`is_reserved=True`** 行** — `RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})`,API 拒绝新建/编辑/删除;`DEFAULT_ROLE_INSTRUCTIONS` 预填 persona 描述。
4. **MAGI 私有 PVC ↔ MAGIS 公共 PVC** 各自独立 — ADAM 不挂子 MAGIS 的公共 PVC。

| Invariant | Detail |
|-----------|--------|
| One MAGIC = one direct membership | `uq_magis_memberships_magic` enforces this. |
| One MAGIS = one direct ADAM | `MAGIS.adam_id` is unique; assigning the `ADAM` role to a MAGIC writes its id into that column. |
| `ADAM`/`EVA` are reserved roles | `RESERVED_ROLE_NAMES = frozenset({"ADAM", "EVA"})`; the API refuses to create / edit / delete them. `DEFAULT_ROLE_INSTRUCTIONS` ships the persona text. |
| Private PVC ≠ public PVC | ADAM never mounts a child MAGIS's public PVC. |

---

## 编程入口 (Code entry points)

| 名词                    | 含义 |
|-------------------------|------|
| **`magi/__main__.py`**  | 单一可执行入口。`magi` 启动 runtime,`magi webui` 启动 singleton 控制面。|
| **`magi-orchestrator`** | 独立 Kubernetes 控制面进程 — 唯一可以创建 / 删除 MAGI Deployment 与 PostgreSQL Secret 的组件。MAGI 节点**不**直接拿 K8s token 或 docker socket。|
| **`magi.bus`**          | BUS — 系统内部 module 边界。ORM 模型、业务服务、DTO 都收编在 `magi/bus/` 下;**所有**通道和 orchestrator 都从这里导入,不允许别处造表。|
| **`magi.channels`**     | 通道适配器层(Telegram、WebUI、A2A、Scheduled)。只通过 bus 业务服务拿数据,不直接碰 ORM。|

| Term                    | Meaning |
|-------------------------|---------|
| **`magi/__main__.py`**  | The single CLI entry. `magi` boots a runtime; `magi webui` boots the singleton control plane. |
| **`magi-orchestrator`** | Standalone K8s control-plane process — the **only** component allowed to create / delete MAGI Deployments and PostgreSQL Secrets. MAGI nodes never receive K8s tokens or docker socket. |
| **`magi.bus`**          | The BUS — internal module boundary. Holds ORM models, business services, DTOs. Channels and orchestrator import from here; nothing else may build tables. |
| **`magi.channels`**     | Channel adapters (Telegram, WebUI, A2A, Scheduled). Read/write only through bus services — no direct ORM access. |

---

## 测试 / 脚本惯例

| 名词                       | 含义 |
|----------------------------|------|
| **`Source.EVA`** (column value) | `source = "eva"` 是一个**数据来源标识**,表示这条 `action_items` / `contacts.note` / `memory_entries` 行由 EVA 写入。该字符串保留为**小写 `eva`** —— 因为它和 `MAGI_NODE_ROLE=eva` 走同一份"角色枚举",与 SQL/Python 标识符保持一致,与 ADAM/EVA 角色**显示**名分开。|
| **`Magi` (class)**         | ORM 模型名,table = `magic`。单数。|
| **`MAGIC` (constant)**     | ORM 模型名,同上。**大写** 是 SQLAlchemy class convention,不是 ADAM/EVA role 显示名。|
| **`magis_*` (table prefix)** | 所有 MAGIS 级表都加 `magis_` 前缀,用于在公共 PostgreSQL 里和未来的其他 schema 隔离。|

| Term                       | Meaning |
|----------------------------|---------|
| **`Source.EVA`** (`source='eva'`) | The **data-source tag** on `action_items` / `contact_notes` / `memory_entries` rows written by an EVA. The literal string stays lowercase `eva` — it shares the same internal role token (`MAGI_NODE_ROLE=eva`), distinct from the ADAM/EVA **display** names. |
| **`MAGIC` (ORM class)**    | The single-MAGI ORM row (table `magic`). All caps — SQLAlchemy convention, not the ADAM/EVA role labels. |
| **`magis_*` (table prefix)** | All MAGIS-level tables are prefixed `magis_` so they stay isolated in the shared public PostgreSQL. |

---

## 部署形态 (Deployment profiles)

| 名词 | 含义 |
|------|------|
| **k8s** | 生产部署形态 — `deploy/k8s/`。把 `magi:0.1.0` 镜像部署到现有 K8s 集群,每个 MAGI 一个 Pod,MAGIS 公共数据库为 PostgreSQL。运维者需提供 K8s 集群配置。|
| **k8s-dev** | kind 单机 dev 形态 — `deploy/k8s-dev/`。单节点 kind 集群,源码挂载到 `/mnt/magi`,后端 Uvicorn + WebUI Vite 都能 HMR。它**也是**一种"local"运行方式(同样跑在本机),所以**不**叫 local,以免和下面的 `cli` 路径混淆。|
| **cli** | 单机非容器形态 — `deploy/cli/`。完全脱离 Docker / k8s,每个 MAGI 一个 OS 进程,systemd 注册可选;由 `magi cli start` / `magi cli install-service` 驱动。原本叫 "local",但 `k8s-dev` 同样跑在本机,名字歧义;改为 **cli**——"command-line driven, container-free"。|

| Term | Meaning |
|------|---------|
| **k8s** | Production deploy — `deploy/k8s/`. Pushes `magi:0.1.0` to an existing cluster; one Pod per MAGIC; MAGIS public DB is PostgreSQL. Operator supplies the cluster config. |
| **k8s-dev** | Single-node kind dev deploy — `deploy/k8s-dev/`. Source mounted into `/mnt/magi`; backend Uvicorn + WebUI Vite both reload on save. This **is** a local-mode run, so we **don't** call it "local" — that name was reserved for the non-container profile to avoid ambiguity. |
| **cli** | Container-free single-machine profile — `deploy/cli/`. Each MAGIC is its own OS process; systemd registration is optional. Driven by `magi cli start` / `magi cli install-service`. The old name "local" was retired because `k8s-dev` is also a kind of local run; **cli** ("command-line, no container") replaces it. |

> **Backend kind identifier** — the runtime backend is selected by
> `MAGI_BACKEND`. Values: `"kubernetes"` (default), `"cli"` (CLI Profile).
> The literal string `"local"` was retired in the 2026-08 rename.
> `BackendKind = Literal["kubernetes", "cli"]` in
> `magi/bus/protocols/runtime.py`.

---

## 一句话总结

> **MAGI** 是产品 / **MAGIS** 是组织 / **MAGIC** 是 agent / **ADAM** 是控制面 leader / **EVA** 是员工 worker。EVA 复用同一份 binary,仅靠 `MAGI_NODE_ROLE` 切身份。私有 SQLite 跟节点走;公共 PostgreSQL 跟 MAGIS 走。orchestrator 是唯一能 shape 集群的进程。**部署形态三选一**:生产用 **k8s**,本地+源码热更新用 **k8s-dev**,纯单机无容器用 **cli**——三者共享同一份应用抽象。

> **MAGI** is the product / **MAGIS** is the organisation / **MAGIC** is an agent.
> **ADAM** is the control-plane leader / **EVA** is the employee worker.
> EVA reuses the same binary; only the `MAGI_NODE_ROLE` env var flips the
> archetype. Private SQLite follows the node; public PostgreSQL follows the
> MAGIS. The orchestrator is the *only* process that may shape the cluster.
> Three deploy profiles ship side-by-side: **k8s** (production), **k8s-dev**
> (kind + HMR on a single box), **cli** (container-free, per-MAGI OS process).
