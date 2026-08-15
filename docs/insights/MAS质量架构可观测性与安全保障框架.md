[English Version](MAS-Quality-Architecture-Observability-Security-Framework.md)

# 多智能体系统的质量、架构、可观测性与安全保障：概念框架与研究议程

## 摘要

多智能体系统（Multi-Agent System，MAS）的研究对象，不应被简化为“多个大语言模型相互对话”。在当前以大语言模型为核心的 Agent 工程中，MAS 更准确地代表一种系统化组织智能的方法：将任务、上下文、工具、权限、状态与责任分配给多个具有一定自主决策能力的 Agent，再通过路由、委派、交接、通信、共享状态和治理机制形成整体行为。

本报告围绕 MAS 的质量目标、协作架构、可观测性与安全保障建立统一分析框架。研究依次回答五个相互关联的问题：MAS 应优化什么；MAS 如何组织；质量评测、红队和蓝队如何共享同一系统模型；研究对象如何从 LLM 的输入/输出演化为 Agent 的轨迹，再演化为 MAS 的动态因果交互图；以及如何在每个关键观测位置上统一设计质量测评、攻击验证与检测归因。该框架既可用于分析现有 MAS，也可用于指导后续 benchmark、运行时插桩和安全保障系统的设计。

本文的核心判断是：**现阶段 MAS 最直接、最可检验的工程价值是上下文隔离（context isolation）**。现实任务的潜在上下文近乎无限，而单个模型的有效上下文、注意力和专业化能力有限。MAS 的价值首先不是“增加 Agent 数量”，而是建立上下文边界、责任边界和权限边界，只让必要信息以可控制、可追溯的形式跨越边界。在此基础上，MAS 还可能带来专业化、并行、异构计算、制衡和故障隔离，但也会引入协调开销、信息失真、路由错误、共享状态污染和故障传播。因此，MAS 的设计与评测本质上是一个**多目标优化（multi-objective optimization）**问题。

可观测性也随研究对象发生变化：LLM 可观测性的主要对象是输入/输出；单 Agent 可观测性的主要对象是行动轨迹（trajectory）；MAS 可观测性的主要对象则是由多个自主主体、共享状态和外部动作共同形成的**动态因果交互图（dynamic causal interaction graph）**。在这张图中，Agent 是重要节点，但委派、消息、交接、状态读写等 **edge / interaction event** 同样是关键的最小观测单位。质量评测、红队和蓝队应共享这套底层 telemetry 和行为语义：质量评测回答系统是否按预期工作；红队回答如何主动使系统偏离预期或突破安全边界；蓝队回答偏离为何发生、如何检测、归因、隔离与恢复。安全还必须额外检查不能被违反的 security invariants，而不能被完全还原为质量指标的反面。

---

## 一、研究问题的优化表述

本报告将研究任务重构为以下五个彼此衔接的问题。

### RQ1：MAS 的成立条件、设计目标与系统级质量模型是什么？

研究重点不只是最终任务准确率，而是回答：为什么该任务需要多个 Agent；一个“好的 MAS”应在上下文隔离、任务效果、专业化、协调、通信、并行、成本、鲁棒性、治理和安全边界上满足什么条件；这些目标之间如何权衡；何时单 Agent 反而是更优解。

### RQ2：MAS 有哪些主要协作与组织架构，它们如何分配控制权、上下文、通信和状态？

研究重点是从运行机制而非产品名称出发，对 Router、Supervisor、Pipeline、Graph/Workflow、Handoff、Self-organizing/Swarm 六类控制架构进行分析；同时把 Group Chat/Debate 作为通信协商模式，把 Logical/Distributed/A2A 作为部署与信任域维度。核心问题是：**谁决定下一步由谁做什么，信息以何种形式跨越 Agent 边界？**

### RQ3：如何建立质量评测、红队与蓝队的统一分析框架？

研究重点是说明三者共享同一组系统行为属性和 telemetry，但优化目标不同：Evaluation 衡量预期行为；Red Team 主动寻找可操纵的失效路径；Blue Team 从异常行为反推原因并完成响应。同时，需要把连续型的性能质量属性与不可违反的安全不变量区分开。

### RQ4：研究对象从 LLM 到 Agent 再到 MAS 后，可观测性应如何演化？

研究重点是从 `input/output`、`trajectory` 进一步转向 `dynamic causal interaction graph`，识别 Entry、Identity、Context、Decision、Memory、Coordination、Communication、Shared State、Tool/Action、Topology、Outcome 等观测面，并定义每类 telemetry 的最小必要字段与因果关联。

### RQ5：如何把架构、观测面和三种视角转化为可执行的 MAS Assurance Matrix？

研究重点是建立：

```text
Observation Surface
        ↓
Quality Failure + Evaluation Method
        ↓
Red-team Exploit Hypothesis
        ↓
Blue-team Detection + Attribution + RCA + Response
```

该矩阵的目标不是罗列攻击名称，而是让每个系统边界都可以被测量、被攻击验证、被检测和被解释。

---

## 二、MAS 的概念边界、意义与成立条件

### 2.1 Agent 与 MAS 的工作定义

本文将 Agent 定义为：在给定身份、目标、上下文、模型、工具、记忆、权限和运行时约束下，能够感知状态、作出决策并采取行动的计算主体。

一个系统仅包含多个 Agent 对象，并不自动构成有研究意义的 MAS。本文所称 MAS 至少具有以下两个特征：

1. 系统中存在两个或以上可区分的决策主体，它们拥有不同的上下文、能力、状态、目标、权限或生命周期。
2. 系统结果依赖主体之间的协调关系，而不仅是若干独立模型调用的简单拼接。

MAS 的意义也不是“一个 Agent 不够聪明，所以增加 Agent 数量”，而是把复杂智能系统从单一认知主体转化为具有分工、边界、协作和治理结构的系统。其主要价值可以概括为六个关键词：

```text
MAS
 │
 ├── Decomposition       问题分解
 ├── Specialization      专业化
 ├── Isolation           隔离
 ├── Parallelism         并行
 ├── Coordination        协作
 └── Governance          治理
```

这六项价值并非相互独立。问题分解创造专业化与并行的可能；隔离建立认知、权限和故障边界；协调把局部工作组合为系统结果；治理则约束控制权、责任和风险。

### 2.2 问题分解与专业化

单 Agent 需要在同一认知空间中承担研究、规划、编码、执行和审查等不同任务。MAS 可以将这些职责分配给具有不同 system prompt、上下文、工具、记忆、模型和权限的 Agent：

```text
                         Coordinator
                    ┌────────┼────────┐
                    ▼        ▼        ▼
               Researcher  Coder  Reviewer
```

其价值不只是角色扮演，而是把任务空间和认知空间同时拆开，使各 Agent 能在有限职责内形成稳定能力，并允许使用领域模型、专用工具和局部记忆。是否真正产生专业化收益，必须通过 Specialist 与 Generalist 的对照和 Agent 边际效用实验验证。

### 2.3 并行搜索、覆盖与规模扩展

可分解任务可以从顺序推理转为并行探索：

```text
                    ┌─> Research A ─┐
Task ───────────────┼─> Research B ─┼─> Synthesis
                    └─> Research C ─┘
```

并行 MAS 可以同时检索不同信息源、测试多个假设、审查不同模块或执行异构工具，从而提高速度、覆盖度和解空间多样性。但只有当同步、合并和协调开销低于节省的时间与获得的信息增益时，并行才具有净价值。Agent 数量增加也不等于可扩展性提高；系统可能因通信边数量、资源竞争和汇总复杂度出现非线性退化。

### 2.4 认知隔离与上下文架构

LLM 在给定上下文中完成推理与行动，但现实世界可用信息、历史、工具结果和领域知识不断增长。Skills、Tools、Retrieval 和 Memory 可以扩展单 Agent 的有效上下文，却无法消除以下矛盾：

```text
Potentially unbounded world context
                 vs.
Finite effective model context and attention
```

当所有信息进入同一上下文时，系统可能遭遇上下文污染、注意力稀释、指令冲突、记忆污染、注入传播和无关 token 成本。MAS 通过建立 Agent 边界，将不同领域的信息放入不同认知空间，仅允许摘要、结构化消息或受控工件跨越边界。

```text
Research Context                    Coding Context
      │                                   │
      ▼                                   ▼
Research Agent ── structured artifact ──> Coding Agent
```

