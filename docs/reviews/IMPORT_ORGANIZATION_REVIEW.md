---
title: Import 组织与错误分层 Review
description: 模块顶层 mid-file import 排查 + 函数体 lazy import 分布 + chatNotifyJob 循环依赖复盘 + bus/errors.py 抽取提案。
permalink: /insights/import-organization-review/
---

# Import 组织与错误分层 Review

> **范围**：`magi/` 下全部 Python 源文件（192 个）。
> **触发**：`magi/bus/guild/chatNotifyJob.py:203` 函数体内的 `from magi.bus.library.local.conversationBook import ChannelMismatchError`，让人怀疑"代码写到一半突然 import"是项目普遍现象。
> **结论速读**：A 类（真·模块顶层 mid-file import）仅 4 个文件 5 处，远比直觉少；其余绝大多数"看起来 mid-file"是合法模式。B-2（函数体 lazy import，209 处）绝大部分**不是循环依赖**，而是 import surface 控制。基于此提议把跨模块的 ErrorCode + `ChannelMismatchError` 抽到 `magi/bus/errors.py`，顺手解掉 `chatNotifyJob.py:203` 的 lazy import。

## TL;DR

| 类型 | 数量 | 性质 | 处置 |
|---|---|---|---|
| **A 类**：模块顶层 mid-file import | 4 个文件 / 5 处 | 真违规 | 3 处可修，1 处保留 |
| **B-1 类**：`if TYPE_CHECKING:` 块内 import | 59 文件 / 76 处 | 合法 | 不动 |
| **B-2 类**：函数/方法体 lazy import | 51 文件 / 209 处 | 大多数合法 | 不动（仅个别疑似可改） |
| **C 类**：异常/ErrorCode 分散定义 | 22 个 + 4 个 StrEnum | 结构问题 | 提议抽 `bus/errors.py` |

---

## §1. 判定标准

- **A 类（真违规）**：模块顶层（缩进为 0）的 `import` 出现在"模块级非 import 语句"之后。
- **B-1 类**：`if TYPE_CHECKING:` 块内的 import（运行时为 False，仅类型注解用）。
- **B-2 类**：函数/方法体内的 `import`（常被解释为"打破循环依赖"，但绝大多数**只是 import surface 控制**——见 §4 实证）。

实现方式：AST 扫描 + 模块 docstring 跳过 + 缩进位置判定。具体脚本见 §8。

---

## §2. A 类命中（4 个文件，5 处）

### 2.1 `magi/agent/system_prompt.py:11` ⚠️ 真违规，建议修

```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus import Bus

from magi.bus.library.local import ActionPriority   # ← L11：在 TYPE_CHECKING 块后，运行时使用

logger = logging.getLogger("magi.agent.system_prompt")
```

`ActionPriority` 是运行时使用的枚举值（L150 `item.priority == ActionPriority.HIGH`），不属于 TYPE_CHECKING 范畴。

**修法**：上移到 `import logging` 那一组顶部。

### 2.2 `magi/proactive/credentials_action.py:16` ⚠️ 真违规，建议修

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi.bus.library.local.actionItemBook import ActionItemBook

from magi.bus.library.local.actionItemBook import ActionSource  # ← L16：同上性质

logger = logging.getLogger("magi.proactive.credentials_action")
```

`ActionSource` 也是运行时值（L64 `source=ActionSource.PROACTIVE`）。

**修法**：上移到顶部 import 组，`ActionItemBook` 留在 TYPE_CHECKING 块里——后者仅作类型注解，前者是真枚举。

### 2.3 `magi/bus/db/alembic/env.py:50-54` ✅ 有意，保留

```python
# ruff: noqa: E402, I001
import magi.bus.guild                        # noqa: F401
import magi.bus.library.local                # noqa: F401
import magi.bus.library.magis                # noqa: F401
from magi.bus.db.base import Base            # noqa: E402
```

Alembic 的 `env.py` 是 alembic 框架按约定路径 import 的钩子，**必须在 sys.path 修正之后**才能 import 应用模型。文件第 28 行已显式 `# ruff: noqa: E402, I001`。

