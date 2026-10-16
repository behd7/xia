from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .db import Database
from .game import battle as battle_mod
from .game import data, mobs as mobs_mod
from .game import player as player_mod
from .game import shop as shop_mod
from .game.clock import GameClock
from .llm import OllamaClient

db = Database(settings.db_path_abs)
ollama = OllamaClient(settings.ollama_host, settings.llm_timeout)
clock = GameClock(db, settings.time_scale, settings.game_start_sec)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await data.seed_and_load(db)
    await clock.load()
    yield
    await ollama.close()
    await db.close()


app = FastAPI(title="Xia server", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NewGameReq(BaseModel):
    name: str
    confirm: bool = False


class PosBody(BaseModel):
    x: float
    y: float


class BattleStartReq(BaseModel):
    target_type: str = "mob"
    target_id: str
    pos: PosBody | None = None


class BattleActionReq(BaseModel):
    action: str
    id: int | None = None


class BuyReq(BaseModel):
    item_id: int
    qty: int = 1
    pos: PosBody | None = None


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
    p = await player_mod.get(db)
    return {
        "player": await player_mod.api_dict(db, p) if p else None,
        "clock": clock.snapshot(),
        "mobs": await mobs_mod.states(db),
        "battle": await battle_mod.active_snapshot(db),
    }


@app.post("/api/game/new")
async def new_game(req: NewGameReq) -> dict[str, Any]:
    existing = await player_mod.get(db)
    if existing and not req.confirm:
        raise HTTPException(409, f"已有存档（{existing['name']}），重开需确认覆盖")
    name = req.name.strip()[:12]
    if not name:
        raise HTTPException(400, "请输入名号")
    p = await player_mod.create(db, name)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/battle/start")
async def battle_start(req: BattleStartReq) -> dict[str, Any]:
    p = await player_mod.get(db)
    if p is None:
        raise HTTPException(409, "无存档，请先开新档")
    if req.target_type != "mob":
        raise HTTPException(400, "M1 仅支持 mob 战斗")
    pos = {"x": req.pos.x, "y": req.pos.y} if req.pos else None
    try:
        snap = await battle_mod.start(db, p, req.target_id, pos)
    except battle_mod.BattleError as e:
        raise HTTPException(409, str(e))
    return {"battle": snap, "player": await player_mod.api_dict(db, p)}


@app.post("/api/battle/action")
async def battle_action(req: BattleActionReq) -> dict[str, Any]:
    try:
        return await battle_mod.apply_action(db, req.action, req.id)
    except battle_mod.BattleError as e:
        raise HTTPException(400, str(e))


@app.get("/api/battle/state")
async def battle_state() -> dict[str, Any]:
    return {"battle": await battle_mod.active_snapshot(db)}


@app.get("/api/shop/{shop_id}")
async def shop_info(shop_id: str) -> dict[str, Any]:
    info = shop_mod.info(shop_id)
    if info is None:
        raise HTTPException(404, "查无此店")
    return info


@app.post("/api/shop/{shop_id}/buy")
async def shop_buy(shop_id: str, req: BuyReq) -> dict[str, Any]:
    p = await player_mod.get(db)
    if p is None:
        raise HTTPException(409, "无存档，请先开新档")
    if req.qty < 1:
        raise HTTPException(400, "购买数量须为正")
    pos = {"x": req.pos.x, "y": req.pos.y} if req.pos else None
    try:
        updated = await shop_mod.buy(db, p, shop_id, req.item_id, req.qty, pos)
    except shop_mod.ShopError as e:
        raise HTTPException(400, str(e))
    return {"player": updated}
