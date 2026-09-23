---
title: 关键业务流程
description: 桌面端、ASP 和单个 MAGI 进程之间必须保持的行为。
lang: zh-CN
permalink: /business-flows/
---

# MAGI 关键业务流程

这里写的是当前代码在做什么。Society 树、ADAM 控制面、MAGI 之间的任务板不在这条路径上。

## 桌面端启动

1. Electron 壳准备好 Node.js 24、npm 和 Bun，把仓库克隆到 `~/.magi/MAGI`。
2. 本地后端在 `asp/` 执行 `npm ci`，在 `magi/` 执行 `bun install`，再构建 `desktop/app`。
3. 后端用 Node 24 运行 `asp/main.ts`，等到 `GET /health` 成功。
4. 如果还没有任何 MAGI，后端创建 `eva-000`、`eva-001`、`eva-002`，并在它们上线后尝试写下昵称 MELCHIOR、BALTHASAR、CASPER。没上线的不因此让启动失败。

壳只负责窗口、克隆和把调用转给 `desktop/app/main`。关掉界面再加载后端，不能把 ASP 子进程杀掉。退出应用才结束 ASP。

## 创建会话

`POST /conversations` 只接受 `{ "kind": "bot" | "group" }`，并且只有操作者的 Bearer 能调。

- `bot`：ASP 分配下一个 `eva-NNN`，写入自己的数据库，再用 Bun 启动 `magi/`。桌面端不自己拉起 MAGI。
- `group`：只打开操作者一个人的会话，不启动进程。

邀请走 `POST /conversations/{id}/members`。被邀请的 MAGI 收到 `session.invited` 后自己 `join`。群里的人必须是 ASP 已经认识的 MAGI。

## 发一条消息

1. 桌面端先把这条消息写进 `~/.magi/app/chat.sqlite`。
2. 再把它交给 ASP。
3. ASP 把事件留给每个还没确认的接收者。MAGI 从 WebSocket `/connect` 收到，写进自己的工作区，再确认这个 `event_id`。
4. 所有预定接收者都确认之后，ASP 才删掉这条消息事件。
5. MAGI 的回复经 ASP 回到桌面端。桌面端先落盘，再确认。

ASP 不是聊天记录的长期存储。桌面端重启后从自己的 SQLite 读历史。

## 切换模型

设置页把 provider、model 和 API key 写在 `~/.magi/app/provider.json`。`PUT /settings/provider` 只把这一组完整的值转给已经连上的 MAGI。ASP 不保存新的 key。MAGI 在自己的 BUS 里应用 `ChangeProviderNotify`，成功后再回复 `agent.provider.updated`。没连上的 MAGI 由桌面端稍后重试。

## 一个 MAGI 回合

ASP 通道、终端或 Telegram 把文本发布成 `ChatNotify`。Agent worker 领取后，需要模型就发布 `CallLLMJob`，需要工具就发布 `RunToolJob`。Providers worker 和 Tools worker 各自领取并回写结果。要发给操作者的文本发布成 `DeliveryNotify`，由对应通道送出。这些 worker 不互相调用。