**修法**：建议改用 `pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]`：

```toml
[tool.ruff.lint.per-file-ignores]
"magi/bus/db/alembic/env.py" = ["E402", "I001"]
"magi/connectors/samples/calendar.py" = ["E402"]
```

更显眼，且确保后续 PR 不会偷偷塞新的 mid-file import 进 env.py。

### 2.4 `magi/connectors/samples/calendar.py:308, 406` ⚠️ 可疑，建议修

```python
# L308
import sys  # noqa: E402  (intentional — after the helpers above)
# L406
import subprocess  # noqa: E402
```

注释说"imported lazily to keep the module import surface minimal"。但：

- `sys` 早在 L303 的 `sys_platform()` 里被引用（`return sys.platform`），靠的是函数调用时才解析名字——这证明所谓"lazy"没有性能价值，只是个误判。
- `subprocess` / `sys` 都是 stdlib，加载延迟微秒级。

**修法**：把两处挪到顶部，删掉 `# noqa: E402` 和"lazy import"注释。

### 2.5 关于原报告的 `chatNotifyJob.py:203`

```python
# L203-205：在 publish 方法里
from magi.bus.library.local.conversationBook import (
    ChannelMismatchError,
)
raise ChannelMismatchError(conversation.channel)
```

属于 B-2（函数体内 lazy import），**不是 A 类**。结论详见 §4。

---

## §3. B 类分布（参考）

按文件统计的 lazy import 数量（已分类 B-1 vs B-2）：

```
FILE                                  | TYPE_CHECKING | lazy imports in fn body
agent/agent_context.py                 |     1 |     0
agent/auto_title.py                    |     1 |     1
agent/compaction.py                    |     2 |     1
agent/instructions.py                  |     1 |     0
agent/system_prompt.py                 |     1 |     1
agent/worker.py                        |     4 |    17
bus/bootstrap.py                       |    26 |    10
bus/library/local/conversationBook.py  |     0 |     9
channels/api/app.py                    |     4 |    22
tools/registry.py                      |     1 |    29
…
TOTAL                                  |    76 |   209
```

**B-1（76 处）**：合法模式，跳过。

**B-2（209 处）热点文件**：
- `tools/registry.py`：29（B-2 全部走 `_build_tools` 的 lazy import，插件加载的标准模式）
- `channels/api/app.py`：22（FastAPI 路由装配，按需加载路由模块）
- `agent/worker.py`：17（agent 内部 cross-module 调用）
- `bus/bootstrap.py`：10（BOARD / Book 反向 init 时拆开 import）
- `bus/library/local/conversationBook.py`：9（消息/对话拆开）

**B-2 是否循环依赖？** 大多数**不是**。同项目里 `bus/guild/base.py:186` 的 `import uuid # local import keeps the base module light and avoids ...` 注释明确写为 "light"——属于延迟加载，不是 cycle。

要真正判定某处是否为循环依赖，必须做双向 grep：

```bash
grep -rn "import X\|from X import" a.py | grep -i B_module
grep -rn "import A\|from A import" b.py | grep -i A_module
```

**双向**都非空才是 cycle。**单向**则不是——那只是设计选择。`chatNotifyJob.py:203` 正是 §4 的反例。

---

## §4. 案例复盘：`chatNotifyJob.py:203` 不是循环依赖

### 反证 1：`conversationBook.py` 不引 `chatNotifyJob`

```bash
$ grep -n "chatNotifyJob\|ChatNotifyJob\|chatNotifyBoard" \
    magi/bus/library/local/conversationBook.py
50:    # reads the row, not a chatNotifyJob payload.
350:        # :meth:`chatNotifyBoard.publish` enforces on the LLM input.
912:        # chatNotifyJob does not need a parallel cap, since the LLM
914:        # :func:`build_messages_from_conversation`, not a chatNotifyJob
```