因此，**现阶段 MAS 最直接、最可检验的工程价值是 context isolation，MAS 也可以被视为一种 Context Architecture**。其核心管理对象包括：谁能看到什么；谁不应看到什么；哪些信息必须跨边界；信息跨边界时是否被删除、修改、注入或误解；该信息最终影响了哪个决策和动作。

### 2.5 责任边界与故障归因

单 Agent 失败时，研究、规划、工具选择、执行和验证往往混合在同一 trajectory 中。MAS 可以把这些责任显式分离：

```text
Planner → Researcher → Executor → Verifier
```

这使系统能够分别评价任务分解、证据质量、动作正确性与验证有效性，并回答“谁对哪部分结果负责”。责任边界为 credit assignment、failure attribution 和 RCA 提供结构基础；如果控制权和责任仍然无法区分，那么多 Agent 只增加了表面角色，而没有提高可治理性。

### 2.6 制衡、职责分离与治理

MAS 可以把提议、执行、审批和审计分配给不同主体：

```text
Planner → Executor → Reviewer → Policy Agent → Action
```

这允许系统实现 verification、debate、consensus、adversarial review 和 separation of duties，并把“智能”与“授权”分开。高能力 Agent 不必自动拥有高权限；能提出动作的 Agent 也不应必然有权批准和执行动作。该结构为高风险场景提供了单 Agent 难以稳定实现的制度性约束。

### 2.7 异构智能与异构资源编排

MAS 可以根据任务类型选择不同模型、运行位置和工具：高成本前沿模型负责复杂研究，小模型负责路由，代码模型负责编程，本地模型处理敏感数据，规则或分类器承担安全门控。其作用类似异构计算中的 CPU、GPU 与专用加速器协同：

```text
                        Coordinator
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        Frontier Model  Code Model  Local Guard Model
           Research       Code       Sensitive Data
```

这种 Heterogeneous Cognitive Computing 既可能提高能力与成本效率，也带来模型能力校准、数据边界、协议兼容和跨信任域治理问题。

### 2.8 从模型能力到系统能力

MAS 最深层的意义，是把研究对象从模型转变为系统。单个 Agent 的能力不会自动转化为整体能力：串联系统会积累局部错误，并行系统会产生冲突，共享状态会传播污染，自组织系统还可能形成低效或危险拓扑。

```text
LLM:    Input → Model → Output

Agent:  Input → Model → Tool/Environment → Replan → Output

MAS:    Agents + Contexts + Tools + State + Interaction + Governance
                                  ↓
                            System Outcome
```

因此，MAS 研究从 intelligence engineering 进一步走向 intelligent systems engineering：不仅评价节点能力，还必须评价边界、接口、控制权、依赖关系、状态一致性、故障传播和整体治理。

### 2.9 协调与治理是 MAS 的独特难题

分解、专业化和并行在传统软件工程中已有成熟先例。真正使 MAS 成为独立研究问题的是：多个具有不完全信息、概率性决策和一定自主性的主体，如何形成可靠、可预测、可解释、可控制的整体行为。

Observability 与 Evaluation 因而不是 MAS 的外围仪表盘，而是其成为工程系统后的基础设施。没有跨 Agent 的上下文与状态 provenance，就很难回答“谁导致了什么”；没有交互级 telemetry，就无法区分节点失误、通信失真和协调失效。

### 2.10 MAS 的必要性判据

MAS 不天然优于单 Agent。判断某个多 Agent 设计是否成立，可以采用反事实合并准则：

> 如果合并两个 Agent 后，并未显著损失上下文隔离、专业化、并行、异构性、治理、权限分离、故障隔离或自主协作能力，那么这两个 Agent 很可能没有必要被设计为两个 Agent。

因此，MAS 必须证明其系统增益大于新增的协调成本、信息传输损失、延迟、费用和故障风险。这一判据也是后续质量标准和多目标优化框架的出发点。

---

## 三、MAS 的质量标准与设计目标

### 3.1 首要标准：相对单 Agent 的净系统增益

MAS 的质量不能脱离基线讨论。至少应比较：

- 单 Agent 基线；
- 使用工具、技能和记忆增强的单 Agent；
- 固定工作流；
- 候选 MAS 架构。

需要回答的不只是“MAS 是否完成任务”，还包括：

```text
MAS Benefit
= Outcome Gain
+ Isolation Gain
+ Parallelism Gain
+ Governance Gain
+ Robustness Gain
- Coordination Overhead
- Transfer Loss
- Cost and Latency
- New Failure Risk
```

推荐使用消融实验：移除某个 Agent、合并两个 Agent、固定某条动态路由、禁用共享记忆、将并行改为串行，再观察整体性能变化。若某个 Agent 的移除几乎不影响结果，却显著降低成本与延迟，则该 Agent 的边际效用很低。

### 3.2 质量维度

#### 3.2.1 任务有效性（Task Effectiveness）

衡量系统是否正确、完整、稳定地完成用户目标，包括成功率、正确性、完整性、约束满足、真实世界动作结果和用户效用。最终结果仍然重要，但它只是系统质量的顶层指标，不能解释结果如何形成。

#### 3.2.2 上下文隔离与充分性（Context Isolation and Sufficiency）

上下文质量必须同时避免“太多”和“太少”：

- Context Pollution：Agent 看到了不相关、不应访问或会产生干扰的信息；
- Context Deficiency：Agent 缺少完成任务所必需的信息；
- Context Leakage：信息越过了不应跨越的 Agent、租户或权限边界；
- Context Distortion：信息在摘要、转述或格式转换中失真。

可定义两个互补指标：

\[
Context\ Precision = \frac{Relevant\ Context\ Consumed}{Total\ Context\ Consumed}
\]

\[
Context\ Recall = \frac{Required\ Context\ Present}{All\ Required\ Context}
\]

前者反映纯度与 token 效率，后者反映任务信息的完整性。只优化其中一个会导致极端结果：上下文极少可能很“纯”，却无法完成任务；上下文极多可能覆盖全面，却失去隔离价值。

#### 3.2.3 专业化与边际效用（Specialization and Marginal Utility）

应验证 Specialist 是否在其职责范围内显著优于 Generalist，并且收益足以覆盖额外成本。可定义：

\[
Agent\ Marginal\ Utility_i = Performance(MAS)-Performance(MAS\setminus Agent_i)
\]

实际分析应同时报告质量、成本、延迟和风险变化，不能压缩为单一分数。还应测试角色遵循、能力边界和职责重叠，避免只换 system prompt 的“名义专业化”。

#### 3.2.4 分解、路由与委派质量（Decomposition, Routing and Delegation）

衡量任务是否被正确拆分，子任务之间的依赖是否成立，是否选择了正确 Agent，输入是否充分，委派时机是否合理，失败后是否正确回退。典型指标包括 task coverage、dependency correctness、routing accuracy、wrong-agent rate、fallback rate 和 delegation rework rate。

#### 3.2.5 交接与通信保真度（Handoff and Communication Fidelity）

Agent A 的正确发现只有在 Agent B 正确接收并使用时才产生系统价值。通信质量至少包括：传输完整性、语义准确性、及时性、来源可信度、接收确认、实际纳入上下文的比例，以及接收方对信息的理解与使用。

可把关键事实分为 required facts、transferred facts、received facts 和 correctly used facts，从而定位丢失发生在生成、转换、传输、上下文组装还是推理阶段。

#### 3.2.6 协调效率（Coordination Efficiency）

MAS 的 token、时间和调用可分为有助于任务的 useful work 与描述任务、转述背景、重复确认、无效争论、等待和重试等 coordination work。一个概念性指标是：

\[
Coordination\ Efficiency = \frac{Useful\ Work}{Total\ Work}
\]

实际评测可用消融或因果贡献估计“useful”，并辅以交互次数、重复劳动率、无效 fan-out、handoff 次数、等待时间、同步开销、loop/deadlock/thrashing 发生率。

#### 3.2.7 并行性与可扩展性（Parallelism and Scalability）

并行分支数量不等于真实加速。应测量关键路径、wall-clock latency、Agent 利用率、排队时间、同步等待、合并开销、资源争用与随 Agent 数量增长的边际收益。扩展性测试应观察吞吐、成本、拓扑复杂度和错误率是否出现非线性恶化。

