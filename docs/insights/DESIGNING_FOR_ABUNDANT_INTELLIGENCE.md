# Designing for Abundant Intelligence: Why We Should Not Build the Future Around Today’s LLM Limitations

## English

## Introduction

A large share of innovation in today’s AI agent ecosystem is, fundamentally, trying to solve the same problem:

**Large language models are still not good enough.**

Tokens are expensive, so we optimize context, reduce communication, and minimize inference calls.

Context windows are limited, so we build sophisticated memory hierarchies, summarization pipelines, and context management systems.

Planning is unreliable, so we use flows, graphs, and state machines to predefine execution paths.

Multiple agents cannot yet reliably coordinate, divide tasks, negotiate responsibilities, and deliver results purely through communication, so we add supervisors, planners, routers, and controllers above them.

All of these technologies are useful today.

But there is a deeper question worth asking:

> **If an innovation exists primarily to compensate for a limitation that AI itself is rapidly eliminating, how durable is that innovation?**

Perhaps we should adopt a fundamentally different design philosophy:

> **Do not design the future around the limitations of today’s models. Design around the problems that will still exist even if AI becomes 100 times more capable.**

In other words:

**Build for abundant intelligence.**

Build systems for a future in which intelligence itself is plentiful.

---

# 1. Two Fundamentally Different Kinds of AI Innovation

We can roughly divide today’s AI infrastructure innovations into two categories.

The first category addresses limitations in current model capabilities.

For example:

- Tokens are expensive
- Context windows are too small
- LLMs are weak at long-horizon planning
- LLMs are unreliable at tool use
- Agents do not know when to ask other agents for help
- Multi-agent systems cannot reliably divide work
- Agent-to-agent communication is expensive
- Individual inference calls are not intelligent enough

These are all real problems.

As a result, we have built many technologies around them:

```text
Context Compression
Memory Hierarchy
Prompt Optimization
Workflow Engine
Graph Orchestration
Supervisor Agent
Planner
Router
Retry Logic
State Machine
```

But these innovations share one important property:

> **Their value is positively correlated with the weaknesses of the model.**

The weaker the model, the more valuable they become.

The stronger the model, the more their value may decline.

Today, for example, we may build:

```text
Agent A
   ↓
Planner
   ↓
Router
   ↓
Controller
   ↓
Graph
   ↓
Agent B
```

This is not necessarily because such an architecture is inherently elegant.

It is because we do not trust Agent A to reliably determine:

- who it should collaborate with;
- how to decompose the task;
- how to describe the delegated task;
- how to validate the result;
- when to re-plan;
- when to ask for help.

So humans encode those decisions into the surrounding system.

From this perspective, much of what we currently call **agent orchestration** is traditional software engineering compensating for limitations in AI capability.

---

# 2. Many “Multi-Agent Systems” Are Actually LLM Workflows

Many systems described today as multi-agent systems are, in a stricter sense, closer to:

> **LLM-powered workflow engines**

than true multi-agent systems.

A typical architecture looks like this:

```text
            Human-defined Workflow
                    │
                    ▼
               Supervisor
              /     |      \
             /      |       \
        Agent A  Agent B  Agent C
             \      |       /
              predetermined
               transitions
```

There may be multiple agents, but the actual control authority still belongs to a graph predefined by humans.

Who executes first?

Who executes next?

Under what condition does the system transition to another node?

When should it retry?

When should it terminate?

These decisions still live inside a centralized workflow.

The agents are therefore often closer to:

> **Language-capable functions attached to nodes in a graph**

than autonomous software actors.

---

# 3. A True Multi-Agent System May Look More Like the Internet

If AI agents continue improving in reasoning, communication, and autonomous decision-making, future multi-agent systems may adopt a very different design philosophy.

Instead of:

```text
Controller
   │
   ├── Agent A
   ├── Agent B
   └── Agent C
```

we may see systems that look more like:

```text
           Environment

     Agent A ←────→ Agent B
        ↕              ↕
     Agent C ←────→ Agent D
        ↘              ↙
            Agent E
```

The system no longer predefines:

> Agent A must invoke Agent B after completing Step 3.

Instead, the infrastructure provides rules and mechanisms that allow agents to:

- discover one another;
- advertise their capabilities;
- publish tasks;
- accept tasks;
- delegate tasks;
- reject tasks;
- negotiate scope;
- request assistance;
- validate each other’s output;
- redistribute work;
- form temporary organizations;
- dissolve those organizations after the objective is complete.