只有 docstring 引用，**没有 import 语句**。

### 反证 2：`chatNotifyJob.py` 顶部 TYPE_CHECKING 块引的是 `ContactBook`，不是 `ConversationBook`

```python
# chatNotifyJob.py:25-28
if TYPE_CHECKING:
    from magi.bus.library.local.contactBook import ContactBook
```

`contactBook` ≠ `conversationBook`，是两个完全不同的模块。

### 反证 3：顶层 import 不会引发结构冲突

```bash
$ ruff check magi/bus/guild/chatNotifyJob.py --select E402,I001
All checks passed!
```

把 `ChannelMismatchError` 提到 `chatNotifyJob.py` 顶部，`ruff` 不报警——证明无循环。

### 反证 4：`ChannelMismatchError` 类定义本身不依赖 `chatNotifyJob`

```python
# conversationBook.py:197
class ChannelMismatchError(ValueError):
    ...
```

`ValueError` 子类，7 行实现，零非 stdlib 依赖。

### 真实动机

`conversationBook.py` 共 1235 行，递归引入 SQLAlchemy ORM 元类 + `Base`（触发 ORM 表注册）+ `BaseBook`——是 bus 里最重的一个模块。`ChannelMismatchError` 只在 `publish()` 的 cross-channel 守卫失败路径（极少数异常）才用；happy path 完全不需要它。

**lazy import 让 happy path 跳过这部分加载成本**——这是 import surface 控制，不是循环依赖。

> **复盘教训**：把"B-2 函数体 lazy import"等同于"打破循环依赖"，是 Lazy Import 误读的一种典型。必须双向 grep 验证。

---

## §5. 三个避免 mid-file import 的方案对比

把"lazy import 写在函数体里"消除，有三种写法：

| 方案 | 代价 | 收益 | 备注 |
|---|---|---|---|
| **A. 顶层 import** | 0 | 静态可见，符合 PEP 8 | 仅当 import 对象本身轻量时可行 |
| **B. PEP 562 模块级 `__getattr__`** | 单文件改动 ~6 行 | 保留 lazy 语义；调用代码干净 | mypy/pyright 偶有偏差 |
| **C. 把被引用类拆到独立零依赖模块** | 触及多个文件 | 同时解掉 A/B/C 三类问题 | 推荐用于跨模块契约 |

### A. 顶层 import

最直接：把 `from magi.bus.library.local.conversationBook import ChannelMismatchError` 从函数体挪到文件顶部。

**适用条件**：被 import 的对象所在模块本身轻量，且不会被反复 import。

### B. PEP 562 `__getattr__`

```python
# chatNotifyJob.py 顶部
def __getattr__(name: str):  # PEP 562
    if name == "ChannelMismatchError":
        from magi.bus.library.local.conversationBook import ChannelMismatchError
        return ChannelMismatchError
    raise AttributeError(name)
```

然后函数体内直接 `raise ChannelMismatchError(...)`，不再需要函数内 import。**单文件改动，零调用代码变化**。

### C. 拆分到零依赖模块（推荐）

新建 `magi/bus/library/local/exceptions.py`，把 `ChannelMismatchError` 单独搬过去。`conversationBook.py` 与 `chatNotifyJob.py` 都从这里 import。

**为什么是 C**：
- `ChannelMismatchError` 是 7 行 Exception 子类，**根本不需要和 1235 行的 ORM 模块同居**。
- 异常类是跨模块契约，本就该独立——`bus/errors.py` 这种"集中异常"的设计自然落到这里。

---

## §6. 提议：`magi/bus/errors.py` 抽取方案

### 6.1 全量图谱

22 个异常 + 4 个 ErrorCode，**按域**分布：