#### 3.2.8 鲁棒性、故障隔离与恢复（Robustness, Containment and Recovery）

应在 Agent 错误、工具失败、消息延迟、状态冲突、部分不可用和恶意输入下测试系统。关键指标包括 failure blast radius、故障传播深度、降级能力、重试有效性、恢复时间、状态一致性和是否能在局部失败时保持安全状态。

#### 3.2.9 治理、责任与可解释性（Governance, Accountability and Explainability）

系统应能回答：谁创建了任务；谁做出路由或授权决策；谁修改了共享状态；谁消费了该状态；哪个证据影响了最终动作；谁应该获得 credit 或承担 blame。责任边界必须与真实控制权、能力和权限一致，而不能仅依赖 Agent 名称或角色描述。

#### 3.2.10 资源效率（Cost, Latency and Resource Efficiency）

应按 session、task、Agent、interaction、model call 和 tool call 归集 token、金额、延迟、算力、网络和外部 API 成本。相同 outcome score 的两个 MAS，可能因交互拓扑和重复劳动而具有完全不同的工程质量。

### 3.3 MAS 是多目标优化，而不是单指标排名

MAS 同时希望最大化任务质量、隔离、覆盖、鲁棒性和治理能力，并最小化成本、延迟、协调开销和风险。可用一个场景化效用函数表达：

\[
U = w_QQ + w_II + w_RR + w_GG - w_CC - w_LL - w_OO - w_KK
\]

其中，\(Q\) 为任务质量，\(I\) 为隔离，\(R\) 为鲁棒性，\(G\) 为治理能力，\(C\) 为成本，\(L\) 为延迟，\(O\) 为协调开销，\(K\) 为安全风险。权重必须由场景决定：支付系统会显著提高安全和授权权重，研究辅助系统可能更重视覆盖与证据质量，实时客服则更重视延迟和连续性。

更稳妥的做法是报告多维结果和 Pareto frontier，而不是制造一个看似精确的总分。一个架构只有在至少一个重要维度上更优、且没有在不可接受的维度上退化，才有采用价值。

---

## 四、MAS 的主要协作与组织架构

### 4.1 分类原则与口径

MAS 可以按控制流、通信拓扑、状态模型或部署边界分类。为避免把不同维度混在同一层级，本报告以**下一步控制权如何产生**为主轴，将协作架构划分为六类：Router、Supervisor、Pipeline、Graph/Workflow、Handoff 和 Self-organizing/Swarm。

```text
                         MAS Collaboration Architecture
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
          中央控制型                流程控制型                自主协作型
              │                       │                       │
       ┌──────┴──────┐         ┌──────┴──────┐         ┌──────┴──────┐
       │             │         │             │         │             │
    Router       Supervisor  Pipeline   Graph/Workflow Handoff  Self-organizing
                                                                     / Swarm
```

Group Chat / Debate 不作为第七类主控制架构，而被视为一种可叠加的通信与协商模式：它可以由 Supervisor 调度，也可以出现在 Handoff 或 Swarm 中。类似地，Distributed/A2A 是部署、协议和信任域维度，而不是第七类控制流。真实系统也常混合多种模式，例如顶层由 Supervisor 分解任务，Research 子系统采用并行 Graph，外部专家通过 A2A Handoff 接入。

### 4.2 六类主要协作架构

#### 4.2.1 Router / Dispatcher：一次或少量分派

```text
                         ┌─> Legal Agent ─────> Output
User Task ──> Router ────┼─> Coding Agent ────> Output
                         └─> Research Agent ──> Output
```

Router 根据任务意图、能力和约束选择一个或多个 Agent，自身通常不持续参与任务推理。Worker 独立执行，结果可以直接返回或进行轻量聚合。其核心价值是把不同 topic 送入不同 context，形成直接的 context partitioning。主要风险是误路由、置信度失真、fallback 缺失，以及攻击者操纵分类使任务绕过专门的安全或审批流程。

#### 4.2.2 Supervisor / Hub-and-Spoke / Agents-as-Tools：中心协调

```text
                              Supervisor
                     ┌────────────┼────────────┐
                     │ delegate   │ delegate   │ delegate
                     ▼            ▼            ▼
                Researcher      Coder       Reviewer
                     │            │            │
                     └────────────┼────────────┘
                                  ▼
                              Supervisor
                                  │
                                  ▼
                                Output
```

Supervisor 持有全局目标和主要会话状态，负责分解、委派、回收结果、再规划与汇总；控制权始终回到中心。Worker 可保持独立 context，并只通过结构化接口返回结果。这是当前最典型的 context-oriented MAS 之一，治理和隔离能力较强；但 Supervisor 也会成为决策瓶颈、单点故障和高价值攻击目标。

#### 4.2.3 Pipeline / Sequential：顺序流水线

```text
Input ──> Planner ──> Researcher ──> Writer ──> Reviewer ──> Output
```

任务按预定义顺序从一个 Agent 流向下一个 Agent，最终结果由最后阶段输出，不需要返回首个 Agent。下一步由开发者规定而非当前 Agent 决定。该架构责任清晰、可预测、协调开销低，适合流程稳定且阶段边界明确的任务；其主要风险是上游错误、上下文缺失和语义失真逐级积累，下游又可能盲目信任上游结果。

#### 4.2.4 Graph / Workflow：分支、汇合、循环与混合控制

```text
                              ┌─> Research A ─┐
Input ──> Planner ────────────┤               ├─> Synthesis ─> Reviewer
                              └─> Research B ─┘                  │
                                                               ├─ pass ─> Output
                                                               └─ fail ─> Planner
```

Agent 或执行器是节点，依赖、消息和状态流是边。Graph 可包含并行分支、条件边、重试、循环、人类审批与 fan-in/fan-out；Pipeline 可以视为 Graph 的线性特例。生产系统常采用“确定性骨架 + 局部 Agent 决策”，以兼顾可控性和适应性。核心问题是实际路径选择、关键路径、状态一致性、循环终止、竞态和不可达节点。

#### 4.2.5 Handoff / Delegation Chain：控制权动态转移

```text
User ──> General Agent
              │ handoff: legal issue
              ▼
          Legal Agent
              │ handoff: tax issue
              ▼
           Tax Agent ──> Output
```

当前 Agent 判断下一个接管者，并将目标、上下文和控制权转移给对方。与 Pipeline 的区别是下一跳并非完全预先指定；与 Supervisor 的区别是控制权不必返回中心。Handoff 能保持用户交互的连续性并支持动态专业化，但会把风险集中到接收者选择、交接时机、context 过滤、责任连续性和权限继承上。

#### 4.2.6 Self-organizing / Swarm：自组织网络

```text
                               Agent A
                            ↙     ↓     ↘
                       Agent B <─→ Agent C
                          ↑          ↓
                          └── Agent D ┘

              discover → select → request → collaborate → reorganize
```

系统没有固定中心或完整预定义路径。Agent 根据当前缺口搜索其他 Agent、比较能力与信任、请求协作、接受或拒绝任务，并动态形成执行拓扑；参与 Agent 的数量和连接关系也可在运行期间变化。其优势是灵活、开放、可支持复杂环境中的涌现协作；难点则集中在 discovery trust、身份认证、能力广告、拓扑涌现、协调爆炸、资源预算、责任归因和安全遏制。

### 4.3 两个正交维度

#### 4.3.1 Group Chat / Debate：通信与协商模式

```text
              ┌─────────────────────────────┐
              │       Shared Channel        │
              │  Agent A: proposal          │
              │  Agent B: critique          │
              │  Agent C: evidence          │
              │  Judge: decision            │
              └─────────────────────────────┘
```

Group Chat 适合 debate、review、投票和 consensus，但它描述的是 Agent 如何共享信息和轮流发言，而不充分说明下一步控制权属于谁。共享 transcript 还会使不同 Agent 的 context 高度重叠，容易产生锚定、从众、回音室、污染传播和 token 膨胀。因此，它可以叠加在六类架构上，但应单独评价群体独立性和共享上下文风险。

#### 4.3.2 Logical MAS 与 Distributed/A2A MAS：部署与信任域

```text
Logical MAS                           Distributed / A2A MAS
┌──────────────────┐                  ┌──────────┐      ┌──────────┐
│ One Application  │                  │Runtime A │ A2A  │Runtime B │
│ A  B  C Agents   │                  │ Agent A  │ ───> │ Agent B  │
└──────────────────┘                  └──────────┘      └──────────┘
```