The Internet provides a useful analogy.

There is no:

```text
Global Internet Controller
```

deciding whether:

```text
Google → Cloudflare → AWS → GitHub
```

should communicate in a particular order.

The infrastructure provides protocols such as:

```text
IP
TCP
DNS
HTTP
TLS
Routing
Identity
```

What participants choose to do on top of those protocols is largely up to them.

Future agent infrastructure may evolve in a similar direction.

The infrastructure defines:

```text
Protocol
Identity
Discovery
Permission
Security
Persistence
Observability
Resource
```

rather than:

```text
Who should think next?
```

---

# 4. We Should Be Careful About Over-Optimizing Tokens

Token optimization is an important part of building AI systems today.

But from a long-term architectural perspective, we should distinguish carefully between:

> **economic constraints**

and:

> **structural constraints**

Token cost is primarily an economic constraint.

Suppose the cost of machine-generated cognition continues to fall:

```text
$ / Token ↓

$ / Reasoning ↓

$ / Intelligence ↓
```

Many optimizations that seem strategically important today may lose their value surprisingly quickly.

Consider a highly sophisticated:

```text
Context Compression Pipeline
```

designed to reduce token consumption by 20%.

If token prices fall by 90%, is the architectural complexity still justified?

Possibly not.

A better long-term assumption is therefore not:

> Tokens will become free.

It is:

> **Cognition will become abundant.**

The cost of producing useful machine intelligence is likely to keep falling.

If an architecture only makes sense when intelligence is expensive, it may not be a future-proof architecture.

---

# 5. Abundant Intelligence Does Not Eliminate Coordination Problems

There is, however, an important distinction.

Even if tokens become nearly free and individual agents become extremely capable, multi-agent coordination does not automatically become easy.

Because:

> **Intelligence ≠ Coordination**

Cheaper intelligence does not eliminate distributed-systems problems.

Consider a system containing `N` agents.

If every agent can communicate freely with every other agent, the number of possible pairwise communication relationships approaches:

```text
O(N²)
```

For 100 agents, there are roughly:

```text
4,950
```

possible pairwise relationships.

For 1,000 agents:

```text
499,500
```

Token cost does not solve the fundamental question:

> **Who should talk to whom?**

This is no longer primarily a token optimization problem.

It becomes a problem of:

```text
Coordination
Organization
Routing
Discovery
Attention Allocation
```

---

# 6. The Future Problem May Be the Opposite: Agents Talking Too Much

Today, we worry that agents cannot communicate well enough.

In the future, we may worry that agents communicate **too much**.

If reasoning and communication become nearly free, even a simple problem could result in:

```text
Agent A:
I think we should use solution X.

Agent B:
I disagree. Solution Y is better.

Agent C:
I want to add another risk.

Agent D:
Let me summarize everyone’s position.

Agent E:
I think B has a good point.

Agent A:
Let me explain again why I still prefer X.

Agent F:
I propose a third solution, Z.
```

And so on.

At that point, the scarce resources are no longer tokens.

They become:

```text
Attention
Latency
Decision Bandwidth
Coordination Efficiency
```

When cognition becomes cheap, the system-level failure mode may shift from:

> not thinking enough

to:

> **thinking, communicating, and acting too much.**

That is why the future of multi-agent infrastructure should not simply eliminate coordination mechanisms.

Instead:

> **Coordination should move from the workflow layer to the protocol layer.**

---

# 7. The Controller Will Not Disappear — It Will Evolve from Manager to Constitution

This distinction is critical.

What may disappear is the:

> **Workflow Controller**

not necessarily the controller itself.

Today, controllers often say:

```text
You do this first.

Then ask B to do that.

If B succeeds, invoke C.

If B fails, return to A.

Finally, let D summarize the result.
```

This is a manager.

It decides how agents should work.

A future controller may instead enforce rules such as:

```text
This agent cannot access that dataset.

This task may consume at most $10.

This agent cannot execute production.write.

Every operation must produce an audit trail.

This task may run for at most 30 minutes.

Agent A cannot impersonate Agent B.

High-risk operations require verification by two independent agents.

This agent may only access resources inside its own workspace.
```

At that point, the controller is no longer primarily a manager.

