"""送礼/切磋的 NPC 感受与好感变化生成（DESIGN §6.5/§6.6）。"""

import json
import logging
import re

from ..config import settings
from ..db import Database
from . import rag as rag_mod

logger = logging.getLogger(__name__)


def _tone(affinity: int, player_name: str) -> str:
    # 与 dialogue._warmth 使用同一套交情档位；改档位时两处同步。
    if affinity < 30:
        return f"你与{player_name}素无深交，不过点头之交。"
    if affinity < 60:
        return f"你与{player_name}算得上相熟，往来客气。"
    if affinity < 70:
        return f"你与{player_name}颇为投缘，来往热络。"
    if affinity < 85:
        return f"你与{player_name}已是亲近，颇有交情。"
    return f"你与{player_name}情谊甚笃，信得过。"


def _parse(result_text: str) -> tuple[str, int]:
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    reply = str(data.get("reply", "")).strip() if isinstance(data, dict) else ""
    if not reply:
        raise ValueError("missing reply")
    try:
        delta = int(data.get("affinity_delta", 0))
    except (TypeError, ValueError):
        delta = 0
    return reply[:80], delta


def _messages(npc: dict, player_name: str, affinity: int, impression: str,
              memories: list[dict], turns: list[dict], event_text: str,
              purpose: str) -> list[dict]:
    mem_lines = "\n".join(f"- {m['content']}" for m in memories) or "（暂无）"
    turn_lines = "\n".join(
        f"{player_name}：{t['user']}\n你：{t['npc']}" for t in turns
    ) or "（尚无可聊）"
    examples = "".join(f"“{x}”" for x in npc.get("speech_examples", []))
    examples_line = (
        f"你说话的样子：参考这些语气，不要逐字照抄：{examples}\n" if examples else ""
    )
    if purpose == "gift":
        guidance = "礼物越贵重越容易打动你；同一天重复送礼，打动程度递减。"
    else:
        guidance = "切磋胜负只影响你对玩家身手的判断，不要虚构其他事实。"
    system = (
        f"你是{npc['name']}。{npc['persona']}你说话{npc['speech_style']}。\n"
        f"{_tone(affinity, player_name)}\n"
        + (f"你对{player_name}的印象：{impression}\n" if impression else "")
        + f"你记得的一些事：\n{mem_lines}\n"
        + f"你们最近的往来：\n{turn_lines}\n"
        + f"刚刚发生的事：{event_text}\n"
        + f"{guidance}\n"
        + examples_line
        + f"请以你的口吻回应这件事（≤60字），并给出这件事让你对{player_name}好感的变化"
        "（-2~+3整数）。\n"
        "只回应这件事本身，不要复述设定，不要编造对话中没有的内容。\n"
        '仅输出JSON：{"reply":"...","affinity_delta":0}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请回应。"}]


async def _affinity(db: Database, npc_id: int) -> int:
    row = await db.fetch_one("SELECT affinity FROM npc_state WHERE npc_id = ?", (npc_id,))
    return int(row["affinity"]) if row else 0


async def _impression(db: Database, npc_id: int, player_name: str) -> str:
    row = await db.fetch_one(
        "SELECT summary FROM relationship"
        " WHERE npc_id = ? AND target_kind = 'player' AND target_id = ?",
        (npc_id, player_name),
    )
    return str(row["summary"]) if row else ""


async def _memories(db: Database, npc_id: int, limit: int = 3) -> list[dict]:
    return await db.fetch_all(
        "SELECT content FROM memory WHERE npc_id = ? ORDER BY ts_game DESC LIMIT ?",
        (npc_id, limit),
    )


async def generate(db, store, npc: dict, player: dict, *, event_text: str,
                   purpose: str, fallback_reply: str) -> dict:
    """Return one in-character reaction and a clamped affinity delta."""
    if store is None:
        return {"reply": fallback_reply, "affinity_delta": 0}
    npc_id = int(npc["id"])
    player_name = str(player["name"])
    affinity = await _affinity(db, npc_id)
    impression = await _impression(db, npc_id, player_name)
    memories = await _memories(db, npc_id)
    turns = await rag_mod.recent_turns(db, npc_id, n=settings.dialogue_window_size)
    msgs = _messages(
        npc, player_name, affinity, impression, memories, turns, event_text, purpose)
    last_error: Exception | None = None
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                purpose, msgs, json_format=True, temperature=temperature,
                metadata={"npc_id": npc_id, "player": player_name, "event": purpose},
            )
            reply, delta = _parse(cr.text)
            delta = max(-settings.max_dialogue_delta,
                        min(settings.max_dialogue_delta, delta))
            return {"reply": reply, "affinity_delta": delta}
        except Exception as exc:
            last_error = exc
            continue
    logger.warning("%s reaction npc=%d fallback after retry: %s: %s",
                   purpose, npc_id,
                   type(last_error).__name__ if last_error else "unknown",
                   last_error)
    return {"reply": fallback_reply, "affinity_delta": 0}