- Logical MAS：Agent 拥有不同 prompt、context、tool 或 memory，但共享应用、编排器和主要故障域。
- Distributed/A2A MAS：Agent 拥有独立运行时、生命周期、部署、资源和安全边界，通过协议跨主机、框架、组织或信任域通信。

分布式部署能够强化故障隔离、资源隔离和异构性，也会引入网络不可靠、身份联合、跨组织授权、消息重放、协议兼容、时钟排序和跨域 RCA 等问题。

#### 4.3.3 Context 与 State 的交换模式

控制架构只说明“谁决定下一步”，并不充分说明 Agent 之间“共享什么”。同一种 Supervisor、Graph 或 Swarm，可以采用完全不同的 context/state architecture；后者往往更直接地决定系统质量和攻击面。

| 交换模式 | 机制 | 主要价值 | 主要风险 |
|---|---|---|---|
| Independent Context | 每个 Agent 保持私有上下文 | 隔离强、污染面小 | 关键信息缺失、重复检索 |
| Full Transcript Sharing | 共享完整会话历史 | 连续性强、实现简单 | context 膨胀、泄漏与污染传播 |
| Filtered Handoff Context | 摘要或筛选后交接 | 在连续性与隔离间折中 | 摘要失真、约束丢失、过滤绕过 |
| Structured Message Passing | 通过 schema 化消息通信 | 接口清晰、易验证 | schema 不足、语义被压缩或注入 |
| Artifact Passing | 传递文件、报告、代码、证据包 | 适合复杂结果和异步协作 | 工件篡改、来源不明、版本漂移 |
| Shared Blackboard / State | 读写共同计划、任务或工作区 | 便于协调与全局同步 | 竞态、陈旧读、单点污染与大范围传播 |
| Shared Memory | 共享长期知识与历史 | 跨任务学习、减少重复工作 | 持久化投毒、跨用户泄漏、错误长期存留 |

这些模式可以组合使用。对 MAS 的分析不应只问“是 Supervisor 还是 Swarm”，还必须问：每个 Agent 实际看到了什么；消息如何转换为接收方 context；共享状态由谁写入和消费；哪些事实以 artifact 或 memory 形式跨越了边界。

### 4.4 控制权连续谱

六类架构并非彼此割裂，而是形成从开发者控制到 Agent 控制的连续谱：

```text
Developer-controlled                                      Agent-controlled
       │                                                          │
       └─ Pipeline ─ Graph ─ Router ─ Supervisor ─ Handoff ─ Swarm ┘

More deterministic                                      More emergent
More predictable                                  More adaptive/autonomous
Lower coordination uncertainty            Higher observability/security cost
```

Router、Pipeline 的相对位置会因实现而变化；重要的不是绝对排序，而是识别路由、路径和下一跳究竟由规则、模型、中央 Agent 还是网络中的局部 Agent 决定。

### 4.5 架构机制比较

| 架构 | 下一步控制权 | 上下文边界 | 主要通信 | 典型状态 | 主要优势 | 主要质量与安全风险 |
|---|---|---|---|---|---|---|
| Router | Router/规则/分类器 | 强；按领域分区 | 任务输入与结果 | 中央会话或无状态 | 简单、低开销、易审计 | 误路由、路由操纵、fallback 缺失 |
| Supervisor | 中央 Supervisor | 通常较强；worker 独立 | 委派与结构化回传 | 全局状态集中，局部状态分离 | 分解、治理、汇总能力强 | 中心瓶颈、过度委派、单点失效 |
| Pipeline | 开发者预定义 | 阶段式隔离 | 上游输出成为下游输入 | 沿流水线传递 | 责任清晰、可预测 | 累积误差、传输损失、下游盲信 |
| Graph/Workflow | 规则与 Agent 混合 | 节点级隔离 | typed edge、事件、共享状态 | checkpoint/graph state | 可控并行、分支、重试 | 错误路径、循环、竞态、状态不一致 |
| Handoff | 当前 Agent | 取决于交接过滤 | 控制权与 context package | 状态随 active Agent 转移 | 连续交互、动态专业化 | context 丢失/泄漏、授权继承错误 |
| Self-organizing/Swarm | Agent 网络 | 可强可弱，动态决定 | discovery、A2A、协作请求 | 分布式/共享/局部混合 | 高适应性、开放协作 | 冒充、能力伪造、拓扑操纵、失控扩散 |

### 4.6 架构权衡的五星级比较

下表是用于形成研究假设的定性评分，而不是未经实验的客观排名。除“协调成本”外，星级越高表示该能力越强；协调成本星级越高表示开销和复杂度越大。具体实现可能显著改变评分。

| Architecture | Context Isolation | Flexibility | Predictability | Parallelism | Coordination Cost | Failure Attribution |
|---|---:|---:|---:|---:|---:|---:|
| Router | ★★★★★ | ★★☆☆☆ | ★★★★★ | ★★★★☆ | ★☆☆☆☆ | ★★★★★ |
| Pipeline | ★★★★☆ | ★★☆☆☆ | ★★★★★ | ★★☆☆☆ | ★☆☆☆☆ | ★★★★★ |
| Graph / Workflow | ★★★★☆ | ★★★☆☆ | ★★★★☆ | ★★★★☆ | ★★☆☆☆ | ★★★★☆ |
| Supervisor | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★★☆☆ | ★★★☆☆ | ★★★☆☆ |
| Handoff | ★★★★☆ | ★★★★☆ | ★★☆☆☆ | ★★★☆☆ | ★★★☆☆ | ★★☆☆☆ |
| Self-organizing / Swarm | ★★★★☆ | ★★★★★ | ★☆☆☆☆ | ★★★★★ | ★★★★★ | ★☆☆☆☆ |

这张表提出了一个需要实证检验的总体趋势：自主性越高，灵活性与并行潜力越高，但协调复杂度、归因难度和安全不确定性也越高。Context Isolation 并不只由拓扑决定，还取决于 message、shared state、memory 和 handoff 的具体数据策略。

### 4.7 架构选择与产业现状的总体判断

当前工程实践的主流并非大量 Agent 完全自由交流，而更接近“少量专业 Agent + 强编排 + 显式 context boundary”。Graph/Workflow 和 Supervisor 能在确定性结构中保留局部自主性，因而更适合生产环境；Handoff 和 Swarm 则为更开放的协作提供能力，但对身份、信任、预算和可观测性提出更高要求。

架构选择不应在“固定工作流”和“完全自组织”之间简单二选一。更合理的原则是按风险划定自治边界：安全不变量、资源预算和高风险动作审批保持确定；局部搜索、伙伴选择和低风险协作可以交由 Agent 自主决定。

---

## 五、质量评测、红队与蓝队的统一三视角框架

### 5.1 三者共享行为模型，但提出不同问题

```mermaid
flowchart LR
    M["MAS actual behavior"] --> T["Unified telemetry and provenance"]
    T --> E["Evaluation: 是否按预期工作"]
    T --> R["Red Team: 如何主动使其偏离"]
    T --> B["Blue Team: 为何偏离及如何处置"]
    E --> I["Design and assurance improvement"]
    R --> I
    B --> I
    I --> M
```

三种视角可以使用相同的指标，但解释方式不同：

- Quality Evaluation：在正常任务、随机扰动与基准场景下，系统是否实现设计目标；
- Red Team：攻击者是否能主动、可重复地操纵这些属性，使性能退化、资源失控或安全边界被突破；
- Blue Team：如何从 telemetry 发现异常，区分良性故障与对抗行为，定位最早原因、传播路径与受影响范围，并完成遏制和恢复。

例如，`routing accuracy` 在质量视角下是正确率；在红队视角下是可否通过输入、能力广告或共享状态操纵路由；在蓝队视角下则是异常路由检测和因果归因问题。

### 5.2 安全不是质量的简单反面

许多安全事件不会降低任务质量。例如，一个 Agent 未经授权读取私人数据库，并据此给出完全正确的答案；从 task accuracy 看系统表现良好，但 confidentiality 和 authorization 已被破坏。

因此，MAS Assurance 至少包含两类目标：

1. Behavioral Quality Properties：质量、成本、延迟、隔离、协调、鲁棒性等连续属性；
2. Security Invariants：任何时候都不应违反的身份、认证、授权、机密性、完整性、租户隔离、最小权限、职责分离、策略合规和动作审批边界。

