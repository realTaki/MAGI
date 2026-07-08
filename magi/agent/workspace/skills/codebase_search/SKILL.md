---
name: codebase_search
description: 在本仓库代码里快速定位某个模块/函数/类的位置，告诉用户 "在哪一行" 以及 "它做了什么"。
version: "1.0"
---

# 代码搜索

## 适用场景

当用户问 "这个项目里 X 怎么做的"、"Y 在哪个文件"、"怎么调
用 Z" 时使用。

## 推荐工具

1. 优先用 `read_file` 读取文件精确段 —— 已知路径时 100% 准
2. 找不到路径 → 用 `list_files` 列目录（一定要指定 path，
   默认是 workspace 根）
3. 还没头绪 → 用 `send_message`（TG-side）让 EVE 复述

## 仓库拓扑

- `magi/agent/agent.py` — chat turn 主入口
- `magi/agent/tools/registry.py` — 内置工具枚举
- `magi/agent/state/orm.py` — SQLAlchemy tables
- `magi/channels/webui/api/` — FastAPI endpoints
- `magi/agent/skills/` — SKILL.md 装载器

## 推荐 reply 模板

> **位置**: `magi/agent/skills/loader.py:42`
> **作用**: 扫描 `workspace/skills/*/SKILL.md` 并提取
> frontmatter。
