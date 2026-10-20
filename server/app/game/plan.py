"""当日心事/偏离读取（docs/DESIGN.md §6.10）。

只做 DB 读取，不 import agent，避免 schedule/npc/heart 之间的循环依赖。
"""

from __future__ import annotations

import json

from ..config import settings
from ..db import Database

DEVIATION_KINDS = ("gather_early", "gather_late", "stay_work", "home_early")


def _deviation(raw: str | None) -> dict | None:
    try:
        dev = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(dev, dict) or dev.get("kind") not in DEVIATION_KINDS:
        return None
    return dev


async def deviation_for(db: Database, npc_id: int, now_game: float) -> dict | None:
    if not settings.plan_enabled:
        return None
    day = int(now_game // 86400)
    row = await db.fetch_one(
        "SELECT deviation_json FROM npc_plan WHERE npc_id = ? AND game_day = ?",
        (npc_id, day),
    )
    return _deviation(row["deviation_json"]) if row else None


async def deviations_for_day(db: Database, now_game: float) -> dict[int, dict]:
    if not settings.plan_enabled:
        return {}
    day = int(now_game // 86400)
    rows = await db.fetch_all(
        "SELECT npc_id, deviation_json FROM npc_plan WHERE game_day = ?", (day,))
    out: dict[int, dict] = {}
    for row in rows:
        dev = _deviation(row["deviation_json"])
        if dev is not None:
            out[row["npc_id"]] = dev
    return out


async def plan_for(db: Database, npc_id: int, now_game: float) -> dict | None:
    if not settings.plan_enabled:
        return None
    day = int(now_game // 86400)
    return await db.fetch_one(
        "SELECT heart, reason, deviation_json FROM npc_plan"
        " WHERE npc_id = ? AND game_day = ?",
        (npc_id, day),
    )


async def plans_for_day(db: Database, now_game: float) -> dict[int, dict]:
    if not settings.plan_enabled:
        return {}
    day = int(now_game // 86400)
    rows = await db.fetch_all(
        "SELECT npc_id, heart, reason, deviation_json FROM npc_plan WHERE game_day = ?",
        (day,),
    )
    return {row["npc_id"]: row for row in rows}
