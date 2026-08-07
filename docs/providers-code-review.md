# Providers 模块代码审查

> 审查日期：2026-08-06
> 范围：`magi/providers/` 全部 9 个文件

## 1. 整体评价

模块架构设计良好：抽象接口 (`LLMProvider`) 清晰、错误映射统一、Worker 通过 new_bus 隔离消费。两类 wire format（Anthropic 兼容 / OpenAI 独立）分层合理，添加新厂商的约定明确。

以下问题按严重度从高到低排列。

---

## 2. 严重 Bug

### 2.1 `_yield` 未定义 —— Anthropic streaming 完全不可用

**文件**: `magi/providers/anthropic.py:166`

`_emit()` 闭包内调用了 `_yield()`，但该符号在任何作用域都不存在：

```python
def _emit(kind: str, payload: dict[str, Any]) -> None:
    asyncio.run_coroutine_threadsafe(
        _yield(LLMStreamEvent(kind, payload)), loop,  # NameError!
    ).result()
```

**影响**：所有 Anthropic 兼容 provider（Claude + Minimax）的 `stream()` 调用会在 `_read()` 线程内触发 `NameError`，导致每次 streaming 请求都返回 `chat.provider_crashed`。

**根因**：`stream()` 方法的"线程→异步桥接"模式未完成。`_read()` 在后台线程消费 SDK stream，需要通过 `asyncio.Queue` 将增量事件传回主异步上下文的 `AsyncIterator`，但队列消费循环缺失。

**修复方案**：

```python
async def stream(self, ...) -> AsyncIterator[LLMStreamEvent]:
    ...
    event_queue: asyncio.Queue[LLMStreamEvent] = asyncio.Queue()

    async def _yield(event: LLMStreamEvent) -> None:
        await event_queue.put(event)

    def _emit(kind: str, payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(
            _yield(LLMStreamEvent(kind, payload)), loop,
        ).result()

    def _read() -> None:
        # ... SDK stream 消费，调用 _emit() ...
        # 最后 emit 一个 sentinel usage.updated

    await asyncio.to_thread(_read)

    # 从队列消费并 yield 给调用方
    while True:
        event = await event_queue.get()
        yield event
        if event.kind == "usage.updated":
            break
```

---

### 2.2 `anthropic.py` stream 错误映射不完整

**文件**: `magi/providers/anthropic.py:243-249`

`_read()` 的 `try/except` 只捕获了 4 类异常，漏掉了 3 种：

| 异常 | `chat()` 是否处理 | `stream()` 是否处理 |
|------|:---:|:---:|
| `AuthenticationError` | ✅ | ✅ |
| `RateLimitError` | ✅ | ✅ |
| `APITimeoutError` | ✅ | ✅ |
| `APIConnectionError` | ✅ | ✅ |
| `BadRequestError` (context length) | ✅ | ❌ |
| `PermissionDeniedError` | ✅ | ❌ |
| `APIStatusError` (5xx generic) | ✅ | ❌ |

漏掉的异常会作为 raw SDK exception 穿透到 Worker 层，被 `_invoke_safe` 的 `except Exception` 兜底捕获，终态为 `chat.provider_crashed` 而非语义化的错误码（`LLMContextLengthError` / `LLMAuthError` / `LLMNetworkError`）。

**修复**：提取公共的 `_wrap_anthropic_error(exc, label)` 函数，`chat()` 和 `stream()` 共用。

---

### 2.3 测试文件与当前 API 不兼容

**文件**: `tests/unit/test_openai_provider.py`、`tests/integration/test_providers_worker.py`

测试仍依赖已删除的类型和接口：

| 测试引用 | 当前代码状态 |
|----------|-------------|
| `ChatMessage` / `ChatResult` (来自 `magi.providers.factory`) | 已删除，wire format 改为 plain `list[dict]` / `dict` |
| `provider.stream(..., on_event=callback)` | 新接口为 `AsyncIterator[LLMStreamEvent]`，无 `on_event` 参数 |
| `store.enqueue_llm_job(...)` / `store.drain_control_jobs(...)` | 已迁移到 new_bus 的 Job Board + settings_book |
| `bus.magic.provider_configuration()` | 改为 `bus.settings_book.get(key=...)` |

**影响**：CI 全红，当前测试无法验证重构后的 provider 行为。

---

## 3. 设计问题

