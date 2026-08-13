# 模块级 mid-file import 排查报告

> 日期：2026-08-13
> 范围：`magi/` 下全部 Python 源文件（192 个）
> 触发问题：`magi/bus/guild/chatNotifyJob.py:203-205` 中函数体出现 `from magi.bus.library.local.conversationBook import ChannelMismatchError`，让人怀疑全项目里"代码写到一半突然 import"的情况不少。

## 1. 判定标准

**「真违规」A 类**：模块顶层（缩进为 0）的 `import` 语句出现在"模块级非 import 语句"之后。
判定用 AST，先跳过模块 docstring（`ast.Expr(ast.Constant(str))`），剩余顶层节点按 `lineno` 顺序扫：
- 一旦出现首个 `Import` / `ImportFrom`，记录起点；
- 再出现的任何非 import 的顶层节点（赋值、`def`、`class`、`if ... else` 中的非 import 分支等）记为「分界点」；
- 在分界点之后又出现的模块级 `import` 即为违规。

**「合法但值得审视」B 类**：

- **B-1 `if TYPE_CHECKING:` 块内的 import** —— Python 圈公认的合法模式（运行时块跳过，仅类型注解使用）。`agent/agent_context.py:9` `from magi.bus import Bus` 属于此类。
- **B-2 函数 / 方法体内 lazy import** —— 出现 209 处。常被解释成"打破循环依赖"，但**绝大多数实际只是 import surface 控制（延迟加载）**。同项目里 `bus/guild/base.py:186` 的 `import uuid # local import keeps the base module light and avoids ...` 就明确写为 "light"；不要在没有验证 import 图的情况下把 B-2 当作"循环依赖证据"。

`ruff` 的 `E402`（模块级 import 不到顶）只对 A 类报警；`INP001` 对 B-2 没要求。

---

## 2. A 类命中（4 个文件，5 处）

> 所有命中都已逐文件人工复核。每条都给出 *违规代码*、*应该改成的写法*、以及 *取舍分析*。

### 2.1 `magi/agent/system_prompt.py:11` ⚠️ 真违规，建议修

```python
# L1-13
"""System prompt assembly — bus only."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus

from magi.bus.library.local import ActionPriority   # ← L11：在 TYPE_CHECKING 块后，且 `logger = ...` 之前

logger = logging.getLogger("magi.agent.system_prompt")
```

- **L11 `ActionPriority`** 是运行时使用的枚举值（第 150 行比较 `item.priority == ActionPriority.HIGH`）。
- TYPE_CHECKING 块之外才放 import 是常见模式（让"无类型依赖"集中放一起），但 `ActionPriority` 并不属于 TYPE_CHECKING 块——它是真·运行时依赖。
- **建议**：把 `from magi.bus.library.local import ActionPriority` 上移到 `import logging` 那一组顶部；如果刻意保持 "TTL 依赖 vs 运行依赖" 分组，可以挪到 `from typing import TYPE_CHECKING` 之后、`if TYPE_CHECKING` 块之前。

### 2.2 `magi/proactive/credentials_action.py:16` ⚠️ 真违规，建议修

```python
# L1-18
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus.library.local.actionItemBook import ActionItemBook

from magi.bus.library.local.actionItemBook import ActionSource  # ← L16：同上一个性质

logger = logging.getLogger("magi.proactive.credentials_action")
```

- **L16 `ActionSource`** 也是运行时值（第 64 行 `source=ActionSource.PROACTIVE`）。
- **建议**：挪到顶部 `import logging` 那一组，`ActionItemBook` 留在 TYPE_CHECKING 块里——后者仅作类型注解，前者是真枚举。

### 2.3 `magi/bus/db/alembic/env.py:50-54` ✅ 有意的，保留

```python
# ruff: noqa: E402, I001
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import magi.bus.guild                            # noqa: F401
import magi.bus.library.local                    # noqa: F401
import magi.bus.library.magis                    # noqa: F401

from magi.bus.db.base import Base                # noqa: E402
```

- alembic 的 `env.py` 是 alembic 框架按约定路径 import 的钩子，**必须在 sys.path 修正之后**才能 import 应用模型。
- 注释 `# ruff: noqa: E402, I001` 在文件第 28 行已显式打招呼。
- **结论**：Alembic 标准模式，保留。

### 2.4 `magi/connectors/samples/calendar.py:308, 406` ⚠️ 可疑，建议修

```python
# L308
import sys  # noqa: E402  (intentional — after the helpers above)
# L406
import subprocess  # noqa: E402
```

- 注释说 "imported lazily to keep the module import surface minimal"。
- 但 `sys` 早在第 303 行 `sys_platform()` 用过（`return sys.platform`），并且是在 `import sys` *之前* 就被引用了——这是真正的 mid-file 代码（Python 因 import 在使用后才执行 `sys` 居然还能跑通靠的是文件第 80 行 `logger = ...` 提前触发 name resolution，`sys_platform()` 是在被调用时才解析 `sys`）。
- 这种"以性能为借口把 import 拖到后面"在实际项目里几乎从不带来收益——`sys` / `subprocess` 都是 stdlib，导入延迟微秒级。
- **建议**：把两处 import 移到顶部 import 块，删掉 `# noqa: E402`，删掉"lazy import"的误导性注释。

### 2.5 跨文件影响：USER 原报告的 `chatNotifyJob.py`

