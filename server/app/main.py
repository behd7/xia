from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Database
from .game.clock import GameClock
from .llm import OllamaClient

db = Database(settings.db_path_abs)
ollama = OllamaClient(settings.ollama_host, settings.llm_timeout)
clock = GameClock(db, settings.time_scale, settings.game_start_sec)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await clock.load()
    yield
    await ollama.close()
    await db.close()


app = FastAPI(title="Xia server", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    try:
        await db.fetch_one("SELECT 1 AS ok")
        db_ok = True
    except Exception:
        db_ok = False

    try:
        names = await ollama.tags()
        ollama_info: dict[str, Any] = {
            "reachable": True,
            "chat_model": settings.chat_model,
            "chat_model_ok": settings.chat_model in names,
            "embed_model": settings.embed_model,
            "embed_model_ok": settings.embed_model in names,
        }
    except Exception:
        ollama_info = {
            "reachable": False,
            "chat_model": settings.chat_model,
            "chat_model_ok": False,
            "embed_model": settings.embed_model,
            "embed_model_ok": False,
        }

    return {"status": "ok", "db": db_ok, "ollama": ollama_info}


@app.get("/api/game/state")
async def game_state() -> dict[str, Any]:
    # Player state joins this payload in M1 (docs/DESIGN.md §9).
    return {"player": None, "clock": clock.snapshot()}
