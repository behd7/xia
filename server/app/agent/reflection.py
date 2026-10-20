"""后台反思（docs/DESIGN.md §6.7）：把近期记忆归纳为高阶洞察，写入 kind=reflection。

触发条件（非 force）：新增记忆达阈值，或有新记忆且距上次反思超过间隔。
输出链路与对话一致：校验 -> 重试一次(0.3) -> 模板兜底，绝不让 4B 模型失败
卡死后台循环。importance 固定 8，不采信模型给值（4B 恒偏高）。
"""

import json
import re
import time

from ..config import settings
from ..db import Database
from ..game import player as player_mod
from ..game import schedule as schedule_mod
from . import npc as npc_mod
from . import relationship as relationship_mod


def _parse(result_text: str) -> tuple[list[str], int]:
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    raw = data.get("insights") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("missing insights")
    insights: list[str] = []
    for item in raw[:3]:
        content = item.get("content", "") if isinstance(item, dict) else item
        content = str(content or "").strip()
        if content:
            insights.append(content[:80])
    if not insights:
        raise ValueError("empty insights")
    try:
        delta = int(data.get("affinity_delta", 0))
    except (TypeError, ValueError):
        delta = 0
    return insights, delta


def _messages(n: dict, player_name: str, rows: list[dict]) -> list[dict]:
    experiences = "\n".join(f"- {r['content']}" for r in rows)
    system = (
        f"你是{n['name']}。{n['persona']}你说话{n['speech_style']}。\n"
        "以下是你近来的经历与见闻（由近到远）：\n"
        f"{experiences}\n"
        f"请以你的视角归纳 1~3 条更高层的洞察：这些人这些事说明了什么、你对{player_name}的观感有何变化。\n"
        "每条一句话，第三人称叙述，不超过50字；只依据上述记录，不要虚构。\n"
        f"若这些经历让你对{player_name}的观感明显改变，另给 -2~+2 的整数；否则给 0。\n"
        '仅输出JSON：{"insights":[{"content":"..."}],"affinity_delta":0}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请开始归纳。"}]


async def run_once(db: Database, store, npc_id: int, *, force: bool = False) -> bool:
    n = await npc_mod.by_id(db, npc_id)
    p = await player_mod.get(db)
    if n is None or p is None:
        return False  # 无玩家的空档不产出反思

    state = await db.fetch_one(
        "SELECT last_reflection_at, mem_since_reflection FROM npc_state WHERE npc_id = ?",
        (npc_id,),
    )
    if state is None:
        return False
    last_at = state["last_reflection_at"]
    mem_count = int(state["mem_since_reflection"] or 0)
    now_real = time.time()

    # 冷却兼失败退避：乘 0.75 避免 60s 循环抖动导致每两拍才反思一次。
    if not force and last_at is not None and now_real - last_at < settings.scheduler_tick_real * 0.75:
        return False
    if not force:
        due_count = mem_count >= settings.reflect_mem_threshold
        due_time = mem_count > 0 and last_at is not None and now_real - last_at >= settings.reflect_interval_real
        if not (due_count or due_time):
            return False

    rows = await db.fetch_all(
        "SELECT id, content, kind, importance, participants_json FROM memory"
        " WHERE npc_id = ? ORDER BY ts_game DESC LIMIT ?",
        (npc_id, settings.reflection_take_n),
    )
    if len(rows) < settings.reflection_min_memories:
        return False

    insights: list[str] | None = None
    delta = 0
    attempts = 0
    for temperature in (0.7, 0.3):
        attempts += 1
        try:
            cr = await store.llm.chat(
                "reflection", _messages(n, p["name"], rows), json_format=True,
                temperature=temperature,
                metadata={"npc_id": npc_id, "attempt": attempts},
            )
            insights, delta = _parse(cr.text)
            break
        except Exception:
            continue
    if insights is None:
        # 不跳过：后台循环不能反复重试同一批记忆。
        insights = [f"近来常想起：{str(rows[0]['content']).strip()}"[:80]]
        delta = 0

    poi = schedule_mod.poi_label_for(n, store.clock.now())
    participants = npc_mod.names_in(" ".join(insights), p["name"])
    for content in insights[:3]:
        await store.add_memory(
            db, npc_id, content=content, kind="reflection",
            participants=participants, poi=poi, importance=8,
        )

    if any(p["name"] in text for text in insights):
        bounded = max(-settings.max_reflection_delta,
                      min(settings.max_reflection_delta, delta))
        if bounded:
            await npc_mod.apply_delta(db, npc_id, bounded)

    # 快照扣减：反思自身不计数，且不能吞掉反思期间并发写入的新记忆。
    await db.execute(
        "UPDATE npc_state SET mem_since_reflection = MAX(0, mem_since_reflection - ?),"
        " last_reflection_at = ? WHERE npc_id = ?",
        (mem_count, time.time(), npc_id),
    )
    try:
        await relationship_mod.maybe_update(db, store, npc_id, "player", p["name"])
    except Exception:
        pass
    return True
