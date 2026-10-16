import httpx


class OllamaClient:
    """Thin async client over the Ollama HTTP API (docs/DESIGN.md §7).

    M0 only needs the connectivity/model check for /api/health.
    Chat/embed calls (with the global semaphore and llm_log) join in M3.
    """

    def __init__(self, host: str, timeout: float) -> None:
        self._client = httpx.AsyncClient(base_url=host, timeout=timeout)

    async def tags(self) -> list[str]:
        res = await self._client.get("/api/tags")
        res.raise_for_status()
        return [m.get("name", "") for m in res.json().get("models", [])]

    async def close(self) -> None:
        await self._client.aclose()
