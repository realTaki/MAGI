---
name: reminder_template
description: 设计一个定时提醒 / 自动任务的 prompt 时用的模板。当用户说 "每天/每周/每月提醒我 ..." 时先调一次。
version: "1.0"
---

# 定时任务模板

## 适用场景

用户用了 "提醒我..."、"每周帮我看下..."、"每月底..."、"固定每
天..."、"到了 X 时间帮我..." 等表达时，先调本 skill 拿模板再写
prompt。

禁止：对触发时间含糊 ("差不多到点了就...")、对内容含糊
("随便写点什么")、依赖未列出的工具。

## Prompt 写法

1. **触发方式 + 时间**：从 4 类 preset 选一个明确
   - hourly: 每小时（指定 minute 0..59）
   - daily: HH:MM 每天
   - weekly: 周几 + HH:MM
   - monthly: 几日 + HH:MM
2. **任务内容**：一段明确的自然语言指令（≤ 8000 字符）
3. **channel**：webui / tg，决定 reply 落地位置
4. **凭据 / 时区**：不需要调；后端绑当前登录者，全局时区

## 触发器转 5 字段 cron

preset 最终会渲染成 5 字段 cron — operator 无需手写 cron。

## 示例

```
name: "每天 9 点拉 A 股盘前"
prompt: "今天上证指数、深证成指、创业板指的盘前集合竞价
        报价列表，按行业板块做 3 句话概述。每条数据后注源链
        接。"
frequency: daily
hour: 9
minute: 0
channel: webui
```

## 验证

保存后:
1. `GET /api/tasks` 应能立刻看到这一行
2. 等到 09:00 时查看 chat session，prompt 作为 user message，
   agent reply 作为 assistant message
3. 失败 5 次自动 disable（操作员在 ActionItems 看见）
