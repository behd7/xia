"""记忆双库（docs/DESIGN.md §6.3 / §8）：SQLite 为记忆 SSOT，Chroma 为派生向量索引。

写入选句：先 SQLite 落库分配 id -> 同一写锁内 embed -> Chroma upsert
（AGENTS.md #6：先 SQLite 后 Chroma）。换 embedding 模型或索引损坏时，
删 Chroma 集合从 SQLite 全量重建即可——向量不进 SQLite 正是为留这条退路。
Chroma 集合恒建为 embedding_function=None + hnsw:space=cosine，add/query
恒定传 embeddings（AGENTS.md ChromaDB 三坑）。

账本模型（docs/MEMORY-V2-PLAN.md）：短期与中期都是 `conversation` 的行，
用 `kind` 区分、用绝对轮号 `turn_no` 排序；窗口 = `turn_no > last_midterm_turn`，
归档只推进游标、不删原文。长期每 36 轮原始对话在后台总结，写独立的
`longterm_memory` 表（v2 只写不读，不参与反思/关系/八卦）。
"""

import asyncio
import base64
import contextvars
import json
import logging
import struct
import time
from dataclasses import dataclass

import chromadb
from chromadb.config import Settings

from ..config import settings
from ..db import Database
from . import summary as summary_mod
from .longterm import LongTermRecall

logger = logging.getLogger(__name__)

MIDTERM_KIND = "midterm"
TURN_KIND = "turn"


def pack_vec(vec: list[float]) -> str:
    """float32 -> base64，中期线索向量随摘要行一起存 SQLite（重启免重嵌入）。"""
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")


def unpack_vec(blob: str) -> list[float]:
    raw = base64.b64decode(blob)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def best_midterm(
    hits: list[tuple[int, list[list[float]]]], query_vec: list[float], threshold: float
) -> tuple[int, float] | None:
    """在 (行 id, 线索向量) 候选里取「最高分线索」最高的一条；必须严格高于阈值。"""
    best: tuple[int, float] | None = None
    for row_id, vectors in hits:
        if not vectors:
            continue
        score = max(dot(query_vec, vec) for vec in vectors)
        if score > threshold and (best is None or score > best[1]):
            best = (row_id, score)
    return best


@dataclass
class MidTerm:
    """中期记忆：单条 summary + 线索 + 线索向量；持久化在 conversation.kind='midterm'。"""

    summary: str
    clues: list[str]
    vectors: list[list[float]]
    ts_game: float
    turn_count: int
    turn_range: tuple[int, int] | None = None
    id: int | None = None
    score: float = 0.0


@dataclass
class TurnOutcome:
    midterm_created: bool = False
    longterm_scheduled: bool = False
    stale: bool = False


@dataclass
class LongTermJob:
    """一条待写入 longterm_memory 的后台任务；generation 用于新局作废旧任务。"""

    db: Database
    generation: int
    npc_id: int
    turn_start: int
    turn_end: int
    ts_game: float
    npc_name: str
    player_name: str


@dataclass
class _Barrier:
    future: asyncio.Future


_STOP = object()
_SHUTDOWN_GRACE = 5.0