红队既可以最大化质量退化，也可以在不明显降低质量的情况下突破不变量；蓝队则必须同时监测两类问题。

### 5.3 将三视角形式化为同一控制问题

质量评测可表示为：在任务分布和良性扰动下，系统能否维持目标属性。

```text
Task + benign perturbation → MAS → metrics and invariants
```

红队可表示为：寻找有限攻击预算内，使指标最大退化或使不变量被违反的输入、Agent、消息、状态或动作序列。

```text
Adversarial perturbation → MAS → maximize degradation / violate invariant
```

蓝队可表示为：根据不完整 telemetry，对观测到的偏离进行检测、分类、因果定位、影响评估、遏制与恢复。

```text
Observed anomaly
      ↓
Detection → Benign/Adversarial → Root cause → Blast radius
      ↓
Containment → Recovery → Corrective control
```

### 5.4 统一 MAS Assurance Stack

```text
                    MAS Runtime
                         │
              Instrumentation / Hooks
                         │
        Unified Events + Traces + Provenance
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     Evaluation       Red Team       Blue Team
     quality/utility  exploitability detect/RCA/respond
          └──────────────┼──────────────┘
                         ▼
              Policy and Design Improvement
```

这一结构意味着底层 instrumentation 不应分别为 Eval、Red 和 Blue 重复建设。三者应共享事件语义、身份模型、context/state provenance、trace correlation 和图重建能力，仅在分析目标、数据集和响应策略上分化。

---

## 六、从 LLM 到 Agent 再到 MAS：研究对象与可观测性的演化

### 6.1 LLM：输入/输出及模型调用

传统研究对象近似为：

```text
Prompt → LLM → Response
```

主要 telemetry 包括 prompt、response、model、token、latency、采样参数、内容安全标签和输出评分。红队常围绕 malicious prompt 与 harmful response 建立输入输出映射。

### 6.2 Agent：行动轨迹与环境状态变化

Agent 引入工具、记忆和循环决策：

```text
State₀ → Model → Action → Observation → State₁ → Replan → ... → Outcome
```

研究单位从单次调用变为 trajectory。必须记录模型调用、工具参数与结果、memory read/write、状态转移、错误、审批以及对浏览器、数据库、文件系统、通信系统、支付和 OS 的真实影响。

### 6.3 MAS：动态因果交互图

MAS 不只是多条 trajectory 并排存在。Agent 会相互创建任务、选择伙伴、转移控制、修改共享状态，并通过消息改变其他 Agent 的后续上下文。因此系统运行应表示为随时间演化的图：

\[
G_t = (V_t, E_t, S_t)
\]

其中：

- \(V_t\)：Agent、Task、Model Call、Tool、Memory Item、Artifact、External Entity 等节点；
- \(E_t\)：delegate、handoff、message、read、write、derive、approve、invoke、cause 等有向关系；
- \(S_t\)：各 Agent 局部状态、共享状态、权限状态和外部环境状态。

```mermaid
flowchart TD
    U["User / Environment"] --> G["Entry / Gateway"]
    G --> C["Coordination plane"]
    C --> A["Agent A"]
    C --> B["Agent B"]
    A -->|"delegate / message"| B
    A -->|"write"| S["Shared state / artifacts"]
    S -->|"read"| B
    B -->|"handoff"| D["Agent C"]
    D --> T["Tools / MCP / APIs"]
    T --> X["External state change"]
```

该图随运行过程动态变化：Agent 和任务可以出现或消失，边可以并发、重试和循环，拓扑也可能由 Agent 自主形成。因此，MAS 可观测性的目标不能停留在展示父子 span tree，而应重建足以支持因果查询的 interaction graph。

### 6.4 为什么 edge / interaction event 是关键最小观测单位

只记录 Agent A 和 Agent B 各自做了什么，仍然无法解释系统行为。真正改变系统的往往是边：

- A 为什么选择 B；
- A 给 B 传了什么；
- 哪些内容在传输前被摘要、过滤或修改；
- B 实际收到并放入 context 的是什么；
- 该消息是否导致 B 的某个决策；
- 控制权、权限或责任是否随交接改变。

因此，一个 interaction event 至少应具有：

```text
event_id / trace_id / causal_parent_ids
event_type / timestamp / ordering
source_agent / destination_agent
source_identity / destination_identity / trust_domain
task_id / parent_task_id / goal
decision_reason / policy_version
payload_hash / schema / sensitivity label
context_sources / transformation / filtering
permission requested / permission granted
delivery / receipt / context inclusion / use
cost / latency / status / error
```

在隐私或商业敏感场景中，不一定保存完整 payload，但必须保留可验证哈希、数据分类、来源、转换记录和访问证据。

### 6.5 MAS 的关键观测面与 telemetry 类型

#### Entry / Session / Goal

记录用户目标、约束、风险等级、租户、会话、外部输入来源、任务创建与验收条件。它是判断后续行为是否偏离原始意图的基线。

#### Identity / Role / Capability / Trust

记录 Agent ID、所有者、角色、模型、版本、能力声明、工具、权限、信任域、部署位置和生命周期。Identity 在 MAS 中不是普通 metadata，而是路由、授权和归责的安全边界。

#### Context Assembly and Provenance

记录每次模型调用实际看到的 context 组成：系统指令、Agent 身份、当前与父任务、用户消息、Agent 消息、memory、tool output、shared state 和 artifact。每个 context item 应能追溯来源、转换和授权依据。

#### Decision / Model

记录 route、plan、delegate、approve、reject、stop、retry 等决策，以及候选项、选择结果、置信度、策略版本和可公开的理由。无需依赖私有 chain-of-thought；结构化决策依据和外部可验证证据更适合作为审计对象。

#### Memory / Retrieval

记录 query、候选、排名、读取、写入、版本、数据标签、过期时间、来源与后续消费者，以识别检索失败、陈旧信息和 memory poisoning。

#### Coordination / Discovery / Scheduling

记录 task_created、agent_search、candidate_agents、ranking、trust_check、agent_selected、task_delegated、accepted、rejected、scheduled、cancelled、completed 等事件。

#### Communication / Handoff / A2A

记录发送方、接收方、意图、消息、附件、转换、签名、送达、接收、实际纳入上下文和控制权变化。需要支持跨 trace、跨运行时和跨信任域关联。

#### Shared State / Blackboard / Artifact

记录 who wrote what when and why、版本 diff、冲突、读者、后续派生物与敏感标签。State provenance 是分析间接故障传播的关键。

#### Tool / MCP / External Action

记录工具发现、选择、参数、授权、执行结果、副作用、幂等键、审批、回滚能力与外部资源标识。必须把“模型提出动作”和“环境实际发生变化”区分开。

#### Topology / Concurrency / Resource

记录活跃 Agent、动态边、fan-out/fan-in、关键路径、循环、等待、队列、并发冲突、token velocity、成本和资源限制，用于识别协调爆炸和资源放大。

#### Outcome / Verification / Recovery

记录任务验收、证据、Verifier 判断、用户结果、真实世界状态、策略违规、恢复点、补偿动作和最终状态，以闭合从目标到结果的因果链。

### 6.6 Trace 模型：树不够，必须支持 links 与 provenance

传统父子 span tree 适合单条调用链，却难以表达 fan-out/fan-in、多父依赖、共享状态、异步消息和循环。MAS telemetry 应至少支持：

- parent-child：调用或包含关系；
- causal links：一个事件受多个前序事件共同影响；
- message correlation：send 与 receive 配对；
- state version lineage：状态版本与读写者；
- artifact/data lineage：事实和工件如何被派生；
- synchronized clocks or logical ordering：跨运行时的事件顺序；
- graph replay：按时间重建拓扑、状态与影响路径。

如果底层采用 OpenTelemetry 一类分布式追踪机制，仅使用 parent-child span hierarchy 仍不足以表达复杂 MAS。实现上需要结合 span links、message events、稳定的 Agent/Task/Interaction 标识，以及面向 context、state、delegation 和 handoff 的语义字段。换言之，通用 tracing 可以提供传输骨架，但 MAS 仍需要补充组织行为和语义因果层。

### 6.7 MAS 可观测性的四个层次

从工程成熟度看，MAS observability 可以分为四个递进层次。更高层依赖低层数据，但分析单位和问题并不相同。