It becomes something closer to a:

> **Constitution**

It does not tell an intelligent system:

> how to think.

It defines:

> what it is allowed to do.

Those are fundamentally different architectural roles.

---

# 8. From Orchestration to Governance

Following this line of reasoning, the center of gravity in agent infrastructure may shift significantly.

Today, the industry spends a great deal of attention on:

```text
Prompt
Workflow
Graph
Planning
Routing
```

But the problems that survive long-term may increasingly be:

```text
Identity
Permission
Security
Governance
Observability
Persistence
Resource Ownership
Fault Isolation
Accountability
Protocol
```

A rough comparison looks like this:

| Problem | Importance as model capability improves |
|---|---|
| Expensive tokens | Decreases |
| Small context windows | Decreases |
| Weak planning | Decreases |
| Unreliable tool use | Decreases |
| Agents cannot find other agents | Decreases |
| Prompt optimization | Decreases |
| Identity | Increases |
| Permission | Increases |
| Security | Increases |
| Governance | Increases |
| Observability | Increases |
| Accountability | Increases |
| Fault isolation | Increases |
| Resource ownership | Increases |
| Communication protocols | Increases |

There may be a deeper principle here:

> **As intelligence improves, the importance of cognitive infrastructure declines, while the importance of institutional infrastructure rises.**

Smarter agents can increasingly solve cognitive problems themselves.

But greater intelligence does not automatically answer:

- who owns a resource;
- who may access it;
- who is responsible for an action;
- how identity is verified;
- how permissions are constrained;
- how failures are isolated;
- how trust is established;
- how history is recorded;
- how rules are enforced.

These problems do not disappear when intelligence improves.

In many cases, the opposite is true:

> **The more capable agents become, the more important these problems become.**

---

# 9. From Centralized Orchestration to Protocol-Mediated Coordination

A major architectural transition may therefore be:

```text
Centralized Orchestration
          ↓
Protocol-Mediated Coordination
```

The traditional model looks like:

```text
Orchestrator
     │
     ├── Agent
     ├── Agent
     ├── Tool
     └── Agent
```

The system knows the workflow.

The system controls the behavior.

A different architecture looks more like:

```text
                 BUS
          ┌───────┼───────┐
          │       │       │
        Agent   Agent   Agent
          │       │       │
          └───────┼───────┘
                  │
             Tools / Services
```

The bus does not decide:

> Agent B should think next.

Instead, it exposes facts and events:

```text
task.created
message.sent
tool.available
artifact.created
agent.failed
request.completed
policy.denied
```

Agents make their own decisions based on:

```text
Identity
Capability
Context
Policy
Goal
```

and determine:

> Is this event relevant to me?

This shifts the system from:

> **commanding agents**

toward:

> **providing an environment in which agents can coordinate autonomously.**

That is a profound architectural distinction.

---

# 10. What We May Really Need Is Agent Society Infrastructure

If this idea is pushed far enough, the central problem of future multi-agent systems may no longer be:

> How do five agents complete this workflow?

It may instead become:

> How do thousands or millions of autonomous intelligent actors coexist over long periods of time?

The important questions become:

```text
How do agents discover each other?

How do agents establish identity?

How do agents advertise capabilities?

How do agents delegate work?

How do agents establish trust?

How do agents exchange resources?

How do agents negotiate?

How do agents form organizations?

How do agents resolve conflicts?

How do agents enforce rules?

How do agents establish reputation?

How do agents isolate malicious actors?

How do agents prove what they did?
```

At that point, multi-agent systems are no longer purely an AI engineering problem.

They become an intersection of:

```text
Artificial Intelligence
        +
Distributed Systems
        +
Operating Systems
        +
Cybersecurity
        +
Economics
        +
Game Theory
        +
Governance
```

The object of study shifts from:

> Agent Workflow

to:

> **Agent Society**

---

# 11. A Design Principle for the Future

If the argument above has to be compressed into a single architectural principle, I would use:

> **Build for abundant intelligence, scarce coordination, and mandatory governance.**

That means we should be cautious about introducing permanent architectural complexity primarily to optimize:

```text
Token
Prompt
Reasoning Steps
Hard-coded Workflow
```

Instead, long-term infrastructure should focus more heavily on:

```text
Protocol
Identity
Trust
Permission
Security
Governance
Observability
Persistence
Fault Isolation
Resource Management
```

