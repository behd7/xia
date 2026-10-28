#!/usr/bin/env python3
"""关键路径冒烟测试（无需 Ollama / Godot / pytest，只用已经装的 httpx）。

以隔离的临时 SQLite + Chroma 目录启动真实的 FastAPI app（走 main.py 的
lifespan：连库 -> seed -> 时钟 -> reconcile -> 注入），并 stub 掉 LLM 客户端，
沿 HTTP 打一遍关键流程：建档 -> 分配 -> 买药 -> 打狼 -> 送礼写记忆 -> 对话。
本文件是安全网：重构改完必须仍能全绿通过。

用法（server 目录下）：
    server/.venv/bin/python tests/smoke.py
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import httpx

# 必须早于 `app.main` 的 import 设置，因为 main 在模块级用 settings 构造 Database。
_tmp = tempfile.mkdtemp(prefix="xia_smoke_")
os.environ["DB_PATH"] = str(Path(_tmp) / "xia.db")
os.environ["CHROMA_DIR"] = str(Path(_tmp) / "chroma")
os.environ["SCHEDULER_ENABLED"] = "false"
# 记忆分层的断言依赖这些值：显式 pin，避免被 server/.env 覆盖。
os.environ["CONVERSATION_WINDOW_CAPACITY"] = "10"
# 用较小的中期上限在 36 轮内验证 FIFO，不必跑到 110 轮。
os.environ["MIDTERM_CAPACITY"] = "2"
os.environ["MIDTERM_TOP_K"] = "3"
os.environ["MIDTERM_MIN_SCORE"] = "0.45"
os.environ["LONGTERM_TURN_INTERVAL"] = "36"

# 把 server 根（含 `app` 包）放进 path，保证从任意 cwd 都能跑。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAIL = 0
CALLED: list[str] = []
CALLS: list[tuple[str, int | None]] = []
PROMPTS: list[tuple[str, list[dict]]] = []
HEART_MODE = {"value": "ok"}


def check(label: str, cond: bool) -> None:
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


async def talk(client, npc_id: int, message: str):
    """Consume the SSE dialogue route into (status, events, done payload)."""
    events: list[tuple[str, dict]] = []
    name: str | None = None
    data_lines: list[str] = []
    async with client.stream("POST", f"/api/npc/{npc_id}/talk",
                             json={"message": message}) as r:
        status = r.status_code
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            elif line == "":
                if name is not None:
                    events.append((name, json.loads("\n".join(data_lines) or "{}")))
                name, data_lines = None, []
    done = next((data for event, data in reversed(events) if event == "done"), None)
    return status, events, done


async def main() -> int:
    from app.main import app, ollama
    from app.config import settings
    from app.game import data
    from app.agent import gossip, npc as npc_mod, reflection, relationship

    # 竞态测试门禁：让对话/长期总结停在 LLM 里，模拟新局穿插。
    GATE = {"reached": asyncio.Event(), "release": asyncio.Event()}
    GATE_LT = {"reached": asyncio.Event(), "release": asyncio.Event()}

    # -- stub LLM：chat 按 purpose 返回确定 JSON；embed 返回确定向量（仅测通路） --
    async def fake_chat(purpose, messages, *, json_format=False, temperature=0.7,
                        metadata=None, priority=None):
        CALLED.append(purpose)
        CALLS.append((purpose, priority))
        PROMPTS.append((purpose, messages))
        if purpose == "dialogue":
            if "慢速对话" in messages[-1]["content"]:
                GATE["reached"].set()
                await GATE["release"].wait()
            end = "告辞" in messages[-1]["content"]
            text = json.dumps({"reply": "阁下所问，某自有计较。",
                               "affinity_delta": 2, "end": end}, ensure_ascii=False)
        elif purpose == "dialogue_summary":
            system = messages[0]["content"]
            if "长期记忆" in system:
                if "门禁长期测试" in system:
                    GATE_LT["reached"].set()
                    await GATE_LT["release"].wait()
                text = json.dumps({"summary": "三十六轮往来已归档。"},
                                  ensure_ascii=False)
            else:
                match = re.search(r"闲谈第(\d+)轮", system)
                first = int(match.group(1)) if match else 0
                text = json.dumps(
                    {"summary": f"闲谈第{first}轮起归档。",
                     "clues": ["后山", "剑法"]},
                    ensure_ascii=False,
                )
        elif purpose == "gift":
            text = json.dumps({"reply": "这份心意，我收下了。", "affinity_delta": 2},
                              ensure_ascii=False)
        elif purpose == "spar":
            text = json.dumps({"reply": "你的身手，我记下了。", "affinity_delta": 2},
                              ensure_ascii=False)
        elif purpose == "reflection":
            text = ('{"insights":[{"content":"冒烟侠屡次来问剑，心性倒是坚定。"}],'
                    '"affinity_delta":1}')
        elif purpose == "relationship":
            text = '{"summary":"此人常来走动，是个有心人。"}'
        elif purpose == "gossip":
            text = '{"line":"那小子昨日在后山崖办了黑旋风。"}'
        elif purpose == "greet":
            text = '{"reply":"别来无恙？今日可有好酒？"}'
        elif purpose == "heart":
            if HEART_MODE["value"] == "fail":
                text = "not json"
            else:
                text = ('{"heart":"后山那伙人总不散，叫人心里不踏实。",'
                        '"reason":"镇上人人自危","deviation":"gather_early","minutes":10}')
        else:
            text = "{}"
        return type("R", (), {"text": text, "in_tokens": 10, "out_tokens": 8})()

    async def fake_chat_stream(purpose, messages, *, json_format=False, temperature=0.7,
                               metadata=None, priority=None):
        result = await fake_chat(purpose, messages, json_format=json_format,
                                 temperature=temperature, metadata=metadata, priority=priority)
        for i in range(0, len(result.text), 7):
            yield result.text[i:i + 7]

    def _fake_vec(text: str) -> list[float]:
        buckets = (
            ("后山", 0), ("剑法", 0),
            ("闲谈", 1),
            ("慢速", 2),
            ("门禁", 3),
            ("近来", 4),
        )
        idx = next((i for key, i in buckets if key in text), 5)
        vec = [0.0] * 1024
        vec[idx] = 1.0
        return vec

    async def fake_embed(texts):
        return [_fake_vec(t) for t in texts]  # 1024 维单位向量，按关键词分桶

    async def fake_tags():
        return ["qwen3:4b-instruct", "qwen3-embedding:0.6b"]

    ollama.chat, ollama.chat_stream, ollama.embed, ollama.tags = (
        fake_chat, fake_chat_stream, fake_embed, fake_tags)

    async with app.router.lifespan_context(app):
        import app.main as main_mod  # lifespan 之后才能取到真实 db/mem_store

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            print("\n== health ==")
            h = (await c.get("/api/health")).json()
            check("status ok", h["status"] == "ok")
            check("db ok", h["db"] is True)
            check("npcs_seeded=7", h["npcs_seeded"] is True)

            print("\n== new game ==")
            r = await c.post("/api/game/new", json={"name": "冒烟侠"})
            p = r.json()["player"]
            check("new game creates player", r.status_code == 200 and p["name"] == "冒烟侠")
            check("starts with HP>0 & gold 100", p["hp"] > 0 and p["gold"] == 100)
            check("fresh char free_points==0 (现状)", p["free_points"] == 0)

            print("\n== npcs/state ==")
            sched = (await c.get("/api/npcs/state")).json()
            check("seven scheduled NPCs", len(sched["npcs"]) == 7)
            check("nine schedule phases", len(sched["phases"]) == 9)
            check("every NPC has both routes", all(
                n["home_to_work"] and n["work_to_gather"] for n in sched["npcs"]))
            check("positions inside world", all(
                0 <= n["pos"][0] <= 1600 and 0 <= n["pos"][1] <= 960
                for n in sched["npcs"]))
            check("greet threshold comes from settings",
                  sched["greet_threshold"] == settings.greet_threshold)

            print("\n== allocate (无自由点时取到 400 = 路由在线) ==")
            r = await c.post("/api/game/allocate", json={"stats": {"str": 1}})
            check("allocate rejected with 0 free points", r.status_code == 400)

            print("\n== shop buy ==")
            potion = data.item(data.SHOPS["clinic"]["items"][0])  # 小回春散
            r = await c.post("/api/shop/clinic/buy",
                             json={"item_id": potion["id"], "qty": 1})
            p = r.json()["player"]
            check("buy potion ok", r.status_code == 200 and p["gold"] < 100)
            check("inventory +1", any(i["name"] == potion["name"] for i in p["inventory"]))

            print("\n== mob battle ==")
            r = await c.post("/api/battle/start",
                             json={"target_type": "mob", "target_id": "wolf_1",
                                   "pos": {"x": 260, "y": 600}})
            check("battle start", r.status_code == 200
                  and r.json()["battle"]["enemy"]["name"] == "野狼")
            outcome = None
            for _ in range(40):  # 规则战斗必然收敛
                r = await c.post("/api/battle/action", json={"action": "attack"})
                outcome = r.json()["result"]
                if outcome:
                    break
            check("battle resolves", outcome in ("win", "lose", "fled"))
            check("no battle active after resolve",
                  (await c.get("/api/battle/state")).json()["battle"] is None)

            print("\n== gift is an LLM conversation turn (npc 5 = 王大柱/smith) ==")
            gift_day = int(main_mod.mem_store.clock.now() // 86400)
            r = await c.post("/api/npc/5/gift", json={"item_id": potion["id"]})
            body = r.json()
            check("gift uses LLM affinity delta",
                  r.status_code == 200 and body["affinity"] == 27)
            check("gift returns LLM reaction",
                  body.get("reply") == "这份心意，我收下了。")

            print("\n== dialogue (stubbed LLM, SSE) ==")
            status, events, done = await talk(c, 1, "先生安好？")
            check("talk returns reply", status == 200 and done is not None
                  and len(done["reply"]) > 0)
            check("reply streams as tokens",
                  "".join(d.get("text", "") for e, d in events if e == "token") == done["reply"])
            check("affinity clamped to [0,100]", 0 <= done["affinity"] <= 100)
            d1 = (await c.get("/api/debug/npc/1")).json()
            check("dialogue pauses NPC", d1["reflection"]["paused_until"] is not None)
            status, _, done = await talk(c, 1, "告辞")
            check("farewell ends dialogue", status == 200 and done is not None
                  and done["end"] is True)
            d1 = (await c.get("/api/debug/npc/1")).json()
            check("farewell clears pause", d1["reflection"]["paused_until"] is None)

            print("\n== gift enters conversation window, not memory ==")
            conv = await main_mod.db.fetch_one(
                "SELECT turns_json FROM conversation WHERE npc_id = 5"
                " ORDER BY id DESC LIMIT 1")
            check("gift stored as conversation turn",
                  conv is not None and potion["name"] in conv["turns_json"]
                  and "这份心意，我收下了。" in conv["turns_json"])
            check("gift count stored in kv",
                  await main_mod.db.kv_get(f"gift_count:5:{gift_day}") == "1")
            d = (await c.get("/api/debug/npc/5")).json()
            check("gift does not write memory directly",
                  not any("送礼" in m["content"] for m in d["memories"]))

            print("\n== spar is an LLM conversation turn ==")
            conv_before = (await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation WHERE npc_id = 5"))["n"]
            r = await c.post("/api/npc/5/challenge", json={"x": 0, "y": 0})
            check("spar starts", r.status_code == 200 and r.json().get("battle") is not None)
            outcome = None
            spar_log: list[dict] = []
            for _ in range(60):
                r = await c.post("/api/battle/action", json={"action": "attack"})
                body = r.json()
                outcome = body.get("result")
                spar_log.extend(body.get("log", []))
                if outcome:
                    break
            check("spar resolves", outcome in ("win", "lose"))
            check("spar reaction logged",
                  any(e.get("kind") == "npc_reaction" for e in spar_log))
            conv_after = (await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation WHERE npc_id = 5"))["n"]
            conv = await main_mod.db.fetch_one(
                "SELECT turns_json FROM conversation WHERE npc_id = 5"
                " ORDER BY id DESC LIMIT 1")
            check("spar stored as conversation turn",
                  conv_after == conv_before + 1 and conv is not None
                  and "切磋" in conv["turns_json"])
            d = (await c.get("/api/debug/npc/5")).json()
            check("spar uses LLM affinity delta", d["affinity"] == 29)
            check("spar does not write memory directly",
                  not any("切磋" in m["content"] for m in d["memories"]))
            check("spar LLM purpose called", "spar" in CALLED)

            print("\n== reflection ==")
            for i in range(8):
                await main_mod.mem_store.add_memory(
                    main_mod.db, 5, content=f"冒烟侠第{i + 1}次来铁匠铺问剑。",
                    kind="event", participants=["冒烟侠"], importance=3)
            before = (await c.get("/api/debug/npc/5")).json()["affinity"]
            ran = await reflection.run_once(main_mod.db, main_mod.mem_store, 5, force=True)
            d5 = (await c.get("/api/debug/npc/5")).json()
            check("forced reflection runs", ran is True)
            check("reflection memory written at importance 8", any(
                m["kind"] == "reflection" and m["importance"] == 8 for m in d5["memories"]))
            check("reflection affinity +1 (stub clamp)", d5["affinity"] == before + 1)

            print("\n== relationship ==")
            await main_mod.mem_store.add_memory(
                main_mod.db, 5, content="冒烟侠与王大柱在炉边闲谈许久。",
                kind="event", participants=["冒烟侠"], importance=4)
            updated = await relationship.maybe_update(
                main_mod.db, main_mod.mem_store, 5, "player", "冒烟侠", force=True)
            d5 = (await c.get("/api/debug/npc/5")).json()
            rel = next((r for r in d5["relationship"]
                        if r["target_kind"] == "player" and r["target_id"] == "冒烟侠"), None)
            check("forced relationship update", updated is True)
            check("relationship summary stored", rel is not None and bool(rel["summary"].strip()))

            print("\n== gossip ==")
            root_id = await main_mod.mem_store.add_memory(
                main_mod.db, 5, content="冒烟侠在后山崖力战黑旋风。",
                kind="event", participants=["冒烟侠"], importance=8)
            spread = await gossip.gossip_between(
                main_mod.db, main_mod.mem_store, 5, 6, poi="镇口")
            d6 = (await c.get("/api/debug/npc/6")).json()
            heard = [m for m in d6["memories"] if m["kind"] == "gossip"]
            check("gossip spreads", spread is True)
            check("gossip keeps source root", bool(heard) and heard[0]["source_memory_id"] == root_id)
            again = await gossip.gossip_between(
                main_mod.db, main_mod.mem_store, 5, 6, poi="镇口")
            check("same root not retold", again is False)

            print("\n== proactive greet ==")
            await npc_mod.set_affinity(main_mod.db, 5, 75)
            r = await c.post("/api/npc/5/greet")
            body = r.json()
            check("greet triggered", r.status_code == 200 and body.get("greet") is True
                  and bool(body.get("reply")))
            again = (await c.post("/api/npc/5/greet")).json()
            check("greet cooldown", again.get("greet") is False
                  and again.get("reason") == "cooldown")

            print("\n== M4 prompt purposes ==")
            check("reflection/relationship/gossip/greet prompts called",
                  {"reflection", "relationship", "gossip", "greet"} <= set(CALLED))

            print("\n== M5-B agenda, speech examples & mood ==")
            from app.game import inventory as inventory_mod
            from app.game import player as player_mod

            p = await player_mod.get(main_mod.db)
            await inventory_mod.add(main_mod.db, p["id"], potion["id"])
            r = await c.post("/api/npc/5/gift", json={"item_id": potion["id"]})
            check("second gift succeeds", r.status_code == 200)
            check("second gift increments day counter",
                  await main_mod.db.kv_get(f"gift_count:5:{gift_day}") == "2")
            gift_prompts = [m for purpose, m in PROMPTS if purpose == "gift"]
            gift_prompt_text = (
                "\n".join(m["content"] for m in gift_prompts[-1])
                if gift_prompts else ""
            )
            check("gift prompt sees repeat count", "今日第2次" in gift_prompt_text)
            d5b = (await c.get("/api/debug/npc/5")).json()
            check("gift sets pleased mood",
                  d5b["mood"] is not None and d5b["mood"]["mood"] == "pleased")
            check("debug exposes speech examples", len(d5b["speech_examples"]) >= 2)

            before_aff = d5b["affinity"]
            status, _, done = await talk(c, 5, "近来生意如何？")
            check("mooded talk still applies only LLM delta",
                  status == 200 and done is not None
                  and done["affinity"] == min(100, before_aff + 2))
            dialogue_prompts = [m for purpose, m in PROMPTS if purpose == "dialogue"]
            system = dialogue_prompts[-1][0]["content"] if dialogue_prompts else ""
            check("dialogue prompt carries agenda", "你此刻的盘算" in system)
            check("dialogue prompt carries speech examples", "参考这些语气" in system)
            check("dialogue prompt carries mood", "你此刻的心气" in system)
            d5b = (await c.get("/api/debug/npc/5")).json()
            check("debug exposes agenda and mood",
                  bool(d5b["agenda"]) and d5b["mood"]["mood"] == "pleased")

            await talk(c, 1, "后山可有消息？")
            a1 = (await c.get("/api/debug/npc/1")).json()["agenda"]["id"]
            await talk(c, 1, "后山可有消息？")
            a2 = (await c.get("/api/debug/npc/1")).json()["agenda"]["id"]
            check("agenda is stable within one conversation", a1 == a2)
            status, _, done = await talk(c, 1, "告辞")
            check("end clears current agenda",
                  status == 200 and done is not None and done["end"] is True
                  and (await c.get("/api/debug/npc/1")).json()["agenda"] is None)
            await talk(c, 1, "再说说后山")
            a3 = (await c.get("/api/debug/npc/1")).json()["agenda"]["id"]
            check("next conversation picks a different agenda", a3 != a1)
            check("B adds no LLM purpose",
                  "agenda" not in CALLED and "mood" not in CALLED)

            print("\n== M5-A daily heart & schedule deviation ==")
            from app.agent import heart as heart_mod
            from app.game import plan as plan_mod
            from app.game import schedule as schedule_mod

            before_aff = (await c.get("/api/debug/npc/1")).json()["affinity"]
            heart_calls = CALLED.count("heart")
            await heart_mod.ensure_today(main_mod.db, main_mod.mem_store, 1)
            now_game = main_mod.mem_store.clock.now()
            day = int(now_game // 86400)
            plan_row = await main_mod.db.fetch_one(
                "SELECT * FROM npc_plan WHERE npc_id = 1 AND game_day = ?", (day,))
            check("heart row generated",
                  plan_row is not None and bool(plan_row["heart"]))
            check("heart uses one LLM call", CALLED.count("heart") == heart_calls + 1)
            await heart_mod.ensure_today(main_mod.db, main_mod.mem_store, 1)
            row_count = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM npc_plan WHERE npc_id = 1")
            check("same day heart not regenerated",
                  row_count["n"] == 1 and CALLED.count("heart") == heart_calls + 1)
            check("heart does not change affinity",
                  (await c.get("/api/debug/npc/1")).json()["affinity"] == before_aff)

            HEART_MODE["value"] = "fail"
            fail_calls = CALLED.count("heart")
            await heart_mod.ensure_today(main_mod.db, main_mod.mem_store, 2)
            plan_row2 = await main_mod.db.fetch_one(
                "SELECT * FROM npc_plan WHERE npc_id = 2 AND game_day = ?", (day,))
            check("failed heart writes empty row and no deviation",
                  plan_row2 is not None and plan_row2["heart"] == ""
                  and plan_row2["deviation_json"] == "{}")
            check("failed heart retries exactly once",
                  CALLED.count("heart") == fail_calls + 2)
            attempts = CALLED.count("heart")
            await heart_mod.ensure_today(main_mod.db, main_mod.mem_store, 2)
            check("failed heart not retried same day", CALLED.count("heart") == attempts)
            HEART_MODE["value"] = "ok"

            dev = {"day": 0, "kind": "gather_early", "shift": 1200}
            check("deviation shifts gather boundary",
                  schedule_mod.phase_at(38999, dev)[0] == "walk_work_to_gather"
                  and schedule_mod.phase_at(39000, dev)[0] == "gather")
            gu = await npc_mod.by_id(main_mod.db, 1)
            pos = schedule_mod.position_for(gu, 39000, dev)
            check("deviated position stays at gather anchor",
                  0 <= pos[0] <= 1600 and 0 <= pos[1] <= 960
                  and abs(pos[0] - 768) <= 10 and abs(pos[1] - 440) <= 8)
            check("stay_work keeps NPC at work",
                  schedule_mod.poi_label_for(
                      gu, 45000, {"kind": "stay_work", "shift": 1200}) == "剑阳道馆")

            sched = (await c.get("/api/npcs/state")).json()
            n1 = next(n for n in sched["npcs"] if n["slug"] == "gu_changfeng")
            n2 = next(n for n in sched["npcs"] if n["slug"] == "tie_wushuang")
            check("npcs/state exposes heart and deviation",
                  bool(n1["heart"]) and n1["deviation"].get("kind") == "gather_early"
                  and n1["deviation"].get("shift") == 600)
            check("failed heart visible as empty", n2["heart"] == "")
            d1a = (await c.get("/api/debug/npc/1")).json()
            check("debug exposes heart and deviation",
                  bool(d1a["heart"]) and d1a["deviation"].get("kind") == "gather_early")
            settings.plan_enabled = False
            check("plan switch disables deviation immediately",
                  await plan_mod.deviation_for(main_mod.db, 1, now_game) is None)
            settings.plan_enabled = True

            print("\n== M5-C nearby events ==")
            from app.agent import events as events_mod
            from app.game import plan as plan_mod

            gu = await npc_mod.by_id(main_mod.db, 1)
            now_game = main_mod.mem_store.clock.now()
            gu_dev = await plan_mod.deviation_for(main_mod.db, 1, now_game)
            gu_poi = schedule_mod.poi_label_for(gu, now_game, gu_dev)
            token = "冒烟测试异动"
            events_mod.record(
                main_mod.mem_store, "battle_win",
                f"{token}：在{gu_poi}有动静", gu_poi, ["冒烟侠"])
            await talk(c, 1, "方才那边怎么了？")
            dialogue_prompts = [m for purpose, m in PROMPTS if purpose == "dialogue"]
            system = dialogue_prompts[-1][0]["content"] if dialogue_prompts else ""
            check("nearby event injected into dialogue prompt", token in system)
            check("expired event is not injected",
                  all(token not in str(event) for event in events_mod.recent(
                      main_mod.mem_store, now_game + 1801, poi=gu_poi)))
            mem_hit = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM memory WHERE content LIKE ?", (f"%{token}%",))
            check("nearby event not written to memory", mem_hit["n"] == 0)

            print("\n== M5-W world premise & artifact ==")
            from app.game import battle as battle_mod
            from app.game import mobs as mobs_mod
            from app.game import player as player_mod
            from app.game import world as world_mod
            from app.game import world_state as world_state_mod

            st = (await c.get("/api/game/state")).json()
            check("world starts at stage 0", st["world"]["stage"] == 0)
            check("world premise has four facts", len(st["world"]["premise"]) == 4)
            check("artifact not acquired", st["world"]["artifact_acquired"] is False)

            d1w = (await c.get("/api/debug/npc/1")).json()
            check("debug exposes stance at stage 0",
                  bool(d1w["world"]["stance"]) and d1w["world"]["stage"] == 0)

            battle_mod.mem_store = main_mod.mem_store
            p = await player_mod.get(main_mod.db)
            boss_state = {
                "mob_id": "boss_1",
                "enemy": {"type": "boss", "name": "黑旋风", "level": 5,
                          "hp": 0, "hp_max": 260, "atk": 30, "def": 9, "spd": 12, "potions": 0},
            }
            wlog: list[dict] = []

            def add(kind: str, text: str, **kw) -> None:
                wlog.append({"kind": kind, "text": text, **kw})

            rewards = await battle_mod._finalize(main_mod.db, p, boss_state, add, "win")
            check("boss drops the artifact",
                  any(d["name"] == "断岳刀" for d in rewards["drops"]))
            check("artifact flag set",
                  await world_state_mod.artifact_acquired(main_mod.db))
            check("boss alone does not announce",
                  rewards["world_announced"] is False
                  and await world_state_mod.stage(main_mod.db) == 0)
            dao = data.item(world_mod.ARTIFACT_ITEM_KEY)
            dao_row = await main_mod.db.fetch_one(
                "SELECT inv.qty FROM inventory inv JOIN item it ON it.id = inv.item_id"
                " WHERE inv.player_id = ? AND it.id = ?", (p["id"], dao["id"]))
            check("artifact in inventory once", dao_row is not None and dao_row["qty"] == 1)
            await battle_mod._finalize(main_mod.db, p, boss_state, add, "win")
            dao_row = await main_mod.db.fetch_one(
                "SELECT inv.qty FROM inventory inv JOIN item it ON it.id = inv.item_id"
                " WHERE inv.player_id = ? AND it.id = ?", (p["id"], dao["id"]))
            check("artifact never drops twice", dao_row is not None and dao_row["qty"] == 1)

            r = await c.post("/api/shop/clinic/sell",
                             json={"item_id": dao["id"], "qty": 1})
            check("artifact cannot be sold",
                  r.status_code == 400 and "不可出售" in r.json()["detail"])

            now_game = main_mod.mem_store.clock.now()
            advanced = await world_state_mod.check_and_advance(main_mod.db, now_game)
            check("boss + artifact alone do not advance", advanced is False)
            for mid in world_state_mod.BANDIT_IDS:
                await mobs_mod.mark_killed(main_mod.db, mid)
            advanced = await world_state_mod.check_and_advance(main_mod.db, now_game)
            check("boss + three bandits + artifact advances",
                  advanced is True and await world_state_mod.stage(main_mod.db) == 2)
            check("advance is idempotent",
                  await world_state_mod.check_and_advance(main_mod.db, now_game) is False)

            brief = world_mod.brief_for(
                npc_mod.NPC_ROSTER["gu_changfeng"], "后山那刀怎么回事", 2, 25, "冒烟侠")
            check("stage2 brief announces the player", "冒烟侠杀了黑旋风" in brief)
            check("stage2 no-topic brief omits secret knowledge",
                  "本派打算借神器" not in world_mod.brief_for(
                      npc_mod.NPC_ROSTER["gu_changfeng"], "你好", 2, 80, "冒烟侠"))
            check("stage2 topic brief reveals eligible knowledge",
                  "本派打算借神器" in world_mod.brief_for(
                      npc_mod.NPC_ROSTER["gu_changfeng"], "后山", 2, 80, "冒烟侠"))
            st = (await c.get("/api/game/state")).json()
            check("state announces world event",
                  st["world"]["stage"] == 2
                  and "冒烟侠" in (st["world"]["announcement"] or ""))

            r = await c.post("/api/npc/1/apprentice")
            check("apprentice rules unchanged after world event", r.status_code == 400)

            r = await c.post("/api/game/new", json={"name": "新冒烟侠", "confirm": True})
            st = (await c.get("/api/game/state")).json()
            check("new game resets world",
                  r.status_code == 200 and st["world"]["stage"] == 0
                  and st["world"]["artifact_acquired"] is False)
            check("new game clears kills and revives mobs",
                  await main_mod.db.kv_get("mob_kills") is None
                  and all(m["alive"] for m in st["mobs"]))
            check("W adds no LLM purpose", "world" not in CALLED)

            print("\n== memory tiers: window -> midterm (memory only) ==")
            npc_window = 7
            conv_before = (await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation WHERE npc_id = ?",
                (npc_window,)))["n"]
            chroma_before = main_mod.mem_store._coll.count()
            summary_calls = CALLED.count("dialogue_summary")
            for i in range(1, 12):
                status, _, done = await talk(c, npc_window, f"闲谈第{i}轮")
                check(f"window talk {i} ok", status == 200 and done is not None)
            conv_after = (await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation WHERE npc_id = ?",
                (npc_window,)))["n"]
            check("window keeps only the newest turn",
                  conv_before == 0 and conv_after == 1)
            check("one LLM summary for the overflowing window",
                  CALLED.count("dialogue_summary") == summary_calls + 1)
            midterms = main_mod.mem_store.debug_midterms(npc_window)
            check("midterm kept in memory",
                  len(midterms) == 1
                  and midterms[0]["summary"] == "闲谈第1轮起归档。"
                  and midterms[0]["clues"] == ["后山", "剑法"]
                  and midterms[0]["turn_count"] == 10)
            midterm_rows = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM memory WHERE npc_id = ? AND content = ?",
                (npc_window, "闲谈第1轮起归档。"))
            check("midterm does not write memory", midterm_rows["n"] == 0)
            check("midterm does not write chroma",
                  main_mod.mem_store._coll.count() == chroma_before)
            raw_hit = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM memory WHERE content LIKE ?",
                ("%闲谈第1轮%",))
            check("raw conversation turns are not memorized", raw_hit["n"] == 0)
            summary_prompts = [m for purpose, m in PROMPTS
                               if purpose == "dialogue_summary"]
            prompt_text = ("\n".join(m["content"] for m in summary_prompts[-1])
                           if summary_prompts else "")
            check("midterm prompt contains oldest ten, not the newest",
                  "闲谈第1轮" in prompt_text and "闲谈第10轮" in prompt_text
                  and "闲谈第11轮" not in prompt_text)

            print("\n== midterm clue retrieval -> separate prompt block ==")
            status, _, done = await talk(c, npc_window, "再说说后山的事")
            check("clue-matched talk ok", status == 200 and done is not None)
            dialogue_prompts = [m for purpose, m in PROMPTS if purpose == "dialogue"]
            last_system = dialogue_prompts[-1][0]["content"] if dialogue_prompts else ""
            check("midterm block injected",
                  "你想起一些旧事（只作参考，不要逐字复述）" in last_system
                  and "闲谈第1轮起归档。" in last_system)
            check("midterm block sits outside recent turns",
                  last_system.find("你想起一些旧事")
                  < last_system.find("你们最近的往来"))
            status, _, done = await talk(c, npc_window, "无关话题")
            check("unrelated talk ok", status == 200 and done is not None)
            dialogue_prompts = [m for purpose, m in PROMPTS if purpose == "dialogue"]
            unrelated_system = (dialogue_prompts[-1][0]["content"]
                                if dialogue_prompts else "")
            check("unrelated message omits midterm block",
                  "你想起一些旧事（只作参考，不要逐字复述）" not in unrelated_system)

            print("\n== 36 turns -> one longterm memory (async, isolated) ==")
            for i in range(14, 37):
                status, _, done = await talk(c, npc_window, f"闲谈第{i}轮")
                check(f"longterm talk {i} ok", status == 200 and done is not None)
            await main_mod.mem_store.wait_background()
            long_rows = await main_mod.db.fetch_all(
                "SELECT npc_id, content, turn_count FROM longterm_memory"
                " WHERE npc_id = ?", (npc_window,))
            check("one longterm memory for 36 turns",
                  len(long_rows) == 1 and long_rows[0]["turn_count"] == 36
                  and long_rows[0]["content"] == "三十六轮往来已归档。")
            check("three midterm + one longterm summary calls",
                  CALLED.count("dialogue_summary") == summary_calls + 4)
            check("longterm summary uses priority 3",
                  any(purpose == "dialogue_summary" and p == 3
                      for purpose, p in CALLS))
            long_mem_hit = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM memory WHERE npc_id = ? AND content = ?",
                (npc_window, "三十六轮往来已归档。"))
            check("longterm does not write memory", long_mem_hit["n"] == 0)
            check("longterm does not write chroma",
                  main_mod.mem_store._coll.count() == chroma_before)
            conv_final = (await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation WHERE npc_id = ?",
                (npc_window,)))["n"]
            check("window keeps turns 31-36", conv_final == 6)
            fifo = [m["summary"] for m in main_mod.mem_store.debug_midterms(npc_window)]
            check("midterm capacity FIFO keeps newest two",
                  fifo == ["闲谈第11轮起归档。", "闲谈第21轮起归档。"])
            check("longterm buffer drained",
                  main_mod.mem_store.pending_longterm_turns(npc_window) == 0)
            long_prompts = [
                m for purpose, m in PROMPTS
                if purpose == "dialogue_summary" and "长期记忆" in m[0]["content"]]
            long_prompt_text = ("\n".join(m["content"] for m in long_prompts[-1])
                                if long_prompts else "")
            check("longterm prompt contains turns 1..36",
                  "闲谈第1轮" in long_prompt_text
                  and "闲谈第36轮" in long_prompt_text)
            debug = (await c.get(f"/api/debug/npc/{npc_window}")).json()
            check("debug exposes midterm and longterm",
                  len(debug.get("midterm", [])) == 2
                  and len(debug.get("longterm", [])) == 1
                  and debug.get("longterm_pending_turns") == 0)

            print("\n== midterm clue matcher (pure function) ==")
            from app.agent.memory import MidTerm
            vec_a = [1.0] + [0.0] * 1023
            vec_b = [0.0, 1.0] + [0.0] * 1022
            main_mod.mem_store._midterms[999] = [
                MidTerm("甲", ["甲"], [vec_a], 0.0, 1),
                MidTerm("乙", ["乙"], [vec_b], 0.0, 1),
            ]
            matched = main_mod.mem_store.match_midterms(999, vec_a)
            check("midterm clue matcher picks nearest",
                  [m.summary for m in matched] == ["甲"])
            below = [0.3, 0.3] + [0.0] * 1022
            check("midterm score threshold filters weak matches",
                  main_mod.mem_store.match_midterms(999, below) == [])

            print("\n== new_game vs in-flight dialogue (generation barrier) ==")
            npc_race = 2
            GATE["reached"].clear()
            GATE["release"].clear()
            race_task = asyncio.create_task(talk(c, npc_race, "慢速对话"))
            await asyncio.wait_for(GATE["reached"].wait(), timeout=5)
            r = await c.post("/api/game/new",
                             json={"name": "重开侠", "confirm": True})
            check("new game during in-flight talk", r.status_code == 200)
            GATE["release"].set()
            status, _, done = await race_task
            check("stale talk still returns",
                  status == 200 and done is not None and done.get("stale") is True)
            conv_race = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation"
                " WHERE npc_id = ? AND turns_json LIKE ?",
                (npc_race, "%慢速对话%"))
            check("stale turn not written to conversation", conv_race["n"] == 0)
            check("stale turn not buffered",
                  all("慢速对话" not in t["user"]
                      for t in main_mod.mem_store._longterm_turns.get(npc_race, [])))
            check("stale turn not in midterm",
                  all("慢速对话" not in m["summary"]
                      for m in main_mod.mem_store.debug_midterms(npc_race)))
            check("new game clears runtime events",
                  main_mod.mem_store.recent_events == [])

            print("\n== longterm worker drops stale generation ==")
            turns = [{"user": "门禁长期测试", "npc": "……", "place": "镇口",
                      "ts_game": 0.0} for _ in range(36)]
            main_mod.mem_store._schedule_longterm(
                main_mod.db, 2, turns, ts_game=main_mod.mem_store.clock.now(),
                npc_name="张馒头", player_name="门禁测试",
                generation=main_mod.mem_store.generation)
            await asyncio.wait_for(GATE_LT["reached"].wait(), timeout=5)
            main_mod.mem_store.begin_reset()
            flush_task = asyncio.create_task(main_mod.mem_store.flush())
            await asyncio.sleep(0)
            GATE_LT["release"].set()
            await asyncio.wait_for(flush_task, timeout=5)
            main_mod.mem_store.reset_runtime()
            stale_rows = await main_mod.db.fetch_one(
                "SELECT COUNT(*) AS n FROM longterm_memory WHERE npc_id = 2")
            check("stale longterm job dropped", stale_rows["n"] == 0)

            print("\n== db lock survives cancelled worker thread ==")
            started = threading.Event()
            release = threading.Event()

            def slow_sql():
                started.set()
                release.wait(2)
                return 42

            slow_task = asyncio.create_task(main_mod.db._call_locked(slow_sql))
            await asyncio.to_thread(started.wait, 1)
            check("slow db op started", started.is_set())
            slow_task.cancel()
            await asyncio.sleep(0.05)
            check("db lock held until thread finishes", main_mod.db.lock.locked())
            release.set()
            try:
                await slow_task
            except asyncio.CancelledError:
                pass
            check("db lock released after thread finishes",
                  not main_mod.db.lock.locked())

    shutil.rmtree(_tmp, ignore_errors=True)
    return FAIL


if __name__ == "__main__":
    code = asyncio.run(main())
    print("\nSMOKE " + ("PASS" if code == 0 else f"FAIL ({code} checks)"))
    sys.exit(code)