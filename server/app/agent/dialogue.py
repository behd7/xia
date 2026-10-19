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
from . import trace as trace_mod

SHICHEN_PHASES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def shichen(clock) -> str:
    hour = int(clock.now() // 3600) % 24
    return SHICHEN_PHASES[(hour + 1) // 2 % 12] + "时"


def _parse(result_text: str) -> dict:
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
        "affinity_delta": int(data.get("affinity_delta", 0)),
        "end": bool(data.get("end", False)),
    }


def _fallback(n: dict) -> dict:
    return {"reply": f"{n['name']}沉吟片刻，不置可否。", "affinity_delta": 0, "end": False}


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
        "请以你的口吻回复玩家（≤80字，古风白话）。同时输出：好感变化（-2~+3整数）；"
        "是否想结束对话（BOOL）。\n"
        '仅输出JSON，不要输出其他文字，格式：'
        '{"reply":"...","affinity_delta":0,"end":false}'
    )
    user = f"当前{now_str}，你在{place}。玩家说：{message}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_events(result_text: str) -> list[dict]:
    """解析批总结的 JSON（1~3 条 {content, importance}）；失败抛异常由调用方兜底。"""
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list) or not events:
        raise ValueError("missing events")
    out = []
    for e in events[:3]:  # 至多 3 条，防模型膨胀
        if not isinstance(e, dict) or not str(e.get("content", "")).strip():
            continue
        out.append({
            "content": str(e["content"]).strip(),
            "importance": max(1, min(10, int(e.get("importance", 3)))),
        })
    if not out:
        raise ValueError("empty events")
    return out


def _summary_messages(player_name: str, place: str, turns: list[dict]) -> list[dict]:
    turn_lines = "\n".join(f"玩家：{t['user']}\nNPC：{t['npc']}" for t in turns)
    system = (
        f"你是武侠NPC。下面是玩家与你最近的一段对话，请以你的视角，把这段交流归纳成"
        "1~3 条简短的第三人称事件摘要（每条一句话，古风白话），并各标记忆重要度（1~10整数）。"
        "只概括对你有意义、日后值得记起的事，不要虚构对话中没提到的内容。\n"
        f"这段对话发生在：{place}。\n"
        '仅输出JSON，格式：{"events":[{"content":"...","importance":5}]}'
    )
    user = f"对话记录：\n{turn_lines}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _summarize_pending(db, mem_store, npc_id: int, player_name: str, place: str,
                             turns: list[dict]) -> list[int]:
    """把窗口内未总结的对话轮批总结为 1~3 条 event 记忆并落库。

    链路 = 校验 -> 重试一次(temperature 0.3) -> 兜底（取最后一条玩家消息作单条
    importance 3）。绝不因 LLM 失败中断整段对话（AGENTS.md #4）。
    """
    events: list[dict] | None = None
    attempts = 0
    for temperature in (0.7, 0.3):
        attempts += 1
        try:
            msgs = _summary_messages(player_name, place, turns)
            cr = await mem_store.llm.chat(
                "dialogue_summary", msgs, json_format=True,
                temperature=temperature, metadata={"attempt": attempts, "temperature": temperature},
            )
            events = _parse_events(cr.text)
            break
        except Exception:
            continue
    if events is None:
        events = [{"content": (turns[-1]["user"] if turns else "与玩家闲聊了几句。"),
                   "importance": 3}]
    mids = []
    for ev in events:
        mids.append(await mem_store.add_memory(
            db, npc_id, content=ev["content"], kind="event",
            participants=[player_name], poi=place, importance=ev["importance"],
        ))
    return mids


