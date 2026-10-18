"""Langfuse 观测结合层（docs/LANGFUSE-PLAN.md §2.2）。

集中管理 langfuse 客户端生命周期，对所有 caller 提供 fail-closed 的安全入口：
- 默认 `langfuse_enabled=False`；不配 / 不可达时所有调用返回 None / no-op，零副作用。
- 打点走 `langfuse.Langfuse`（v2.14，内部后台线程 task_manager），创建 observation
  只入队、不阻塞事件循环；flush/shutdown 一律在 lifespan shutdown 做，绝不放热路径。

SDK 形态：v2.14 Fern 重写后 Top-level 只导出同步 `Langfuse`，用「stateful client」串
父-子：`cr.trace(...).span(...).generation(...)`，各自 create 后可用 `.update(...)` 补
output/元数据。这里把父子串接封装给 caller，caller 不必 import langfuse。
"""

import logging
from contextvars import ContextVar
from typing import Any

from ..config import settings

logger = logging.getLogger("langfuse")
logger.setLevel(logging.CRITICAL)  # host 不可达时 SDK 的 backoff ERROR 太吵，本地实验不需要

_lf: Any = None  # Langfuse | None
_current_trace: ContextVar[Any | None] = ContextVar("langfuse_current_trace", default=None)


def trace_enabled() -> bool:
    """读取 settings：默认 fail-closed。"""
    return bool(getattr(settings, "langfuse_enabled", False))


def langfuse() -> Any:
    """全局客户端 getter（None 安全），供 llm.py / dialogue.py 使用。"""
    return _lf


def get_current_trace() -> Any | None:
    """对话链路在 talk() 入口设入的根 trace；OllamaClient 借此把 generation 挂进上下文。"""
    return _current_trace.get()


async def setup() -> None:
    """在 main lifespan 启动时调用；enabled 才建客户端，异常则置 None（fail-closed）。"""
    global _lf
    _lf = None
    if not trace_enabled():
        return
    try:
        from langfuse import Langfuse

        _lf = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:
        _lf = None  # 不可用则彻底关掉，绝不让观测拖垮玩法


async def close() -> None:
    """shutdown 时 flush 缓冲再关客户端；异常吞掉，best-effort。"""
    global _lf, _current_trace
    f = _lf
    _lf = None
    _current_trace.set(None)
    if f is not None:
        try:
            f.flush()
            f.shutdown()
        except Exception:
            pass


def _try_start() -> Any | None:
    """当前 trace 有值且客户端在 -> 返回 trace，否则 None。所有入口先过这一关。"""
    trace = _current_trace.get()
    if trace is None or _lf is None:
        return None
    return trace


async def trace_turn(purpose: str, input_: Any, *, tags: list[str] | None = None,
                     metadata: dict | None = None) -> Any | None:
    """开一条「对话回合」根 trace，并设为当前上下文；返回 trace 对象（None 时 no-op）。"""
    if _lf is None:
        return None
    try:
        trace = _lf.trace(name=purpose, input=input_, tags=tags, metadata=metadata)
        _current_trace.set(trace)
        return trace
    except Exception:
        return None


async def span(trace: Any, name: str, *, input_: Any = None, output: Any = None,
               metadata: dict | None = None) -> None:
    """在指定 trace 下挂一条一次性 span（包某个已完成步骤）。trace 为 None 时 no-op。"""
    if trace is None or _lf is None:
        return
    try:
        sp = trace.span(name=name, input=input_, metadata=metadata)
        if output is not None:
            sp.update(output=output)
    except Exception:
        pass