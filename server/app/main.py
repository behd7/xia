from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .db import Database
from .game import battle as battle_mod
from .game import data, mobs as mobs_mod
from .game import inventory as inventory_mod
from .game import player as player_mod
from .game import shop as shop_mod
from .game import skills as skills_mod
from .game.clock import GameClock
from .llm import OllamaClient
from .agent import dialogue as dialogue_mod
from .agent import memory as memory_mod
from .agent import npc as npc_mod
from .agent import reflect as reflect_mod
from .agent import trace as trace_mod

db = Database(settings.db_path_abs)
ollama = OllamaClient(settings.ollama_host, settings.llm_timeout, db)
clock = GameClock(db, settings.time_scale, settings.game_start_sec)
mem_store: memory_mod.MemoryStore | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global mem_store
    await db.connect()
    if settings.reset_on_start:
        await db.reset()
        print("[startup] reset_on_start=true：SQLite 已清空，本次全新开局")
    await data.seed_and_load(db)
    await skills_mod.seed_and_load(db)
    await npc_mod.seed_and_load(db)
    await clock.load()
    await trace_mod.setup()
    mem_store = memory_mod.MemoryStore(settings.chroma_dir_abs, ollama, clock)
    await mem_store.reconcile(db)  # Chroma 派生索引与 SQLite SSOT 对齐（损坏/缺失时自愈）
    battle_mod.mem_store = mem_store  # 切磋写入 NPC 记忆的注入点
    shop_mod.mem_store = mem_store  # 大额消费写入店主记忆的注入点
    battle_mod.reflect_bump = reflect_mod.bump  # 切磋累加互动判断值
    shop_mod.reflect_bump = reflect_mod.bump  # 购买累加互动判断值
    yield
    await trace_mod.close()
    if mem_store is not None:
        mem_store.close()
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


class AllocateReq(BaseModel):
    stats: dict[str, int]


class EquipReq(BaseModel):
    item_id: int


class UnequipReq(BaseModel):
    slot: str


class SkillEquipReq(BaseModel):
    skill_id: int
    equip: bool


class SkillUpgradeReq(BaseModel):
    skill_id: int


class SectJoinReq(BaseModel):
    sect_id: str


class SellReq(BaseModel):
    item_id: int
    qty: int = 1


class TalkReq(BaseModel):
    message: str


class GiftReq(BaseModel):
    item_id: int


@app.post("/api/npc/{npc_id}/talk")
async def npc_talk(npc_id: int, req: TalkReq) -> dict[str, Any]:
    await _player_or_409()
    try:
        return await dialogue_mod.talk(db, mem_store, npc_id, req.message)
    except LookupError:
        raise HTTPException(404, "查无此人")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/npc/{npc_id}/gift")