async def talk(db, mem_store, npc_id: int, message: str) -> dict:
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise LookupError("查无此人")
    message = (message or "").strip()
    if not message:
        raise ValueError("空话一句，开不了口。")

    p = await player_mod.get(db)
    current_affinity = await npc_mod.affinity(db, npc_id)
    place = n["work"] or ""

    trace = await trace_mod.trace_turn(
        "dialogue", input_=message,
        tags=[f"npc:{n['name']}", f"npc_id:{npc_id}", f"player:{p['name']}"],
        metadata={"affinity_before": current_affinity, "place": place,
                  "rag_top_k": settings.rag_top_k},
    )

    # 记忆检索 与 最近对话 并行做（DESIGN §7.2 前台等待 2~5s）
    memories = await rag_mod.retrieve(db, mem_store, npc_id, message, settings=settings)
    turns = await rag_mod.recent_turns(db, npc_id, n=settings.dialogue_window_size)
    if trace is not None:
        await trace_mod.span(trace, "retrieve", input_=message,
                             output=[{k: m.get(k) for k in
                                      ("memory_id", "content", "score", "importance", "poi", "kind")}
                                     for m in memories],
                             metadata={"top_k": settings.rag_top_k, "rerank": "w_rel/w_rec/w_imp"})
        await trace_mod.span(trace, "recent_turns", output=turns)

    result: dict | None = None
    attempts = 0
    for temperature in (0.7, 0.3):  # 校验 -> 重试一次(temperature 0.3)
        attempts += 1
        try:
            msgs = _build_messages(p, n, current_affinity, memories, turns,
                                   shichen(mem_store.clock), place, message)
            cr = await mem_store.llm.chat("dialogue", msgs, json_format=True, temperature=temperature,
                                          metadata={"attempt": attempts, "temperature": temperature})
            result = _parse(cr.text)
            break
        except Exception:
            continue
    fallback_used = result is None
    if result is None:
        result = _fallback(n)

    delta = max(-settings.max_dialogue_delta, min(settings.max_dialogue_delta, result["affinity_delta"]))
    new_affinity = await affinity_mod.apply_delta(db, npc_id, delta)

    # 本轮流进对话表（短期窗口）；不改逐轮入忆。计数+1，达阈值或 end=true 时
    # 把该批未总结轮批总结成 event 落库（DESIGN §6.6 窗口批总结）。
    await db.execute(
        "INSERT INTO conversation(npc_id, ts_game, turns_json, affinity_delta_total)"
        " VALUES(?,?,?,?)",
        (npc_id, mem_store.clock.now(),
         json.dumps({"user": message, "npc": result["reply"]}, ensure_ascii=False), delta),
    )
    await db.execute(
        "UPDATE npc_state SET unsummarized_turns = unsummarized_turns + 1 WHERE npc_id = ?",
        (npc_id,),
    )
    pending_row = await db.fetch_one(
        "SELECT unsummarized_turns FROM npc_state WHERE npc_id = ?", (npc_id,),
    )
    pending = int((pending_row or {}).get("unsummarized_turns", 0))
    summarized = False
    if pending >= settings.event_summary_threshold or result["end"]:
        win_turns = await rag_mod.recent_turns(db, npc_id, n=pending)
        mids = await _summarize_pending(db, mem_store, npc_id, p["name"], place, win_turns)
        if trace is not None:
            await trace_mod.span(trace, "summarize_events", input_=win_turns, output=mids,
                                 metadata={"pending": pending, "threshold": settings.event_summary_threshold})
        await db.execute(
            "UPDATE npc_state SET unsummarized_turns = 0 WHERE npc_id = ?", (npc_id,),
        )
        summarized = True
    if trace is not None and not summarized:
        await trace_mod.span(trace, "defer_summary",
                             metadata={"pending": pending, "threshold": settings.event_summary_threshold})
    if trace is not None:
        await trace_mod.span(trace, "outcome",
                             metadata={"affinity_after": new_affinity, "fallback_used": fallback_used,
                                       "attempts": attempts, "end": result["end"],
                                       "summarized": summarized})

    return {
        "reply": result["reply"], "affinity": new_affinity,
        "end": result["end"], "player": await player_mod.api_dict(db, p),
    }