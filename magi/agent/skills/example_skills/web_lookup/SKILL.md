---
name: web_lookup
description: 在互联网上检索公开信息（汇率、新闻、股价等），查询结果包含链接与摘要。
version: "1.0"
---

# Web 检索

## 适用场景

当用户问 "今天 X 多少钱"、"最近关于 Y 的新闻" 等需要
**公开信息 / 时效性数据** 的问题时调用本 skill。

禁止用于：内部文件搜索（用 `read_file`/`grep`）、员工
信息（用 `employees` 表查询接口）、聊天历史（用
`search_sessions` tool）。

## 推荐步骤

1. 用户问题里有具体 ticker / 关键词，**直接答**；如缺关
   键词先追问一次。
2. 数据后注明 "截至 YYYY-MM-DD HH:MM UTC"。
3. 答案≥2 段；段落短而具体。
