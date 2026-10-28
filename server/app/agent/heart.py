"""每日心事与安全偏离（docs/DESIGN.md §6.10）：每个 NPC 每天最多生成一次。

LLM 输出经校验 -> 重试一次(temperature 0.3) -> 空心事/无偏离兜底；失败当天
不再重试。偏离只平移既有日程阶段，不新增坐标或寻路。
"""

from __future__ import annotations

import json
import logging
import time

from ..config import settings
from ..db import Database
from ..game import player as player_mod
from ..game import world as world_mod
from ..game import world_state as world_state_mod
from . import npc as npc_mod
from . import relationship as relationship_mod
from .json_utils import strip_fence

DEVIATION_KINDS = ("gather_early", "gather_late", "stay_work", "home_early")

logger = logging.getLogger(__name__)


def _parse(result_text: str) -> tuple[str, str, str, int]:
    data = json.loads(strip_fence(result_text))
    if not isinstance(data, dict):
        raise ValueError("heart must be an object")
    heart = str(data.get("heart", "")).strip().replace("\n", " ")
    heart = heart[: settings.plan_heart_max_chars]
    reason = str(data.get("reason", "")).strip().replace("\n", " ")[:40]
    deviation = str(data.get("deviation", "none")).strip()
    if deviation not in DEVIATION_KINDS:
        deviation = "none"
    try:
        minutes = int(data.get("minutes", 10))
    except (TypeError, ValueError):
        minutes = 10
    return heart, reason, deviation, max(5, min(20, minutes))


def _messages(n: dict, p: dict, rows: list[dict], summary: str, stage: int) -> list[dict]:
    stance = world_mod.stance_entry(str(n.get("slug", "")))
    experiences = "\n".join(f"- {r['content']}" for r in rows) or "（今日尚无特别见闻）"
    world_line = ("后山神器之事已传遍全镇，人人都在议论。"
                  if stage >= 2 else "后山来了黑旋风一伙，镇上人心不定。")
    avoid_line = f"你不愿提：{stance['avoid']}\n" if stance.get("avoid") else ""
    system = (
        f"你是{n['name']}。{n['persona']}你说话{n['speech_style']}。\n"
        f"当前清溪镇：{world_line}\n"
        f"你对后山神器的立场：{stance.get('stance', '')}\n"
        f"{avoid_line}"
        f"你与{p['name']}的来往：{summary or '（尚无深交）'}\n"
        f"你最近的见闻：\n{experiences}\n"
        "挑一件你今天挂在心上的事，必须与你的立场或最近见闻有关；"
        "不要写空泛的“想喝酒”。\n"
        "再从安全菜单里选一个今天的小偏离：none、gather_early、gather_late、"
        "stay_work、home_early；没有就选 none。\n"
        f"heart 不超过{settings.plan_heart_max_chars}字，reason 不超过40字，"
        "minutes 为5~20。\n"
        '仅输出JSON：{"heart":"...","reason":"...","deviation":"none","minutes":10}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请开始想。"}]


async def ensure_today(db: Database, store, npc_id: int) -> dict | None:
    if not settings.plan_enabled:
        return None
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        return None
    now_game = store.clock.now()
    day = int(now_game // 86400)
    existing = await db.fetch_one(
        "SELECT * FROM npc_plan WHERE npc_id = ? AND game_day = ?", (npc_id, day))
    if existing is not None:
        return existing
    p = await player_mod.get(db)
    if p is None:
        return None

    rows = await db.fetch_all(
        "SELECT content, kind, importance FROM memory"
        " WHERE npc_id = ? ORDER BY ts_game DESC LIMIT 10",
        (npc_id,),
    )
    summary = await relationship_mod.summary_for(db, npc_id, "player", p["name"])
    stage = await world_state_mod.stage(db)

    heart, reason, kind = "", "", "none"
    minutes = 10
    last_error: Exception | None = None
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                "heart", _messages(n, p, rows, summary, stage), json_format=True,
                temperature=temperature,
                metadata={"npc_id": npc_id, "day": day},
            )
            heart, reason, kind, minutes = _parse(cr.text)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        logger.warning("heart npc=%d day=%d generation failed after retry: %s: %s",
                       npc_id, day, type(last_error).__name__, last_error)

    deviation: dict = {}
    if kind != "none":
        shift = min(minutes * 60, max(0, settings.plan_deviation_shift_sec))
        if shift > 0:
            deviation = {"day": day, "kind": kind, "shift": shift}
    await db.execute(
        "INSERT OR IGNORE INTO npc_plan(npc_id, game_day, heart, reason, deviation_json,"
        " created_at_real) VALUES(?,?,?,?,?,?)",
        (npc_id, day, heart, reason, json.dumps(deviation, ensure_ascii=False), time.time()),
    )
    logger.info("heart npc=%d day=%d heart=%r deviation=%s",
                npc_id, day, heart[:40], deviation.get("kind", "none"))
    return await db.fetch_one(
        "SELECT * FROM npc_plan WHERE npc_id = ? AND game_day = ?", (npc_id, day))


async def ensure_all(db: Database, store) -> None:
    for npc_id in npc_mod.ids():
        try:
            await ensure_today(db, store, npc_id)
        except Exception:
            logger.exception("heart npc=%d failed", npc_id)
