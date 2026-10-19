"""日常互动判断值 + 对话窗口蒸馏成 event（docs/DESIGN.md §6.6）。

机制：每个 NPC 维护 `interacts_since_summary` 判断值，**每次日常互动**（对话、
送礼、切磋、大额消费）都 +1；对话轮只进 `conversation` 短期窗口（原始文本，
不逐轮入忆）。当判断值达到 `event_summary_threshold`（或某次对话 end=true 提前
收尾）时，触发一次"反思"：把 `last_summary_conv_id` 水位线以来的未蒸馏对话轮，
一次 LLM 归纳成 1~3 条 `kind=event` 记忆落库，然后清零判断值、推进水位线。

Gift/切磋/大额消费的规则事件仍各自即时入忆（§6.3 直接交互），它们只累加判断值、
不产生新的对话素材。事件重要度由 LLM 给出（4B 恒偏高，见项目笔记），此处不调节。
"""

import json
import re

from ..config import settings
from . import trace as trace_mod


def _parse_events(result_text: str) -> list[dict]:
    """解析批总结 JSON（1~3 条 {content, importance}）；失败抛异常由调用方兜底。"""
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


async def _distill_window(db, mem_store, npc_id: int, player_name: str, place: str,
                          low_id: int) -> int:
    """把 conversation.id>low_id 的未蒸馏对话轮归纳成 1~3 条 event 落库，返回新水位线。

    校验 -> 重试一次(temperature 0.3) -> 兜底（取最后一条玩家消息作单条
    importance 3）。绝不因 LLM 失败中断玩法（AGENTS.md #4）。窗口为空则原样返回。
    """
    rows = await db.fetch_all(
        "SELECT id, turns_json FROM conversation WHERE npc_id = ? AND id > ? ORDER BY id",
        (npc_id, low_id),
    )
    turns: list[dict] = []
    max_id = low_id
    for r in rows:
        max_id = r["id"]
        try:
            d = json.loads(r["turns_json"])
            turns.append({"user": d.get("user", ""), "npc": d.get("npc", "")})
        except Exception:
            continue
    if not turns:
        return low_id  # 只有礼物/切磋等规则互动、无对话素材可蒸馏

    events: list[dict] | None = None
    attempts = 0
    for temperature in (0.7, 0.3):
        attempts += 1
        try:
            msgs = _summary_messages(player_name, place, turns)
            cr = await mem_store.llm.chat(
                "dialogue_summary", msgs, json_format=True,
                temperature=temperature, metadata={"npc_id": npc_id, "attempt": attempts},
            )
            events = _parse_events(cr.text)
            break
        except Exception:
            continue
    if events is None:
        events = [{"content": turns[-1]["user"] if turns else "与玩家闲聊了几句。",
                   "importance": 3}]
    for ev in events:
        await mem_store.add_memory(
            db, npc_id, content=ev["content"], kind="event",
            participants=[player_name], poi=place, importance=ev["importance"],
        )
    return max_id


async def bump(db, mem_store, npc_id: int, player_name: str, place: str, *,
               force: bool = False, trace=None) -> bool:
    """每次日常互动后调用：判断值+1，达阈值(end=force)则蒸馏窗口并清零。

    返回是否真的触发了一次蒸馏。gift/切磋/大额消费的规则事件已由各自落库，
    这里只把它们计入判断值。
    """
    await db.execute(
        "UPDATE npc_state SET interacts_since_summary = interacts_since_summary + 1"
        " WHERE npc_id = ?",
        (npc_id,),
    )
    row = await db.fetch_one(
        "SELECT interacts_since_summary, last_summary_conv_id FROM npc_state WHERE npc_id = ?",
        (npc_id,),
    )
    count = int((row or {}).get("interacts_since_summary", 0))
    low_id = int((row or {}).get("last_summary_conv_id", 0))
    if count < settings.event_summary_threshold and not force:
        return False
    low_id = await _distill_window(db, mem_store, npc_id, player_name, place, low_id)
    await db.execute(
        "UPDATE npc_state SET interacts_since_summary = 0, last_summary_conv_id = ?"
        " WHERE npc_id = ?",
        (low_id, npc_id),
    )
    if trace is not None:
        await trace_mod.span(trace, "interact_bump",
                             metadata={"count": count, "summarized": True,
                                       "threshold": settings.event_summary_threshold})
    return True