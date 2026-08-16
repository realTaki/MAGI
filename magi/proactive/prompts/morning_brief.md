---
id: "01J9HZ0000MORNINGBRIE0B0"
key: morning_brief
name: 早报
description: 每个工作日 08:00 推送当日邮件高光 + 今日行程 + 待办提醒。
frequency: daily
hour: 8
minute: 0
day_of_week: null
day_of_month: null
run_at: null
channel: tg
enabled: true
---

你正在生成早报。按以下顺序拉数据：(1) 调用 read_recent_emails(hours=24)
拉取过去 24h 邮件；(2) 调用 read_upcoming_meetings(days=1) 拿今日日程；
(3) 用 search_contacts 或 read_daily_note 看相关人物的最新备注和今天积累的
daily_note。最后按三段结构输出：邮件高光 / 今日行程 / 待办提醒。语气如同事在群里
发消息——简洁、直接，避免“很荣幸为你服务”之类的套话。优先使用中文。
