import asyncio
import heapq
import logging
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from .config import settings
from .db import Database
from .agent import trace as trace_mod

logger = logging.getLogger(__name__)


# LLM 调用优先级（AGENTS #7：对话 > 搭话 > 关系摘要 > 反思 > 八卦）。
# dialogue_summary 发生在一次前台对话请求内，必须与对话同优先级。
PRIORITY = {
    "dialogue": 0, "dialogue_summary": 0, "embed": 0,
    "greet": 1, "relationship": 2, "reflection": 3, "heart": 3, "gossip": 4,
}
DEFAULT_PRIORITY = 3


class PriorityLimiter:
    """并发上限内的优先级队列；数值越小越先拿到槽位。"""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._seq = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []

    @asynccontextmanager
    async def slot(self, priority: int = DEFAULT_PRIORITY) -> AsyncIterator[None]:
        await self._acquire(priority)
        try:
            yield
        finally:
            self._release()

    async def _acquire(self, priority: int) -> None:
        if self._active < self._limit and not self._waiters:
            self._active += 1
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, self._seq, fut))
        self._seq += 1
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self._release()  # 槽位已分配但调用方在返回前被取消
            raise

    def _release(self) -> None:
        self._active -= 1
        while self._active < self._limit and self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if fut.done():
                continue
            self._active += 1
            fut.set_result(True)


@dataclass
class ChatResult:
    """Structured result of one /api/chat call (docs/DESIGN.md §7)."""

    text: str
    in_tokens: int | None
    out_tokens: int | None


