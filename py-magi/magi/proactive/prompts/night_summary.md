---
id: "01J9HZ0000NIGHTSUMMAR0Y0"
key: night_summary
name: 晚报
description: 每天 22:00 推送当日完成情况 + 明早首个会议。
frequency: daily
hour: 22
minute: 0
day_of_week: null
day_of_month: null
run_at: null
channel: tg
enabled: true
---

你正在生成晚报。按以下顺序拉数据：(1) 调用 read_recent_emails(hours=24)
看下午 / 晚上的邮件；(2) 调用 read_upcoming_meetings(days=2) 看今晚 + 明早会议；
(3) 读今天的 daily_note 总结今天做完了什么。最后按三段结构输出：今日完成 /
明日首会 / 待办提醒。语气如同事在群里发消息——简洁、直接。优先使用中文。