### 3.1 Logger 名称违反架构边界

架构测试 `test_provider_boundary.py` 强制 `magi.providers` 不 import `magi.agent`，但 logger 名称用了 `magi.agent.llm.*` 前缀，语义上暗示属于 agent 层：

| 文件 | 当前 logger | 应改为 |
|------|------------|--------|
| `factory.py:50` | `magi.agent.llm.factory` | `magi.providers.factory` |
| `anthropic.py:35` | `magi.agent.llm.anthropic` | `magi.providers.anthropic` |
| `openai.py:48` | `magi.agent.llm.openai` | `magi.providers.openai` |

Worker (`worker.py:59`) 已经正确使用了 `magi.providers.worker`，只需对齐其余 3 个。

---

### 3.2 重复的上下文长度检测逻辑

`anthropic.py` 和 `openai.py` 各自实现了 `_is_context_length_error()`，且检测逻辑不一致：

```python
# anthropic.py:47-53 — 直接字符串包含
return (
    "context length" in m
    or "prompt is too long" in m
    or "maximum context" in m
    or "context_length" in m
)

# openai.py:54-66 — 元组 + any() 匹配
_CONTEXT_LENGTH_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "prompt is too long",
    "reduce the length",     # ← anthropic.py 没有这一项
    "tokens must be reduced", # ← anthropic.py 没有这一项
)
```

两套逻辑的关键词集合不同，且检测方式不同（直接 `in` vs `any()`）。应提取到 `errors.py` 或新建 `magi/providers/_utils.py`。

---

### 3.3 Pydantic 对象 → dict 转换逻辑重复

三个地方做类似的 SDK 对象序列化：

| 函数 | 文件 | 行号 | fallback 链 |
|------|------|------|------------|
| `_dump()` | `anthropic.py` | 319-335 | `model_dump()` → `dict()` → `__dict__` |
| `_convert_usage()` | `openai.py` | 172-201 | `model_dump()` → `to_dict()` → `__dict__` |

注意 openai 使用 `to_dict()`（第 2 步），而 anthropic 使用 `dict()`。两者还有不同的结果后处理。可提取公共的 `_safe_dump(obj) -> dict | None` 到 `base.py`，允许调用方对结果做二次处理。

---

### 3.4 `minimax.py` 每次 `for_region()` 动态创建子类

**文件**: `magi/providers/minimax.py:96-99`

```python
class _RegionMinimax(MinimaxProvider):
    pass
_RegionMinimax._BASE_URL = _BASE_URLS[region]
return _RegionMinimax(api_key=api_key, model=model)
```

每次调用都创建新的 class 对象。两个 region 是固定的，可缓存：

```python
_region_classes: dict[str, type[MinimaxProvider]] = {}

@classmethod
def for_region(cls, region, api_key, model=None):
    if region not in _region_classes:
        _region_classes[region] = type(
            f"_Minimax_{region.replace('-', '_')}",
            (MinimaxProvider,),
            {"_BASE_URL": _BASE_URLS[region]},
        )
    return _region_classes[region](api_key=api_key, model=model)
```

或更彻底地，让 `MinimaxProvider.__init__` 接受显式 `base_url` 参数，避免子类绕弯。

---

### 3.5 `__init__.py` docstring 类型位置标注有误

**文件**: `magi/providers/__init__.py:40-41`

```text
:- :class:`LLMProvider` / :class:`LLMStreamEvent` →
:  :mod:`magi.providers.factory`
```

`LLMProvider` 和 `LLMStreamEvent` 实际定义在 `magi.providers.base`，不是 `factory`。应修正为 `:mod:`magi.providers.base``。

---

## 4. 代码质量优化

### 4.1 `anthropic.py` stream 的 `tool_buffers` slot 映射脆弱

**文件**: `magi/providers/anthropic.py:200-216`

`input_json_delta` 事件不携带 `tool_use.id`，只能通过 `index` 遍历 `tool_buffers.values()` 匹配内部标记 `_slot`。多个 slot 时靠遍历匹配不够可靠。建议改用 `dict[int, dict]` 以 slot index 为 key：

```python
# 当前：遍历 values 匹配 _slot
for slot in tool_buffers.values():
    if slot.get("_slot") == tid:
        ...

# 推荐：直接按 index 索引
tool_buffers_by_slot: dict[int, dict] = {}
# content_block_start:  tool_buffers_by_slot[index] = {...}
# content_block_delta:  tool_buffers_by_slot[tid]["input_json"] += partial
```

