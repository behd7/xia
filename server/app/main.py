import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import settings
from .logging_setup import log_proxy_env, setup_logging
from .db import Database
from .game import arts as arts_mod
from .game import battle as battle_mod
from .game import data, mobs as mobs_mod
from .game import inventory as inventory_mod
from .game import plan as plan_mod
from .game import player as player_mod
from .game import schedule as schedule_mod
from .game import shop as shop_mod
from .game import skills as skills_mod
from .game import world as world_mod
from .game import world_state as world_state_mod
from .game.clock import GameClock
from .llm import OllamaClient
from .agent import agenda as agenda_mod
from .agent import dialogue as dialogue_mod
from .agent import greet as greet_mod
from .agent import memory as memory_mod
from .agent import mood as mood_mod
from .agent import npc as npc_mod
from .agent import scheduler as scheduler_mod
from .agent import trace as trace_mod

LOG_PATH = setup_logging()
logger = logging.getLogger(__name__)

db = Database(settings.db_path_abs)
ollama = OllamaClient(settings.ollama_host, settings.llm_timeout, db)
clock = GameClock(db, settings.time_scale, settings.game_start_sec)
mem_store: memory_mod.MemoryStore | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global mem_store
    use_api = bool(settings.llm_api_key and settings.llm_api_base_url)
    logger.info("startup: log=%s db=%s chroma=%s reset_on_start=%s",
                LOG_PATH, settings.db_path_abs, settings.chroma_dir_abs,
                settings.reset_on_start)
    logger.info("startup: chat_backend=%s model=%s api_base=%s api_trust_env=%s "
                "api_timeout=%.1fs ollama=%s chat_model=%s llm_timeout=%.1fs "
                "llm_num_ctx=%d",
                "openai-api" if use_api else "ollama",
                (settings.llm_api_model or settings.chat_model) if use_api else settings.chat_model,
                settings.llm_api_base_url or "-", settings.llm_api_trust_env,
                settings.llm_api_timeout, settings.ollama_host, settings.chat_model,
                settings.llm_timeout, settings.llm_num_ctx)
    if bool(settings.llm_api_key) != bool(settings.llm_api_base_url):
        logger.warning("startup: LLM_API_KEY 与 LLM_API_BASE_URL 只配置了一个，将使用本地 Ollama")
    log_proxy_env(logger)

    await db.connect()
    if settings.reset_on_start:
        await db.reset()
        logger.warning("startup: reset_on_start=true，SQLite 已清空，本次全新开局")
    await data.seed_and_load(db)
    await arts_mod.seed_and_load(db)
    await skills_mod.seed_and_load(db)
    await arts_mod.migrate_legacy(db)
    await npc_mod.seed_and_load(db)
    await clock.load()
    # 补算上次进程在“最后一名山贼胜利结算”后、写世界阶段前退出的情况；幂等。
    await world_state_mod.check_and_advance(db, clock.now())
    snap = clock.snapshot()
    logger.info("startup: npcs=%d game_day=%d game_sec=%.1f world_stage=%d",
                len(npc_mod.ids()), snap["day"], snap["game_sec"],
                await world_state_mod.stage(db))
    await trace_mod.setup()
    mem_store = memory_mod.MemoryStore(settings.chroma_dir_abs, ollama, clock)
    reconciled = await mem_store.reconcile(db)
    mem_store.start()  # 长期总结单 worker 队列
    logger.info("startup: chroma reconcile %s",
                "rebuilt from SQLite" if reconciled else "already in sync")
    battle_mod.mem_store = mem_store  # 切磋写入 NPC 记忆的注入点
    shop_mod.mem_store = mem_store  # 大额消费写入店主记忆的注入点
    scheduler_tasks = await scheduler_mod.start(db, mem_store)
    logger.info("startup: scheduler_enabled=%s plan_enabled=%s tasks=%d",
                settings.scheduler_enabled, settings.plan_enabled, len(scheduler_tasks))
    yield
    logger.info("shutdown: stopping scheduler")
    await scheduler_mod.stop(scheduler_tasks)  # 必须先于 close mem_store/llm/db
    if mem_store is not None:
        await mem_store.shutdown()  # 丢弃排队任务，当前任务收尾后停 worker
    await trace_mod.close()
    if mem_store is not None:
        mem_store.close()
    await ollama.close()
    await db.close()
    logger.info("shutdown: complete")