```text
L1 Agent Observability
   这个 Agent 做了什么？
   model / tool / memory / context / local state

                    ↓

L2 Interaction Observability
   Agent 之间发生了什么？
   message / handoff / delegation / artifact transfer

                    ↓

L3 Coordination Observability
   为什么形成这种协作方式？
   routing / discovery / scheduling / dependency / topology

                    ↓

L4 System Observability
   整个 MAS 是否有效、可控且值得？
   outcome / cost / latency / robustness / scalability
   failure propagation / security invariants / recovery
```

对应的 evaluation 也应从 Agent Evaluation 逐层上升到 Interaction、Coordination 和 System Evaluation。仅具备 LLM、Tool 和 Agent spans 的平台主要覆盖 L1；能显示跨 Agent 调用但无法表达消息转换、共享状态和动态拓扑的平台，最多覆盖部分 L2。真正的 MAS system observability 需要在 L3 和 L4 上重建组织行为、全局状态与因果传播。

---

## 七、MAS Assurance Matrix：观测位置 × 质量 × 红队 × 蓝队

下表将关键观测面转化为统一测试框架。每一行都应进一步落地为正常基准、扰动测试、对抗测试、检测规则、归因查询和响应剧本。

| 观测位置 | 质量问题与测评方法 | 红队可能利用方式 | 蓝队检测、归因与 RCA |
|---|---|---|---|
| Entry / Goal / Session | **问题**：目标误解、约束遗漏、任务边界漂移、跨租户混淆。**测评**：目标抽取准确率、约束覆盖、对话重放、等价改写稳定性、与最终行为的 goal alignment。 | 在用户输入、外部页面或附件中埋入冲突目标；利用模糊请求诱导越界；混淆租户、会话或任务 ID；将恶意子目标伪装成系统目标。 | 比较原始目标、解析目标与执行图；检测任务范围突变和跨租户关联；沿首个异常子任务追溯输入来源；隔离会话并重建受影响任务。 |
| Agent Identity / Role / Capability / Trust | **问题**：角色名实不符、能力声明错误、权限过宽、版本漂移。**测评**：能力基准、角色遵循、权限最小化、身份与运行时绑定、能力声明校准。 | Agent impersonation、capability spoofing、恶意能力广告、角色劫持、低信任 Agent 诱导高权限 Agent 代为执行。 | 验证签名和运行时身份；检测角色与实际工具行为偏差；比较能力广告、历史表现和选择结果；沿 trust-domain crossing 定位 confused deputy 链。 |
| Context Assembly / Provenance | **问题**：污染、缺失、泄漏、来源不明、指令冲突。**测评**：Context Precision/Recall、敏感信息暴露率、必要事实覆盖、来源可追溯率、删减/增补对照实验。 | Prompt injection 经消息、网页、memory 或 artifact 跨 Agent 传播；诱导加载无关信息造成注意力稀释；窃取其他 Agent 或租户 context；伪造高优先级指令。 | 检测异常来源占比、敏感标签跨界和优先级冲突；从异常 context item 回溯 message、tool result 和外部源；比较注入前后 context diff；撤销污染源并重放任务。 |
| Model / Decision | **问题**：计划错误、停止条件错误、置信度失准、决策不可复核。**测评**：决策准确率、候选覆盖、校准曲线、策略一致性、可验证理由、反事实重放。 | 操纵候选集或评分；隐藏高风险属性；诱导 premature stop、无限 retry 或错误审批；利用模型差异绕过策略。 | 监测低置信高风险决策、候选集异常和策略版本变化；用相同输入重放并比较；定位决定性证据和首个偏离决策；切换安全策略或人工审批。 |
| Memory / Retrieval | **问题**：召回不足、错误排名、陈旧记忆、重复写入、跨任务污染。**测评**：retrieval precision/recall、freshness、source quality、写入必要性、记忆消融与污染测试。 | Memory poisoning、恶意高相关文本挤占排名、持久化注入、删除或覆盖安全记忆、跨用户数据泄漏。 | 监测异常写入者、排名突变、旧版本复活和跨域读取；用版本 lineage 找到首次污染；分析哪些 Agent 消费并传播；隔离条目、回滚版本、使缓存失效。 |
| Task Decomposition / Plan | **问题**：漏子任务、错误依赖、粒度不当、重复工作、不可完成计划。**测评**：子任务覆盖、依赖正确性、计划可执行率、与专家计划对比、plan ablation。 | 诱导删除验证或安全步骤；制造大量细碎任务造成资源放大；插入与目标无关但高权限的子任务；构造循环依赖。 | 检测安全节点缺失、子任务数异常、依赖环和计划范围扩张；比较 plan versions；追溯谁修改了关键步骤；恢复可信计划并取消派生任务。 |
| Routing / Discovery / Selection | **问题**：选错 Agent、候选不全、排名偏差、无 fallback。**测评**：routing accuracy、top-k recall、confidence calibration、fallback success、按任务类型的选择稳定性。 | Routing manipulation、恶意 Agent 提高排名、能力关键词投毒、阻断可信 Agent、将敏感任务导向低信任或高权限错误主体。 | 建立任务类型到 Agent 的正常分布；检测异常候选、排名骤变和信任检查缺失；回溯 capability query、广告来源、评分特征与策略；撤销注册、重路由任务。 |
| Delegation / Acceptance | **问题**：任务描述不完整、责任不清、接收者超载、错误接受/拒绝。**测评**：delegation accuracy、输入充分性、接受校准、返工率、队列与负载匹配。 | 伪造委派来源；把危险任务拆成看似无害的局部请求；利用委派链实施权限借用；制造 delegation storm。 | 校验委派者身份和 authority；检测任务语义与权限不匹配、链深和频率异常；沿 delegation chain 定位最初请求；停止后续委派并收回临时权限。 |
| Handoff / Message Transformation | **问题**：关键事实丢失、语义失真、接收者错误、交接过早/过晚、责任断裂。**测评**：required/transferred/received/used facts 对照、消息完整性、handoff necessity、上下文压缩消融。 | 删除安全约束、修改事实、注入隐含指令、伪造发送者、重放旧消息、借交接错误继承权限。 | 配对 send/receive，验证 hash、签名、版本与时序；比较转换前后语义 diff；检查接收上下文实际纳入项；定位失真阶段并从上一个可信 checkpoint 重放。 |
| A2A / Communication Channel | **问题**：丢包、重复、乱序、协议不兼容、语义误解。**测评**：delivery/ack rate、延迟、重复率、schema compliance、互操作与故障注入测试。 | Message spoofing/tampering/replay、降级协议、流量放大、阻断关键消息、利用 schema 歧义注入额外语义。 | 验证双向身份、nonce、签名和 schema；检测重放、时序异常和流量突增；关联网络事件与语义事件；隔离对端、重建会话并核查所有消费者。 |
| Shared State / Blackboard / Artifact | **问题**：写冲突、陈旧读、覆盖、来源缺失、错误被多方继承。**测评**：一致性、版本正确性、读写必要性、artifact correctness、并发与回滚测试。 | Poison shared plan/memory、移除安全约束、篡改工件、利用竞态覆盖可信更新、向公共区写入敏感数据。 | 记录不可变版本、diff、writer/readers 和因果 link；检测敏感字段变化、异常写者和短时高扇出读取；计算受污染版本的消费者集合；冻结状态、回滚并重算派生物。 |
| Tool / MCP Selection and Parameters | **问题**：工具选错、参数错误、重复调用、观察结果误读、调用无效。**测评**：tool selection/argument accuracy、执行成功率、幂等性、必要性、工具结果利用率。 | Tool poisoning、恶意 MCP 描述、参数注入、诱导调用高风险工具、让低权限 Agent 借高权限 Agent 成为 confused deputy。 | 检测角色-工具不匹配、异常参数、未登记端点和高风险调用链；关联工具描述来源与选择决策；暂停工具、轮换凭据、检查副作用和所有下游消费者。 |
| Permission / Approval / Security Boundary | **问题**：权限过宽、审批缺失、临时授权不回收、职责分离失效。**测评**：policy coverage、最小权限差距、拒绝正确率、approval latency、越权模拟。 | Privilege escalation、authorization bypass、审批伪造、权限继承、跨 Agent 拼接多个低风险动作形成高风险效果。 | 对每个动作记录 principal、resource、action、policy、decision；检测跨角色组合和异常授权路径；以不变量违规为最高优先级；吊销 token、阻断动作并审计同类访问。 |
| Topology / Scheduling / Concurrency | **问题**：无效 fan-out、超级节点、瓶颈、死锁、活锁、ping-pong、竞态。**测评**：图密度、关键路径、centrality、loop rate、等待时间、加速比、并发故障注入。 | Coordination amplification/Agent DoS；诱导无限发现与委派；操纵拓扑孤立审查 Agent；制造竞态和资源饥饿。 | 监测动态基线中的 Agent 数、边数、fan-out、token velocity 和循环；定位首个异常扩散边；限流、设 hop/预算上限、切断子图并恢复到稳定拓扑。 |
| Aggregation / Debate / Consensus / Verification | **问题**：错误汇总、少数正确信息被淹没、从众、Verifier 漏检、循环争论。**测评**：synthesis fidelity、独立性、diversity、judge calibration、known-error detection、无 Reviewer 对照。 | 操纵多数、sybil agents、锚定首个答案、让恶意 Agent 充当 Judge、用大量一致低质意见压制可信证据。 | 分析贡献来源与相关性，识别高度相似输出和异常投票团；检查 Judge 身份、证据引用和利益冲突；重跑独立 verifier，隔离可疑群组并重聚合。 |
| Outcome / External State Change | **问题**：最终答案错误、部分完成、动作与陈述不一致、不可逆副作用。**测评**：任务验收、环境状态 diff、端到端 replay、用户效用、约束与不变量检查。 | 诱导有害但表面成功的结果；隐藏副作用；改变支付、文件、数据库或通信状态；在高质量输出下实施数据外泄。 | 将模型意图、工具回执与真实状态三方核对；检测未经批准的 effect；从 outcome 逆向遍历因果图找最早可控原因；执行补偿事务、通知和恢复。 |
| Cost / Lifecycle / Error / Recovery | **问题**：成本失控、超时、Agent 泄漏、错误重试、恢复不完整。**测评**：每任务/Agent/edge 成本、预算遵守、MTTR、checkpoint correctness、降级测试。 | Resource exhaustion、token flooding、retry storm、阻止终止、破坏 checkpoint、利用错误处理绕过策略。 | 检测成本和速率偏离、反复相同错误、孤儿 Agent 与 checkpoint 异常；把资源增长关联到首个触发事件；终止子图、封顶预算、恢复可信状态并审计未完成副作用。 |