class OllamaClient:
    """Async client over the Ollama HTTP API (docs/DESIGN.md §7).

    M0 carried only the /api/tags health check; M3 adds chat (with JSON
    coercion, no-think, llm_log) and embed (with L2 normalisation). All
    LLM round-trips share one global PriorityLimiter (AGENTS.md #7) so
    dialogue + background agent jobs never flood Ollama, and background
    work cannot starve foreground dialogue. Structured-output robustness
    (verify -> retry once -> template fallback) lives in the callers;
    this class only guarantees a parsed result or raises.
    """

    def __init__(self, host: str, timeout: float, db: Database) -> None:
        self._client = httpx.AsyncClient(base_url=host, timeout=timeout)
        self._db = db
        self._limiter = PriorityLimiter(settings.llm_concurrency)
        # Optional OpenAI-compatible chat backend: configured with key+base_url it
        # takes precedence over local Ollama for chat only (embeds stay local).
        self._api_client: httpx.AsyncClient | None = None
        self._api_model = settings.chat_model
        if settings.llm_api_key and settings.llm_api_base_url:
            self._api_model = settings.llm_api_model or settings.chat_model
            self._api_client = httpx.AsyncClient(
                base_url=settings.llm_api_base_url,
                timeout=settings.llm_api_timeout,
                trust_env=settings.llm_api_trust_env,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )

    async def tags(self) -> list[str]:
        res = await self._client.get("/api/tags")
        res.raise_for_status()
        return [m.get("name", "") for m in res.json().get("models", [])]

    async def _log(self, purpose: str, ok: bool, in_tok: int | None, out_tok: int | None, ms: int, model: str | None = None) -> None:
        """Record one call in llm_log for experiment analysis (DESIGN §7.3)."""
        try:
            await self._db.execute(
                "INSERT INTO llm_log(purpose, model, in_tokens, out_tokens, ms, ok, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (purpose, model or settings.chat_model, in_tok, out_tok, ms, 1 if ok else 0, time.time()),
            )
        except Exception:
            pass  # the log is best-effort; never let bookkeeping break a call

    async def chat(
        self,
        purpose: str,
        messages: list[dict[str, str]],
        *,
        json_format: bool = False,
        temperature: float = 0.7,
        metadata: dict | None = None,
        priority: int | None = None,
    ) -> ChatResult:
        """One chat completion.

        request body carries "think": false to disable qwen3 reasoning.
        On older Ollamas that reject the field we degrade to appending
        "/no_think" — current tags (2507) accept the flag, so the primary
        route is plain "think": false (AGENTS.md 已知坑). Raises on failure;
        callers own the fallback chain.
        """
        model = self._api_model if self._api_client is not None else settings.chat_model
        p = priority if priority is not None else PRIORITY.get(purpose, DEFAULT_PRIORITY)
        start = time.time()
        in_tok = out_tok = None
        ok = False
        error: Exception | None = None
        try:
            async with self._limiter.slot(p):
                if self._api_client is not None:
                    text, in_tok, out_tok = await self._call_api(messages, json_format, temperature)
                else:
                    text, in_tok, out_tok = await self._call_ollama(messages, json_format, temperature)
            ok = True
            self._trace_generation(purpose, messages, text, temperature, json_format,
                                   {**(metadata or {}), "priority": p},
                                   in_tok, out_tok, model=model)
            return ChatResult(text=text, in_tokens=in_tok, out_tokens=out_tok)
        except Exception as exc:
            error = exc
            raise
        finally:
            ms = int((time.time() - start) * 1000)
            if error is None:
                logger.info("llm %s ok model=%s priority=%d %dms in=%s out=%s",
                            purpose, model, p, ms, in_tok, out_tok)
            else:
                logger.warning("llm %s FAILED model=%s priority=%d %dms error=%s: %s",
                               purpose, model, p, ms, type(error).__name__, error)
            await self._log(purpose, ok, in_tok, out_tok, ms, model=model)

    async def _call_ollama(self, messages, json_format, temperature):
        body: dict[str, Any] = {
            "model": settings.chat_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature},
        }
        if json_format:
            body["format"] = "json"
        res = await self._client.post("/api/chat", json=body)
        res.raise_for_status()
        data = res.json()
        return (
            data.get("message", {}).get("content", ""),
            data.get("prompt_eval_count"),
            data.get("eval_count"),
        )

    async def _call_api(self, messages, json_format, temperature):
        """OpenAI-compatible /chat/completions (base_url should end in /v1)."""
        body: dict[str, Any] = {
            "model": self._api_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if json_format:
            body["response_format"] = {"type": "json_object"}
        res = await self._api_client.post("/chat/completions", json=body)
        res.raise_for_status()
        data = res.json()
        usage = data.get("usage", {}) or {}
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = ""
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts and L2-normalise each vector (DESIGN §8).

        Chroma is built with embedding_function=None and always receives
        explicit vectors (AGENTS.md ChromaDB 坑①); normalisation happens here
        so the caller never has to repeat it.
        """
        start = time.time()
        ok = False
        error: Exception | None = None
        try:
            async with self._limiter.slot(0):
                res = await self._client.post(
                    "/api/embed", json={"model": settings.embed_model, "input": texts}
                )
                res.raise_for_status()
                data = res.json()
            out: list[list[float]] = []
            for v in data.get("embeddings", []):
                norm = math.sqrt(sum(x * x for x in v))
                out.append([x / norm for x in v] if norm else v)
            ok = True
            self._trace_embed(texts, out)
            return out
        except Exception as exc:
            error = exc
            raise
        finally:
            ms = int((time.time() - start) * 1000)
            if error is None:
                logger.debug("embed ok n=%d %dms", len(texts), ms)
            else:
                logger.warning("embed FAILED n=%d %dms error=%s: %s",
                               len(texts), ms, type(error).__name__, error)
            await self._log("embed", ok, None, None, ms, model=settings.embed_model)

    async def close(self) -> None:
        await self._client.aclose()
        if self._api_client is not None:
            await self._api_client.aclose()

    # -- langfuse 打点（best-effort，绝不影响返回值；见 LANGFUSE-PLAN §2.3） --

    def _trace_generation(self, purpose, messages, text, temperature, json_format, metadata,
                          in_tok, out_tok, *, model=None) -> None:
        tr = trace_mod.get_current_trace()
        if tr is None:
            return
        params = {"temperature": temperature}
        if self._api_client is None:  # Ollama disables thinking; API models differ
            params["think"] = False
        try:
            tr.generation(
                name=purpose,
                model=model or settings.chat_model,
                model_parameters=params,
                input=messages,
                output=text,
                metadata={**(metadata or {}), "json_format": json_format},
                usage={"input": in_tok, "output": out_tok},
            )
        except Exception:
            pass

    def _trace_embed(self, texts: list[str], vectors: list[list[float]]) -> None:
        tr = trace_mod.get_current_trace()
        if tr is None:
            return
        try:
            dim = len(vectors[0]) if vectors else 0
            tr.span(name="embed", input=texts,
                    output=f"top 记忆向量 {len(texts)}x{dim} L2-norm")
        except Exception:
            pass