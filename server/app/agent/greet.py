"""主动搭话（docs/DESIGN.md §6.5）：好感≥阈值、冷却外、非对话中时生成一句招呼。

搭话不改好感，且只在成功后消耗冷却——避免站在 NPC 旁边反复进出刷好感。
RAG 失败时按无记忆处理：搭话不能因为检索失败而不搭。
"""

import asyncio
import json
import re
import time

from ..config import settings
from ..game import plan as plan_mod
from ..game import player as player_mod
from ..game import schedule as schedule_mod
from ..game import world as world_mod
from ..game import world_state as world_state_mod
from . import dialogue as dialogue_mod
from . import mood as mood_mod
from . import npc as npc_mod
from . import rag as rag_mod

_locks: dict[int, asyncio.Lock] = {}


def _parse(result_text: str) -> str:
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    reply = str(data.get("reply", "")).strip() if isinstance(data, dict) else ""
    if not reply:
        raise ValueError("missing reply")
    return reply


def _messages(n: dict, p: dict, affinity: int, memories: list[dict], now_str: str,
              place: str, world_brief: str = "", mood_text: str = "",
              heart: str = "") -> list[dict]:
    mem_lines = "\n".join(m["content"] for m in memories[: settings.rag_top_k]) or "（暂无）"
    heart_line = f"你眼下挂心的事：{heart}\n" if heart else ""
    mood_line = f"你此刻的心气：{mood_text}\n" if mood_text else ""
    system = (
        f"你是{n['name']}。{n['persona']}你说话{n['speech_style']}。\n"
        f"{world_brief}\n"
        f"{dialogue_mod._warmth(affinity, p['name'])}\n"
        f"你记得的一些事：\n{mem_lines}\n"
        f"{heart_line}{mood_line}"
        f"此刻{now_str}，你正在{place}，看见{p['name']}走近。\n"
        f"以你的口吻主动招呼他一句（≤40字），可顺带提起一件你记得的事，不要说套话，"
        "不要每句都提后山。\n"
        '仅输出JSON：{"reply":"..."}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请打招呼。"}]


def _fallback(n: dict, p: dict, affinity: int) -> str:
    if affinity >= 85:
        return f"{n['name']}远远便朝你招手：“{p['name']}，可算见着你了。”"
    return f"{n['name']}朝你略一颔首：“{p['name']}，别来无恙。”"


async def greet(db, store, npc_id: int) -> dict:
    # 客户端连点/并发请求只放一个进来。
    async with _locks.setdefault(npc_id, asyncio.Lock()):
        n = await npc_mod.by_id(db, npc_id)
        if n is None:
            raise LookupError("查无此人")
        p = await player_mod.get(db)
        if p is None:
            raise ValueError("无存档")

        aff = await npc_mod.affinity(db, npc_id)
        if aff < settings.greet_threshold:
            return {"greet": False, "reason": "affinity", "affinity": aff}

        row = await db.fetch_one(
            "SELECT paused_until, last_greet_real FROM npc_state WHERE npc_id = ?",
            (npc_id,),
        )
        now = time.time()
        last_greet = row["last_greet_real"] if row else None
        if last_greet is not None and now - last_greet < settings.greet_cooldown_real:
            return {"greet": False, "reason": "cooldown", "affinity": aff}
        if row and row["paused_until"] and row["paused_until"] > now:
            return {"greet": False, "reason": "paused", "affinity": aff}

        try:
            memories = await rag_mod.retrieve(
                db, store, npc_id, f"{p['name']}近来", settings=settings
            )
        except Exception:
            memories = []

        now_game = store.clock.now()
        place = schedule_mod.poi_label_for(
            n, now_game, await plan_mod.deviation_for(db, npc_id, now_game))
        stage = await world_state_mod.stage(db)
        world_brief = world_mod.brief_for(n, "", stage, aff, p["name"])
        mood = await mood_mod.current(db, npc_id, now)
        mood_text = str(mood.get("text", "")) if mood else ""
        heart_row = await plan_mod.plan_for(db, npc_id, now_game)
        heart = str(heart_row["heart"]) if heart_row else ""
        msgs = _messages(n, p, aff, memories, dialogue_mod.shichen(store.clock), place,
                         world_brief, mood_text, heart)
        reply: str | None = None
        for temperature in (0.7, 0.3):
            try:
                cr = await store.llm.chat(
                    "greet", msgs, json_format=True, temperature=temperature,
                    metadata={"npc_id": npc_id, "affinity": aff},
                )
                reply = _parse(cr.text)
                break
            except Exception:
                continue
        if not reply:
            reply = _fallback(n, p, aff)

        await db.execute(
            "UPDATE npc_state SET last_greet_real = ? WHERE npc_id = ?",
            (time.time(), npc_id),
        )
        return {"greet": True, "reply": reply, "affinity": aff, "npc_id": npc_id}
