"""自由对话（docs/DESIGN.md §6.6）：POST /api/npc/{id}/talk（SSE 流式）。

单次 LLM 调用（qwen3:4b-instruct, format:json, 禁思考），从单回合回到：
记忆检索（RAG）+ 最近对话平铺 并行做 -> prompt -> 流式 JSON -> 校验 -> 重试一次
(temperature 0.3) -> 模板兜底。绝不让模型输出失败阻塞游戏（AGENTS.md #4）。

仍是**一次**前台调用（M5 预算）：模型输出 JSON，`reply` 字段边到边解码成
SSE token，`affinity_delta`/`end` 在流结束后解析，最后发 `done`。
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator

from ..config import settings
from ..game import plan as plan_mod
from ..game import player as player_mod
from ..game import schedule as schedule_mod
from ..game import world as world_mod
from ..game import world_state as world_state_mod
from . import agenda as agenda_mod
from . import events as events_mod
from . import mood as mood_mod
from . import npc as npc_mod
from . import rag as rag_mod
from . import relationship as relationship_mod
from . import trace as trace_mod
from .json_utils import strip_fence

logger = logging.getLogger(__name__)

SHICHEN_PHASES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def shichen(clock) -> str:
    hour = int(clock.now() // 3600) % 24
    return SHICHEN_PHASES[(hour + 1) // 2 % 12] + "时"


def _parse(result_text: str) -> dict:
    """解析 LLM 的 JSON 输出；失败抛异常由调用方走重试/兜底。"""
    data = json.loads(strip_fence(result_text))
    if not isinstance(data, dict) or "reply" not in data:
        raise ValueError("missing reply")
    return {
        "reply": str(data.get("reply", "")) or "……",
        "affinity_delta": int(data.get("affinity_delta", 0)),
        "end": bool(data.get("end", False)),
    }


def _fallback(n: dict) -> dict:
    return {"reply": f"{n['name']}沉吟片刻，不置可否。", "affinity_delta": 0, "end": False}


_REPLY_KEY = re.compile(r'"reply"\s*:\s*"')
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
            "n": "\n", "r": "\r", "t": "\t"}


def _reply_prefix(text: str) -> str | None:
    """Decode the already-complete prefix of the JSON `reply` string.

    Returns None until the key and opening quote arrive. A trailing incomplete
    escape (or a lone high surrogate whose pair may still come) is held back —
    the next chunk completes it, so the returned value only ever grows.
    """
    m = _REPLY_KEY.search(text)
    if m is None:
        return None
    out: list[str] = []
    i = m.end()
    while i < len(text):
        c = text[i]
        if c == '"':
            return "".join(out)
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(text):
            break
        esc = text[i + 1]
        if esc == "u":
            if i + 6 > len(text) or not _HEX4.fullmatch(text[i + 2:i + 6]):
                break
            code = int(text[i + 2:i + 6], 16)
            i += 6
            if 0xD800 <= code <= 0xDBFF:
                # A surrogate pair may be split across chunks; wait for the low half.
                if not (text.startswith("\\u", i) and i + 6 <= len(text)):
                    break
                low_hex = text[i + 2:i + 6]
                if not _HEX4.fullmatch(low_hex):
                    break
                low = int(low_hex, 16)
                if not 0xDC00 <= low <= 0xDFFF:
                    break
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                i += 6
                continue
            out.append(chr(code))
            continue
        if esc not in _ESCAPES:
            break
        out.append(_ESCAPES[esc])
        i += 2
    return "".join(out)


class _ReplyExtractor:
    """Turn streamed JSON fragments into `reply` text deltas for SSE."""

    def __init__(self) -> None:
        self._raw = ""
        self._sent = ""

    def feed(self, chunk: str) -> str:
        self._raw += chunk
        value = _reply_prefix(self._raw)
        if value is None or not value.startswith(self._sent):
            return ""
        delta = value[len(self._sent):]
        self._sent = value
        return delta


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


def _build_messages(p, n, affinity, memories, turns, impression, now_str, place, message,
                    world_brief: str = "", agenda: dict | None = None,
                    mood_text: str = "", speech_examples: list[str] | None = None,
                    heart: str = "", events_text: str = "",
                    midterm_summaries: list[str] | None = None) -> list[dict]:
    mem_lines = "\n".join(m["content"] for m in memories[: settings.rag_top_k]) or "（暂无）"
    midterm_lines = "\n".join(f"- {s}" for s in (midterm_summaries or []))
    midterm_block = (
        f"你想起一些旧事（只作参考，不要逐字复述）：\n{midterm_lines}\n"
        if midterm_lines else ""
    )
    turn_lines = "\n".join(f"{p['name']}：{t['user']}\n你：{t['npc']}" for t in turns) or "（尚无可聊）"
    events_line = f"近年附近发生的事：\n{events_text}\n" if events_text else ""
    heart_line = f"你眼下挂心的事：{heart}\n" if heart else ""
    agenda_line = ""
    if agenda:
        agenda_line = f"你此刻的盘算：{agenda.get('text', '')}\n"
        if agenda.get("avoid"):
            agenda_line += f"你不愿提：{agenda['avoid']}\n"
    examples_line = ""
    if speech_examples:
        quoted = "".join(f"“{example}”" for example in speech_examples)
        examples_line = f"你说话的样子：参考这些语气，不要逐字照抄：{quoted}\n"
    mood_line = f"你此刻的心气：{mood_text}\n" if mood_text else ""
    art_line = npc_mod.art_brief(n)
    system = (
        f"你是{n['name']}。{n['persona']}你说话{n['speech_style']}。\n"
        f"{art_line}"
        f"{world_brief}\n"
        f"{_warmth(affinity, p['name'])}\n"
        + (f"你对{p['name']}的印象：{impression}\n" if impression else "")
        + f"你记得的一些事：\n{mem_lines}\n"
        f"{midterm_block}"
        f"{events_line}"
        f"你们最近的往来：\n{turn_lines}\n"
        f"{heart_line}{agenda_line}{examples_line}{mood_line}"
        f"此刻{now_str}，你正在{place}。\n"
        f"以你的口吻应答{p['name']}（≤80字）。并给出这次你与{p['name']}的关系变化"
        "（-2~+3整数）、是否想就此收住话头（BOOL）。\n"
        '仅输出JSON，不要输出其他文字：{"reply":"...","affinity_delta":0,"end":false}\n'
        "不要复述上面的设定，只在相关或你想推进话题时提起。\n"
        "不知道的事就岔开、装糊涂或反问，不要编造新人物、新宝物、新门派。"
    )
    user = f"{p['name']}说：{message}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def talk_stream(db, mem_store, npc_id: int, message: str, *,
                      purpose: str = "dialogue",
                      pause: bool = True) -> AsyncIterator[tuple[str, dict]]:
    """One turn as ``(event, payload)`` pairs for the SSE route.

    Events: ``start`` -> ``token``* -> ``done``; ``reset`` asks the client to
    drop the text streamed so far (the parse-failed attempt is being retried or
    the template fallback takes over). Gift/spar reuse this with
    ``purpose="gift"/"spar"`` and ``pause=False``; the final payload includes
    ``{reply, affinity, end, player, fallback, place, stale}``.
    """
    generation = mem_store.generation  # 对话开始时的局代；新局后旧回合作废
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise LookupError("查无此人")
    message = (message or "").strip()
    if not message:
        raise ValueError("空话一句，开不了口。")
    # Flush headers/status immediately: RAG and the first model token take seconds.
    yield "start", {"npc": n["name"]}

    p = await player_mod.get(db)
    current_affinity = await npc_mod.affinity(db, npc_id)
    now_game = mem_store.clock.now()
    now_real = time.time()
    stage = await world_state_mod.stage(db)
    world_brief = world_mod.brief_for(n, message, stage, current_affinity, p["name"])
    agenda = await agenda_mod.current_or_pick(
        db, npc_id, str(n.get("slug", "")), now_game, now_real)
    mood = await mood_mod.current(db, npc_id, now_real)
    mood_text = str(mood.get("text", "")) if mood else ""
    heart_row = await plan_mod.plan_for(db, npc_id, now_game)
    heart = str(heart_row["heart"]) if heart_row else ""
    place = schedule_mod.poi_label_for(
        n, now_game, await plan_mod.deviation_for(db, npc_id, now_game))
    nearby = events_mod.recent(mem_store, now_game, poi=place, limit=3)
    events_text = "\n".join(f"- {event['text']}" for event in nearby)
    # 对话期间标记暂停：八卦扫描跳过、debug 可见；超时 5 现实分自动失效（DESIGN §6.10）。
    if pause:
        await db.execute(
            "UPDATE npc_state SET paused_until = ? WHERE npc_id = ?",
            (time.time() + 300, npc_id),
        )

    trace = await trace_mod.trace_turn(
        purpose, input_=message,
        tags=[f"npc:{n['name']}", f"npc_id:{npc_id}", f"player:{p['name']}"],
        metadata={"affinity_before": current_affinity, "place": place,
                  "rag_top_k": settings.rag_top_k, "agenda": agenda["id"],
                  "mood": mood["mood"] if mood else None},
    )

    # 查询向量只算一次：Chroma RAG 与中期线索匹配共用（DESIGN §7.2 前台等待 2~5s）
    qvec, turns, impression = await asyncio.gather(
        rag_mod.embed_query(mem_store, message),
        rag_mod.recent_turns(db, npc_id, n=settings.dialogue_window_size),
        relationship_mod.summary_for(db, npc_id, "player", p["name"]),
    )
    memories = await rag_mod.retrieve(
        db, mem_store, npc_id, message, settings=settings, qvec=qvec)
    midterms = mem_store.match_midterms(npc_id, qvec)
    midterm_summaries = [m.summary for m in midterms]
    if trace is not None:
        await trace_mod.span(trace, "retrieve", input_=message,
                             output=[{k: m.get(k) for k in
                                      ("memory_id", "content", "score", "importance", "poi", "kind")}
                                     for m in memories],
                             metadata={"top_k": settings.rag_top_k, "rerank": "w_rel/w_rec/w_imp"})
        await trace_mod.span(trace, "midterm", output=[
            {"summary": m.summary, "clues": m.clues, "turn_count": m.turn_count}
            for m in midterms])
        await trace_mod.span(trace, "recent_turns", output=turns)
        await trace_mod.span(trace, "relationship", output={"summary": impression})

    result: dict | None = None
    attempts = 0
    for temperature in (0.7, 0.3):  # 校验 -> 重试一次(temperature 0.3)
        attempts += 1
        raw: list[str] = []
        extractor = _ReplyExtractor()
        try:
            msgs = _build_messages(
                p, n, current_affinity, memories, turns, impression,
                shichen(mem_store.clock), place, message, world_brief,
                agenda=agenda, mood_text=mood_text,
                speech_examples=n.get("speech_examples", []), heart=heart,
                events_text=events_text, midterm_summaries=midterm_summaries,
            )
            async for chunk in mem_store.llm.chat_stream(
                    purpose, msgs, json_format=True, temperature=temperature,
                    metadata={"attempt": attempts, "temperature": temperature}):
                raw.append(chunk)
                delta = extractor.feed(chunk)
                if delta:
                    yield "token", {"text": delta}
            result = _parse("".join(raw))
            break
        except Exception:
            # The retry restarts the reply; tell the client to drop partial text.
            yield "reset", {}
            continue
    fallback_used = result is None
    if result is None:
        result = _fallback(n)
        yield "token", {"text": result["reply"]}

    stale = mem_store.generation != generation
    outcome = None
    if stale:
        logger.info("discard stale turn npc=%d gen=%d current=%d",
                    npc_id, generation, mem_store.generation)
        new_affinity = current_affinity
        if pause:
            await db.execute(
                "UPDATE npc_state SET paused_until = NULL WHERE npc_id = ?", (npc_id,)
            )
        end = False
    else:
        delta = max(-settings.max_dialogue_delta,
                    min(settings.max_dialogue_delta, result["affinity_delta"]))
        new_affinity = await npc_mod.apply_delta(db, npc_id, delta)
        if result["end"]:
            # 先恢复暂停：中期总结失败也不能让 NPC 一直处于暂停。
            if pause:
                await db.execute(
                    "UPDATE npc_state SET paused_until = NULL WHERE npc_id = ?", (npc_id,)
                )
            await agenda_mod.clear(db, npc_id)
            if new_affinity < 30:
                try:
                    await mood_mod.set(db, npc_id, "impatient", "话不投机", 20)
                except Exception:
                    logger.exception("mood update failed for talk npc=%d", npc_id)
        outcome = await mem_store.add_conversation_turn(
            db, npc_id,
            user=message, npc=result["reply"], npc_name=n["name"],
            player_name=p["name"], place=place,
            affinity_delta=delta, generation=generation,
        )
        stale = outcome.stale
        end = result["end"] if not stale else False
    summarized = outcome.midterm_created if outcome else False
    longterm_scheduled = outcome.longterm_scheduled if outcome else False
    if trace is not None:
        await trace_mod.span(trace, "outcome",
                             metadata={"affinity_after": new_affinity, "fallback_used": fallback_used,
                                       "attempts": attempts, "end": end,
                                       "summarized": summarized,
                                       "longterm_scheduled": longterm_scheduled,
                                       "stale": stale})

    player_payload = await player_mod.api_dict(db, p)
    if stale:
        current = await player_mod.get(db)
        if current is not None:
            player_payload = await player_mod.api_dict(db, current)

    yield "done", {
        "reply": result["reply"], "affinity": new_affinity,
        "end": end, "player": player_payload,
        "fallback": fallback_used, "place": place, "stale": stale,
    }


async def talk(db, mem_store, npc_id: int, message: str, *,
               purpose: str = "dialogue", pause: bool = True) -> dict:
    """Non-streaming wrapper (tools/tests): drain the stream, return the done payload."""
    payload: dict | None = None
    async for event, data in talk_stream(
            db, mem_store, npc_id, message, purpose=purpose, pause=pause):
        if event == "done":
            payload = data
    if payload is None:
        raise RuntimeError("dialogue stream ended without done")
    return payload