### 7.1 横向指标：从单点分数到图级分析

除逐行测试外，MAS 还需要跨观测面的图级指标：

- Failure Blast Radius：一个错误或 compromised Agent 影响的 Agent、任务、状态与外部资源数量；
- Propagation Depth / Time：从根因到最终影响经历的边数和时间；
- Attribution Accuracy：蓝队定位根因、传播路径和责任主体的准确率；
- Mean Time to Detect / Contain / Recover：发现、隔离和恢复的时间；
- Causal Trace Completeness：最终关键决策和动作中，可追溯到来源的比例；
- Interaction Necessity：移除某条边后，任务质量是否下降；
- Structural Efficiency：达到同等 outcome 所需的节点、边、token 与关键路径长度；
- Security Invariant Violation Rate：在正常与对抗测试中不变量被突破的比例；
- Containment Effectiveness：检测后阻断的传播比例与剩余影响。

### 7.2 建议的实验方法

1. Baseline comparison：与增强型单 Agent、固定 workflow 和其他 MAS 架构比较。
2. Component ablation：移除、合并或替换 Agent、边、memory、shared state 和 verifier。
3. Counterfactual replay：保持其他条件不变，替换一次路由、消息或状态版本，观察 outcome。
4. Perturbation testing：注入延迟、丢包、工具失败、错误事实、Agent 不可用和并发冲突。
5. Adversarial testing：在 Entry、Message、Memory、Discovery、State、Tool 和 Permission 面设计攻击预算明确的测试。
6. Causal fault injection：在已知位置注入故障，检验 telemetry 能否定位根因与传播路径。
7. Long-horizon and scale testing：增加任务长度、Agent 数量、并发度和信任域，观察非线性退化。
8. Online shadow evaluation：生产环境只观测或影子执行候选策略，不直接改变高风险动作。

---

## 八、面向不同架构的观测重点

统一 Assurance Matrix 并不意味着所有架构平均采集和分析全部信号。控制权、状态位置和通信方式决定了各类架构最关键的 failure surface，也决定了 Quality、Red 和 Blue 三种视角的优先级。

| 架构 | 核心观测单位 | 首要质量问题 | 首要攻击面 | 蓝队 RCA 起点 |
|---|---|---|---|---|
| Router | 一次 route decision | 是否选对 Agent | 路由与候选操纵 | 异常路由及其输入、候选和评分 |
| Supervisor | plan/delegation/synthesis | 是否正确分解、委派和汇总 | 中心决策劫持与权限借用 | 首个错误中心决策及其下游委派树 |
| Pipeline | stage transition | 信息在哪一阶段衰减 | 上游投毒与逐级放大 | 首个产生错误或丢失事实的 stage edge |
| Graph/Workflow | node、edge 与 state transition | 是否选择正确路径并保持状态一致 | 路径、循环、竞态和 checkpoint 操纵 | 与正常执行图的首个分叉点 |
| Handoff | control/context transfer | 控制权和 context 是否正确转移 | 消息篡改、冒充与授权继承 | send/transform/receive/context-use 链 |
| Self-organizing/Swarm | discovery event 与动态图 | 是否形成有效组织结构 | Sybil、能力伪造、拓扑与资源放大 | 首个异常 discovery/selection/propagation edge |

### 8.1 Router：重点观察路由决策边界

**质量评测。** 核心是 `Task → Correct Agent`。除 routing accuracy 外，还应测 top-k recall、置信度校准、任务分布变化下的稳定性、错误路由成本、fallback 触发和多路并发是否产生实际收益。Router 的 context isolation 价值应通过“目标 Agent 实际 context 相比单 Agent 减少了多少无关信息”来验证。

**红队。** 攻击者会尝试改变任务表述、隐藏风险属性、污染能力目录或操纵评分，使任务进入能力不足、信任较低或权限不合适的 Agent；也可能故意制造低置信输入，迫使系统进入宽松 fallback。

**蓝队。** 重点建立任务类型到 Agent 的正常路由分布，并保留候选列表、评分特征、置信度、策略版本和信任检查。RCA 应从异常 route decision 回溯输入来源、候选变化和评分依据，而不是只检查最终 worker 的失败。

### 8.2 Supervisor：重点观察中心决策链

**质量评测。** 需要分开评价 task decomposition、delegation、worker input sufficiency、结果接纳、再规划和 synthesis。Supervisor 可能把错误任务交给优秀 worker，也可能忽略正确 worker 结果，因此最终 outcome 不能替代中心决策级评价。还应测试 Supervisor bottleneck、过度委派和单点故障。

**红队。** Supervisor 是高价值目标。攻击者可通过输入或 worker 回传操纵全局计划，诱导其删除安全步骤、把危险任务拆成看似无害的子任务、向高权限 worker 借权，或制造 delegation storm 消耗资源。

**蓝队。** 应保存 plan versions、每次 delegation 的候选与理由、worker context package、返回结果和 synthesis provenance。RCA 从最终失败向上遍历委派树，区分“Supervisor 给错任务”“Worker 执行错误”和“Supervisor 错误汇总”，并确定最早偏离点。

### 8.3 Pipeline：重点观察阶段间信息衰减

**质量评测。** 每个 stage 都要分别测输入质量、输出质量、required facts 保留率、阶段延迟和错误率。可在各边注入已知关键事实，检查其是否被删除、修改、误解或正确使用；也可绕过某一阶段进行消融，估计该阶段边际效用和 chaining effect。

**红队。** Pipeline 的上游节点具有放大效应。攻击者可能在早期阶段植入错误事实、移除安全约束或改变格式，使后续 Agent 将其视为可信输入；也可能瞄准瓶颈阶段造成整个流程阻塞。

**蓝队。** 适合采用 stage-by-stage diff 和二分式重放。RCA 不是寻找最后一个输出错误的 Agent，而是定位第一个生成错误、丢失必要信息或错误接纳上游内容的阶段，并测量错误从该点开始的传播深度。