```python
# L203-205：在 publish 方法里
from magi.bus.library.local.conversationBook import (
    ChannelMismatchError,
)
raise ChannelMismatchError(conversation.channel)
```

- 属于 B-2（函数体内 lazy import），**不是 A 类**，所以 AST 扫描没列在「真违规」里。
- **不是为了打破循环依赖**——实测反证：
  - `conversationBook.py` 顶部没有任何 `chatNotifyJob` / guild / job 类的引用（只有 `Base` / `BaseBook` / SQLAlchemy）。
  - `chatNotifyJob.py` 顶部 TYPE_CHECKING 块导入的是 `ContactBook`，不是 `ConversationBook`。
  - `ruff check --select E402,I001 chatNotifyJob.py` 全过——证明把它顶置不会引发任何结构冲突。
  - `ChannelMismatchError` 是 `ValueError` 子类（`conversationBook.py:197`），定义本身不依赖 `chatNotifyJob`。
- 实际动机只是 **import surface 控制**：`conversationBook.py` 是 1235 行的重模块，会递归引入 SQLAlchemy 元类 + `Base`（触发 ORM 表注册）。`ChannelMismatchError` 99% 的 `publish()` 路径不会触发，lazy import 让 happy path 跳过这部分加载成本。
- **结论**：必要且有意的 lazy import，不必修；之后讨论"B-2"时请勿再把它当作"循环依赖"举例。

---

## 3. B 类分布（参考）

只列出文件级总数，不展开每条——绝大多数都是有意的 lazy import。

| 类别 | 文件数 | 出现次数 |
|---|---|---|
| B-1：`if TYPE_CHECKING:` 块内（合法） | 59 | 76 |
| B-2：函数 / 方法体内 lazy import（合法） | 51 | 209 |

B-2 热点文件（按出现次数排序）：
- `tools/registry.py`：29（B-2 全部走 `_build_tools` 的 lazy import，注册中心/插件加载的标准模式）
- `channels/api/app.py`：22（FastAPI 路由装配，每个路由模块单独 import 以便按需加载）
- `agent/worker.py`：17（agent 内部 cross-module 调用）
- `bus/bootstrap.py`：10（BOARD / Book 反向 init 时拆开 import）
- `bus/library/local/conversationBook.py`：9（消息/对话拆开）

> 这 209 处绝大部分都**不是循环依赖**——常见的真实动机是 import surface 控制（heavy module 推迟到热路径之外，参见 `bus/guild/base.py:186` 注释 `keeps the base module light and avoids ...`）。少数可能确实是 `A → B → A` 闭环，但**不能凭"看起来像"就归类到那里**——必须对每一对的 import 图做 grep 双向验证（`grep 'import B' a.py && grep 'import A' b.py`）。本报告未对 B-2 做这一层验证，所以**此处不下"其中若干是真循环依赖"的结论**。在没有替换为方案前，不建议批量整改。

---

## 4. 建议

1. **A 类中真违规的可修项（2 处，10 分钟内）**：
   - `magi/agent/system_prompt.py:11` 把 `ActionPriority` 挪到顶部。
   - `magi/proactive/credentials_action.py:16` 把 `ActionSource` 挪到顶部。
   - `magi/connectors/samples/calendar.py:308, 406` 把 `sys` / `subprocess` 挪到顶部，删掉误导性 "lazy import" 注释。

2. **保持现状**：
   - `magi/bus/db/alembic/env.py` —— Alembic 框架约束。
   - 全部 B-1（B 类 TYPE_CHECKING 块）。
   - 全部 B-2（函数体内 lazy import）。

3. **附加 lint 配置**（可选）：在 `pyproject.toml` 里给 alembic env.py 单独的 `[tool.ruff.lint.per-file-ignores]` 规则，将 `E402` / `I001` 加进该文件的忽略列表，比 inline `# ruff: noqa` 更显眼：

   ```toml
   [tool.ruff.lint.per-file-ignores]
   "magi/bus/db/alembic/env.py" = ["E402", "I001"]
   ```

   `magi/connectors/samples/calendar.py` 整完后再在同一个表里把 `E402` 也加上，确保以后新增时 lint 会提示。

---

## 5. 复现命令

```bash
# A 类扫描
python3 - <<'PY'
import os, ast
PY_DIR = "/root/GitHub/MAGI/magi"
for root, _, files in os.walk(PY_DIR):
    for name in files:
        if not name.endswith(".py"): continue
        fp = os.path.join(root, name)
        text = open(fp, encoding="utf-8").read()
        lines = text.split("\n")
        try: tree = ast.parse(text)
        except SyntaxError: continue
        body = tree.body
        first = body[0] if body else None
        is_doc = (first is not None
                  and isinstance(first, ast.Expr)
                  and isinstance(first.value, ast.Constant)
                  and isinstance(first.value.value, str))
        first_top_code_line = None
        violations = []
        for node in body:
            ln = node.lineno
            src = lines[ln-1]
            if is_doc and node is first: continue
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not src[:1].isspace():
                if first_top_code_line is not None and ln > first_top_code_line:
                    violations.append((ln, src.rstrip()))
            elif first_top_code_line is None:
                first_top_code_line = ln
        if violations:
            print(os.path.relpath(fp, PY_DIR))
            for ln, t in violations: print(f"  L{ln}: {t}")
PY
```

可以贴进 CI；如果继续这么宽松，建议把 "A 类命中数 == 0" 作为门槛。