class MemoryStore:
    """Chroma 索引、记忆写锁、中期召回冷却与长期总结 worker 的持有者。"""

    def __init__(self, chroma_dir, llm, clock) -> None:
        self.llm = llm
        self.clock = clock
        self._client = chromadb.PersistentClient(
            path=str(chroma_dir), settings=Settings(anonymized_telemetry=False)
        )
        self._coll = self._client.get_or_create_collection(
            "memories", embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        # 长期记忆的派生索引：只索引 longterm_memory；读路径见 agent/longterm.py（P5）。
        self._lt_coll = self._client.get_or_create_collection(
            "longterm", embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        self._lock = asyncio.Lock()
        self._window_locks: dict[int, asyncio.Lock] = {}
        # 中期条目与长期原文都在 SQLite；这里只留召回冷却。
        self._recalled_at: dict[int, dict[int, int]] = {}
        # 长期召回的节流状态挂在 store 上，随 new_game 一起清空。
        self.longterm = LongTermRecall()
        # 长期总结走单 worker 队列；generation 在 new_game 时递增，旧局任务据此丢弃。
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._generation = 0
        self._draining = False
        self._closed = False
        # Phase C：附近显著事件的进程内环形缓冲，不落库。
        self.recent_events: list[dict] = []

    @property
    def generation(self) -> int:
        return self._generation

    def start(self) -> None:
        self._closed = False
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._closed:
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._longterm_worker(),
                context=contextvars.Context(),  # 不继承请求侧 Langfuse trace
                name="longterm-worker",
            )

    async def add_memory(
        self,
        db: Database,
        npc_id: int,
        *,
        content: str,
        kind: str = "event",
        participants: list[str] | None = None,
        poi: str | None = None,
        importance: int = 3,
        source_memory_id: int | None = None,
    ) -> int:
        """Persist one memory: SQLite row + Chroma vector, atomically-ordered."""
        ts_game = self.clock.now()
        async with self._lock:
            mid = await db.execute(
                "INSERT INTO memory(npc_id, ts_game, kind, content, participants_json, poi,"
                " importance, created_at_real, source_memory_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (npc_id, ts_game, kind, content,
                 json.dumps(participants or [], ensure_ascii=False),
                 poi, importance, time.time(), source_memory_id),
            )
            vec = (await self.llm.embed([content]))[0]
            self._coll.add(
                ids=[str(mid)], embeddings=[vec], documents=[content],
                metadatas=[{
                    "npc_id": str(npc_id), "kind": kind, "importance": importance,
                    "ts_game": ts_game, "poi": poi or "",
                }],
            )
        # Reflection itself must not re-trigger reflection (DESIGN §6.7).
        if kind != "reflection":
            await db.execute(
                "UPDATE npc_state SET mem_since_reflection = mem_since_reflection + 1"
                " WHERE npc_id = ?",
                (npc_id,),
            )
        return mid

    async def add_conversation_turn(
        self,
        db: Database,
        npc_id: int,
        *,
        user: str,
        npc: str,
        npc_name: str,
        player_name: str,
        place: str,
        affinity_delta: int = 0,
        generation: int,
        acts: list[dict] | None = None,
        turn_id: str = "",
    ) -> TurnOutcome:
        """写入账本一轮；窗口超水位时把最老一批归档成中期记忆。

        账本模型（docs/MEMORY-V2-PLAN.md）：原文永不删除，窗口 = `turn_no >
        last_midterm_turn` 的行；归档只推进游标，因此总结失败时游标不动，
        同一批轮下一轮自然重试。

        generation 是对话开始时捕获的局代：新局 begin_reset 后旧回合直接丢弃，
        避免旧玩家的话本写进新局的中期/长期记忆。
        """
        lock = self._window_locks.setdefault(npc_id, asyncio.Lock())
        async with lock:
            if generation != self._generation:
                logger.info("drop stale conversation turn npc=%d gen=%d current=%d",
                            npc_id, generation, self._generation)
                return TurnOutcome(stale=True)
            now_game = self.clock.now()
            turn_no, last_mid = await self._ledger_cursor(db, npc_id)
            turn_no += 1
            await db.execute(
                "UPDATE npc_state SET turn_no = ? WHERE npc_id = ?",
                (turn_no, npc_id))
            conv_id = await db.execute(
                "INSERT INTO conversation(npc_id, ts_game, turns_json,"
                " affinity_delta_total, kind, turn_no) VALUES(?,?,?,?,'turn',?)",
                (npc_id, now_game,
                 json.dumps({"user": user, "speech": npc, "npc": npc,
                             "place": place, "acts": acts or [],
                             "turn_id": turn_id},
                            ensure_ascii=False),
                 affinity_delta, turn_no),
            )
            if generation != self._generation:
                # 唯一允许的运行期删除：这一轮属于已作废的旧局，留着会被新局归档。
                await db.execute("DELETE FROM conversation WHERE id = ?", (conv_id,))
                return TurnOutcome(stale=True)

            outcome = TurnOutcome()
            capacity = max(1, settings.conversation_window_capacity)
            if turn_no - last_mid > capacity:
                per = max(1, settings.midterm_turns_per_entry)
                start, end = last_mid + 1, last_mid + per
                turns = await self._window_turns(db, npc_id, start, end)
                result = await summary_mod.summarize_midterm(
                    self, npc_id, npc_name, player_name, place, turns)
                stored = await self._store_midterm(
                    db, npc_id, result, ts_game=now_game, generation=generation,
                    turn_range=(start, end))
                if not stored:
                    await db.execute("DELETE FROM conversation WHERE id = ?", (conv_id,))
                    return TurnOutcome(stale=True)
                await db.execute(
                    "UPDATE npc_state SET last_midterm_turn = ? WHERE npc_id = ?",
                    (end, npc_id))
                outcome.midterm_created = True

            if generation != self._generation:
                await db.execute("DELETE FROM conversation WHERE id = ?", (conv_id,))
                return TurnOutcome(stale=True)
            interval = max(1, settings.longterm_turn_interval)
            last_long = await self._longterm_cursor(db, npc_id)
            if turn_no - last_long >= interval:
                self._schedule_longterm(
                    db, npc_id, last_long + 1, last_long + interval,
                    ts_game=now_game, npc_name=npc_name,
                    player_name=player_name, generation=generation)
                outcome.longterm_scheduled = True
            return outcome

    async def _ledger_cursor(self, db: Database, npc_id: int) -> tuple[int, int]:
        """读取该 NPC 的 (最新轮号, 已归档到的轮号)。"""
        row = await db.fetch_one(
            "SELECT turn_no, last_midterm_turn FROM npc_state WHERE npc_id = ?",
            (npc_id,))
        if row is None:
            return 0, 0
        return int(row["turn_no"] or 0), int(row["last_midterm_turn"] or 0)

    async def _longterm_cursor(self, db: Database, npc_id: int) -> int:
        row = await db.fetch_one(
            "SELECT last_longterm_turn FROM npc_state WHERE npc_id = ?", (npc_id,))
        return int(row["last_longterm_turn"] or 0) if row else 0

    async def _window_turns(self, db: Database, npc_id: int,
                            start: int, end: int) -> list[dict]:
        """按轮号区间取原文轮（长期总结的素材）。"""
        rows = await db.fetch_all(
            "SELECT turns_json FROM conversation WHERE npc_id = ?"
            " AND kind = ? AND turn_no BETWEEN ? AND ? ORDER BY turn_no",
            (npc_id, TURN_KIND, start, end))
        turns: list[dict] = []
        for row in rows:
            try:
                data = json.loads(row["turns_json"])
            except (TypeError, ValueError):
                continue
            turns.append({
                "user": data.get("user", ""),
                "npc": data.get("speech") or data.get("npc", ""),
                "place": data.get("place", ""),
                "acts": [str(act.get("text") or "") for act in
                         (data.get("acts") or []) if isinstance(act, dict)],
            })
        return turns

    async def _store_midterm(self, db: Database, npc_id: int, result: dict, *,
                             ts_game: float, generation: int,
                             turn_range: tuple[int, int] | None = None) -> bool:
        if generation != self._generation:
            return False
        clues = result.get("clues") or []
        vectors: list[list[float]] = []
        if clues:
            try:
                vectors = await self.llm.embed(clues)
            except Exception:
                logger.warning("midterm clue embed failed npc=%d clues=%d",
                               npc_id, len(clues))
        if generation != self._generation:
            return False
        if not clues:
            logger.warning("midterm stored without clues npc=%d; not retrievable", npc_id)
        payload = {
            "summary": result["summary"],
            "clues": clues,
            "clue_vectors": [pack_vec(vec) for vec in vectors],
            "turn_range": list(turn_range) if turn_range else [],
            "turn_count": int(result.get("turn_count", 0)),
        }
        end_turn = turn_range[1] if turn_range else 0
        row_id = await db.execute(
            "INSERT INTO conversation(npc_id, ts_game, turns_json,"
            " affinity_delta_total, kind, turn_no) VALUES(?,?,?,0,?,?)",
            (npc_id, ts_game, json.dumps(payload, ensure_ascii=False),
             MIDTERM_KIND, end_turn),
        )
        # 派生摘要的 FIFO 淘汰：每 NPC 只留最近 MIDTERM_CAPACITY 条。
        cap = max(1, settings.midterm_capacity)
        rows = await db.fetch_all(
            "SELECT id FROM conversation WHERE npc_id = ? AND kind = ?"
            " ORDER BY turn_no DESC, id DESC", (npc_id, MIDTERM_KIND))
        stale = [str(r["id"]) for r in rows[cap:]]
        if stale:
            await db.execute(
                f"DELETE FROM conversation WHERE id IN ({','.join('?' for _ in stale)})",
                stale)
        return row_id > 0

    async def recall_midterm(self, db: Database, npc_id: int,
                             query_vec: list[float]) -> list[MidTerm]:
        """用长拼接 query 匹配中期线索，返回命中的完整条目（≤MIDTERM_TOP_K）。

        阈值是严格大于；同一条在冷却期内不重复注入（MaiBot 的「已注入身份去重」
        等价物，不设累计配额）。
        """
        rows = await db.fetch_all(
            "SELECT id, ts_game, turn_no, turns_json FROM conversation"
            " WHERE npc_id = ? AND kind = ? ORDER BY turn_no DESC, id DESC LIMIT ?",
            (npc_id, MIDTERM_KIND, max(1, settings.midterm_capacity)))
        if not rows:
            return []
        turn_no, _ = await self._ledger_cursor(db, npc_id)
        now_turn = turn_no + 1  # 本轮尚未落库，按将要写入的轮号算冷却
        cooldown = max(0, settings.midterm_recall_cooldown_turns)
        seen = self._recalled_at.setdefault(npc_id, {})
        hits: list[tuple[int, list[list[float]]]] = []
        parsed: dict[int, MidTerm] = {}
        for row in rows:
            row_id = int(row["id"])
            last = seen.get(row_id)
            if cooldown and last is not None and now_turn - last < cooldown:
                continue
            try:
                data = json.loads(row["turns_json"])
                vectors = [unpack_vec(blob) for blob in data.get("clue_vectors") or []]
            except (TypeError, ValueError, KeyError):
                continue
            if not vectors:
                continue
            turn_range = data.get("turn_range") or []
            parsed[row_id] = MidTerm(
                summary=str(data.get("summary", "")),
                clues=list(data.get("clues") or []),
                vectors=vectors,
                ts_game=float(row["ts_game"] or 0.0),
                turn_count=int(data.get("turn_count") or 0),
                turn_range=(int(turn_range[0]), int(turn_range[1]))
                if len(turn_range) == 2 else None,
                id=row_id,
            )
            hits.append((row_id, vectors))
        if not hits:
            return []
        top_k = max(1, settings.midterm_top_k)
        picked: list[MidTerm] = []
        threshold = settings.midterm_min_score
        remaining = list(hits)
        for _ in range(top_k):
            best = best_midterm(remaining, query_vec, threshold)
            if best is None:
                break
            row_id, score = best
            item = parsed[row_id]
            item.score = score
            picked.append(item)
            seen[row_id] = now_turn
            remaining = [hit for hit in remaining if hit[0] != row_id]
        return picked

    def _schedule_longterm(self, db: Database, npc_id: int, turn_start: int,
                           turn_end: int, *, ts_game: float, npc_name: str,
                           player_name: str, generation: int) -> None:
        if self._closed or self._draining or generation != self._generation:
            logger.info("drop longterm schedule npc=%d gen=%d current=%d "
                        "draining=%s closed=%s",
                        npc_id, generation, self._generation,
                        self._draining, self._closed)
            return
        self._ensure_worker()
        self._queue.put_nowait(LongTermJob(
            db=db, generation=generation, npc_id=npc_id,
            turn_start=turn_start, turn_end=turn_end,
            ts_game=ts_game, npc_name=npc_name, player_name=player_name,
        ))

    async def _run_longterm(self, job: LongTermJob) -> None:
        """按轮号区间从 SQLite 取原文，总结后写 longterm_memory + `longterm` 集合。"""
        turns = await self._window_turns(
            job.db, job.npc_id, job.turn_start, job.turn_end)
        result = await summary_mod.summarize_longterm(
            self, job.npc_id, job.npc_name, job.player_name, turns)
        if job.generation != self._generation:
            logger.info("drop stale longterm result npc=%d gen=%d current=%d",
                        job.npc_id, job.generation, self._generation)
            return
        poi = ""
        for turn in reversed(turns):
            if turn.get("place"):
                poi = turn["place"]
                break
        async with self._lock:
            row_id = await job.db.execute(
                "INSERT INTO longterm_memory(npc_id, ts_game, content, turn_count,"
                " created_at_real, turn_range_start, turn_range_end,"
                " participants_json, poi, importance) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job.npc_id, job.ts_game, result["summary"], len(turns),
                 time.time(), job.turn_start, job.turn_end,
                 json.dumps([job.player_name], ensure_ascii=False),
                 poi, settings.longterm_importance),
            )
            vec = (await self.llm.embed([result["summary"]]))[0]
            self._lt_coll.add(
                ids=[str(row_id)], embeddings=[vec], documents=[result["summary"]],
                metadatas=[{
                    "npc_id": str(job.npc_id), "ts_game": job.ts_game,
                    "turn_range_end": job.turn_end, "poi": poi,
                }],
            )
        if job.generation != self._generation:
            await job.db.execute(
                "DELETE FROM longterm_memory WHERE id = ?", (row_id,))
            try:
                self._lt_coll.delete(ids=[str(row_id)])
            except Exception:
                logger.warning("longterm vector delete failed id=%d", row_id)
            logger.info("drop stale longterm row npc=%d gen=%d current=%d",
                        job.npc_id, job.generation, self._generation)
            return
        await job.db.execute(
            "UPDATE npc_state SET last_longterm_turn = CASE"
            " WHEN last_longterm_turn < ? THEN ? ELSE last_longterm_turn END"
            " WHERE npc_id = ?",
            (job.turn_end, job.turn_end, job.npc_id))
        logger.info("longterm memory stored npc=%d turns=%d chars=%d",
                    job.npc_id, len(turns), len(result["summary"]))

    async def _longterm_worker(self) -> None:
        while True:
            job = await self._queue.get()
            if job is _STOP:
                return
            if isinstance(job, _Barrier):
                if not job.future.done():
                    job.future.set_result(None)
                continue
            try:
                if job.generation != self._generation:
                    logger.info("drop stale longterm job npc=%d gen=%d current=%d",
                                job.npc_id, job.generation, self._generation)
                    continue
                await self._run_longterm(job)
            except asyncio.CancelledError:
                logger.info("longterm worker cancelled during npc=%d", job.npc_id)
                raise
            except Exception:
                logger.exception("longterm job failed npc=%d turns=%d",
                                 job.npc_id, len(job.turns))

    async def flush(self) -> None:
        """等待队列里已有的长期任务全部处理完（包括当前正在跑的）。"""
        if self._closed:
            return
        self._ensure_worker()
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(_Barrier(future))
        await future

    async def wait_background(self) -> None:
        """测试用：等所有已入队的长期总结完成/丢弃。"""
        await self.flush()

    async def shutdown(self) -> None:
        """停机：丢弃排队任务，让当前任务收尾后停掉 worker。"""
        self._draining = True
        self._closed = True
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        barriers: list[_Barrier] = []
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(job, _Barrier):
                barriers.append(job)
        for barrier in barriers:
            self._queue.put_nowait(barrier)
        self._queue.put_nowait(_STOP)
        try:
            await asyncio.wait_for(worker, timeout=_SHUTDOWN_GRACE)
        except asyncio.TimeoutError:
            logger.warning("longterm worker shutdown timeout; cancelling")
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    def begin_reset(self) -> None:
        """新局开始：旧 generation 的对话与长期任务全部作废。"""
        self._generation += 1
        self._draining = True

    def reset_runtime(self) -> None:
        """新局只清运行时；conversation 与 longterm_memory 是持久记忆，不在此清。"""
        self._recalled_at.clear()
        self.longterm.reset()
        self.recent_events.clear()
        self._draining = False

    async def reset_ledger(self, db: Database) -> None:
        """新局：清空窗口与中期派生摘要，原文保留供长期归档与复盘。"""
        await db.execute(
            "DELETE FROM conversation WHERE kind = ?", (MIDTERM_KIND,))
        await db.execute("UPDATE npc_state SET last_midterm_turn = turn_no")

    async def debug_midterms(self, db: Database, npc_id: int) -> list[dict]:
        rows = await db.fetch_all(
            "SELECT id, ts_game, turn_no, turns_json FROM conversation"
            " WHERE npc_id = ? AND kind = ? ORDER BY turn_no DESC, id DESC",
            (npc_id, MIDTERM_KIND))
        items: list[dict] = []
        for row in rows:
            try:
                data = json.loads(row["turns_json"])
            except (TypeError, ValueError):
                continue
            items.append({
                "id": row["id"],
                "summary": data.get("summary", ""),
                "clues": data.get("clues") or [],
                "turn_count": data.get("turn_count") or 0,
                "turn_range": data.get("turn_range") or None,
                "ts_game": row["ts_game"],
            })
        return items

    async def ledger_view(self, db: Database, npc_id: int) -> dict:
        """调试用：账本游标与窗口大小。"""
        row = await db.fetch_one(
            "SELECT turn_no, last_midterm_turn, last_longterm_turn"
            " FROM npc_state WHERE npc_id = ?", (npc_id,))
        if row is None:
            return {"turn_no": 0, "last_midterm_turn": 0,
                    "last_longterm_turn": 0, "window_turns": 0}
        turn_no = int(row["turn_no"] or 0)
        last_mid = int(row["last_midterm_turn"] or 0)
        return {
            "turn_no": turn_no,
            "last_midterm_turn": last_mid,
            "last_longterm_turn": int(row["last_longterm_turn"] or 0),
            "window_turns": max(0, turn_no - last_mid),
        }

    def longterm_indexed(self) -> int:
        """`longterm` 集合里的条数；v2 没有读路径，靠它与表行数对账。"""
        try:
            return self._lt_coll.count()
        except Exception:
            return -1

    async def pending_longterm_turns(self, db: Database, npc_id: int) -> int:
        """还没归档进长期记忆的轮数（v2：由游标算，不再依赖内存缓冲）。"""
        turn_no, _ = await self._ledger_cursor(db, npc_id)
        last_long = await self._longterm_cursor(db, npc_id)
        return max(0, turn_no - last_long)

    async def ledger_turn(self, db: Database, npc_id: int) -> int:
        """当前绝对轮号（`npc_state.turn_no`，不含本轮）。"""
        turn_no, _last = await self._ledger_cursor(db, npc_id)
        return turn_no

    def query(self, npc_id: int, query_vec: list[float], n_results: int = 20) -> dict:
        """ANN search over this NPC's memories (vector similarity only)."""
        return self._coll.query(
            query_embeddings=[query_vec], n_results=n_results,
            where={"npc_id": str(npc_id)},
        )

    def query_longterm(self, npc_id: int, query_vec: list[float],
                       n_results: int = 20) -> dict:
        """ANN search over this NPC's longterm_memory index (P5 召回)."""
        return self._lt_coll.query(
            query_embeddings=[query_vec], n_results=n_results,
            where={"npc_id": str(npc_id)},
        )

    async def reconcile(self, db: Database) -> bool:
        """按集合分别校验派生索引，True 表示重建过。

        Chroma 是派生索引：`memories` 对 `memory`、`longterm` 对 `longterm_memory`，
        数量不符或格式不可读就删该集合、从 SQLite 全量重嵌入（AGENTS.md：恒可重建）。
        两个集合必须各自校验——一张表数量不符不能连带重建另一个。
        """
        rebuilt = False
        rebuilt |= await self._reconcile_memories(db)
        rebuilt |= await self._reconcile_longterm(db)
        return rebuilt

    async def _reconcile_memories(self, db: Database) -> bool:
        try:
            in_chroma = self._coll.count()
        except Exception:
            in_chroma = -1  # unreadable format -> rebuild from SSOT
        sql_count = (await db.fetch_one("SELECT COUNT(*) AS n FROM memory"))["n"]
        if in_chroma == sql_count and in_chroma >= 0:
            return False
        try:
            self._client.delete_collection("memories")
        except Exception:
            pass
        self._coll = self._client.get_or_create_collection(
            "memories", embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        rows = await db.fetch_all(
            "SELECT id, npc_id, ts_game, kind, content, participants_json, poi, importance"
            " FROM memory ORDER BY id"
        )
        ids, embeds, docs, metas = [], [], [], []
        for r in rows:
            vec = (await self.llm.embed([r["content"]]))[0]
            ids.append(str(r["id"]))
            embeds.append(vec)
            docs.append(r["content"])
            metas.append({
                "npc_id": str(r["npc_id"]), "kind": r["kind"], "importance": r["importance"],
                "ts_game": r["ts_game"], "poi": r["poi"] or "",
            })
        if ids:
            self._coll.add(ids=ids, embeddings=embeds, documents=docs, metadatas=metas)
        return True

    async def _reconcile_longterm(self, db: Database) -> bool:
        """`longterm` 集合（v2 只写不读，重建后供 P5 召回）。"""
        try:
            in_chroma = self._lt_coll.count()
        except Exception:
            in_chroma = -1
        sql_count = (await db.fetch_one(
            "SELECT COUNT(*) AS n FROM longterm_memory"))["n"]
        if in_chroma == sql_count and in_chroma >= 0:
            return False
        try:
            self._client.delete_collection("longterm")
        except Exception:
            pass
        self._lt_coll = self._client.get_or_create_collection(
            "longterm", embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        rows = await db.fetch_all(
            "SELECT id, npc_id, ts_game, content, turn_range_end, poi"
            " FROM longterm_memory ORDER BY id")
        ids, embeds, docs, metas = [], [], [], []
        for r in rows:
            vec = (await self.llm.embed([r["content"]]))[0]
            ids.append(str(r["id"]))
            embeds.append(vec)
            docs.append(r["content"])
            metas.append({
                "npc_id": str(r["npc_id"]), "ts_game": r["ts_game"],
                "turn_range_end": r["turn_range_end"], "poi": r["poi"] or "",
            })
        if ids:
            self._lt_coll.add(ids=ids, embeddings=embeds, documents=docs,
                              metadatas=metas)
        return True

    def close(self) -> None:
        # chromadb 0.5.0 PersistentClient 无 close()，且持久化在每次 upsert 即落盘，
        # 无需 flush。关机收敛只做资源清理；此处保持空实现以免 teardown 崩溃进程。
        pass