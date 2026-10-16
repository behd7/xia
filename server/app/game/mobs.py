import json
import time

from ..db import Database
from . import data


def find_spawn(mob_id: str) -> dict | None:
    return next((s for s in data.MOB_SPAWNS if s["id"] == mob_id), None)


async def _kills(db: Database) -> dict[str, float]:
    raw = await db.kv_get("mob_kills")
    return json.loads(raw) if raw else {}


async def states(db: Database) -> list[dict]:
    kills = await _kills(db)
    now = time.time()
    out = []
    for s in data.MOB_SPAWNS:
        t = data.MOB_TYPES[s["type"]]
        killed_at = kills.get(s["id"])
        respawn_at = killed_at + data.MOB_RESPAWN_REAL if killed_at else None
        out.append({
            "id": s["id"], "type": s["type"], "name": t["name"], "level": t["level"],
            "x": s["x"] * data.TILE + 8, "y": s["y"] * data.TILE + 8,
            "alive": respawn_at is None or respawn_at <= now,
            "respawn_at_real": respawn_at,
        })
    return out


async def mark_killed(db: Database, mob_id: str) -> None:
    kills = await _kills(db)
    kills[mob_id] = time.time()
    await db.kv_set("mob_kills", json.dumps(kills))
