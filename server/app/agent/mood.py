"""轻情绪（docs/DESIGN.md §6.6）：只进 prompt 措辞，不改好感、不参与结算。"""

from __future__ import annotations

import time

from ..db import Database

MOOD_TEXT = {
    "pleased": "你心情不错，说话比平日热络。",
    "respectful": "你刚见识了玩家的身手，语气里多了几分认真。",
    "impatient": "你有些不耐烦，话比平日短。",
    "wary": "你心存戒备，不愿多说。",
}


async def set(db: Database, npc_id: int, mood: str, reason: str = "",
              minutes: float = 30) -> None:
    if mood not in MOOD_TEXT:
        return
    until_real = time.time() + max(1.0, float(minutes)) * 60
    await db.execute(
        "INSERT INTO npc_mood(npc_id, mood, reason, until_real) VALUES(?,?,?,?)"
        " ON CONFLICT(npc_id) DO UPDATE SET mood = excluded.mood,"
        " reason = excluded.reason, until_real = excluded.until_real",
        (npc_id, mood, reason, until_real),
    )


async def current(db: Database, npc_id: int, now_real: float) -> dict | None:
    row = await db.fetch_one(
        "SELECT mood, reason, until_real FROM npc_mood"
        " WHERE npc_id = ? AND until_real > ?",
        (npc_id, now_real),
    )
    if row is None:
        return None
    return {
        "mood": row["mood"],
        "reason": row["reason"],
        "until_real": row["until_real"],
        "text": MOOD_TEXT.get(row["mood"], ""),
    }
