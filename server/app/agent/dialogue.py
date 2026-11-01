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
import uuid
from collections.abc import AsyncIterator

from ..config import settings
from ..game import plan as plan_mod
from ..game import player as player_mod
from ..game import schedule as schedule_mod
from ..game import world as world_mod
from ..game import world_state as world_state_mod
from . import actions as acts_mod
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
    """解析 LLM 的 JSON 输出；失败抛异常由调用方走重试/兜底。

    `reply` 可以为空（只行动或沉默），`acts` 可以省略（只说话）；但两者不能都空，
    否则视为解析失败走重试。
    """
    data = json.loads(strip_fence(result_text))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    acts = data.get("acts")
    if acts is None:
        acts = []
    if not isinstance(acts, list):
        raise ValueError("acts must be a list")
    reply = str(data.get("reply") or "").strip()
    clean_acts = [act for act in acts if isinstance(act, dict)]
    if not reply and not clean_acts:
        raise ValueError("empty reply")
    return {
        "reply": reply,
        "affinity_delta": int(data.get("affinity_delta", 0) or 0),
        "end": bool(data.get("end", False)),
        "acts": clean_acts,
    }


def _fallback(n: dict) -> dict:
    return {"reply": f"{n['name']}沉吟片刻，不置可否。", "affinity_delta": 0,
            "end": False, "acts": []}


async def _correct_after_reject(mem_store, msgs: list[dict], result: dict,
                                report) -> str | None:
    """动作被拒后的一次「改口」：只重生成文本，输出里的 acts 一律丢弃。

    这是前台唯一允许的额外调用（仍用 dialogue purpose，不新增 purpose），
    只在动作合法性校验失败时触发；执行的副作用永不重跑。
    """
    try:
        cr = await mem_store.llm.chat(
            "dialogue",
            [
                *msgs,
                {"role": "assistant", "content": json.dumps(
                    {"reply": result.get("reply", ""), "acts": result.get("acts", [])},
                    ensure_ascii=False)},
                {"role": "user", "content": (
                    f"（旁白：{report.observation}你没能做成那件事。"
                    "只说一句符合眼下情形的话，不要再带任何动作。）")},
            ],
            json_format=True, temperature=0.3,
            metadata={"stage": "act_correction", "error": report.error},
        )
        data = json.loads(strip_fence(cr.text))
        reply = str((data or {}).get("reply") or "").strip()
        return reply or None
    except Exception:
        logger.exception("act correction failed")
        return None


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


def _midterm_query(window_turns: list[dict], message: str) -> str:
    """中期召回 query：窗口内最近若干条消息 + 本轮原话，截尾到字数上限。

    0.8 的阈值只在长文本 query 上成立（MaiBot 用 2400 字上限），所以这里不用
    玩家单句——短句 query 会让阈值形同虚设（docs/MEMORY-V2-PLAN.md §12）。
    """
    lines = [f"玩家：{turn.get('user', '')}\n你：{turn.get('npc', '')}"
             for turn in window_turns[-max(1, settings.midterm_query_message_limit):]]
    lines.append(f"玩家：{message}")
    return "\n".join(lines)[-max(200, settings.midterm_query_max_chars):]


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
    turn_lines = "\n".join(
        f"{p['name']}：{t['user']}\n你：{t['npc']}{acts_mod.acts_text(t.get('acts') or [], '你')}"
        for t in turns) or "（尚无可聊）"
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
        f"{acts_mod.PROMPT_SPECS}\n"
        f"以你的口吻应答{p['name']}（≤80字，也可以不作声）。并给出这次你与{p['name']}的关系变化"
        "（-2~+3整数）、是否想就此收住话头（BOOL）。\n"
        '仅输出JSON，不要输出其他文字：{"reply":"...","acts":[],'
        '"affinity_delta":0,"end":false}\n'
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

    # RAG 用本轮原话；中期召回用「最近 N 条消息拼接」的长 query（docs/MEMORY-V2-PLAN.md §5.1），
    # 两者由同一次批量 embed 产出，前台仍只有一次嵌入往返。
    window_turns, impression = await asyncio.gather(
        rag_mod.recent_turns(
            db, npc_id,
            n=max(settings.midterm_query_message_limit, settings.dialogue_window_size)),
        relationship_mod.summary_for(db, npc_id, "player", p["name"]),
    )
    turns = window_turns[-max(1, settings.dialogue_window_size):]
    midterm_query = _midterm_query(window_turns, message)
    qvec, midterm_qvec = (await mem_store.llm.embed([message, midterm_query]))[:2]
    memories = await rag_mod.retrieve(
        db, mem_store, npc_id, message, settings=settings, qvec=qvec)
    midterms = await mem_store.recall_midterm(db, npc_id, midterm_qvec)
    midterm_summaries = [m.summary for m in midterms]
    if trace is not None:
        await trace_mod.span(trace, "retrieve", input_=message,
                             output=[{k: m.get(k) for k in
                                      ("memory_id", "content", "score", "importance", "poi", "kind")}
                                     for m in memories],
                             metadata={"top_k": settings.rag_top_k, "rerank": "w_rel/w_rec/w_imp"})
        await trace_mod.span(trace, "midterm", input_=midterm_query,
                             output=[{"summary": m.summary, "clues": m.clues,
                                      "turn_count": m.turn_count, "score": round(m.score, 4)}
                                     for m in midterms],
                             metadata={"threshold": settings.midterm_min_score,
                                       "top_k": settings.midterm_top_k})
        await trace_mod.span(trace, "recent_turns", output=turns)
        await trace_mod.span(trace, "relationship", output={"summary": impression})

    result: dict | None = None
    attempts = 0
    turn_id = uuid.uuid4().hex
    last_msgs: list[dict] = []
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
            last_msgs = msgs
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

    # 动作：先全量校验再执行，失败只「改口」一次。副作用永不进重试路径——
    # 一旦执行成功就不能再重跑整轮（docs/MEMORY-V2-PLAN.md §6.3）。
    executed: list[dict] = []
    if result["acts"]:
        if mem_store.generation != generation:
            result["acts"] = []  # 新局已换，动作一律不执行
        else:
            report = await acts_mod.execute(
                db, mem_store, npc=n, player=p, affinity=current_affinity,
                acts=result["acts"], place=place, turn_id=turn_id)
            if report.ok:
                executed = report.acted
            else:
                logger.info("act batch rejected npc=%d turn=%s: %s",
                            npc_id, turn_id, report.error)
                corrected = await _correct_after_reject(
                    mem_store, last_msgs, result, report)
                if corrected:
                    yield "reset", {}
                    yield "token", {"text": corrected}
                    result["reply"] = corrected
                result["acts"] = []

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
            acts=executed, turn_id=turn_id,
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
                                       "acts": [a["tool"] for a in executed],
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
        "acts": [{"tool": act["tool"], "text": act["text"]} for act in executed],
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