because these problems are unlikely to disappear as models improve.

They may become more important.

---

# 12. A Simple Test for Whether an Agent Technology Is Durable

When evaluating a new agent framework, workflow engine, or infrastructure idea, one question may be especially useful:

> **If models become 10× better at reasoning, context windows become 100× larger, and token costs fall by 100× over the next few years, will this technology still matter?**

If the answer is:

> No.

Then the technology may primarily be solving a temporary limitation of the current generation of models.

That does not mean it has no value.

It simply means it is closer to:

> **Optimization**

than:

> **Infrastructure**

By contrast, if a problem still exists — or becomes even more serious — after models become 100 times more capable:

```text
Security
Identity
Permission
Trust
Coordination
Governance
Accountability
Isolation
```

then it is probably a genuinely durable infrastructure problem.

---

# Conclusion

Over the past few years, much of the industry has been asking:

> How can we make AI behave more like traditional software?

So we surround AI with workflows, graphs, state machines, and controllers, forcing it to operate according to structures inherited from conventional software engineering.

But as AI systems become increasingly autonomous and capable, perhaps the more important question is the reverse:

> **When software itself begins to possess substantial intelligence, should we still control it using traditional workflows?**

Perhaps the most important infrastructure of the future will not be an increasingly sophisticated agent orchestrator.

It may instead be an environment in which large numbers of autonomous intelligent actors can:

**collaborate freely without crossing boundaries, make autonomous decisions while remaining accountable, and communicate openly while still being governable.**

From this perspective, the multi-agent system of the future may not evolve into a more complicated workflow engine.

It may evolve into:

> **the infrastructure of a digital society of intelligences.**

---

# 为丰裕智能设计系统：为什么我们不应该围绕今天的大模型局限构建未来

## 中文

## 引言

今天 AI Agent 领域的大量创新，本质上都在解决一个共同的问题：

**当前的大语言模型还不够好。**

Token 太贵，于是我们研究如何压缩上下文、减少通信和降低推理次数。

Context Window 不够大，于是我们设计复杂的 Memory 管理、摘要机制和上下文搬运系统。

模型规划能力不够稳定，于是我们使用 Flow、Graph、State Machine，将任务执行过程提前固化。

多个 Agent 无法可靠地仅通过交流完成任务分配、协作和交付，于是我们在它们之上再增加 Supervisor、Planner、Router 和 Controller。

这些技术今天当然有价值。

但这里存在一个值得思考的问题：

> **如果这些创新解决的恰好是 AI 正在快速消失的缺陷，那么它们的生命周期究竟有多长？**

我们或许应该采用一种完全不同的系统设计哲学：

> **不要围绕今天模型的局限设计未来，而应该围绕即使 AI 能力提升 100 倍之后仍然存在的问题设计系统。**

换句话说：

**Build for abundant intelligence.**

为一个“智能极其丰富”的未来设计系统。

---

# 一、两类完全不同的 AI 创新

可以粗略地把今天的 AI 基础设施创新分成两类。

第一类，是解决模型当前能力不足的问题。

例如：

- Token 成本过高
- Context Window 太小
- LLM 不善于长期规划
- LLM 不善于可靠调用工具
- Agent 不知道什么时候应该找其他 Agent
- 多 Agent 无法稳定完成任务分工
- Agent 之间通信成本太高
- 单次推理能力有限

这些问题都是真实存在的。

因此，我们围绕它们构建了大量技术：

```text
Context Compression
Memory Hierarchy
Prompt Optimization
Workflow Engine
Graph Orchestration
Supervisor Agent
Planner
Router
Retry Logic
State Machine
```

但这一类创新存在一个共同特点：

> **它们的价值与模型的缺陷呈正相关。**

模型越弱，它们越重要。

模型越强，它们的价值越可能下降。

例如，今天我们可能需要：

```text
Agent A
   ↓
Planner
   ↓
Router
   ↓
Controller
   ↓
Graph
   ↓
Agent B
```

原因并不是这种架构天然优雅，而是我们并不相信 Agent A 能够自己判断：

- 应该找谁合作；
- 如何拆分任务；
- 如何描述任务；
- 如何检查结果；
- 什么时候重新规划；
- 什么时候请求帮助。

于是，人类提前把这些 decision logic 写进系统。

