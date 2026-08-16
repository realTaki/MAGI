---
id: "01J9HZ0000DAILYSTAND000UPBR"
key: daily_standup_brief
name: 每日晨报
description: 每个工作日 09:00 推送当日待办摘要 + 昨日完成情况。
frequency: daily
hour: 9
minute: 0
day_of_week: null
day_of_month: null
run_at: null
channel: tg
enabled: true
---

You are generating a brief morning summary for the assigned user.
Today's open tasks: {tasks_open}. Yesterday's completed tasks:
{tasks_done}. Urgent action items: {action_items}. Write a concise
(under 120 words) stand-up brief in the user's preferred language.
Highlight anything due today, any blockers, and a single suggested
focus for the morning.
