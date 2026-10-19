"""自由对话（docs/DESIGN.md §6.6）：POST /api/npc/{id}/talk。

单次 LLM 调用（qwen3:4b-instruct, format:json, 禁思考），从单回合回到：
记忆检索（RAG）+ 最近对话平铺 并行做 -> prompt -> 校验 JSON -> 重试一次
(temperature 0.3) -> 模板兜底。绝不让模型输出失败阻塞游戏（AGENTS.md #4）。
"""

import json
import re

from ..config import settings
from ..game import player as player_mod
from . import npc as npc_mod
from . import rag as rag_mod
from . import reflect as reflect_mod
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


def _warmth(affinity: int, name: str) -> str:
    """把好感档位转成第一人称的交情描述，避免在 prompt 里出现"好感度/玩家"这类词的机械设定。"""
    if affinity < 30:
        return f"你与{name}素无深交，不过点头之交。"
    if affinity < 60:
        return f"你与{name}算得上相熟，往来客气。"
    if affinity < 70:
        return f"你与{name}颇为投缘，来往热络。"
    if affinity < 85:
        return f"你与{name}已是亲近，颇有交情。"
    return f"你与{name}情谊甚笃，信得过。"


def _build_messages(p, n, affinity, memories, turns, now_str, place, message) -> list[dict]:
    mem_lines = "\n".join(m["content"] for m in memories[: settings.rag_top_k]) or "（暂无）"
    turn_lines = "\n".join(f"{p['name']}：{t['user']}\n你：{t['npc']}" for t in turns) or "（尚无可聊）"
    system = (
        f"你是{n['name']}。{n['persona']}你说话{n['speech_style']}。\n"
        f"{_warmth(affinity, p['name'])}\n"
        f"你记得的一些事：\n{mem_lines}\n"
        f"你们最近的往来：\n{turn_lines}\n"
        f"此刻{now_str}，你正在{place}。\n"
        f"以你的口吻应答{p['name']}（≤80字）。并给出这次你与{p['name']}的关系变化"
        "（-2~+3整数）、是否想就此收住话头（BOOL）。\n"
        '仅输出JSON，不要输出其他文字：{"reply":"...","affinity_delta":0,"end":false}'
    )
    user = f"{p['name']}说：{message}"
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
    new_affinity = await npc_mod.apply_delta(db, npc_id, delta)

    # 本轮流进对话表（短期窗口）；不改逐轮入忆。计数+1，达阈值或 end=true 时
    # 把该批未总结轮批总结成 event 落库（DESIGN §6.6 窗口批总结）。
    await db.execute(
        "INSERT INTO conversation(npc_id, ts_game, turns_json, affinity_delta_total)"
        " VALUES(?,?,?,?)",
        (npc_id, mem_store.clock.now(),
         json.dumps({"user": message, "npc": result["reply"]}, ensure_ascii=False), delta),
    )
    # 互动判断值+1；对话 end 即使未满阈值也提前蒸馏。蒸馏逻辑在 reflect.py
    # （DESIGN §6.6：闲聊只进短期窗口，达阈值才反思成 event 入忆）。
    summarized = await reflect_mod.bump(db, mem_store, npc_id, p["name"], place,
                                        force=result["end"], trace=trace)
    if trace is not None:
        await trace_mod.span(trace, "outcome",
                             metadata={"affinity_after": new_affinity, "fallback_used": fallback_used,
                                       "attempts": attempts, "end": result["end"],
                                       "summarized": summarized})

    return {
        "reply": result["reply"], "affinity": new_affinity,
        "end": result["end"], "player": await player_mod.api_dict(db, p),
    }