async def npc_gift(npc_id: int, req: GiftReq) -> dict[str, Any]:
    await _player_or_409()
    try:
        return await npc_mod.gift(db, mem_store, npc_id, req.item_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/npc/{npc_id}/apprentice")
async def npc_apprentice(npc_id: int) -> dict[str, Any]:
    await _player_or_409()
    try:
        return await npc_mod.apprentice(db, mem_store, npc_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/npc/{npc_id}/challenge")
async def npc_challenge(npc_id: int, pos: PosBody | None = None) -> dict[str, Any]:
    p = await _player_or_409()
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise HTTPException(404, "查无此人")
    if n.get("role") == "master" and await npc_mod.affinity(db, npc_id) < npc_mod.CHALLENGE_AFFINITY:
        raise HTTPException(409, "掌门对你尚存戒心，暂不应战。")
    pos_d = {"x": pos.x, "y": pos.y} if pos else None
    try:
        snap = await battle_mod.start_spar(db, p, npc_id, pos_d)
    except battle_mod.BattleError as e:
        raise HTTPException(409, str(e))
    return {"battle": snap, "player": await player_mod.api_dict(db, p)}


@app.get("/api/debug/npc/{npc_id}")
async def npc_debug(npc_id: int) -> dict[str, Any]:
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise HTTPException(404, "查无此人")
    aff = await npc_mod.affinity(db, npc_id)
    ns = await db.fetch_one(
        "SELECT last_reflection_at, mem_since_reflection, interacts_since_summary,"
        " last_summary_conv_id FROM npc_state WHERE npc_id = ?",
        (npc_id,),
    )
    memories = await db.fetch_all(
        "SELECT id, ts_game, kind, importance, poi, content FROM memory"
        " WHERE npc_id = ? ORDER BY ts_game DESC LIMIT 50",
        (npc_id,),
    )
    rel = await db.fetch_all("SELECT * FROM relationship WHERE npc_id = ?", (npc_id,))
    return {
        "npc": {"id": npc_id, "name": n["name"], "role": n.get("role"), "sect": n.get("sect")},
        "affinity": aff,
        "reflection": ns,
        "memories": memories,
        "relationship": rel,
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    try:
        await db.fetch_one("SELECT 1 AS ok")
        db_ok = True
    except Exception:
        db_ok = False

    # 双后端：配置了 OpenAI 兼容 API 时对话走 API，嵌入仍走本地 Ollama。
    use_api = settings.llm_api_key and settings.llm_api_base_url
    try:
        names = await ollama.tags()
        ollama_info: dict[str, Any] = {
            "reachable": True,
            "chat_backend": "openai-api" if use_api else "ollama",
            "api_configured": bool(use_api),
            "api_model": (settings.llm_api_model or settings.chat_model) if use_api else None,
            "chat_model": settings.chat_model,
            "chat_model_ok": (True if use_api else settings.chat_model in names),
            "embed_model": settings.embed_model,
            "embed_model_ok": settings.embed_model in names,
        }
    except Exception:
        ollama_info = {
            "reachable": False,
            "chat_backend": "openai-api" if use_api else "ollama",
            "api_configured": bool(use_api),
            "api_model": (settings.llm_api_model or settings.chat_model) if use_api else None,
            "chat_model": settings.chat_model,
            "chat_model_ok": bool(use_api),
            "embed_model": settings.embed_model,
            "embed_model_ok": False,
        }

    npc_ok = False
    try:
        row = await db.fetch_one("SELECT COUNT(*) AS n FROM npc")
        npc_ok = bool(row and row["n"] == len(npc_mod.NPC_ROSTER))
    except Exception:
        npc_ok = False
    return {"status": "ok", "db": db_ok, "ollama": ollama_info, "npcs_seeded": npc_ok}


@app.get("/api/game/state")
async def game_state() -> dict[str, Any]:
    p = await player_mod.get(db)
    return {
        "player": await player_mod.api_dict(db, p) if p else None,
        "clock": clock.snapshot(),
        "mobs": await mobs_mod.states(db),
        "battle": await battle_mod.active_snapshot(db),
        "npcs": await npc_mod.list_for_client(db),
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
    info = await shop_mod.info(db, shop_id)
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


async def _player_or_409() -> dict:
    p = await player_mod.get(db)
    if p is None:
        raise HTTPException(409, "无存档，请先开新档")
    return p


async def _clamp_after_derived_change(p: dict) -> None:
    d = await player_mod.stats(db, p)
    p["hp"] = min(p["hp"], d["hp_max"])
    p["mp"] = min(p["mp"], d["mp_max"])


@app.post("/api/game/allocate")
async def game_allocate(req: AllocateReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await player_mod.allocate(db, p, req.stats)
    except player_mod.PlayerError as e:
        raise HTTPException(400, str(e))
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/game/equip")
async def game_equip(req: EquipReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await inventory_mod.equip(db, p["id"], req.item_id)
    except inventory_mod.InvError as e:
        raise HTTPException(400, str(e))
    await _clamp_after_derived_change(p)
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/game/unequip")
async def game_unequip(req: UnequipReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await inventory_mod.unequip(db, p["id"], req.slot)
    except inventory_mod.InvError as e:
        raise HTTPException(400, str(e))
    await _clamp_after_derived_change(p)
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/game/skill/equip")
async def skill_equip(req: SkillEquipReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await skills_mod.set_equip(db, p, req.skill_id, req.equip)
    except skills_mod.SkillError as e:
        raise HTTPException(400, str(e))
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/game/skill/upgrade")
async def skill_upgrade(req: SkillUpgradeReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await skills_mod.upgrade(db, p, req.skill_id)
    except skills_mod.SkillError as e:
        raise HTTPException(400, str(e))
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/sect/join")
async def sect_join(req: SectJoinReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await skills_mod.join_sect(db, p, req.sect_id)
    except skills_mod.SkillError as e:
        raise HTTPException(400, str(e))
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/shop/{shop_id}/sell")
async def shop_sell(shop_id: str, req: SellReq) -> dict[str, Any]:
    p = await _player_or_409()
    if shop_mod.info(shop_id) is None:
        raise HTTPException(404, "查无此店")
    try:
        gain = await inventory_mod.sell(db, p["id"], req.item_id, req.qty)
    except inventory_mod.InvError as e:
        raise HTTPException(400, str(e))
    p["gold"] += gain
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p), "gain": gain}
