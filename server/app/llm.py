import asyncio
import heapq
import json
import logging
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .config import settings
from .db import Database
from .llm_tools import ToolCall, ToolCallBuffer, ToolCallDelta, complete_tool_calls
from .agent import trace as trace_mod

logger = logging.getLogger(__name__)


# LLM 调用优先级（AGENTS #7：对话/送礼/切磋 > 搭话 > 关系摘要 > 反思 > 八卦）。
# dialogue_summary 前台中期总结发生在一次对话请求内，优先级 0；
# 36 轮长期总结在后台任务里复用同一 purpose，调用时显式传 priority=3。
PRIORITY = {
    "dialogue": 0, "dialogue_summary": 0, "embed": 0, "gift": 0, "spar": 0,
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
    tool_calls: list[ToolCall] = field(default_factory=list)


class OllamaClient:
    """Async client over the Ollama HTTP API (docs/DESIGN.md §7).

    M0 carried only the /api/tags health check; later stages add ordinary
    text, native tools, background JSON tasks, no-think, llm_log and embed
    (with L2 normalisation). All LLM round-trips share one global
    PriorityLimiter (AGENTS.md #7) so dialogue + background agent jobs never
    flood Ollama, and background work cannot starve foreground dialogue.
    Structured-output robustness (verify -> retry once -> template fallback)
    lives in the callers; this class validates transport/tool argument framing
    or raises.
    """

    def __init__(self, host: str, timeout: float, db: Database) -> None:
        self._client = httpx.AsyncClient(base_url=host, timeout=timeout)
        self._db = db
        self._limiter = PriorityLimiter(settings.llm_concurrency)
        # Route that last answered an embed call, reused to skip dead candidates.
        self._embed_route: tuple[httpx.AsyncClient, str] | None = None
        # Optional OpenAI-compatible backend: chat and embedding may use
        # different base URLs and separate API keys.
        self._api_client: httpx.AsyncClient | None = None
        self._api_embed_client: httpx.AsyncClient | None = None
        self._api_model = settings.chat_model
        if settings.llm_api_key and settings.llm_api_base_url:
            self._api_model = settings.llm_api_model or settings.chat_model
            self._api_client = httpx.AsyncClient(
                base_url=settings.llm_api_base_url,
                timeout=settings.llm_api_timeout,
                trust_env=settings.llm_api_trust_env,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
        embed_base_url = settings.llm_api_embed_base_url or settings.llm_api_base_url
        if settings.llm_api_embed_key and embed_base_url:
            self._api_embed_client = httpx.AsyncClient(
                base_url=embed_base_url,
                timeout=settings.llm_api_timeout,
                trust_env=settings.llm_api_trust_env,
                headers={"Authorization": f"Bearer {settings.llm_api_embed_key}"},
            )

    async def tags(self) -> list[str]:
        """Model names from an Ollama host, falling back to OpenAI ``/v1/models``.

        The configured host may be either kind of server, so the health check
        should not report "unreachable" just because ``/api/tags`` is missing.
        """
        res = await self._client.get("/api/tags")
        if res.status_code >= 400:
            res = await self._client.get("/models")
        res.raise_for_status()
        payload = res.json()
        models = payload.get("models")
        if isinstance(models, list) and models and "name" in models[0]:
            return [str(m.get("name", "")) for m in models]
        return [str(m.get("id", "")) for m in (payload.get("data") or [])]

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
        messages: list[dict[str, Any]],
        *,
        json_format: bool = False,
        tools: list[dict] | None = None,
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
                    text, in_tok, out_tok, calls = await self._call_api(messages, json_format, temperature, tools)
                else:
                    text, in_tok, out_tok, calls = await self._call_ollama(messages, json_format, temperature, tools)
            ok = True
            self._trace_generation(purpose, messages, self._trace_output(text, calls, tools), temperature, json_format,
                                   {**(metadata or {}), "priority": p},
                                   in_tok, out_tok, model=model)
            return ChatResult(text=text, in_tokens=in_tok, out_tokens=out_tok, tool_calls=calls)
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

    async def chat_stream(
        self,
        purpose: str,
        messages: list[dict[str, Any]],
        *,
        json_format: bool = False,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        metadata: dict | None = None,
        priority: int | None = None,
    ) -> AsyncIterator[str | ToolCallDelta]:
        """Streaming chat completion, yielding content deltas as they arrive.

        Same priority queue and accounting as ``chat``; the limiter slot is held
        for the whole stream so a background job cannot interleave with a
        half-finished dialogue. Deltas are raw model output — with
        ``json_format=True`` that means fragments of the JSON object, and the
        caller is responsible for decoding them. With native ``tools``, yields
        ``ToolCallDelta`` alongside ordinary text; callers buffer intents until
        the completion succeeds. Other JSON tasks keep their old string API.
        """
        model = self._api_model if self._api_client is not None else settings.chat_model
        p = priority if priority is not None else PRIORITY.get(purpose, DEFAULT_PRIORITY)
        start = time.time()
        parts: list[str] = []
        call_buffer = ToolCallBuffer()
        in_tok = out_tok = None
        ok = False
        error: Exception | None = None
        try:
            async with self._limiter.slot(p):
                if self._api_client is not None:
                    source = self._stream_api(messages, json_format, temperature, tools)
                else:
                    source = self._stream_ollama(messages, json_format, temperature, tools)
                async for delta, usage in source:
                    if usage is not None:
                        in_tok, out_tok = usage
                    if isinstance(delta, ToolCallDelta):
                        call_buffer.feed(delta)
                        yield delta
                    elif delta:
                        parts.append(delta)
                        yield delta
                calls = call_buffer.finish()
            ok = True
        except Exception as exc:
            error = exc
            raise
        finally:
            ms = int((time.time() - start) * 1000)
            if ok:
                text = "".join(parts)
                logger.info("llm %s ok model=%s priority=%d %dms stream in=%s out=%s",
                            purpose, model, p, ms, in_tok, out_tok)
                self._trace_generation(purpose, messages, self._trace_output(text, calls, tools), temperature, json_format,
                                       {**(metadata or {}), "priority": p, "stream": True},
                                       in_tok, out_tok, model=model)
            elif error is None:
                logger.info("llm %s stream closed early model=%s priority=%d %dms",
                            purpose, model, p, ms)
            else:
                logger.warning("llm %s stream FAILED model=%s priority=%d %dms error=%s: %s",
                               purpose, model, p, ms, type(error).__name__, error)
            try:
                await self._log(purpose, ok, in_tok, out_tok, ms, model=model)
            except BaseException:
                pass  # cancellation / bookkeeping must never mask the stream result

    async def _stream_ollama(self, messages, json_format, temperature, tools=None):
        """Ollama /api/chat NDJSON stream; the final line carries token counts."""
        body: dict[str, Any] = {
            "model": settings.chat_model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": temperature, "num_ctx": settings.llm_num_ctx},
        }
        self._add_output_options(body, json_format, tools, ollama=True)
        done = False
        next_tool_index = 0
        async with self._client.stream("POST", "/api/chat", json=body) as res:
            res.raise_for_status()
            async for line in res.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(f"ollama: {data['error']}")
                chunk = (data.get("message") or {}).get("content") or ""
                if chunk:
                    yield chunk, None
                for call in (data.get("message") or {}).get("tool_calls") or []:
                    fn = call["function"]
                    index = fn.get("index", call.get("index", next_tool_index))
                    next_tool_index = max(next_tool_index, index + 1)
                    args = fn.get("arguments", {})
                    yield ToolCallDelta(index, fn["name"],
                                        args if isinstance(args, str) else json.dumps(args),
                                        call.get("id", "")), None
                if data.get("done"):
                    if data.get("done_reason") == "length":
                        raise ValueError("incomplete Ollama completion")
                    done = True
                    yield "", (data.get("prompt_eval_count"), data.get("eval_count"))
        if not done:
            raise ValueError("Ollama stream ended before done")

    async def _stream_api(self, messages, json_format, temperature, tools=None):
        """OpenAI-compatible SSE stream; retries without stream_options on 400/422.

        ``stream_options.include_usage`` is an OpenAI extension that some
        compatible servers reject, so the first attempt drops it on refusal.
        ``raise_for_status`` runs before any delta is yielded, so the retry
        cannot duplicate text.
        """
        try:
            async for item in self._stream_api_once(messages, json_format, temperature, True, tools):
                yield item
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 422):
                raise
            async for item in self._stream_api_once(messages, json_format, temperature, False, tools):
                yield item

    async def _stream_api_once(self, messages, json_format, temperature, include_usage, tools=None):
        body: dict[str, Any] = {
            "model": self._api_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        self._add_output_options(body, json_format, tools)
        if include_usage:
            body["stream_options"] = {"include_usage": True}
        done = False
        async with self._api_client.stream("POST", "/chat/completions", json=body) as res:
            res.raise_for_status()
            async for line in res.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    done = True
                    break
                data = json.loads(payload)
                if data.get("error"):
                    raise RuntimeError(f"chat API: {data['error']}")
                usage = data.get("usage")
                if usage:
                    yield "", (usage.get("prompt_tokens"), usage.get("completion_tokens"))
                for choice in data.get("choices") or []:
                    if choice.get("index", 0) != 0:
                        continue
                    chunk = (choice.get("delta") or {}).get("content") or ""
                    if chunk:
                        yield chunk, None
                    for call in (choice.get("delta") or {}).get("tool_calls") or []:
                        fn = call.get("function") or {}
                        yield ToolCallDelta(call["index"], fn.get("name") or "",
                                            fn.get("arguments") or "", call.get("id") or ""), None
                    reason = choice.get("finish_reason")
                    if reason in ("length", "content_filter"):
                        raise ValueError(f"incomplete API completion: {reason}")
                    if reason in ("stop", "tool_calls"):
                        done = True
        if not done:
            raise ValueError("API stream ended before done")

    async def _call_ollama(self, messages, json_format, temperature, tools=None):
        body: dict[str, Any] = {
            "model": settings.chat_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_ctx": settings.llm_num_ctx},
        }
        self._add_output_options(body, json_format, tools, ollama=True)
        res = await self._client.post("/api/chat", json=body)
        res.raise_for_status()
        data = res.json()
        if data.get("error"):
            raise RuntimeError(f"ollama: {data['error']}")
        if data.get("done_reason") == "length":
            raise ValueError("incomplete Ollama completion")
        return (
            data.get("message", {}).get("content") or "",
            data.get("prompt_eval_count"),
            data.get("eval_count"),
            complete_tool_calls((data.get("message") or {}).get("tool_calls") or []),
        )

    async def _call_api(self, messages, json_format, temperature, tools=None):
        """OpenAI-compatible /chat/completions (base_url should end in /v1)."""
        body: dict[str, Any] = {
            "model": self._api_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        self._add_output_options(body, json_format, tools)
        res = await self._api_client.post("/chat/completions", json=body)
        res.raise_for_status()
        data = res.json()
        if data.get("error"):
            raise RuntimeError(f"chat API: {data['error']}")
        usage = data.get("usage", {}) or {}
        choice = data["choices"][0]
        if choice.get("finish_reason") in ("length", "content_filter"):
            raise ValueError("incomplete API completion")
        message = choice["message"]
        text = message.get("content") or ""
        calls = complete_tool_calls(message.get("tool_calls") or [])
        return text or "", usage.get("prompt_tokens"), usage.get("completion_tokens"), calls

    @staticmethod
    def _add_output_options(body, json_format, tools, *, ollama=False) -> None:
        if tools and json_format:
            raise ValueError("native tools and JSON output mode are mutually exclusive")
        if tools:
            body["tools"] = tools
        elif json_format:
            if ollama:
                body["format"] = "json"
            else:
                body["response_format"] = {"type": "json_object"}

    @staticmethod
    def _trace_output(text, calls, tools):
        if not tools:
            return text
        return {"content": text, "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls]}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts and L2-normalise each vector (DESIGN §8).

        Both wire formats are supported because the embedding backend may be
        Ollama (``/api/embed`` -> ``{"embeddings": [[...]]}``) or any
        OpenAI-compatible server (``/embeddings`` -> ``{"data": [{"embedding":
        [...]}]}``); the API route can use a separate base URL from chat; see
        ``_embed_routes``. Chroma is built with
        embedding_function=None and always receives explicit vectors
        (AGENTS.md ChromaDB 坑①); normalisation happens here so the caller
        never has to repeat it.
        """
        start = time.time()
        ok = False
        error: Exception | None = None
        try:
            async with self._limiter.slot(0):
                vectors = await self._embed_request(texts)
            out: list[list[float]] = []
            for v in vectors:
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
            await self._log("embed", ok, None, None, ms, model=self._embed_label())

    def _embed_label(self) -> str:
        return settings.llm_api_embed_model or settings.embed_model

    def _embed_routes(self, texts: list[str]) -> list[tuple[httpx.AsyncClient, str, dict[str, Any]]]:
        """Candidate (client, path, body) calls in preference order.

        The OpenAI-compatible embedding backend is only tried when its own key
        and an effective base URL are configured; the Ollama host is kept as
        the fallback so a missing local server or a mismatched model id cannot
        take RAG down permanently.
        """
        routes: list[tuple[httpx.AsyncClient, str, dict[str, Any]]] = []
        if self._api_embed_client is not None:
            routes.append((
                self._api_embed_client,
                "/embeddings",
                {"model": self._embed_label(), "input": texts},
            ))
        routes.append((self._client, "/api/embed", {"model": settings.embed_model, "input": texts}))
        routes.append((self._client, "/embeddings", {"model": settings.embed_model, "input": texts}))
        return routes

    async def _embed_request(self, texts: list[str]) -> list[list[float]]:
        """Try the cached route first, then every remaining candidate once."""
        routes = self._embed_routes(texts)
        ordered = routes
        if self._embed_route is not None:
            ordered = [r for r in routes if (r[0], r[1]) == self._embed_route]
            ordered += [r for r in routes if (r[0], r[1]) != self._embed_route]
        last_error: Exception | None = None
        for client, path, body in ordered:
            try:
                res = await client.post(path, json=body)
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if res.status_code >= 400:
                last_error = httpx.HTTPStatusError(
                    f"{path} -> {res.status_code}: {res.text[:120]}",
                    request=res.request, response=res,
                )
                continue
            try:
                vectors = self._parse_embeddings(res.json(), len(texts))
            except Exception as exc:  # unexpected payload shape -> next route
                last_error = exc
                continue
            self._embed_route = (client, path)
            return vectors
        raise last_error or RuntimeError("no embedding route available")

    @staticmethod
    def _parse_embeddings(payload: Any, count: int) -> list[list[float]]:
        """Accept both OpenAI and Ollama embedding payloads.

        OpenAI: ``{"data": [{"embedding": [...]}, ...]}``
        Ollama: ``{"embeddings": [[...], ...]}``
        Legacy Ollama single: ``{"embedding": [...]}``
        """
        if not isinstance(payload, dict):
            raise ValueError("embedding payload is not an object")
        vectors: list[list[float]] = []
        rows = payload.get("data")
        if isinstance(rows, list):
            entries = [row for row in rows if isinstance(row, dict) and "embedding" in row]
            entries.sort(key=lambda row: row.get("index", 0))
            vectors = [list(row["embedding"]) for row in entries]
        if not vectors and isinstance(payload.get("embeddings"), list):
            vectors = [list(v) for v in payload["embeddings"]]
        if not vectors and isinstance(payload.get("embedding"), list):
            vectors = [list(payload["embedding"])]
        if len(vectors) != count:
            raise ValueError(f"embedding count {len(vectors)} != {count} for payload keys {sorted(payload)}")
        return vectors

    async def close(self) -> None:
        await self._client.aclose()
        if self._api_client is not None:
            await self._api_client.aclose()
        if self._api_embed_client is not None:
            await self._api_embed_client.aclose()

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
