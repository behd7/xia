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
import os
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

# 必须早于 `app.main` 的 import 设置，因为 main 在模块级用 settings 构造 Database。
_tmp = tempfile.mkdtemp(prefix="xia_smoke_")
os.environ["DB_PATH"] = str(Path(_tmp) / "xia.db")
os.environ["CHROMA_DIR"] = str(Path(_tmp) / "chroma")
os.environ["SCHEDULER_ENABLED"] = "false"

# 把 server 根（含 `app` 包）放进 path，保证从任意 cwd 都能跑。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAIL = 0


def check(label: str, cond: bool) -> None:
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


async def main() -> int:
    from app.main import app, ollama
    from app.game import data

    # -- stub LLM：chat 按 purpose 返回确定 JSON；embed 返回确定向量（仅测通路） --
    async def fake_chat(purpose, messages, *, json_format=False, temperature=0.7, metadata=None):
        if purpose == "dialogue":
            text = '{"reply":"阁下所问，某自有计较。","affinity_delta":2,"end":false}'
        elif purpose == "dialogue_summary":
            text = '{"events":[{"content":"与玩家交谈甚欢。","importance":3}]}'
        else:
            text = "{}"
        return type("R", (), {"text": text, "in_tokens": 10, "out_tokens": 8})()

    async def fake_embed(texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]  # 1024 维单位向量

    async def fake_tags():
        return ["qwen3:4b-instruct", "qwen3-embedding:0.6b"]

    ollama.chat, ollama.embed, ollama.tags = fake_chat, fake_embed, fake_tags

    async with app.router.lifespan_context(app):
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

            print("\n== gift writes memory (npc 5 = 王大柱/smith) ==")
            r = await c.post("/api/npc/5/gift", json={"item_id": potion["id"]})
            check("gift smith", r.status_code == 200 and r.json()["affinity"] > 25)

            print("\n== dialogue (stubbed LLM) ==")
            r = await c.post("/api/npc/1/talk", json={"message": "先生安好？"})
            check("talk returns reply", r.status_code == 200 and len(r.json()["reply"]) > 0)
            check("affinity clamped to [0,100]", 0 <= r.json()["affinity"] <= 100)

            print("\n== debug/npc includes memory ==")
            d = (await c.get("/api/debug/npc/5")).json()
            check("debug shows gift memory", any("送礼" in m["content"] for m in d["memories"]))

    shutil.rmtree(_tmp, ignore_errors=True)
    return FAIL


if __name__ == "__main__":
    code = asyncio.run(main())
    print("\nSMOKE " + ("PASS" if code == 0 else f"FAIL ({code} checks)"))
    sys.exit(code)