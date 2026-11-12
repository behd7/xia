"""NPC 对话：普通文本 + 原生工具调用，效果仍走原来的结算链。

RAG / prompt -> 流式内容与工具意图 -> 完整校验 -> 重试一次 -> 模板兜底。
回复在 SSE 中流式显示；动作、好感和结束意图只在整轮成功后结算。
送礼/切磋复用同一链路；副作用不进入生成重试路径。
"""

import asyncio
import logging
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
from . import profile as profile_mod
from . import prompt_budget as budget_mod
from . import rag as rag_mod
from . import relationship as relationship_mod
from . import trace as trace_mod
from .dialogue_tools import DIALOGUE_TOOLS, PROMPT_SPECS, REPLY_TOOL, TurnStream, parse_turn

logger = logging.getLogger(__name__)

SHICHEN_PHASES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def shichen(clock) -> str:
    hour = int(clock.now() // 3600) % 24
    return SHICHEN_PHASES[(hour + 1) // 2 % 12] + "时"


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
                {"role": "assistant", "content": (
                    result.get("reply", "") or "（你正想动手，但尚未做成。）")},
                {"role": "user", "content": (
                    f"（旁白：{report.observation}你没能做成那件事。"
                    "只说一句符合眼下情形的话，不要再带任何动作。）")},
            ],
            tools=[REPLY_TOOL], temperature=0.3,
            metadata={"stage": "act_correction", "error": report.error},
        )
        return parse_turn(cr.text, cr.tool_calls)["reply"] or None
    except Exception:
        logger.exception("act correction failed")
        return None


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
                    midterm_summaries: list[str] | None = None,
                    profile_text: str = "",
                    longterm_summaries: list[str] | None = None) -> list[dict]:
    mem_lines = "\n".join(m["content"] for m in memories[: settings.rag_top_k]) or "（暂无）"
    midterm_lines = "\n".join(f"- {s}" for s in (midterm_summaries or []))
    midterm_block = (
        f"你想起一些旧事（只作参考，不要逐字复述）：\n{midterm_lines}"
        if midterm_lines else ""
    )
    longterm_lines = "\n".join(f"- {s}" for s in (longterm_summaries or []))
    longterm_block = (
        f"你记得更早年间的事（只是些旧印象，别当成刚发生的）：\n{longterm_lines}"
        if longterm_lines else ""
    )
    # 中期 / 画像 / 长期共享一个总预算（docs/MEMORY-V2-PLAN.md §5.3），按此优先级装填。
    extra_memory = budget_mod.fit_blocks(
        [("midterm", midterm_block), ("profile", profile_text),
         ("longterm", longterm_block)],
        settings.memory_context_max_chars)
    extra_block = f"{extra_memory}\n" if extra_memory else ""
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
        f"{extra_block}"
        f"{events_line}"
        f"你们最近的往来：\n{turn_lines}\n"
        f"{heart_line}{agenda_line}{examples_line}{mood_line}"
        f"此刻{now_str}，你正在{place}。\n"
        f"{PROMPT_SPECS}\n"
        f"以你的口吻应答{p['name']}，按工具说明表达关系变化与行动意图。\n"
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
    profile_text = await profile_mod.build(db, npc_id, p["name"], now_game)
    longterms = await mem_store.longterm.recall(
        db, mem_store, npc_id, query_text=midterm_query, query_vec=midterm_qvec,
        turn_no=await mem_store.ledger_turn(db, npc_id))
    longterm_summaries = [h["content"] for h in longterms]
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
        await trace_mod.span(trace, "profile", output={"text": profile_text})
        await trace_mod.span(trace, "relationship", output={"summary": impression})

    result: dict | None = None
    attempts = 0
    turn_id = uuid.uuid4().hex
    last_msgs: list[dict] = []
    for temperature in (0.7, 0.3):  # 校验 -> 重试一次(temperature 0.3)
        attempts += 1
        stream = TurnStream()
        try:
            msgs = _build_messages(
                p, n, current_affinity, memories, turns, impression,
                shichen(mem_store.clock), place, message, world_brief,
                agenda=agenda, mood_text=mood_text,
                speech_examples=n.get("speech_examples", []), heart=heart,
                events_text=events_text, midterm_summaries=midterm_summaries,
                profile_text=profile_text, longterm_summaries=longterm_summaries,
            )
            last_msgs = msgs
            async for chunk in mem_store.llm.chat_stream(
                    purpose, msgs, tools=DIALOGUE_TOOLS, temperature=temperature,
                    metadata={"attempt": attempts, "temperature": temperature}):
                reset, delta = stream.feed(chunk)
                if reset:
                    yield "reset", {}
                if delta:
                    yield "token", {"text": delta}
            result = stream.finish()
            # Whitespace trimming / explicit reply overriding ordinary content
            # must leave displayed SSE text identical to the settled reply.
            if stream.sent != result["reply"]:
                yield "reset", {}
                if result["reply"]:
                    yield "token", {"text": result["reply"]}
            break
        except Exception:
            logger.warning("dialogue generation rejected npc=%d attempt=%d",
                           npc_id, attempts, exc_info=True)
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
