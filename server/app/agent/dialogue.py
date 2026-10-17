"""自由对话（docs/DESIGN.md §6.6）：POST /api/npc/{id}/talk。

单次 LLM 调用（qwen3:4b-instruct, format:json, 禁思考），从单回合回到：
记忆检索（RAG）+ 最近对话平铺 并行做 -> prompt -> 校验 JSON -> 重试一次
(temperature 0.3) -> 模板兜底。绝不让模型输出失败阻塞游戏（AGENTS.md #4）。
"""

import json
import re

from ..config import settings
from ..game import player as player_mod
from . import affinity as affinity_mod
from . import npc as npc_mod
from . import rag as rag_mod

SHICHEN_PHASES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def shichen(clock) -> str:
    hour = int(clock.now() // 3600) % 24
    return SHICHEN_PHASES[(hour + 1) // 2 % 12] + "时"


def _parse(result_text: str, message: str) -> dict:
    """解析 LLM 的 JSON 输出；失败抛异常由调用方走重试/兜底。"""
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    if not isinstance(data, dict) or "reply" not in data:
        raise ValueError("missing reply")
    return {
        "reply": str(data.get("reply", "")) or "……",
        "event": str(data.get("event") or message),
        "affinity_delta": int(data.get("affinity_delta", 0)),
        "importance": max(1, min(10, int(data.get("importance", 3)))),
        "end": bool(data.get("end", False)),
    }


def _fallback(n: dict, message: str) -> dict:
    return {
        "reply": f"{n['name']}沉吟片刻，不置可否。",
        "event": message, "affinity_delta": 0, "importance": 3, "end": False,
    }


def _build_messages(p, n, affinity, memories, turns, now_str, place, message) -> list[dict]:
    sect_desc = f"（{p.get('sect', {}).get('name', '江湖散人')}）" if p.get("sect") else "（江湖散人）"
    mem_lines = "\n".join(
        f"[重要度{m['importance']}·{m.get('poi') or '—'}] {m['content']}"
        for m in memories[: settings.rag_top_k]
    ) or "（暂无）"
    turn_lines = "\n".join(f"玩家：{t['user']}\n你：{t['npc']}" for t in turns) or "（尚无可聊）"
    system = (
        f"你扮演武侠游戏NPC。{n['persona']} 说话风格：{n['speech_style']}，口头禅常用「{n['catchphrase']}」。\n"
        f"你与玩家（{p['name']}{sect_desc}）好感度 {affinity}/100。\n"
        f"你对他的一些记忆（按重要度倒序）：\n{mem_lines}\n"
        f"你们最近的对话：\n{turn_lines}\n"
        "请以你的口吻回复玩家（≤80字，古风白话白话）。同时输出："
        "第三人称事件摘要（你的视角，一句话）；好感变化（-2~+3整数）；"
        "记忆重要度（1~10整数）；是否想结束对话（BOOL）。\n"
        '仅输出JSON，不要输出其他文字，格式：'
        '{"reply":"...","event":"...","affinity_delta":0,"importance":5,"end":false}'
    )
    user = f"当前{now_str}，你在{place}。玩家说：{message}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def talk(db, mem_store, npc_id: int, message: str) -> dict:
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise LookupError("查无此人")
    message = (message or "").strip()
    if not message:
        raise ValueError("空话一句，开不了口。")

    p = await player_mod.get(db)
    current_affinity = await npc_mod.affinity(db, npc_id)

    # 记忆检索 与 最近对话 并行做（DESIGN §7.2 前台等待 2~5s）
    memories = await rag_mod.retrieve(db, mem_store, npc_id, message, settings=settings)
    turns = await rag_mod.recent_turns(db, npc_id, n=3)

    place = n["work"] or ""
    result: dict | None = None
    for temperature in (0.7, 0.3):  # 校验 -> 重试一次(temperature 0.3)
        try:
            msgs = _build_messages(p, n, current_affinity, memories, turns,
                                   shichen(mem_store.clock), place, message)
            cr = await mem_store.llm.chat("dialogue", msgs, json_format=True, temperature=temperature)
            result = _parse(cr.text, message)
            break
        except Exception:
            continue
    if result is None:
        result = _fallback(n, message)

    delta = max(-settings.max_dialogue_delta, min(settings.max_dialogue_delta, result["affinity_delta"]))
    new_affinity = await affinity_mod.apply_delta(db, npc_id, delta)

    await mem_store.add_memory(
        db, npc_id, content=result["event"], kind="event",
        participants=[p["name"]], poi=place, importance=result["importance"],
    )
    await db.execute(
        "INSERT INTO conversation(npc_id, ts_game, turns_json, affinity_delta_total)"
        " VALUES(?,?,?,?)",
        (npc_id, mem_store.clock.now(),
         json.dumps({"user": message, "npc": result["reply"]}, ensure_ascii=False), delta),
    )

    return {
        "reply": result["reply"], "affinity": new_affinity,
        "end": result["end"], "player": await player_mod.api_dict(db, p),
    }