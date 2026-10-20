"""世界阶段推进（docs/DESIGN.md §6.11）：黑旋风 + 三名山贼 + 断岳刀齐备后全镇皆知。"""

import logging

from ..db import Database
from . import mobs, world

logger = logging.getLogger(__name__)

BANDIT_IDS = ("bandit_1", "bandit_2", "bandit_3")
BOSS_ID = "boss_1"


async def stage(db: Database) -> int:
    raw = await db.kv_get(world.STAGE_KEY)
    return int(raw) if raw else 0


async def artifact_acquired(db: Database) -> bool:
    return await db.kv_get(world.ARTIFACT_ACQUIRED_KEY) == "1"


async def mark_artifact_acquired(db: Database) -> None:
    await db.kv_set(world.ARTIFACT_ACQUIRED_KEY, "1")


async def announced_at(db: Database) -> float | None:
    raw = await db.kv_get(world.ANNOUNCED_AT_KEY)
    return float(raw) if raw else None


async def check_and_advance(db: Database, now_game: float) -> bool:
    if await stage(db) >= 2:
        return False
    if not await artifact_acquired(db):
        return False
    kills = await mobs.kill_times(db)
    if BOSS_ID not in kills or any(mob_id not in kills for mob_id in BANDIT_IDS):
        return False
    await db.kv_set(world.STAGE_KEY, "2")
    await db.kv_set(world.ANNOUNCED_AT_KEY, str(now_game))
    logger.info("world stage advanced to 2 at game_sec=%.1f", now_game)
    return True


async def reset(db: Database) -> None:
    await db.execute(
        "DELETE FROM kv WHERE k IN (?,?,?)",
        (world.STAGE_KEY, world.ARTIFACT_ACQUIRED_KEY, world.ANNOUNCED_AT_KEY),
    )
    await db.execute("DELETE FROM kv WHERE k = 'mob_kills'")