从这个角度来看，很多所谓的 Agent Orchestration，本质上是在用传统软件工程弥补 AI 本身的能力不足。

---

# 二、很多 Multi-Agent System，其实是 LLM Workflow

今天很多被称为 Multi-Agent System 的产品，从严格意义上说，更接近：

> **LLM-powered Workflow Engine**

而不是一个真正意义上的 Multi-Agent System。

典型架构类似：

```text
            Human-defined Workflow
                    │
                    ▼
               Supervisor
              /     |      \
             /      |       \
        Agent A  Agent B  Agent C
             \      |       /
              predetermined
               transitions
```

Agent 看起来很多。

但真正掌握系统控制权的仍然是人类预先设计的 Graph。

谁先执行？

谁后执行？

什么条件进入下一个节点？

什么时候 retry？

什么时候结束？

这些决定仍然存在于一个中心化 workflow 中。

因此 Agent 更像：

> **Graph 上具有语言能力的 Function。**

而不是自主的软件主体。

---

# 三、真正的 Multi-Agent System 可能更接近互联网

如果 AI Agent 的推理能力、通信能力和自主决策能力继续快速提高，那么未来的 Multi-Agent System 可能会采用完全不同的设计哲学。

它可能不是：

```text
Controller
   │
   ├── Agent A
   ├── Agent B
   └── Agent C
```

而是：

```text
           Environment

     Agent A ←────→ Agent B
        ↕              ↕
     Agent C ←────→ Agent D
        ↘              ↙
            Agent E
```

系统不再预先规定：

> Agent A 做完以后必须调用 Agent B。

而只是提供一套基础规则，使 Agent 能够：

- 发现彼此；
- 描述自己的能力；
- 发布任务；
- 接受任务；
- 委托任务；
- 拒绝任务；
- 协商任务边界；
- 请求帮助；
- 检查彼此的结果；
- 重新分配任务；
- 形成临时组织；
- 在任务完成后解散。

这里可以类比互联网。

互联网并不存在一个：

```text
Global Internet Controller
```

决定：

```text
Google → Cloudflare → AWS → GitHub
```

应该按照什么顺序通信。

基础设施提供的是：

```text
IP
TCP
DNS
HTTP
TLS
Routing
Identity
```

至于上层参与者如何组织业务，则由参与者自己决定。

未来的 Agent Infrastructure 很可能也会经历类似的变化。

基础设施负责定义：

```text
Protocol
Identity
Discovery
Permission
Security
Persistence
Observability
Resource
```

而不是：

```text
Who should think next?
```

---

# 四、因此，我们也许不应该过度优化 Token

Token Optimization 是今天 AI 系统非常重要的一部分。

但从长期架构设计来看，我认为应该非常谨慎地区分：

> **经济约束**

与：

> **结构约束**

Token 成本主要属于前者。

假设未来单位智能成本持续下降：

```text
$ / Token ↓

$ / Reasoning ↓

$ / Intelligence ↓
```

那么很多今天看来非常重要的优化可能迅速失去战略价值。

例如为了减少 20% Token 而引入非常复杂的：

```text
Context Compression Pipeline
```

如果两年以后 Token 价格下降 90%，这种复杂度是否仍然值得？

这是非常值得怀疑的。

因此，更好的假设可能不是：

> Token 将完全免费。

而是：

> **Cognition will become abundant.**

计算产生的“认知能力”会越来越便宜。

如果一个架构只有在 Intelligence 很昂贵的时候才合理，那么它可能并不是一个适合未来的架构。

---

# 五、但智能丰富，并不意味着 Multi-Agent Coordination 自动消失

这里需要一个非常重要的区分。

即使 Token 接近免费，即使 Agent 的智力水平极高，Multi-Agent System 仍然不会变得没有挑战。

因为：

> **Intelligence ≠ Coordination**

智能成本下降，并不会消除分布式系统问题。

例如一个包含 `N` 个 Agent 的系统，如果所有 Agent 都能够自由相互通信，那么潜在通信关系数量接近：

```text
O(N²)
```

100 个 Agent 大约存在：

```text
4,950
```

种两两关系。

1000 个 Agent 则接近：

```text
499,500
```

种。

因此，即使 Agent 交流本身几乎免费，仍然存在一个基本问题：

> **谁应该和谁交流？**

这已经不再是 Token Optimization。

而是：

```text
Coordination
Organization
Routing
Discovery
Attention Allocation
```