app = FastAPI(title="Xia server", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request(request, call_next):
    started = time.perf_counter()
    client = request.client.host if request.client else "-"
    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - started) * 1000
        logger.exception("http %s %s from=%s failed after %.1fms",
                         request.method, request.url.path, client, elapsed)
        raise
    elapsed = (time.perf_counter() - started) * 1000
    level = logging.INFO if response.status_code < 400 else logging.WARNING
    logger.log(level, "http %s %s -> %d %.1fms from=%s",
               request.method, request.url.path, response.status_code, elapsed, client)
    response.headers["X-Process-Time-Ms"] = f"{elapsed:.1f}"
    return response


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


class ArtLearnReq(BaseModel):
    art_id: int
    npc_id: int | None = None
    confirm_reset: bool = False
    confirm_clamp: bool = False


class SellReq(BaseModel):
    item_id: int
    qty: int = 1


class TalkReq(BaseModel):
    message: str


class GiftReq(BaseModel):
    item_id: int


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/npc/{npc_id}/talk")
async def npc_talk(npc_id: int, req: TalkReq):
    """SSE dialogue: start -> token* -> done, with reset on retry/fallback.

    404/400 are checked before the stream starts so ordinary callers still get
    a normal JSON error; once streaming, failures arrive as an ``error`` event.
    """
    await _player_or_409()
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(400, "空话一句，开不了口。")
    if await npc_mod.by_id(db, npc_id) is None:
        raise HTTPException(404, "查无此人")

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event, data in dialogue_mod.talk_stream(db, mem_store, npc_id, message):
                yield _sse(event, data)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("talk stream failed npc=%d", npc_id)
            yield _sse("error", {"detail": "话到嘴边却断了，稍后再叙。"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/npc/{npc_id}/greet")
async def npc_greet(npc_id: int) -> dict[str, Any]:
    await _player_or_409()
    try:
        return await greet_mod.greet(db, mem_store, npc_id)
    except LookupError:
        raise HTTPException(404, "查无此人")
    except ValueError as e:
        raise HTTPException(409, str(e))


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


@app.get("/api/npcs/state")
async def npcs_state() -> dict[str, Any]:
    now_game = clock.now()
    now_real = time.time()
    deviations = await plan_mod.deviations_for_day(db, now_game)
    plans = await plan_mod.plans_for_day(db, now_game)
    rows = await db.fetch_all(
        "SELECT n.id, ns.affinity, ns.paused_until, ns.last_greet_real"
        " FROM npc n JOIN npc_state ns ON ns.npc_id = n.id ORDER BY n.id"
    )
    out = []
    for r in rows:
        n = await npc_mod.by_id(db, r["id"])
        routes = schedule_mod.ROUTES.get(str((n or {}).get("slug", "")))
        if n is None or routes is None:
            continue
        home_work, work_gather = routes
        paused_until = r["paused_until"]
        last_greet = r["last_greet_real"]
        greet_ready = (
            r["affinity"] >= settings.greet_threshold
            and (last_greet is None or now_real - last_greet >= settings.greet_cooldown_real)
            and (paused_until is None or paused_until <= now_real)
        )
        dev = deviations.get(r["id"])
        plan = plans.get(r["id"], {})
        pos = schedule_mod.position_for(n, now_game, dev)
        out.append({
            "id": r["id"], "slug": n["slug"], "name": n["name"],
            "home": n.get("home", ""), "work": n.get("work", ""),
            "home_anchor": list(home_work[0]), "work_anchor": list(home_work[-1]),
            "home_to_work": [list(p) for p in home_work],
            "work_to_gather": [list(p) for p in work_gather],
            "affinity": r["affinity"], "paused_until": paused_until,
            "greet_ready": greet_ready,
            "pos": [round(pos[0], 2), round(pos[1], 2)],
            "poi": schedule_mod.poi_label_for(n, now_game, dev),
            "heart": plan.get("heart", ""),
            "deviation": dev or {},
        })
    return {
        "clock": clock.snapshot(),
        "sense_radius": settings.npc_sense_radius,
        "greet_threshold": settings.greet_threshold,
        "phases": [{"kind": k, "start": s, "end": e} for k, s, e in schedule_mod.PHASES],
        "pacing": dict(schedule_mod.PACING),
        "gather_anchor": list(schedule_mod.GATHER_ANCHOR),
        "npcs": out,
    }


@app.get("/api/debug/npc/{npc_id}")
async def npc_debug(npc_id: int) -> dict[str, Any]:
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise HTTPException(404, "查无此人")
    aff = await npc_mod.affinity(db, npc_id)
    ns = await db.fetch_one(
        "SELECT last_reflection_at, mem_since_reflection, paused_until, last_greet_real"
        " FROM npc_state WHERE npc_id = ?",
        (npc_id,),
    )
    memories = await db.fetch_all(
        "SELECT id, ts_game, kind, importance, poi, source_memory_id, content FROM memory"
        " WHERE npc_id = ? ORDER BY ts_game DESC LIMIT 50",
        (npc_id,),
    )
    longterm = await db.fetch_all(
        "SELECT id, ts_game, content, turn_count, created_at_real FROM longterm_memory"
        " WHERE npc_id = ? ORDER BY ts_game DESC LIMIT 20",
        (npc_id,),
    )
    rel = await db.fetch_all("SELECT * FROM relationship WHERE npc_id = ?", (npc_id,))
    now_game = clock.now()
    now_real = time.time()
    deviation = await plan_mod.deviation_for(db, npc_id, now_game)
    plan = await plan_mod.plan_for(db, npc_id, now_game)
    kind, _, _ = schedule_mod.phase_at(
        now_game % schedule_mod.GAME_SEC_PER_DAY, deviation)
    pos = schedule_mod.position_for(n, now_game, deviation)
    world_stage = await world_state_mod.stage(db)
    stance = world_mod.stance_entry(n.get("slug", ""))
    reflection = dict(ns or {})
    last_at = reflection.get("last_reflection_at")
    reflection["last_reflection_real"] = last_at
    reflection["seconds_since_reflection"] = (
        round(now_real - last_at, 1) if last_at is not None else None
    )
    paused = bool(reflection.get("paused_until") and reflection["paused_until"] > now_real)
    return {
        "npc": {"id": npc_id, "name": n["name"], "role": n.get("role"), "sect": n.get("sect")},
        "affinity": aff,
        "reflection": reflection,
        "schedule": {
            "phase": kind,
            "poi": schedule_mod.poi_label_for(n, now_game, deviation),
            "pos": [round(pos[0], 1), round(pos[1], 1)], "paused": paused,
        },
        "heart": plan.get("heart", "") if plan else "",
        "deviation": deviation or {},
        "memories": memories,
        "midterm": mem_store.debug_midterms(npc_id) if mem_store else [],
        "longterm": longterm,
        "longterm_pending_turns": (
            mem_store.pending_longterm_turns(npc_id) if mem_store else 0),
        "relationship": rel,
        "agenda": await agenda_mod.current(db, npc_id),
        "mood": await mood_mod.current(db, npc_id, now_real),
        "speech_examples": n.get("speech_examples", []),
        "world": {
            "stage": world_stage,
            "stance": stance.get("stance", ""),
            "knowledge": world_mod.knowledge_for(
                n.get("slug", ""), world_stage, aff,
                ["bandit", "artifact", "zhang", "sect"],
            ),
            "stage2": stance.get("stage2", ""),
        },
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
            "api_base_url": settings.llm_api_base_url or None,
            "api_trust_env": settings.llm_api_trust_env,
            "api_timeout": settings.llm_api_timeout,
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
            "api_base_url": settings.llm_api_base_url or None,
            "api_trust_env": settings.llm_api_trust_env,
            "api_timeout": settings.llm_api_timeout,
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
    return {"status": "ok", "db": db_ok, "ollama": ollama_info,
            "npcs_seeded": npc_ok, "log_path": LOG_PATH}


@app.get("/api/game/state")
async def game_state() -> dict[str, Any]:
    p = await player_mod.get(db)
    stage = await world_state_mod.stage(db)
    now_game = clock.now()
    return {
        "player": await player_mod.api_dict(db, p) if p else None,
        "clock": clock.snapshot(),
        "mobs": await mobs_mod.states(db),
        "battle": await battle_mod.active_snapshot(db),
        "npcs": await npc_mod.list_for_client(
            db, clock, await plan_mod.deviations_for_day(db, now_game)),
        "world": {
            "stage": stage,
            "artifact_acquired": await world_state_mod.artifact_acquired(db),
            "announced_at_game": await world_state_mod.announced_at(db),
            "announcement": (
                world_mod.STAGE2_FACT_TEMPLATE.format(player=p["name"])
                if stage >= 2 and p else None
            ),
            "premise": world_mod.public_facts_for_client(),
        },
    }


@app.get("/api/game/arts")
async def game_arts() -> dict[str, Any]:
    p = await _player_or_409()
    return {
        "arts": await arts_mod.list_for_client(db, p),
        "player": await player_mod.api_dict(db, p),
    }


@app.post("/api/game/art/learn")
async def game_art_learn(req: ArtLearnReq) -> dict[str, Any]:
    p = await _player_or_409()
    art_row = await arts_mod.by_id(db, req.art_id)
    if art_row is None:
        raise HTTPException(404, "查无此功法")
    learned = await db.fetch_one(
        "SELECT 1 AS ok FROM character_art"
        " WHERE character_id = ? AND art_id = ?",
        (p["character_id"], req.art_id),
    )
    try:
        if learned:
            result = await arts_mod.switch_art(
                db, p, art_row, confirm_reset=req.confirm_reset,
                confirm_clamp=req.confirm_clamp, source="switch")
        else:
            if req.npc_id is None:
                raise arts_mod.ArtError("需向传授者学艺。")
            result = await arts_mod.learn_from_npc(
                db, p, art_row, req.npc_id,
                confirm_reset=req.confirm_reset,
                confirm_clamp=req.confirm_clamp)
    except arts_mod.ArtError as e:
        detail: dict[str, Any] = {"message": str(e)}
        if e.requires_reset:
            detail["requires_reset"] = True
        if e.requires_clamp:
            detail["requires_clamp"] = True
            detail["clamp_to"] = e.clamp_to
        if detail.get("requires_reset") or detail.get("requires_clamp"):
            raise HTTPException(409, detail=detail)
        raise HTTPException(400, str(e))
    await player_mod.save(db, p)
    payload = await player_mod.api_dict(db, p)
    return {
        "player": payload, "art": payload.get("art"),
        "reset": result.get("reset", False),
        "clamped": result.get("clamped", False),
        "tuition": result.get("tuition", 0),
    }


@app.post("/api/game/new")
async def new_game(req: NewGameReq) -> dict[str, Any]:
    existing = await player_mod.get(db)
    if existing and not req.confirm:
        raise HTTPException(409, f"已有存档（{existing['name']}），重开需确认覆盖")
    name = req.name.strip()[:12]
    if not name:
        raise HTTPException(400, "请输入名号")
    if mem_store is not None:
        mem_store.begin_reset()  # 先作废旧局 generation，再 await 建档
    p = await player_mod.create(db, name)
    # 新档不能继承上个存档的世界阶段，否则 NPC 会对着新角色讲旧角色的事。
    await world_state_mod.reset(db)
    if mem_store is not None:
        await mem_store.flush()  # 等旧局在跑的长期总结收尾/丢弃
        mem_store.reset_runtime()
    logger.info("new game: name=%s id=%s; world state reset, "
                "midterm/longterm runtime cleared", name, p["id"])
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
        await inventory_mod.equip(db, p["character_id"], req.item_id)
    except inventory_mod.InvError as e:
        raise HTTPException(400, str(e))
    await _clamp_after_derived_change(p)
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p)}


@app.post("/api/game/unequip")
async def game_unequip(req: UnequipReq) -> dict[str, Any]:
    p = await _player_or_409()
    try:
        await inventory_mod.unequip(db, p["character_id"], req.slot)
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
    if await shop_mod.info(db, shop_id) is None:
        raise HTTPException(404, "查无此店")
    try:
        gain = await inventory_mod.sell(db, p["character_id"], req.item_id, req.qty)
    except inventory_mod.InvError as e:
        raise HTTPException(400, str(e))
    p["gold"] += gain
    await player_mod.save(db, p)
    return {"player": await player_mod.api_dict(db, p), "gain": gain}