### 8.4 Graph / Workflow：重点观察实际路径与全局状态

**质量评测。** 需要回答实际执行了哪条路径、分支选择是否正确、关键路径是否最短、并行是否带来加速、循环是否正常终止、retry 是否有效、checkpoint 是否一致。静态设计图不能代替运行时图；应比较 planned graph、actual graph 和理想/基准 graph。

**红队。** 攻击者可操纵条件边和 branch decision，绕过 Reviewer 或 Policy 节点；也可制造循环、不可达状态、竞态、重复执行和恶意 checkpoint，使系统进入高成本或不安全路径。

**蓝队。** 应保留 node/edge event、branch reason、state version、checkpoint、fan-out/fan-in 和逻辑时序。RCA 可将异常执行图与正常同类任务进行 graph diff，优先寻找第一个路径分叉、状态冲突或循环形成事件。

### 8.5 Handoff：重点观察控制权与 context 的共同转移

**质量评测。** Handoff 不是普通消息，因为它同时改变 active Agent、责任主体和后续 context。应测试 why handoff、recipient correctness、timing、required facts、payload transformation、接收确认、实际 context inclusion，以及交接前后权限是否符合策略。

**红队。** 典型利用包括伪造发送者、选择恶意接收者、删除安全约束、向 payload 注入指令、重放旧 handoff、利用 context 全量转移窃取信息，以及错误继承发送者权限。

**蓝队。** 每次 handoff 应形成完整的 `send → transform/filter → receive → context inclusion → first decision` 链。RCA 比较转换前后语义与权限 diff，定位失真、泄漏或越权发生在哪一步，并从上一个可信交接点重放。

### 8.6 Self-organizing / Swarm：重点观察组织形成过程

**质量评测。** 首要问题不再是单个 Agent 输出是否正确，而是 Agent society 是否形成了有效组织结构。应评价 discovery quality、candidate diversity、trust calibration、动态 topology、重复寻找、孤立节点、超级节点、cluster/echo chamber、deadlock、ping-pong、协调效率、failure blast radius 和重组能力。

**红队。** 攻击者可以伪造身份和能力、批量创建 Sybil Agent、操纵能力排名、孤立审查 Agent、控制超级节点、污染共享状态，或诱导网络无限发现与委派，形成 coordination amplification / Agent DoS。一个 compromised Agent 的目标往往是扩大传播范围，而非直接给出有害输出。

**蓝队。** 应持续重建动态图，并对 Agent 数、边数、centrality、fan-out、社区结构、trust-domain crossing、token velocity 和传播链建立动态基线。RCA 从首个异常 discovery、selection 或 message edge 出发，向前计算所有消费者和受影响子图；响应需要支持切断边、隔离 Agent、冻结共享状态和安全重组。

### 8.7 Group Chat / Debate：重点观察群体独立性

Group Chat 是可叠加模式，其质量重点是观点是否独立、证据是否多样、讨论是否真正纠错以及 consensus 是否优于最佳单 Agent。红队可利用锚定、从众、恶意 Judge、Sybil 投票或大量相似低质意见压制少数正确证据。蓝队应分析发言相似度、信息来源相关性、speaker selection、投票团和 Judge provenance，识别回音室与协同操纵。

### 8.8 Distributed / A2A：重点观察跨信任域因果链

分布式部署要求把本地语义事件与网络事件关联起来。质量评测应覆盖互操作、消息送达、乱序、幂等、超时和远程状态一致性；红队重点包括身份冒充、消息篡改/重放、协议降级、能力广告欺骗和跨组织 confused deputy；蓝队则依赖双向身份、签名、nonce、send/receive correlation、逻辑时序和跨域 trace link 完成归因。

---

## 九、研究与工程落地建议

### 9.1 先定义语义模型，再做仪表盘

产品建设不应从 trace viewer 开始，而应先统一 Agent、Task、Interaction、Context Item、State Version、Artifact、Tool Action、Permission Decision 和 Outcome 的事件语义与标识关联。没有统一语义，Eval、Red 和 Blue 会各自制造无法互通的数据孤岛。

### 9.2 把 Context Provenance 与 State Provenance 设为核心能力

MAS 中最关键的问题往往不是“哪个模型调用失败”，而是“这个事实或指令从哪里进入系统，经过哪些 Agent、消息和状态版本，最终影响了哪个动作”。因此 provenance 不应是可选审计附件，而应是动态因果图的骨架。

### 9.3 将 edge-level hook 作为一等插桩接口

运行时至少应为 route、delegate、handoff、message send/receive、state read/write、artifact publish/consume、permission grant 和 tool effect 提供 hook。只采集 Agent 内部 LLM/tool spans，会看见节点却看不见组织行为。

### 9.4 用不变量约束自治，用预算约束涌现

对于高自治架构，应明确不可突破的身份、权限、数据和外部动作边界；同时为 hop、fan-out、Agent 数、token、时间、金额和重试设置预算。自治可以决定局部路径，但不能自行取消系统级边界。

### 9.5 评测必须支持从 outcome 下钻到因果链

一个成熟界面或分析系统应允许从失败 outcome 下钻到相关 Agent、interaction、context item、state version 和 tool effect；也应从一个受污染 source 向前计算所有消费者和 blast radius。这比只按时间查看日志更接近真正的 MAS RCA。

### 9.6 建立最小可行的 MAS Assurance 闭环

可按以下顺序落地：

```text
Identity and task correlation
        ↓
Agent/tool/model trajectory
        ↓
Cross-agent interaction events
        ↓
Context and state provenance
        ↓
Dynamic graph reconstruction
        ↓
Evaluation + Red tests + Blue RCA
        ↓
Runtime policy and recovery controls
```

---

## 十、结论

MAS 不应被理解为“多个 LLM 比一个 LLM 更聪明”，而应被理解为一种组织有限智能、有限上下文和有限权限的系统架构。其当前最直接的工程价值是 context isolation：通过不同 Agent 的认知边界，使相关信息进入相关主体，使不相关或不应访问的信息留在边界之外，并以可验证的接口跨域传递。专业化、并行、异构性、责任分离和制衡机制建立在这一基础之上。

但边界本身不会自动产生好系统。MAS 新增了任务分解、路由、委派、交接、共享状态、拓扑形成和故障传播等系统性风险。它必须以单 Agent 或简单工作流为基线，证明质量、隔离、鲁棒性或治理上的收益足以覆盖协调开销、成本、延迟和新攻击面。正因如此，MAS 的评价是 multi-objective optimization，而不是单一准确率排名。

研究对象的演化可以概括为：

```text
LLM:   Input / Output
Agent: Trajectory
MAS:   Dynamic Causal Interaction Graph
```

在 MAS 中，节点级观测仍然必要，但不足以解释系统行为。委派、消息、交接、状态读写和权限变化等 edge / interaction event 是关键最小观测单位之一；Context Provenance 与 State Provenance 则把这些事件连接为可用于归因的因果网络。

质量评测、红队和蓝队并不是三个孤立领域。它们共享同一套 MAS 行为模型和底层 telemetry：Evaluation 判断系统是否按预期工作；Red Team 检验系统如何被主动操纵；Blue Team 解释偏离为何发生并完成检测、归因、遏制和恢复。安全还必须独立维护 security invariants，因为高质量输出完全可能伴随未授权访问、数据泄漏或权限滥用。

最终，一个可信 MAS 所需要的不是更多日志，而是一套统一的 MAS Assurance Stack：它能够重建动态交互图，解释信息和控制权如何跨越边界，验证架构是否实现设计目标，并在错误或攻击发生时给出可操作的因果答案。

---

## 后续研究方向

本文提出的质量维度、架构分类和保障矩阵仍需通过理论对照与实验验证进一步校准。后续工作可沿三条路线展开：

1. 理论与文献路线：将本文框架与传统 MAS、分布式系统、软件可靠性、组织科学及安全工程中的相关研究对照，明确概念继承关系与 MAS 特有问题。
2. Benchmark 路线：从 Assurance Matrix 中选择可控任务，构建正常、故障和对抗三类数据集，检验指标的可计算性、区分度与诊断价值。
3. 系统路线：设计统一事件 schema、edge-level hooks、context/state provenance 和动态图查询，形成可由 Evaluation、Red Team 与 Blue Team 共用的原型平台。