问题。

---

# 六、未来甚至可能出现相反的问题：Agent 说得太多

今天，我们担心 Agent 不会沟通。

未来，我们可能担心 Agent **过度沟通**。

假设通信和推理几乎免费，那么一个简单问题可能演变为：

```text
Agent A:
我认为应该使用方案 X。

Agent B:
我不同意，我认为 Y 更合理。

Agent C:
我补充一个风险。

Agent D:
我重新总结一下大家的观点。

Agent E:
我认为 B 的观点值得考虑。

Agent A:
让我解释一下为什么我仍然认为 X 更好。

Agent F:
我提出第三种方案 Z。
```

不断继续。

这时候真正稀缺的东西已经不是 Token。

而是：

```text
Attention
Latency
Decision Bandwidth
Coordination Efficiency
```

也就是说：

> 当 cognition 足够廉价以后，系统面临的问题可能从“想得不够多”，变成“想得太多”。

因此，未来 Multi-Agent Infrastructure 的核心问题并不是简单地取消所有协调机制。

而是：

> **让协调发生在协议层，而不是 Workflow 层。**

---

# 七、Controller 不会消失，它会从 Manager 变成 Constitution

这也是一个非常重要的区别。

未来可能消失的是：

> **Workflow Controller**

而不是所有 Controller。

今天的 Controller 经常负责：

```text
你先做这个。

然后让 B 做那个。

如果 B 成功，让 C 工作。

如果失败，再调用 A。

最后由 D 汇总。
```

这是 Manager。

它负责安排 Agent 如何思考。

但未来更重要的 Controller 可能负责另外一些事情：

```text
这个 Agent 不允许读取该数据。

这个 Agent 本次任务最多消费 $10。

这个 Agent 不允许执行 production.write。

所有操作必须保留 Audit Trail。

这个任务最长允许运行 30 分钟。

Agent A 不允许伪造 Agent B 的身份。

高风险操作必须由两个独立 Agent 验证。

这个 Agent 只能访问自己 Workspace 中的数据。
```

这时候 Controller 已经不再像 Manager。

它更像：

> **Constitution**

它并不告诉智能体：

> 你应该怎么思考。

而是规定：

> 你被允许做什么。

这是两个完全不同的系统哲学。

---

# 八、从 Orchestration 转向 Governance

如果沿着这个方向继续推演，可以发现 Agent Infrastructure 的重点可能会发生一次非常大的迁移。

今天行业大量关注：

```text
Prompt
Workflow
Graph
Planning
Routing
```

未来真正长期存在的问题可能更多是：

```text
Identity
Permission
Security
Governance
Observability
Persistence
Resource Ownership
Fault Isolation
Accountability
Protocol
```

可以简单表示为：

| 问题 | 随模型能力增长的重要性 |
|---|---|
| Token 太贵 | 下降 |
| Context 太小 | 下降 |
| LLM 不会规划 | 下降 |
| LLM 不会使用工具 | 下降 |
| Agent 不会找 Agent | 下降 |
| Prompt Optimization | 下降 |
| Identity | 上升 |
| Permission | 上升 |
| Security | 上升 |
| Governance | 上升 |
| Observability | 上升 |
| Accountability | 上升 |
| Fault Isolation | 上升 |
| Resource Ownership | 上升 |
| Communication Protocol | 上升 |

这里实际上存在一个更深层次的规律：

> **随着 Intelligence 能力提升，认知层基础设施的重要性下降，而制度层基础设施的重要性上升。**

因为更加聪明的 Agent 可以自己解决越来越多的 cognition problems。

但它无法凭借“更加聪明”解决：

- 谁拥有某个资源；
- 谁允许谁访问；
- 谁应该为一次操作负责；
- 如何验证身份；
- 如何限制权限；
- 如何隔离故障；
- 如何建立信任；
- 如何记录历史；
- 如何执行规则。

这些问题不会因为 Intelligence 提升而自动消失。

甚至恰恰相反：

> Agent 越强，这些问题越重要。

---

# 九、从 Centralized Orchestration 到 Protocol-Mediated Coordination

因此，我认为未来 Multi-Agent Architecture 一个非常重要的转变可能是：

```text
Centralized Orchestration
          ↓
Protocol-Mediated Coordination
```

传统方式是：

