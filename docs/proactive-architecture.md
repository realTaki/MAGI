# 主动性与定时任务

MAGI 将“什么触发一次 Agent 调用”与“系统是否应该主动行动”分为两层。

- `magi/channels/tasks` 是内部的 scheduled-task channel。它只负责已存在任务的持久化、CRUD、cron/一次性时间校验、节点启动后的调度恢复，以及经 `TaskChannel` 进入正常 Agent loop 的执行。
- `magi/proactive` 是系统级主动性的策略层。现有的 `TaskPreset`、内置 YAML 模板和“为新 assigned 联系人注入任务”的规则在这里：它们决定应当创建哪些主动任务，但不会亲自调度或执行。未来的心跳、状态信号、策略评估、规划器与执行审计也将在这里产生“是否行动”的判断，再选择合适的 channel 执行。

因此，cron 不是一个独立的 Agent 行为模型：它只是 `Channel.SCHEDULED` 的一种触发方式。反过来，未来系统主动性也不应直接耦合 APScheduler；它可以在被批准后创建或调用任务 channel，也可以使用其他受控的执行通道。

## 当前边界

已实现：持久化的用户定时/循环任务、一次性任务、手动立即执行、节点重启后的调度恢复，以及基于主动性预设的任务注入。

尚未实现：系统心跳、自动策略、信号采集、自治规划、主动行为的权限与预算控制。实现这些能力时，应先扩展 `magi.proactive.contracts`，并在明确的执行/审计边界后再接入某个 channel。
