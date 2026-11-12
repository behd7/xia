#!/usr/bin/env python3
"""Native NPC protocol regression, no external model or persistent game data.

Run from the repository root:
    server/.venv/bin/python server/tests/npc_tools.py

The legacy JSON decoder below is a test-only oracle. It feeds the same unchanged
settlement entry point to compare database effects, not stochastic model choices.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_tmp = tempfile.TemporaryDirectory(prefix="xia_npc_tools_")
os.environ["DB_PATH"] = str(Path(_tmp.name) / "xia.db")
os.environ["CHROMA_DIR"] = str(Path(_tmp.name) / "chroma")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["LANGFUSE_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.dialogue_tools import DIALOGUE_TOOLS, REPLY_TOOL, TurnStream, parse_turn
from app.llm import ChatResult, OllamaClient
from app.llm_tools import ToolCall, ToolCallBuffer, ToolCallDelta, string_prefix


def legacy_parse(text):
    """Frozen pre-migration valid-turn contract (no production fallback to JSON)."""
    data = json.loads(text)
    acts = [a for a in (data.get("acts") or []) if isinstance(a, dict)]
    reply = str(data.get("reply") or "").strip()
    if not reply and not acts:
        raise ValueError("empty reply")
    return {"reply": reply, "affinity_delta": int(data.get("affinity_delta", 0) or 0),
            "end": bool(data.get("end", False)), "acts": acts}


def native_calls(payload):
    calls = []
    if payload.get("reply"):
        calls.append(ToolCall("reply", {"text": payload["reply"]}))
    if "affinity_delta" in payload:
        calls.append(ToolCall("change_affinity", {"delta": payload["affinity_delta"]}))
    if payload.get("end"):
        calls.append(ToolCall("end_dialogue", {}))
    calls.extend(ToolCall(a["tool"], a["args"]) for a in payload.get("acts", []))
    return calls


def fragments(calls, size=1):
    for index, call in enumerate(calls):
        yield ToolCallDelta(index, call.name, id=f"call-{index}")
        args = json.dumps(call.arguments, ensure_ascii=True)
        for offset in range(0, len(args), size):
            yield ToolCallDelta(index, arguments=args[offset:offset + size])


def visible(events):
    text = ""
    for event, data in events:
        if event == "reset":
            text = ""
        elif event == "token":
            text += data["text"]
    return text


class ProtocolTests(unittest.TestCase):
    def test_old_new_intent_mapping(self):
        fixtures = [
            {"reply": "别来无恙。"},
            {"reply": "好！", "affinity_delta": 3},
            {"reply": "不便奉陪。", "affinity_delta": -3, "end": True},
            {"reply": "拿着。", "acts": [{"tool": "give_item", "args": {"item": "小回春散", "count": 2}}]},
            {"reply": "", "acts": [{"tool": "refuse", "args": {"reason": "今日不便"}}]},
            {"reply": "教你。", "acts": [{"tool": "teach_art", "args": {"art": "烈阳剑诀"}}]},
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                expected = legacy_parse(json.dumps(fixture))
                self.assertEqual(parse_turn("", native_calls(fixture)), expected)
                for size in (1, 2, 7, 1000):
                    stream = TurnStream()
                    events = []
                    for fragment in fragments(native_calls(fixture), size):
                        reset, delta = stream.feed(fragment)
                        if reset:
                            events.append(("reset", {}))
                        if delta:
                            events.append(("token", {"text": delta}))
                    self.assertEqual(stream.finish(), expected)
                    self.assertEqual(visible(events), expected["reply"])

    def test_normal_content_and_explicit_reply_do_not_duplicate(self):
        stream = TurnStream()
        events = []
        for chunk in ["旁白", *fragments([ToolCall("reply", {"text": '说“好”\n😀\\'})]), "多余的重复话"]:
            reset, delta = stream.feed(chunk)
            if reset:
                events.append(("reset", {}))
            if delta:
                events.append(("token", {"text": delta}))
        self.assertEqual(visible(events), '说“好”\n😀\\')
        self.assertEqual(stream.finish()["reply"], visible(events))
        self.assertEqual(parse_turn("  普通回复。  ", [])["reply"], "普通回复。")

    def test_duplicate_controls_and_bad_arguments_are_rejected(self):
        cases = [
            [ToolCall("reply", {"text": "甲"}), ToolCall("reply", {"text": "乙"})],
            [ToolCall("change_affinity", {"delta": 1})] * 2,
            [ToolCall("end_dialogue", {})] * 2,
            [ToolCall("change_affinity", {"delta": True})],
            [ToolCall("change_affinity", {"delta": "2"})],
            [ToolCall("reply", {"text": None})],
            [ToolCall("reply", {"text": "甲", "acts": []})],
            [ToolCall("end_dialogue", {"end": False})],
        ]
        for calls in cases:
            with self.subTest(calls=calls), self.assertRaises(ValueError):
                parse_turn("台词", calls)
        with self.assertRaises(ValueError):
            parse_turn("", [ToolCall("change_affinity", {"delta": 2})])
        self.assertEqual(parse_turn("台词", [ToolCall("unknown", {})])["acts"],
                         [{"tool": "unknown", "args": {}}])

    def test_fragmented_names_indices_and_incomplete_json(self):
        buffer = ToolCallBuffer()
        buffer.feed(ToolCallDelta(1, "change_", '{"delta":'))
        buffer.feed(ToolCallDelta(0, "rep", '{"text":"好'))
        buffer.feed(ToolCallDelta(1, "affinity", "2}"))
        buffer.feed(ToolCallDelta(0, "ly", '"}'))
        self.assertEqual([c.name for c in buffer.finish()], ["reply", "change_affinity"])
        for args in ('{"text":"好', '[]', '{broken}'):
            bad = ToolCallBuffer()
            bad.feed(ToolCallDelta(0, "reply", args))
            with self.assertRaises(ValueError):
                bad.finish()
        for index in (-1, None, True):
            with self.assertRaises(ValueError):
                buffer.feed(ToolCallDelta(index))
        self.assertEqual(string_prefix('{"text":"\\ud83d', "text"), "")
        self.assertEqual(string_prefix('{"text":"\\ud83d\\ude00"}', "text"), "😀")


class LogDB:
    def __init__(self):
        self.rows = []

    async def execute(self, sql, params):
        self.rows.append(params)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = LogDB()
        self.client = OllamaClient("http://ollama", 1, self.db)
        await self.client.close()
        self.requests = []
        self.client._api_client = None
        self.client._api_embed_client = None

    async def asyncTearDown(self):
        await self.client.close()

    def transport(self, response, *, api=True):
        async def handler(request):
            self.requests.append(json.loads(request.content))
            return response(request) if callable(response) else response
        client = httpx.AsyncClient(base_url="http://fake", transport=httpx.MockTransport(handler))
        if api:
            self.client._api_client = client
            self.client._api_model = "test-model"
        else:
            self.client._client = client

    def assert_tools_only(self):
        for request in self.requests:
            self.assertEqual(request["tools"], DIALOGUE_TOOLS)
            self.assertNotIn("format", request)
            self.assertNotIn("response_format", request)

    @staticmethod
    def sse(rows, done=True):
        return httpx.Response(200, text="".join("data: " + json.dumps(row) + "\n\n" for row in rows)
                              + ("data: [DONE]\n\n" if done else ""))

    async def test_api_stream_interleaving_content_and_usage(self):
        rows = [{"choices": [{"index": 0, "delta": {"content": "普通"}}]}]
        for fragment in fragments(native_calls({"reply": "好😀", "affinity_delta": 2, "end": True}), 2):
            rows.append({"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": fragment.index, "id": fragment.id,
                "function": {"name": fragment.name, "arguments": fragment.arguments}}]}}]})
        rows += [{"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                 {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 12}}]
        self.transport(self.sse(rows))
        stream = TurnStream()
        async for chunk in self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS):
            stream.feed(chunk)
        self.assertEqual(stream.finish(), {"reply": "好😀", "affinity_delta": 2, "end": True, "acts": []})
        self.assert_tools_only()
        self.assertEqual(self.db.rows[-1][2:4], (11, 12))
        self.assertEqual(self.client._limiter._active, 0)

    async def test_ollama_stream_complete_calls(self):
        payload = {"reply": "收下了。", "affinity_delta": -2,
                   "acts": [{"tool": "take_item", "args": {"item": "小回春散", "count": 1}}]}
        rows = [{"message": {"content": "", "tool_calls": [
            {"function": {"name": call.name, "arguments": call.arguments}}]}}
                for call in native_calls(payload)]
        rows.append({"done": True, "prompt_eval_count": 21, "eval_count": 22})
        self.transport(httpx.Response(200, text="\n".join(json.dumps(r) for r in rows)), api=False)
        stream = TurnStream()
        async for chunk in self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS):
            stream.feed(chunk)
        self.assertEqual(stream.finish(), legacy_parse(json.dumps(payload)))
        self.assert_tools_only()
        self.assertFalse(self.requests[0]["think"])
        self.assertEqual(self.db.rows[-1][2:4], (21, 22))

    async def test_api_nonstream_tool_calls(self):
        self.transport(httpx.Response(200, json={"choices": [{"message": {
            "content": None, "tool_calls": [{"id": "abc", "function": {
                "name": "reply", "arguments": '{"text":"你好"}'}}]}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4}}))
        result = await self.client.chat("greet", [], tools=DIALOGUE_TOOLS)
        self.assertEqual(result.tool_calls, [ToolCall("reply", {"text": "你好"}, "abc")])
        self.assertEqual(result.text, "")
        self.assert_tools_only()

    async def test_ollama_nonstream_and_background_json_unchanged(self):
        self.transport(lambda r: httpx.Response(200, json={"message": {
            "content": '{"summary":"旧事。"}' if json.loads(r.content).get("format") else "",
            "tool_calls": [] if json.loads(r.content).get("format") else [{"function": {
                "name": "reply", "arguments": {"text": "你好"}}}]}}), api=False)
        result = await self.client.chat("greet", [], tools=DIALOGUE_TOOLS)
        self.assertEqual(result.tool_calls, [ToolCall("reply", {"text": "你好"})])
        result = await self.client.chat("dialogue_summary", [], json_format=True)
        self.assertEqual(result.text, '{"summary":"旧事。"}')
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(self.requests[-1]["format"], "json")
        self.assertNotIn("tools", self.requests[-1])

    async def test_stream_options_retry_keeps_tools(self):
        self.transport(lambda r: httpx.Response(422, json={"error": "stream_options unsupported"})
                       if len(self.requests) == 1 else self.sse([
                           {"choices": [{"delta": {"content": "你好"}, "finish_reason": "stop"}]}]))
        text = [c async for c in self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS)]
        self.assertEqual(text, ["你好"])
        self.assertEqual(len(self.requests), 2)
        self.assertIn("stream_options", self.requests[0])
        self.assertNotIn("stream_options", self.requests[1])
        self.assert_tools_only()

    async def test_incomplete_stream_and_length_never_succeed(self):
        self.transport(self.sse([{"choices": [{"delta": {"content": "半句话"}}]}], done=False))
        with self.assertRaises(ValueError):
            _ = [c async for c in self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS)]
        self.assertEqual(self.db.rows[-1][5], 0)
        await self.client._api_client.aclose()
        self.transport(self.sse([{"choices": [{"delta": {}, "finish_reason": "length"}]}]))
        with self.assertRaises(ValueError):
            _ = [c async for c in self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS)]
        self.assertEqual(self.client._limiter._active, 0)

    async def test_unsupported_tools_never_downgrade_to_json(self):
        self.transport(httpx.Response(400, json={"error": "tools unsupported"}))
        with self.assertRaises(httpx.HTTPStatusError):
            _ = [c async for c in self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS)]
        self.assert_tools_only()
        self.assertEqual(len(self.requests), 2)
        with self.assertRaises(ValueError):
            await self.client.chat("dialogue", [], tools=DIALOGUE_TOOLS, json_format=True)
        self.assertEqual(len(self.requests), 2)

    async def test_stream_cancel_releases_priority_slot(self):
        self.transport(self.sse([{"choices": [{"delta": {"content": "你好"}, "finish_reason": "stop"}]}]))
        gen = self.client.chat_stream("dialogue", [], tools=DIALOGUE_TOOLS)
        self.assertEqual(await anext(gen), "你好")
        self.assertEqual(self.client._limiter._active, 1)
        await gen.aclose()
        self.assertEqual(self.client._limiter._active, 0)
        self.assertEqual(self.db.rows[-1][5], 0)


class SettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_effects_and_retry_safety(self):
        from app import main as main_mod
        from app.agent import dialogue, npc
        from app.game import player

        async def embed(texts):
            return [[1.0] + [0.0] * 1023 for _ in texts]

        async def tags():
            return ["test-model"]

        fixture = {}
        state = {"legacy": False, "fail_attempts": 0, "always_fail": False,
                 "calls": 0, "corrections": 0, "reset": False}

        async def chat(purpose, messages, *, tools=None, **kwargs):
            self.assertFalse(kwargs.get("json_format", False))
            self.assertEqual(tools, [REPLY_TOOL])
            state["corrections"] += 1
            return ChatResult("做不成，改日再说。", 1, 1)

        async def chat_stream(purpose, messages, *, tools=None, **kwargs):
            self.assertEqual(tools, DIALOGUE_TOOLS)
            self.assertFalse(kwargs.get("json_format", False))
            state["calls"] += 1
            if state["legacy"]:
                raw = json.dumps(fixture, ensure_ascii=True)
                for offset in range(0, len(raw), 2):
                    yield raw[offset:offset + 2]
                return
            calls = native_calls(fixture)
            bad = state["always_fail"] or state["fail_attempts"] > 0
            if bad:
                state["fail_attempts"] = max(0, state["fail_attempts"] - 1)
                # Complete action intent, then malformed control JSON: nothing
                # may settle until the entire completion has been validated.
                calls = [ToolCall("give_item", {"item": "小回春散", "count": 1}),
                         ToolCall("reply", {"text": "这药拿好。"})]
            for fragment in fragments(calls, 2):
                yield fragment
            if bad:
                yield ToolCallDelta(len(calls), "change_affinity", '{"delta":')
            if state["reset"]:
                main_mod.mem_store.begin_reset()

        class LegacyStream:
            def __init__(self):
                self.raw = ""
                self.sent = ""

            def feed(self, chunk):
                self.raw += chunk
                value = string_prefix(self.raw, "reply") or ""
                delta = value[len(self.sent):]
                self.sent = value
                return False, delta

            def finish(self):
                return legacy_parse(self.raw)

        main_mod.ollama.embed = embed
        main_mod.ollama.tags = tags
        main_mod.ollama.chat = chat
        main_mod.ollama.chat_stream = chat_stream
        async with main_mod.app.router.lifespan_context(main_mod.app):
            main_mod.mem_store.clock.now = lambda: 7 * 3600
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://test") as http:
                async def run(payload, *, legacy=False, affinity=75, purpose="dialogue", pause=True):
                    nonlocal fixture
                    fixture = payload
                    await http.post("/api/game/new", json={"name": "等价侠", "confirm": True})
                    n = await npc.by_id(main_mod.db, 1)
                    p = await player.get(main_mod.db)
                    await npc.set_affinity(main_mod.db, 1, affinity)
                    for holder in (p["character_id"], n["character_id"]):
                        await main_mod.db.execute(
                            "INSERT OR REPLACE INTO character_inventory(character_id,item_id,qty)"
                            " SELECT ?,id,3 FROM item WHERE name='小回春散'", (holder,))
                    before_logs = (await main_mod.db.fetch_one("SELECT COUNT(*) AS n FROM tool_call_log"))["n"]
                    before_mem = (await main_mod.db.fetch_one("SELECT COUNT(*) AS n FROM memory WHERE kind='action'"))["n"]
                    before_turn = (await main_mod.db.fetch_one("SELECT turn_no FROM npc_state WHERE npc_id=1"))["turn_no"]
                    state.update(legacy=legacy, calls=0, corrections=0)
                    stream_type = LegacyStream if legacy else TurnStream
                    with patch.object(dialogue, "TurnStream", stream_type):
                        events = [e async for e in dialogue.talk_stream(main_mod.db, main_mod.mem_store, 1,
                                                                       "先生安好？", purpose=purpose, pause=pause)]
                    done = events[-1][1]
                    self.assertEqual(visible(events), done["reply"])
                    pdata = dict(done["player"])
                    for key in ("id", "character_id", "last_saved_at"):
                        pdata.pop(key, None)
                    final = await main_mod.db.fetch_one("SELECT affinity,paused_until,turn_no FROM npc_state WHERE npc_id=1")
                    holdings = await main_mod.db.fetch_all(
                        "SELECT qty FROM character_inventory WHERE character_id=? AND item_id=(SELECT id FROM item WHERE name='小回春散')",
                        (n["character_id"],))
                    conv = await main_mod.db.fetch_one(
                        "SELECT turns_json,affinity_delta_total FROM conversation WHERE npc_id=1 AND kind='turn' ORDER BY turn_no DESC LIMIT 1")
                    turn = json.loads(conv["turns_json"]) if conv else {}
                    turn.pop("turn_id", None)
                    return {"reply": done["reply"], "affinity": final["affinity"], "end": done["end"],
                            "acts": done["acts"], "fallback": done["fallback"], "stale": done["stale"],
                            "player": pdata, "npc_potions": holdings, "paused": final["paused_until"] is not None,
                            "turn_delta": final["turn_no"] - before_turn, "turn": turn,
                            "logged": (await main_mod.db.fetch_one("SELECT COUNT(*) AS n FROM tool_call_log"))["n"] - before_logs,
                            "memories": (await main_mod.db.fetch_one("SELECT COUNT(*) AS n FROM memory WHERE kind='action'"))["n"] - before_mem,
                            "events": [e["text"] for e in main_mod.mem_store.recent_events],
                            "agenda_active": bool(await main_mod.db.kv_get("agenda_current:1")),
                            "mood": await main_mod.db.fetch_one("SELECT mood,reason FROM npc_mood WHERE npc_id=1"),
                            "delta": conv["affinity_delta_total"] if conv else 0,
                            "calls": state["calls"], "corrections": state["corrections"]}

                cases = [
                    ("reply", {"reply": "先生安好。"}, 75, "dialogue", True),
                    ("upper clamp", {"reply": "好。", "affinity_delta": 999}, 99, "dialogue", True),
                    ("lower clamp/end", {"reply": "告辞。", "affinity_delta": -999, "end": True}, 1, "dialogue", True),
                    ("give", {"reply": "拿好。", "affinity_delta": 2, "acts": [{"tool": "give_item", "args": {"item": "小回春散", "count": 2}}]}, 75, "dialogue", True),
                    ("take", {"reply": "收下了。", "acts": [{"tool": "take_item", "args": {"item": "小回春散", "count": 1}}]}, 75, "dialogue", True),
                    ("teach", {"reply": "学着。", "acts": [{"tool": "teach_art", "args": {"art": "烈阳剑诀"}}]}, 75, "dialogue", True),
                    ("silence", {"reply": "", "acts": [{"tool": "refuse", "args": {"reason": "今日不便"}}]}, 75, "dialogue", True),
                    ("two acts", {"reply": "拿好。", "affinity_delta": 2, "acts": [{"tool": "give_item", "args": {"item": "小回春散"}}, {"tool": "refuse", "args": {"reason": "别的没有"}}]}, 75, "dialogue", True),
                    ("invalid batch", {"reply": "拿好。", "affinity_delta": 2, "end": True, "acts": [{"tool": "give_item", "args": {"item": "小回春散"}}, {"tool": "give_item", "args": {"item": "不存在"}}]}, 75, "dialogue", True),
                    ("low affinity", {"reply": "收下了。", "acts": [{"tool": "take_item", "args": {"item": "小回春散"}}]}, 49, "dialogue", True),
                    ("delta cannot bypass threshold", {"reply": "学着。", "affinity_delta": 3, "acts": [{"tool": "teach_art", "args": {"art": "烈阳剑诀"}}]}, 69, "dialogue", True),
                    ("count limit", {"reply": "拿好。", "acts": [{"tool": "give_item", "args": {"item": "小回春散", "count": 4}}]}, 75, "dialogue", True),
                    ("three acts rejected", {"reply": "不行。", "acts": [{"tool": "refuse", "args": {}}] * 3}, 75, "dialogue", True),
                    ("unknown action", {"reply": "走。", "acts": [{"tool": "follow", "args": {}}]}, 75, "dialogue", True),
                    ("gift", {"reply": "心意收下了。", "affinity_delta": 2}, 75, "gift", False),
                    ("spar", {"reply": "身手不错。", "affinity_delta": 2}, 75, "spar", False),
                ]
                for name, payload, affinity, purpose, pause in cases:
                    with self.subTest(case=name):
                        old = await run(payload, legacy=True, affinity=affinity, purpose=purpose, pause=pause)
                        new = await run(payload, affinity=affinity, purpose=purpose, pause=pause)
                        self.assertEqual(new, old)
                        print(f"EQUIVALENT {name}: affinity={new['affinity']} acts={len(new['acts'])} logs={new['logged']} memory={new['memories']} end={new['end']}")

                state.update(fail_attempts=1)
                retried = await run({"reply": "拿好。", "affinity_delta": 2,
                                     "acts": [{"tool": "give_item", "args": {"item": "小回春散", "count": 1}}]})
                self.assertFalse(retried["fallback"])
                self.assertEqual((retried["logged"], retried["memories"], retried["affinity"], retried["calls"]), (1, 1, 77, 2))
                state.update(always_fail=True)
                fallback = await run({"reply": "拿好。", "affinity_delta": 2})
                self.assertTrue(fallback["fallback"])
                self.assertEqual((fallback["logged"], fallback["memories"], fallback["affinity"], fallback["calls"]), (0, 0, 75, 2))
                state.update(always_fail=False, reset=True)
                stale = await run({"reply": "拿好。", "affinity_delta": 2, "end": True,
                                   "acts": [{"tool": "give_item", "args": {"item": "小回春散"}}]})
                self.assertTrue(stale["stale"])
                self.assertEqual((stale["logged"], stale["memories"], stale["affinity"], stale["turn_delta"]), (0, 0, 75, 0))
                self.assertFalse(stale["end"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _tmp.cleanup()
