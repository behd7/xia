#!/usr/bin/env python3
"""长期召回标定（docs/MEMORY-V2-PLAN.md P5 / §8.2）。

回答三件事，全部用**真实对话后端 + 真实嵌入**：

1. `LONGTERM_MIN_SCORE` 该定多少（逐条二元判定，与中期同一口径）；
2. 动态阈值（`LONGTERM_DYNAMIC_THRESHOLD`）相对固定阈值是加分还是减分；
3. 稀疏 BM25 路、实体图 + PPR 这两路混进来，检索质量是涨还是跌。

做法：把 6 个主题各总结成一条长期记忆（等于 6 个「36 轮归档」），语料就是
这 6 条——这正是 Xia 里单个 NPC 的规模。探针复用中期标定的那套（相关 3 / 无关 3）。

用法（server 目录下）：
    NO_PROXY=localhost,127.0.0.1 .venv/bin/python tools/calibrate_longterm.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_midterm as base  # noqa: E402  复用同一套语料与探针

from app.agent import dialogue as dialogue_mod  # noqa: E402
from app.agent import retrieval  # noqa: E402
from app.agent import summary as summary_mod  # noqa: E402
from app.agent.memory import MemoryStore  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.llm import OllamaClient  # noqa: E402

NPC_ID = 1


class _Clock:
    def now(self) -> float:
        return 0.0


class _Store:
    def __init__(self, llm) -> None:
        self.llm = llm


def _q(window, message: str) -> str:
    """与生产一致：把探针窗口拼成 12 条消息的长 query。"""
    return dialogue_mod._midterm_query(
        [{"user": u, "npc": n} for u, n in window], message)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _sweep(related: list[float], unrelated: list[float]) -> str:
    lines = ["τ 扫描（逐条二元判定，与中期同口径）："]
    best = -9.0
    for i in range(13):
        tau = 0.30 + i * 0.05
        recall = sum(1 for s in related if s > tau) / len(related)
        fpr = sum(1 for s in unrelated if s > tau) / len(unrelated)
        j = recall - fpr
        mark = ""
        if j > best:
            best = j
        lines.append(f"  τ={tau:.2f}  recall={recall:.2f}  FPR={fpr:.2f}  J={j:+.2f}")
    lines.append(f"  （最大 J={best:+.2f}）")
    return "\n".join(lines)


async def _evaluate(memo, db, mem_store, entries, probes, label: str) -> str:
    """跑一遍检索质量：相关 query 是否召回「对的那条」，误召回其它主题算噪声。"""
    wins = 0
    noise = 0
    topics = 0
    wrong_theme = 0
    for index, queries in enumerate(probes):
        for text in queries:
            hits = await mem_store.longterm._search(
                db, mem_store, NPC_ID, query_text=text, query_vec=await memo.qvec(text))
            ids = [h["id"] for h in hits]
            if ids:
                topics += 1
            if entries[index]["id"] in ids:
                wins += 1
            else:
                wrong_theme += 1
            noise += max(0, len(ids) - (1 if entries[index]["id"] in ids else 0))
    total = sum(len(q) for q in probes)
    return (f"{label:<26} 命中正确条目={wins}/{total} "
            f"漏召={wrong_theme}/{total} 多带噪声={noise} 有返回的查询={topics}/{total}")


class _EmbedMemo:
    """同一条文本只嵌入一次（探针在两轮评估里重复出现）。"""

    def __init__(self, llm) -> None:
        self.llm = llm
        self._cache: dict[str, list[float]] = {}

    async def qvec(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = (await self.llm.embed([text]))[0]
        return self._cache[text]


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="xia_lt_")
    db = Database(Path(tmp) / "lt.db")
    await db.connect()
    llm = OllamaClient(settings.ollama_host, settings.llm_timeout, db)
    store = _Store(llm)
    memo = _EmbedMemo(llm)
    mem_store = MemoryStore(Path(tmp) / "chroma", llm, _Clock())
    now = time.time()
    print(f"chat={settings.llm_api_model or settings.chat_model} "
          f"embed={settings.embed_model} 语料=6 条长期记忆\n")

    entries = []
    try:
        for index, block in enumerate(base.BLOCKS):
            turns = [{"user": u, "npc": n, "place": block["place"]}
                     for u, n in block["turns"]]
            result = await summary_mod.summarize_longterm(
                store, NPC_ID, base.NPC_NAME, base.PLAYER_NAME, turns)
            content = result["summary"]
            vec = (await llm.embed([content]))[0]
            row_id = await db.execute(
                "INSERT INTO longterm_memory(npc_id, ts_game, content, turn_count,"
                " created_at_real, turn_range_start, turn_range_end,"
                " participants_json, poi, importance) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (NPC_ID, now, content, 36, now, index * 36, index * 36 + 36,
                 '["%s"]' % base.PLAYER_NAME, block["place"], 5))
            mem_store._lt_coll.add(
                ids=[str(row_id)], embeddings=[vec], documents=[content],
                metadatas=[{"npc_id": str(NPC_ID), "ts_game": now,
                            "turn_range_end": index * 36 + 36, "poi": block["place"]}])
            entries.append({"id": row_id, "content": content, "vec": vec,
                            "theme": block["place"]})
            print(f"[{index + 1}] {block['place']}: {content}")

        # 探针：与生产一致，用 12 条消息拼接的长 query。
        related: list[float] = []
        unrelated: list[float] = []
        probe_texts: list[list[str]] = []
        for index, probe in enumerate(base.PROBES):
            texts: list[str] = []
            for kind, bucket in (("related", related), ("unrelated", unrelated)):
                for window in probe[kind]:
                    text = _q(window, window[-1][0])
                    if kind == "related":
                        texts.append(text)
                    vec = await memo.qvec(text)
                    bucket.append(_dot(vec, entries[index]["vec"]))
            probe_texts.append(texts)

        print("\n" + _sweep(related, unrelated))
        print(f"相关分：中位数 {statistics.median(related):.3f} "
              f"min {min(related):.3f} max {max(related):.3f}")
        print(f"无关分：中位数 {statistics.median(unrelated):.3f} "
              f"min {min(unrelated):.3f} max {max(unrelated):.3f}")

        # 动态阈值会落在哪：把每次召回的候选分数分布打印出来。
        tau = retrieval.dynamic_threshold(related + unrelated,
                                          floor=settings.longterm_dynamic_floor,
                                          ceil=settings.longterm_dynamic_ceil)
        print(f"\n动态阈值在合并分布上的取值：{tau:.3f}"
              f"（夹 [{settings.longterm_dynamic_floor}, {settings.longterm_dynamic_ceil}]）\n")

        print("检索质量（top-3）：")
        settings.longterm_recall_limit = 3
        settings.longterm_hybrid_enabled = False
        settings.longterm_graph_enabled = False
        settings.longterm_dynamic_threshold = False
        for tau in (0.40, 0.45, 0.50):
            settings.longterm_min_score = tau
            print(await _evaluate(memo, db, mem_store, entries, probe_texts,
                                  f"① 仅向量 τ={tau:.2f}"))
        settings.longterm_min_score = 0.45
        settings.longterm_hybrid_enabled = True
        print(await _evaluate(memo, db, mem_store, entries, probe_texts, "② +BM25(RRF) τ=0.45"))
        settings.longterm_graph_enabled = True
        print(await _evaluate(memo, db, mem_store, entries, probe_texts, "③ +BM25+图/PPR τ=0.45"))
        settings.longterm_hybrid_enabled = False
        print(await _evaluate(memo, db, mem_store, entries, probe_texts, "④ +实体图/PPR τ=0.45"))
        settings.longterm_graph_enabled = False
        settings.longterm_dynamic_threshold = True
        print(await _evaluate(memo, db, mem_store, entries, probe_texts, "⑤ 动态阈值（现夹区间）"))
        print("\n说明：① 是现行实现；②③④⑤ 需要改配置才会生效。"
              "噪声=多带出来的其它主题条目，会挤占注入预算。")
    finally:
        await llm.close()
        await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
