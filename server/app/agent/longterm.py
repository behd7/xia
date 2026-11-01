"""长期记忆召回（docs/MEMORY-V2-PLAN.md P5）。

v2 的长期记忆只写不读；P5 把读路径补上：对话前用它回答「这个 NPC 是否
记得很久以前的某件事」。三路候选（向量 / BM25 / 实体图）各自排序后按加权
RRF 融合，但**阈值始终落在余弦分数上**——RRF 的分数是名次派生的，没有绝对
含义，拿它做阈值没有解释力。

召回是节流的（缓存 TTL → 最短间隔 → 最少新增轮数），因为它每轮都要跑一次
Chroma 查询，而长期记忆的变化频率极低（每 36 轮才多一条）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from ..config import settings
from ..db import Database
from . import retrieval

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    id: int
    content: str
    ts_game: float
    poi: str
    participants: list[str]
    turn_range: tuple[int, int] | None
    score: float = 0.0
    path: str = "dense"

    def as_dict(self) -> dict:
        return {"id": self.id, "content": self.content,
                "score": round(self.score, 3), "path": self.path,
                "turn_range": self.turn_range}


@dataclass
class _State:
    cached: list[dict] = field(default_factory=list)
    cached_at: float = 0.0
    last_recall_at: float = 0.0
    last_turn: int = 0
    primed: bool = False


class LongTermRecall:
    """每 NPC 一份节流状态；`reset()` 在 new_game 时调用。"""

    def __init__(self) -> None:
        self._states: dict[int, _State] = {}

    def reset(self) -> None:
        self._states.clear()

    def reset_npc(self, npc_id: int) -> None:
        self._states.pop(npc_id, None)

    def snapshot(self, npc_id: int) -> dict:
        """调试用：最近一次召回缓存与节流时间点。"""
        state = self._states.get(npc_id)
        if state is None:
            return {"cached": [], "cached_at": None, "last_recall_at": None,
                    "last_turn": None}
        return {
            "cached": list(state.cached),
            "cached_at": state.cached_at or None,
            "last_recall_at": state.last_recall_at or None,
            "last_turn": state.last_turn,
        }

    async def recall(self, db: Database, store, npc_id: int, *,
                     query_text: str, query_vec: list[float], turn_no: int,
                     now_real: float | None = None) -> list[dict]:
        if not settings.longterm_recall_enabled:
            return []
        now = time.time() if now_real is None else now_real
        state = self._states.setdefault(npc_id, _State())
        if state.cached and now - state.cached_at < settings.longterm_recall_cache_ttl:
            return state.cached
        if not state.primed:
            # 首次召回直接放行：不该让 NPC 等 30 轮才想起第一件旧事。
            state.primed = True
            state.last_turn = turn_no - settings.longterm_recall_min_new_turns
        if state.last_recall_at and now - state.last_recall_at < settings.longterm_recall_min_interval:
            return []
        if turn_no - state.last_turn < settings.longterm_recall_min_new_turns:
            return []
        hits = await self._search(db, store, npc_id, query_text=query_text, query_vec=query_vec)
        state.last_recall_at = now
        state.last_turn = turn_no
        state.cached = hits
        state.cached_at = now
        return hits

    async def _search(self, db: Database, store, npc_id: int, *,
                      query_text: str, query_vec: list[float]) -> list[dict]:
        rows = await db.fetch_all(
            "SELECT id, content, ts_game, poi, participants_json,"
            " turn_range_start, turn_range_end"
            " FROM longterm_memory WHERE npc_id = ? ORDER BY ts_game DESC",
            (npc_id,),
        )
        if not rows:
            return []
        by_id = {int(r["id"]): r for r in rows}
        dense = self._dense(store, npc_id, query_vec, len(by_id))
        if not dense:
            return []

        threshold = settings.longterm_min_score
        if settings.longterm_dynamic_threshold:
            threshold = retrieval.dynamic_threshold(
                [score for _id, score in dense],
                floor=settings.longterm_dynamic_floor,
                ceil=settings.longterm_dynamic_ceil,
            )
            # 动态阈值在候选少时会砍到一个不剩；保底取回 top-2（同类实现的 min(2,K) 兜底）。
            admitted = [(i, s) for i, s in dense if s >= threshold]
            if len(admitted) < 2:
                admitted = dense[:2]
        else:
            admitted = [(i, s) for i, s in dense if s > threshold]
        if not admitted:
            if settings.longterm_fallback_enabled:
                return self._fallback(rows)
            return []

        ranks = {"dense": admitted}
        sparse = self._sparse(rows, query_text)
        if settings.longterm_hybrid_enabled and sparse:
            ranks["sparse"] = sparse
        graph = self._graph(rows, query_text)
        if settings.longterm_graph_enabled and graph:
            ranks["graph"] = graph

        cosine = dict(admitted)
        if len(ranks) == 1:
            ordered = [(str(i), score) for i, score in admitted]
        else:
            weights = self._weights(ranks)
            ordered = retrieval.rrf(
                [([(str(i), score) for i, score in rows_], weights[name])
                 for name, rows_ in ranks.items()],
                k=settings.longterm_rrf_k,
            )

        hits: list[dict] = []
        budget = settings.longterm_recall_max_chars
        for doc_id, score in ordered:
            row = by_id.get(int(doc_id))
            if row is None:
                continue
            content = str(row["content"] or "").strip()
            if not content or len(content) > budget:
                continue
            hits.append(Hit(
                id=int(doc_id),
                content=content,
                ts_game=float(row["ts_game"] or 0.0),
                poi=str(row["poi"] or ""),
                participants=_json_list(row["participants_json"]),
                turn_range=_turn_range(row),
                score=cosine.get(int(doc_id), score),
                path="fused" if len(ranks) > 1 else "dense",
            ).as_dict())
            budget -= len(content)
            if len(hits) >= settings.longterm_recall_limit:
                break
        return retrieval.dedupe(hits)

    def _weights(self, ranks: dict[str, list[tuple[int, float]]]) -> dict[str, float]:
        weights = {
            "dense": settings.longterm_dense_weight,
            "sparse": settings.longterm_sparse_weight,
            "graph": 0.15,
        }
        total = sum(weights[name] for name in ranks) or 1.0
        return {name: weights[name] / total for name in ranks}

    def _dense(self, store, npc_id: int, query_vec: list[float],
               known: int) -> list[tuple[int, float]]:
        k = max(1, min(settings.longterm_candidate_k, known))
        try:
            result = store.query_longterm(npc_id, query_vec, k)
        except Exception as exc:  # 向量库异常不该拖垮对话
            logger.warning("longterm dense query failed: %s: %s", type(exc).__name__, exc)
            return []
        ids = (result.get("ids") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        scored = [(int(i), 1.0 - float(d)) for i, d in zip(ids, dists)]
        scored.sort(key=lambda pair: -pair[1])
        return scored

    def _sparse(self, rows: list[dict], query_text: str) -> list[tuple[int, float]]:
        if not query_text:
            return []
        index = retrieval.BM25Index([(str(r["id"]), str(r["content"] or "")) for r in rows])
        hits = index.search(query_text, settings.longterm_candidate_k)
        return [(int(i), s) for i, s in hits]

    def _graph(self, rows: list[dict], query_text: str) -> list[tuple[int, float]]:
        vocabulary = _vocabulary(rows)
        seeds = retrieval.entity_links(query_text, vocabulary)
        if not seeds:
            return []
        graph = retrieval.CooccurGraph()
        for row in rows:
            entities = retrieval.entity_links(
                " ".join([str(row["content"] or ""), str(row["poi"] or "")]
                         + _json_list(row["participants_json"])),
                vocabulary,
            )
            entities = sorted(entities)
            for i, left in enumerate(entities):
                for right in entities[i + 1:]:
                    graph.add_edge(left, right)
        if not len(graph):
            return []
        rank = graph.ppr({seed: 1.0 for seed in seeds})
        scored: list[tuple[int, float]] = []
        for row in rows:
            entities = retrieval.entity_links(
                str(row["content"] or "") + str(row["poi"] or ""), vocabulary)
            score = sum(rank.get(e, 0.0) for e in entities)
            if score > 0:
                scored.append((int(row["id"]), score))
        scored.sort(key=lambda pair: -pair[1])
        return scored[:settings.longterm_candidate_k]

    def _fallback(self, rows: list[dict]) -> list[dict]:
        hits: list[dict] = []
        for row in rows[:settings.longterm_recall_limit]:
            content = str(row["content"] or "").strip()
            if not content:
                continue
            hits.append(Hit(
                id=int(row["id"]), content=content,
                ts_game=float(row["ts_game"] or 0.0),
                poi=str(row["poi"] or ""),
                participants=_json_list(row["participants_json"]),
                turn_range=_turn_range(row), score=0.0, path="fallback",
            ).as_dict())
        return hits


def _json_list(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
    else:
        value = raw
    return [str(v) for v in value] if isinstance(value, list) else []


def _turn_range(row: dict) -> tuple[int, int] | None:
    start, end = row["turn_range_start"], row["turn_range_end"]
    if start is None and end is None:
        return None
    return int(start or 0), int(end or 0)


def _vocabulary(rows: list[dict]) -> set[str]:
    terms: set[str] = set()
    for row in rows:
        terms.update(_json_list(row["participants_json"]))
        poi = str(row["poi"] or "").strip()
        if poi:
            terms.add(poi)
    return terms
