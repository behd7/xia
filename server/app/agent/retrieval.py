"""长期召回的检索原语（docs/MEMORY-V2-PLAN.md §8.2 / §14）。

纯函数 + 纯内存结构，不依赖第三方库（图只有几十个节点，暴力算比引入
networkx/scipy 更省事）：

- `BM25Index`：中文用 char 2-gram、ASCII 按整词，免去分词依赖；
- `rrf`：加权 Reciprocal Rank Fusion（不同路的分数尺度不可比，只能融名次）；
- `dynamic_threshold`：百分位 75 / 均值−1.5σ / 跳变（以 p40 封顶）取中位数再夹区间；
- `entity_links`：闭集实体链接（花名册 / POI / 物品 / 功法），等价于 Aho-Corasick 的朴素版；
- `CooccurGraph`：实体共现图的多跳扩散与 Personalized PageRank；
- `dedupe`：内容去重（包含关系 + 字符 2-gram Jaccard）。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

_ASCII_WORD = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_PUNCT = re.compile(r"[\s，。、；：？！“”‘’（）《》…—·,.!?;:'\"()\[\]<>/\\|`~@#$%^&*+=_-]+")


def tokenize(text: str) -> list[str]:
    """中文按 2-gram、ASCII 按整词切；标点全部丢掉。"""
    text = (text or "").lower()
    tokens: list[str] = [w for w in _ASCII_WORD.findall(text) if len(w) > 1 or w.isdigit()]
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class BM25Index:
    """极小语料（几十~几百条）的 BM25，纯 Python 暴力算。"""

    def __init__(self, docs: Sequence[tuple[str, str]], *, k1: float = 1.2,
                 b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._ids: list[str] = []
        self._docs: list[list[str]] = []
        self._freqs: list[dict[str, int]] = []
        self._df: dict[str, int] = {}
        self._len: list[int] = []
        for doc_id, text in docs:
            tokens = tokenize(text)
            freq: dict[str, int] = {}
            for token in tokens:
                freq[token] = freq.get(token, 0) + 1
            self._ids.append(str(doc_id))
            self._docs.append(tokens)
            self._freqs.append(freq)
            self._len.append(len(tokens))
            for token in freq:
                self._df[token] = self._df.get(token, 0) + 1
        total = sum(self._len) or 1
        self._avgdl = total / max(1, len(self._len))
        self._n = len(self._ids)

    def __len__(self) -> int:
        return self._n

    def search(self, query: str, k: int = 20) -> list[tuple[str, float]]:
        if not self._n:
            return []
        terms = tokenize(query)
        if not terms:
            return []
        scores: list[tuple[str, float]] = []
        for index, doc_id in enumerate(self._ids):
            freq = self._freqs[index]
            dl = self._len[index] or 1
            score = 0.0
            for term in terms:
                tf = freq.get(term)
                if not tf:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / self._avgdl)
                score += idf * tf * (self.k1 + 1.0) / denom
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda pair: -pair[1])
        return scores[:max(1, k)]


def rrf(ranked: Sequence[tuple[Sequence[tuple[str, float]], float]],
        *, k: int = 60) -> list[tuple[str, float]]:
    """按名次融合多路召回：score = Σ weight/(k + rank)。"""
    merged: dict[str, float] = {}
    for rows, weight in ranked:
        for rank, (doc_id, _score) in enumerate(rows, 1):
            merged[doc_id] = merged.get(doc_id, 0.0) + weight / (k + rank)
    return sorted(merged.items(), key=lambda pair: -pair[1])


def dynamic_threshold(scores: Sequence[float], *, floor: float, ceil: float,
                      percentile: float = 75.0, std_multiplier: float = 1.5,
                      min_results: int = 2) -> float:
    """三统计量取中位数：p75 / mean−1.5σ / 跳变（以 p40 封顶），再夹到 [floor, ceil]。

    候选池只有十几条时，跳变检测最容易把结果砍太狠，所以用 p40 封顶——
    这与同类实现的处理一致（显式 GAP 模式下才用原始断层）。
    """
    values = sorted((float(v) for v in scores), reverse=True)
    if not values:
        return floor
    if len(values) < min_results:
        return floor
    p75 = _percentile(values, percentile)
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    std_dev = mean - std_multiplier * std
    gap = _gap_threshold(values)
    gap = min(gap, _percentile(values, 40.0))
    threshold = sorted([p75, std_dev, gap])[1]
    return min(ceil, max(floor, threshold))


def _percentile(sorted_desc: Sequence[float], pct: float) -> float:
    if not sorted_desc:
        return 0.0
    if len(sorted_desc) == 1:
        return float(sorted_desc[0])
    position = (len(sorted_desc) - 1) * (1.0 - pct / 100.0)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(sorted_desc) - 1)
    frac = position - lower
    return float(sorted_desc[lower] * (1 - frac) + sorted_desc[upper] * frac)


def _gap_threshold(sorted_desc: Sequence[float]) -> float:
    if len(sorted_desc) < 2:
        return float(sorted_desc[0]) if sorted_desc else 0.0
    drops = [sorted_desc[i] - sorted_desc[i + 1] for i in range(len(sorted_desc) - 1)]
    index = max(range(len(drops)), key=lambda i: drops[i])
    return (sorted_desc[index] + sorted_desc[index + 1]) / 2.0


def entity_links(text: str, vocabulary: Iterable[str]) -> set[str]:
    """闭集实体链接：长词优先，命中即记（不做歧义消解，词表本来就很小）。"""
    text = text or ""
    hits: set[str] = set()
    for term in sorted({v for v in vocabulary if v}, key=len, reverse=True):
        if term in text:
            hits.add(term)
            text = text.replace(term, "\x00")  # 避免短词重复命中同一段字符
    return hits


class CooccurGraph:
    """实体共现图：节点是实体，边权是共现次数。规模小，全部内存态。"""

    def __init__(self) -> None:
        self._adj: dict[str, dict[str, float]] = {}

    def __len__(self) -> int:
        return len(self._adj)

    def add_edge(self, left: str, right: str, weight: float = 1.0) -> None:
        if not left or not right or left == right:
            return
        self._adj.setdefault(left, {})
        self._adj.setdefault(right, {})
        self._adj[left][right] = self._adj[left].get(right, 0.0) + weight
        self._adj[right][left] = self._adj[right].get(left, 0.0) + weight

    def neighbors(self, node: str) -> dict[str, float]:
        return dict(self._adj.get(node, {}))

    def multi_hop(self, seeds: Iterable[str], *,
                  decays: Sequence[float] = (1.0, 0.75, 0.55),
                  max_depth: int = 2) -> dict[str, float]:
        """从种子实体向外扩散，逐跳衰减；同一实体取最高分。"""
        scores: dict[str, float] = {}
        frontier: dict[str, float] = {s: decays[0] for s in seeds if s}
        for depth in range(0, max_depth + 1):
            nxt: dict[str, float] = {}
            for node, score in frontier.items():
                scores[node] = max(scores.get(node, 0.0), score)
                if depth >= max_depth or depth + 1 >= len(decays):
                    continue
                decay = decays[depth + 1]
                for nbr, weight in self._adj.get(node, {}).items():
                    nxt[nbr] = max(nxt.get(nbr, 0.0), score * decay)
            frontier = nxt
            if not frontier:
                break
        return scores

    def ppr(self, seeds: dict[str, float], *, alpha: float = 0.85,
            max_iter: int = 100, tol: float = 1e-8) -> dict[str, float]:
        """Personalized PageRank：种子做重启分布，返回全图分数（按种子质量归一）。"""
        nodes = list(self._adj)
        if not nodes:
            return {}
        total = sum(seeds.values()) or 1.0
        restart = {node: seeds.get(node, 0.0) / total for node in nodes}
        rank = dict(restart)
        for _ in range(max_iter):
            nxt = {node: (1.0 - alpha) * restart[node] for node in nodes}
            dangling = 0.0
            for node in nodes:
                weight = sum(self._adj[node].values())
                if weight <= 0:
                    dangling += rank[node]
                    continue
                share = alpha * rank[node] / weight
                for nbr, edge in self._adj[node].items():
                    nxt[nbr] = nxt.get(nbr, 0.0) + share * edge
            if dangling:
                for node in nodes:
                    nxt[node] += alpha * dangling * restart[node]
            delta = sum(abs(nxt[node] - rank[node]) for node in nodes)
            rank = nxt
            if delta < tol:
                break
        return rank


def _normalize(text: str) -> str:
    return _PUNCT.sub("", (text or "").lower())


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(max(0, len(text) - 1))} or {text}


def dedupe(items: Sequence[dict], *, jaccard: float = 0.6,
           key: str = "content") -> list[dict]:
    """去重：包含关系（≥8 字）+ 字符 2-gram Jaccard；保留先出现的那条。"""
    kept: list[dict] = []
    seen: list[str] = []
    for item in items:
        text = _normalize(str(item.get(key) or ""))
        if not text:
            continue
        duplicate = False
        for other in seen:
            if len(text) >= 8 and len(other) >= 8 and (text in other or other in text):
                duplicate = True
                break
            left, right = _bigrams(text), _bigrams(other)
            if left and right:
                overlap = len(left & right) / len(left | right)
                if overlap >= jaccard:
                    duplicate = True
                    break
        if duplicate:
            continue
        seen.append(text)
        kept.append(item)
    return kept
