"""三层记忆的摘要器（docs/DESIGN.md §6.6）。

中期：窗口溢出时把最早 10 轮归纳成 1 条 summary + 3~6 条检索线索，
只存 MemoryStore 内存，不落库。
长期：每 36 轮原始对话在后台归纳成 1 条长期记忆，由 MemoryStore 写入
longterm_memory 表；不写 memory/Chroma。
失败链路统一：校验 -> 重试一次(0.3) -> 模板兜底，绝不阻塞玩法。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

MIDTERM_SUMMARY_MAX = 120
MIDTERM_CLUE_MAX = 12
MIDTERM_CLUES_MAX = 6
LONGTERM_SUMMARY_MAX = 200
TURN_TEXT_MAX = 200


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    return text


def _clip(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _parse_midterm(result_text: str) -> tuple[str, list[str]]:
    data = json.loads(_strip_fence(result_text))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    summary = _clip(data.get("summary", ""), MIDTERM_SUMMARY_MAX)
    if not summary:
        raise ValueError("missing summary")
    raw = data.get("clues")
    clues: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            clue = _clip(item, MIDTERM_CLUE_MAX)
            if clue and clue not in clues:
                clues.append(clue)
            if len(clues) >= MIDTERM_CLUES_MAX:
                break
    return summary, clues


def _turn_lines(player_name: str, turns: list[dict]) -> str:
    lines: list[str] = []
    for turn in turns:
        place = _clip(turn.get("place", ""), 30)
        where = f"（{place}）" if place else ""
        lines.append(
            f"{player_name}{where}：{_clip(turn.get('user', ''), TURN_TEXT_MAX)}\n"
            f"你：{_clip(turn.get('npc', ''), TURN_TEXT_MAX)}"
        )
    return "\n".join(lines) or "（无有效对话）"


def _midterm_messages(npc_name: str, player_name: str, turns: list[dict]) -> list[dict]:
    system = (
        f"你是{npc_name}。下面是玩家{player_name}与你最近的一段对话。\n"
        f"{_turn_lines(player_name, turns)}\n"
        "请以你的视角完成两件事：\n"
        "1. 把这段交流归纳成 1 条简短摘要（一句话，古风白话，不超过120字），"
        "只概括对你有意义、日后值得记起的事，不要虚构对话中没提到的内容；\n"
        "2. 提炼 3~6 条“线索”：日后听到或聊到时能让你想起这段往事的短词或短语，"
        "可以是人名、地点、物品、事由，每条不超过12字。\n"
        '仅输出JSON：{"summary":"...","clues":["...","..."]}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请开始归纳。"}]


async def summarize_midterm(store, npc_id: int, npc_name: str, player_name: str,
                            place: str, turns: list[dict]) -> dict:
    """窗口溢出 -> 1 条 summary + 线索；永不抛异常。"""
    last_error: Exception | None = None
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                "dialogue_summary", _midterm_messages(npc_name, player_name, turns),
                json_format=True, temperature=temperature,
                metadata={"npc_id": npc_id, "stage": "midterm", "window": len(turns)},
            )
            summary, clues = _parse_midterm(cr.text)
            logger.info("midterm_summary npc=%d window=%d clues=%d",
                        npc_id, len(turns), len(clues))
            return {"summary": summary, "clues": clues, "turn_count": len(turns)}
        except Exception as exc:
            last_error = exc
            continue
    last_user = _clip(turns[-1].get("user", ""), MIDTERM_SUMMARY_MAX) if turns else ""
    summary = (f"与{player_name}在{place}闲谈：{last_user}"
               if last_user else "与玩家闲谈了几句。")
    logger.warning("midterm_summary npc=%d fallback after retry: %s: %s",
                   npc_id, type(last_error).__name__ if last_error else "unknown",
                   last_error)
    return {"summary": summary[:MIDTERM_SUMMARY_MAX], "clues": [],
            "turn_count": len(turns)}


def _parse_longterm(result_text: str) -> str:
    data = json.loads(_strip_fence(result_text))
    summary = (_clip(data.get("summary", ""), LONGTERM_SUMMARY_MAX)
               if isinstance(data, dict) else "")
    if not summary:
        raise ValueError("missing summary")
    return summary


def _longterm_messages(npc_name: str, player_name: str, turns: list[dict]) -> list[dict]:
    system = (
        f"你是{npc_name}。下面是你与玩家{player_name}从早前到最近的一段往来，"
        f"共{len(turns)}轮。\n"
        f"{_turn_lines(player_name, turns)}\n"
        "请以你的视角，把这段往来归纳成一条长期记忆：第三人称，古风白话，不超过200字；"
        "只保留对你有意义、日后值得记起的人、事、约定与态度变化，不要虚构；不要逐轮复述。\n"
        '仅输出JSON：{"summary":"..."}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请开始归纳。"}]


async def summarize_longterm(store, npc_id: int, npc_name: str, player_name: str,
                             turns: list[dict]) -> dict:
    """36 轮原始对话 -> 1 条长期记忆；后台调用，永不抛异常。"""
    last_error: Exception | None = None
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                "dialogue_summary", _longterm_messages(npc_name, player_name, turns),
                json_format=True, temperature=temperature, priority=3,
                metadata={"npc_id": npc_id, "stage": "longterm", "turns": len(turns)},
            )
            summary = _parse_longterm(cr.text)
            logger.info("longterm_summary npc=%d turns=%d chars=%d",
                        npc_id, len(turns), len(summary))
            return {"summary": summary}
        except Exception as exc:
            last_error = exc
            continue
    first_user = _clip(turns[0].get("user", ""), 60) if turns else ""
    last_user = _clip(turns[-1].get("user", ""), 60) if turns else ""
    if first_user or last_user:
        summary = f"与{player_name}的一段往来：{first_user}……{last_user}"
    else:
        summary = "与玩家的一段往来。"
    logger.warning("longterm_summary npc=%d fallback after retry: %s: %s",
                   npc_id, type(last_error).__name__ if last_error else "unknown",
                   last_error)
    return {"summary": summary[:LONGTERM_SUMMARY_MAX]}
