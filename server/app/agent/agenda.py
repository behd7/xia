"""对话议程（docs/DESIGN.md §6.6）：预置池 + 确定性加权抽取，不调 LLM。

同一场对话 5 现实分钟内复用当前议程；每次新对话按 npc/day/seq 做确定性抽取，
并排除最近 3 个议程。end=true 时清除当前议程。
"""

from __future__ import annotations

import json
import random

from ..db import Database
from ..game import world as world_mod

AGENDAS = {
    "test_artifact": "你想知道玩家上没上过后山、见没见过黑旋风。",
    "probe_zhang": "你想试探玩家对张馒头的看法，或知不知道“山外”。",
    "urge_go": "你想怂恿玩家上山。",
    "warn_off": "你想劝玩家别去后山。",
    "boast_info": "你想炫耀自己知道的那点后山传闻。",
    "avoid_artifact": "你想避开神器这个话题。",
    "recruit": "你想替自家门派拉人。",
    "generic": "你想说点别的：炫耀、诉苦或赶时间。",
}

AGENDA_POOLS = {
    "gu_changfeng": {"test_artifact": 3, "recruit": 2, "warn_off": 2, "probe_zhang": 1},
    "tie_wushuang": {"urge_go": 3, "boast_info": 2, "test_artifact": 2, "recruit": 1},
    "su_wanqing": {"probe_zhang": 3, "avoid_artifact": 2, "warn_off": 2, "test_artifact": 1},
    "wang_dazhu": {"boast_info": 3, "test_artifact": 2, "avoid_artifact": 1, "generic": 1},
    "chen_bansong": {"warn_off": 3, "avoid_artifact": 2, "generic": 1},
    "liu_xiaodie": {"test_artifact": 3, "boast_info": 2, "urge_go": 1},
    "zhang_mantou": {"probe_zhang": 3, "avoid_artifact": 2, "generic": 1},
}

RECENT_LIMIT = 3
SESSION_REUSE_REAL = 300


def _current_key(npc_id: int) -> str:
    return f"agenda_current:{npc_id}"


def _recent_key(npc_id: int) -> str:
    return f"agenda_recent:{npc_id}"


def _avoid(slug: str) -> str:
    return str(world_mod.stance_entry(slug).get("avoid", ""))


def _load(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def current(db: Database, npc_id: int) -> dict | None:
    """只读当前议程（debug 用），不触发新抽取。"""
    record = _load(await db.kv_get(_current_key(npc_id)))
    agenda_id = str(record.get("id", ""))
    if agenda_id not in AGENDAS:
        return None
    return {
        "id": agenda_id,
        "text": str(record.get("text") or AGENDAS[agenda_id]),
        "avoid": str(record.get("avoid", "")),
        "day": record.get("day"),
        "last_real": record.get("last_real"),
    }


async def clear(db: Database, npc_id: int) -> None:
    await db.execute("DELETE FROM kv WHERE k = ?", (_current_key(npc_id),))


async def current_or_pick(db: Database, npc_id: int, slug: str,
                          now_game: float, now_real: float) -> dict:
    day = int(now_game // 86400)
    record = _load(await db.kv_get(_current_key(npc_id)))
    agenda_id = str(record.get("id", ""))
    same_day = int(record.get("day", -1)) == day
    fresh = now_real - float(record.get("last_real", 0) or 0) < SESSION_REUSE_REAL
    if agenda_id in AGENDAS and same_day and fresh:
        return {
            "id": agenda_id,
            "text": str(record.get("text") or AGENDAS[agenda_id]),
            "avoid": str(record.get("avoid") or _avoid(slug)),
        }

    seq_key = f"agenda_seq:{npc_id}:{day}"
    seq = int(await db.kv_get(seq_key) or 0) + 1
    await db.kv_set(seq_key, str(seq))

    try:
        recent = json.loads(await db.kv_get(_recent_key(npc_id)) or "[]")
    except (TypeError, ValueError):
        recent = []
    recent = [str(x) for x in recent if str(x) in AGENDAS][-RECENT_LIMIT:]

    pool = AGENDA_POOLS.get(slug) or {"generic": 1}
    candidates = [key for key in pool if key not in recent] or list(pool)
    rng = random.Random(npc_id * 1000003 + day * 1009 + seq)
    picked = rng.choices(candidates, weights=[pool[key] for key in candidates], k=1)[0]

    record = {
        "day": day,
        "id": picked,
        "text": AGENDAS[picked],
        "avoid": _avoid(slug),
        "last_real": now_real,
    }
    await db.kv_set(_current_key(npc_id), json.dumps(record, ensure_ascii=False))
    await db.kv_set(_recent_key(npc_id),
                    json.dumps([*recent, picked][-RECENT_LIMIT:], ensure_ascii=False))
    return {"id": picked, "text": AGENDAS[picked], "avoid": record["avoid"]}
