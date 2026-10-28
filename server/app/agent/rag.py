"""RAG 检索（docs/DESIGN.md §6.4）：Chroma ANN 取 top20 -> 应用侧三因子重排取 top6。

Chroma 只算向量相似度；recency/importance 加权必须自己排（Chroma 元数据
不支持排序）。半衰期 3 游戏日、权重 w_rel/w_rec/w_imp 走 settings。
"""

import json
import math

from ..db import Database


async def recent_turns(db: Database, npc_id: int, n: int = 3) -> list[dict]:
    """最近 n 条对话平铺为 {user, npc} 列表（DESIGN §6.4 最近 3 轮不走检索）。"""
    rows = await db.fetch_all(
        "SELECT turns_json FROM conversation WHERE npc_id = ? ORDER BY id DESC LIMIT ?",
        (npc_id, n),
    )
    turns: list[dict] = []
    for r in reversed(rows):
        try:
            data = json.loads(r["turns_json"])
            turns.append({
                "user": data.get("user", ""),
                "npc": data.get("npc", ""),
            })
        except Exception:
            continue
    return turns


async def embed_query(store, query: str) -> list[float]:
    """查询向量只算一次，供 Chroma RAG 与中期线索匹配共用。"""
    return (await store.llm.embed([query]))[0]


async def retrieve(db: Database, store, npc_id: int, query: str, *, settings,
                   qvec: list[float] | None = None) -> list[dict]:
    """Chroma ANN + 三因子重排，返回按综合分降序的 TOP_K 条记忆。"""
    qvec = qvec if qvec is not None else await embed_query(store, query)
    res = store.query(npc_id, qvec, n_results=20)
    ids = res.get("ids", [[]])[0]
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]

    now = store.clock.now()
    scored: list[tuple[float, str, dict, str]] = []
    for i in range(len(ids)):
        m = metas[i]
        try:
            ts = float(m.get("ts_game", 0))
        except (TypeError, ValueError):
            ts = 0.0
        sim = 1.0 - float(dists[i])                      # cosine distance -> similarity
        age_days = (now - ts) / 86400.0
        rec = math.exp(-math.log(2) * age_days / settings.recency_half_life)
        imp = float(m.get("importance", 3)) / 10.0
        score = settings.w_rel * sim + settings.w_rec * rec + settings.w_imp * imp
        scored.append((score, ids[i], m, docs[i]))

    scored.sort(key=lambda t: -t[0])
    return [
        {
            "score": round(s, 4), "memory_id": ids_i, "ts_game": m.get("ts_game"),
            "importance": m.get("importance"), "poi": m.get("poi"),
            "content": doc, "kind": m.get("kind", "event"),
        }
        for s, ids_i, m, doc in scored[: settings.rag_top_k]
    ]