---

### 4.2 `_config_signature` 双重错误处理

**文件**: `magi/providers/worker.py:86-103`

`_config_signature()` 内部 `except Exception` 返回 `(None, None, None)`，这会触发 `_check_config_drift()` → `_rebuild_provider()`，后者又做一遍 `except LLMError`。中间的 drift 日志对于配置读取异常的场景会产生误导。简化为：`_config_signature` 直接 propagate，`_check_config_drift` 单独 catch。

---

### 4.3 stream fallback 到 `chat()` 缺乏语义说明

**文件**: `magi/providers/worker.py:469-475`

```python
if terminal is None:
    return await provider.chat(
        system=system, messages=messages,
        max_tokens=max_tokens, tools=tools,
    )
```

当 stream 没有产生 `usage.updated` 事件时（例如 SDK stream 返回空），fallback 到非 streaming `chat()`。这意味着：
- 同一请求被双重计费
- 没有超时保护
- 没有 `stream_key` 供调用方读取增量文本
- 注释未说明此行为的设计意图

建议至少加注释说明预期的触发场景（SDK bug？空响应？），或加 flag 控制是否允许 fallback。

---

### 4.4 文本拼接方式不一致

| 位置 | 函数 | 拼接方式 |
|------|------|----------|
| `anthropic.py:365` | `_response_to_dict` | `"\n".join()` |
| `anthropic.py:269` | `stream()` text_parts | `"".join()` |
| `anthropic.py:270` | `stream()` thinking_parts | `"\n".join()` |
| `openai.py:135` | `_convert_messages` | `"\n".join()` |
| `openai.py:411` | `stream()` text_parts | `"".join()` |

增量 delta 应该用 `"".join()`（delta 之间本不含换行），`_response_to_dict` 中多段 text block 用 `"\n".join()` 合理。openai 的 `_convert_messages:135` 用 `"\n".join()` 拼接多段 assistant text（来自 content_blocks），用 `"\n"` 也说得通。但 stream 的 thinking_parts 聚合用了 `"\n".join()`，与同方法的 text_parts `"".join()` 不一致。建议统一：所有 delta 级拼接用 `"".join()`。

---

## 5. 问题汇总与优先级

| # | 问题 | 严重度 | 影响 |
|---|------|:---:|------|
| 2.1 | `_yield` 未定义 | 🔴 P0 | Anthropic/Claude/Minimax streaming 全部崩溃 |
| 2.2 | stream 错误映射不完整 | 🔴 P0 | 部分上游错误绕过语义化错误码 |
| 2.3 | 测试不兼容 | 🔴 P0 | CI 全红，无法验证重构正确性 |
| 3.1 | Logger 名称不规范 | 🟡 P1 | 架构语义混乱 |
| 3.2 | 重复的上下文长度检测 | 🟡 P1 | 维护成本，关键词不同步 |
| 3.3 | Pydantic dump 重复 | 🟡 P1 | 维护成本 |
| 3.4 | minimax 每次动态子类 | 🟢 P2 | 轻微内存浪费 |
| 3.5 | docstring 类型位置错误 | 🟢 P2 | 误导开发者 |
| 4.1 | tool_buffers slot 映射脆弱 | 🟢 P2 | 并行 tool call 时误匹配 |
| 4.2 | `_config_signature` 双重异常处理 | 🟢 P3 | 冗余日志 |
| 4.3 | stream fallback 缺乏语义 | 🟢 P3 | 计费/超时风险 |
| 4.4 | 文本拼接方式不一致 | 🟢 P3 | 代码风格 |

## 6. 推荐修复顺序

1. **修复 `_yield` bug**（2.1）—— 修复 Anthropic streaming
2. **补齐 stream 错误映射**（2.2）—— 保持与 `chat()` 一致
3. **修复 Logger 名称**（3.1）—— 对齐架构边界
4. **提取公共 helper**（3.2 + 3.3）—— 消除 `_is_context_length_error` 和 Pydantic dump 重复
5. **更新 docstring**（3.5）
6. **更新测试**（2.3）—— 按照新的 dict wire format 和 AsyncIterator 接口重写
7. **minimax 缓存 / slot 优化**（3.4 + 4.1）—— 按需