```text
Orchestrator
     │
     ├── Agent
     ├── Agent
     ├── Tool
     └── Agent
```

系统知道所有工作流。

系统控制所有行为。

而另外一种方式是：

```text
                 BUS
          ┌───────┼───────┐
          │       │       │
        Agent   Agent   Agent
          │       │       │
          └───────┼───────┘
                  │
             Tools / Services
```

BUS 并不负责决定：

> 接下来应该让 Agent B 思考。

而只是表达：

> 一个事件发生了。

比如：

```text
task.created
message.sent
tool.available
artifact.created
agent.failed
request.completed
policy.denied
```

Agent 根据：

```text
Identity
Capability
Context
Policy
Goal
```

自己决定：

> 这个事件是否与我有关？

这意味着系统从：

> **命令 Agent。**

逐渐转向：

> **为 Agent 提供一个可以自主协作的环境。**

这是一个非常根本的架构差异。

---

# 十、真正值得构建的是 Agent Society Infrastructure

如果继续把这个思想推到极致，那么未来 Multi-Agent System 最重要的问题可能甚至不再是：

> 如何让五个 Agent 完成一个 Workflow？

而是：

> 如何让数千乃至数百万个自主智能体长期共存？

问题会逐渐变成：

```text
How do agents discover each other?

How do agents establish identity?

How do agents advertise capabilities?

How do agents delegate work?

How do agents establish trust?

How do agents exchange resources?

How do agents negotiate?

How do agents form organizations?

How do agents resolve conflicts?

How do agents enforce rules?

How do agents establish reputation?

How do agents isolate malicious actors?

How do agents prove what they did?
```

此时 Multi-Agent System 已经不再只是 AI Engineering。

它逐渐成为以下领域的交叉：

```text
Artificial Intelligence
        +
Distributed Systems
        +
Operating Systems
        +
Cybersecurity
        +
Economics
        +
Game Theory
        +
Governance
```

研究对象也从：

> Agent Workflow

变成：

> **Agent Society**

---

# 十一、一个更适合未来的设计原则

如果需要把以上思想浓缩成一句架构原则，我会使用：

> **Build for abundant intelligence, scarce coordination, and mandatory governance.**

即：

> **为丰裕的智能、稀缺的协调能力和不可缺失的治理而设计系统。**

这意味着，我们应该谨慎投入那些主要优化：

```text
Token
Prompt
Reasoning Steps
Hard-coded Workflow
```

的长期架构复杂度。

而应该更加关注：

```text
Protocol
Identity
Trust
Permission
Security
Governance
Observability
Persistence
Fault Isolation
Resource Management
```

因为后者并不会随着模型进步而消失。

它们反而可能随着 Agent 能力的增强而变得更加重要。

---

# 十二、判断一项 Agent 技术是否长期有效的一个问题

因此，当看到一个新的 Agent Framework、Workflow Engine 或 Infrastructure Idea 时，也许可以问一个很简单的问题：

> **如果未来几年模型的推理能力提高 10 倍、Context Window 提高 100 倍、Token 成本下降 100 倍，这项技术还重要吗？**

如果答案是：

> 不再重要。

那么它可能主要是在解决一个时代性的模型缺陷。

这并不意味着它没有价值。

但意味着它更像：

> **Optimization**

而不是：

> **Infrastructure**

相反，如果模型能力提高 100 倍以后，这个问题仍然存在，甚至更加严重：

```text
Security
Identity
Permission
Trust
Coordination
Governance
Accountability
Isolation
```

那么它很可能属于真正长期的基础设施问题。

---

# 结语

过去几年，我们一直在思考：

> 如何让 AI 更像软件？

于是我们给 AI 加上 Workflow、Graph、State Machine 和 Controller，让它按照传统软件的方式工作。

但随着 AI 能力继续提升，也许真正值得思考的问题正在反过来：

> **当软件本身开始拥有越来越强的自主智能以后，我们是否仍然应该用传统 Workflow 控制它？**

也许未来真正重要的基础设施，并不是一个越来越复杂的 Agent Orchestrator。

而是一个允许大量自主智能体：

**自由协作，但不能越界；自主决策，但必须负责；自由通信，但可以被治理。**

的运行环境。

从这个意义上说，Multi-Agent System 最终可能不会演化成一个更加复杂的 Workflow Engine。

它更可能演化成：

> **一个数字智能社会的基础设施。**