| 域 | 现有位置 | 跨模块契约？ | 处置 |
|---|---|---|---|
| **bus/guild** | a2aJob.py:49, callLLMJob.py:22, chatNotifyJob.py:73, runToolJob.py:28 | 全部 | **搬到 bus/errors.py** |
| **bus/library/local** | conversationBook.py:165/176/186/198 | **只 ChannelMismatchError** | 搬 1 个；其余 3 个保留 |
| **bus/library/file** | skillsBook.py:101 | 待定（看 §6.3） | 评估 |
| **bus/db** | db/file.py:184/188/194 | 否（FileShelf 内部） | 保留 |
| channels/api | errors.py:38 | 是 | **已在专门 errors.py，别动** |
| providers | errors.py:30+ | 是 | **已在专门 errors.py，别动** |
| startup | config.py:22 | 启动期 | 保留 |

### 6.2 推荐结构

**单文件 `magi/bus/errors.py`**，**不是 `bus/errors/` 包**。理由：

- 8 个跨模块异常 + 4 个 ErrorCode = 12 个类，单文件够用。
- `from magi.bus.errors import ChatErrorCode, ChannelMismatchError` 一行即用，目录层次更扁。
- 子包适合"会持续扩张"的场景——目前不必要。

样例：

```python
"""Bus-level error contracts.

DEPENDENCY RULE — this module is import-leaf: it must not
import anything from magi.bus.db, magi.bus.guild,
magi.bus.library, or magi.bus.bootstrap. The whole point of
moving errors here is to break the chatNotifyJob ↔
conversationBook (and friends) coupling that motivates
function-body lazy imports. The moment anyone reaches for a
non-stdlib dep here, the cycle reappears under another name.

Errors live here if and only if they cross module boundaries:
a third module imports them, or two callers from different
sub-packages handle them. Module-internal exceptions
(FormatError, ConversationPathError, …) stay where they are.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Final


class ChannelMismatchError(ValueError):
    """The conversation was created on a different channel …
    Surface as 403 with ``conversation_channel`` …
    """

    def __init__(self, conversation_channel: str) -> None:
        super().__init__(
            f"conversation was created on {conversation_channel!r}; "
            f"refusing cross-channel publish"
        )
        self.conversation_channel: Final[str] = conversation_channel


class ChatErrorCode(StrEnum):
    RUN_CANCELLED = "magi.run_cancelled"
    AGENT_CRASHED = "agent_crashed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_FAILED = "llm_failed"
    LEASE_LOST = "lease_lost"


class LLMErrorCode(StrEnum):
    NONE = ""
    CREDENTIALS_REQUIRED = "magi.llm_credentials_required"
    AUTH_FAILED = "llm.auth_failed"
    RATE_LIMITED = "llm.rate_limited"
    NETWORK_ERROR = "llm.network_error"
    CONTEXT_TOO_LONG = "llm.context_too_long"


class ToolErrorCode(StrEnum):
    NONE = ""
    UNKNOWN = "tool.unknown"
    CRASHED = "tool.crashed"
    CANCELLED = "tool.cancelled"
    UNAUTHORIZED = "tool.unauthorized"
    FAILED = "tool.failed"


class A2AErrorCode(StrEnum):
    TIMEOUT = "a2a_timeout"
    PEER_UNREACHABLE = "a2a.peer_unreachable"
    # ...


__all__ = [
    "ChannelMismatchError",
    "A2AErrorCode",
    "ChatErrorCode",
    "LLMErrorCode",
    "ToolErrorCode",
]
```

### 6.3 决策点

**Q1：4 个 ErrorCode 是合并还是独立？**

**独立**。理由：
- 跨 Job 的语义本质不同——`A2AErrorCode.TIMEOUT ≠ ChatErrorCode.TIMEOUT`，值不同。
- 合并成 `MagiErrorCode` 加前缀成员会失去类型层的"哪个 Job 的错误"信号，handler 反序列化时要做 string parsing。
- `chatNotifyJob.py:79-80` 已经在 docstring 中显式"Mirrors LLMErrorCode/A2AErrorCode"——契约已经稳定，没必要再动结构。

**Q2：`SkillBookError` 搬不搬？**

需要先评估：

```bash
grep -rn "from magi.bus.library.file.skillsBook import SkillBookError" magi --include="*.py"
```

若 import 落点只在 `tools/comms/`、`tools/skills/` 等同目录模块，则不算跨 bus 域，保留；若调用方跨子包（guild + library 都用），就搬。

**Q3：对话 3 个内部异常搬不搬？**

**不搬**。`ConversationPathError` / `ConversationCorruptError` / `ConversationNotFoundError` 只在 `conversationBook.py` 内部抛，外部从未 `except`。搬出去等于把"模块内部类型"和"公开契约"混在一起，污染对外 API 表面。

### 6.4 工作量

触碰 5 个文件，diff 估计 100-200 行：

- 新建 `magi/bus/errors.py`（~150 行，来自 4 个 guild ErrorCode + 1 个 ChannelMismatchError）。
- `bus/guild/{a2aJob,callLLMJob,chatNotifyJob,runToolJob}.py` 删 StrEnum，改 `from magi.bus.errors import XxxErrorCode`。
- `bus/library/local/conversationBook.py:197` 删 `ChannelMismatchError`，改为 `from magi.bus.errors import ChannelMismatchError`；`__all__` 里那一行同步。
- `bus/guild/chatNotifyJob.py:203-205` 函数内 lazy import **直接提到顶部**，import `magi.bus.errors`。

### 6.5 红线

**`magi/bus/errors.py` 必须零依赖**——不能 import sqlalchemy、不能 import `db.base`、不能 import 任何 guild/library 模块。Errors 是 import graph 最底层，**只有它依赖 stdlib**。

任何 PR 想在这里加 ORM 类型做 `IntegrityError` 重抛，立刻拒——那正是当年让你写"打破循环依赖" lazy import 的根源。

---

## §7. 推荐实施顺序

按风险/收益比从低到高排：

1. **A 类 3 处可修**（10 分钟）
   - [agent/system_prompt.py:11](/root/GitHub/MAGI/magi/agent/system_prompt.py#L11) `ActionPriority` 上移
   - [proactive/credentials_action.py:16](/root/GitHub/MAGI/magi/proactive/credentials_action.py#L16) `ActionSource` 上移
   - [connectors/samples/calendar.py:308, 406](/root/GitHub/MAGI/magi/connectors/samples/calendar.py#L308) `sys` / `subprocess` 上移 + 删误导注释
2. **把 alembic / calendar 的 noqa 集中到 pyproject.toml**（5 分钟）
   - 见 §2.3 末尾
3. **新建 `magi/bus/errors.py`，搬 5 个跨模块契约类**（30 分钟 + 测试）
   - 见 §6
4. **`chatNotifyJob.py:203` lazy import 提到顶部**（5 分钟，与 §6 同步完成）

如果只能做一步：**§7.3 + §7.4 合并**——一次 PR 干掉 209 处 lazy import 中最有代表性的一处，同时建立 bus 域的契约层。

---

## §8. 复现命令 / CI 建议

### 8.1 A 类扫描脚本

```bash
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

期望输出为空（4 处都修完后）。

### 8.2 双向 import 图验证

```bash
# 用法：grep A 是否引 B，同时 grep B 是否引 A；双向才叫循环依赖
grep -n "import B\|from B " a.py
grep -n "import a\|from a " b.py
```

对 `chatNotifyJob.py:203` 的反证：

```bash
$ grep -n "chatNotifyJob" magi/bus/library/local/conversationBook.py
# 仅 docstring，无 import
```

### 8.3 CI 建议

把 §8.1 脚本塞进 CI（例如 `.github/workflows/lint.yml` 的额外 step），期望输出空。一旦命中非零，立即 fail。这比单纯靠 ruff `E402` 更严格——能抓到 `TYPE_CHECKING` 块后的运行时 import 这类 ruff 放过的场景。