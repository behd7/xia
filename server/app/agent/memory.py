"""记忆双库（docs/DESIGN.md §6.3 / §8）：SQLite 为记忆 SSOT，Chroma 为派生向量索引。

写入选句：先 SQLite 落库分配 id -> 同一写锁内 embed -> Chroma upsert
（AGENTS.md #6：先 SQLite 后 Chroma）。换 embedding 模型或索引损坏时，
删 Chroma 集合从 SQLite 全量重建即可——向量不进 SQLite 正是为留这条退路。
Chroma 集合恒建为 embedding_function=None + hnsw:space=cosine，add/query
恒定传 embeddings（AGENTS.md ChromaDB 三坑）。

三层记忆：短期 conversation 窗口仍走 SQLite；中期 summary+线索只存
`_midterms` 进程内存；长期每 36 轮原始对话在后台总结，写独立的
`longterm_memory` 表（不写 memory、不写 Chroma，也不参与反思/关系/八卦）。
"""

import asyncio
import contextvars
import json
import logging
import time
from dataclasses import dataclass

import chromadb
from chromadb.config import Settings

from ..config import settings
from ..db import Database
from . import summary as summary_mod

logger = logging.getLogger(__name__)


@dataclass
class MidTerm:
    """进程内中期记忆：单条 summary + 线索向量，不入库。"""

    summary: str
    clues: list[str]
    vectors: list[list[float]]
    ts_game: float
    turn_count: int


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
    turns: list[dict]
    ts_game: float
    npc_name: str
    player_name: str


@dataclass
class _Barrier:
    future: asyncio.Future


_STOP = object()
_SHUTDOWN_GRACE = 5.0


class MemoryStore:
    """Chroma 索引、记忆写锁、中期内存与长期总结 worker 的持有者。"""

    def __init__(self, chroma_dir, llm, clock) -> None:
        self.llm = llm
        self.clock = clock
        self._client = chromadb.PersistentClient(
            path=str(chroma_dir), settings=Settings(anonymized_telemetry=False)
        )
        self._coll = self._client.get_or_create_collection(
            "memories", embedding_function=None, metadata={"hnsw:space": "cosine"}
        )
        self._lock = asyncio.Lock()
        self._window_locks: dict[int, asyncio.Lock] = {}
        # 中期记忆与长期原始轮缓冲都只在进程内，重启清零。
        self._midterms: dict[int, list[MidTerm]] = {}
        self._longterm_turns: dict[int, list[dict]] = {}
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
    ) -> TurnOutcome:
        """写入短期窗口；满窗生成中期内存摘要，攒满 36 轮入长期队列。

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
            conv_id = await db.execute(
                "INSERT INTO conversation(npc_id, ts_game, turns_json, affinity_delta_total)"
                " VALUES(?,?,?,?)",
                (npc_id, now_game,
                 json.dumps({"user": user, "npc": npc, "place": place},
                            ensure_ascii=False),
                 affinity_delta),
            )
            if generation != self._generation:
                await db.execute("DELETE FROM conversation WHERE id = ?", (conv_id,))
                return TurnOutcome(stale=True)
            self._longterm_turns.setdefault(npc_id, []).append({
                "user": user, "npc": npc, "place": place, "ts_game": now_game,
            })

            outcome = TurnOutcome()
            row = await db.fetch_one(
                "SELECT COUNT(*) AS n FROM conversation WHERE npc_id = ?", (npc_id,))
            capacity = max(1, settings.conversation_window_capacity)
            if int(row["n"]) > capacity:
                rows = await db.fetch_all(
                    "SELECT id, turns_json FROM conversation WHERE npc_id = ?"
                    " ORDER BY id LIMIT ?",
                    (npc_id, capacity),
                )
                ids: list[int] = []
                turns: list[dict] = []
                for item in rows:
                    ids.append(item["id"])
                    try:
                        data = json.loads(item["turns_json"])
                    except (TypeError, ValueError):
                        continue
                    turns.append({
                        "user": data.get("user", ""),
                        "npc": data.get("npc", ""),
                        "place": data.get("place", place),
                    })
                result = await summary_mod.summarize_midterm(
                    self, npc_id, npc_name, player_name, place, turns)
                stored = await self._store_midterm(
                    npc_id, result, ts_game=now_game, generation=generation)
                if ids:
                    marks = ",".join("?" for _ in ids)
                    await db.execute(
                        f"DELETE FROM conversation WHERE id IN ({marks})", ids)
                if not stored:
                    await db.execute("DELETE FROM conversation WHERE id = ?", (conv_id,))
                    return TurnOutcome(stale=True)
                outcome.midterm_created = True

            if generation != self._generation:
                await db.execute("DELETE FROM conversation WHERE id = ?", (conv_id,))
                return TurnOutcome(stale=True)
            interval = max(1, settings.longterm_turn_interval)
            buffer = self._longterm_turns.setdefault(npc_id, [])
            if len(buffer) >= interval:
                batch = buffer[:interval]
                del buffer[:interval]
                self._schedule_longterm(
                    db, npc_id, batch, ts_game=now_game,
                    npc_name=npc_name, player_name=player_name,
                    generation=generation)
                outcome.longterm_scheduled = True
            return outcome

    async def _store_midterm(self, npc_id: int, result: dict, *, ts_game: float,
                             generation: int) -> bool:
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
        bucket = self._midterms.setdefault(npc_id, [])
        bucket.append(MidTerm(
            summary=result["summary"], clues=clues, vectors=vectors,
            ts_game=ts_game, turn_count=int(result.get("turn_count", 0)),
        ))
        cap = max(1, settings.midterm_capacity)
        if len(bucket) > cap:
            del bucket[:len(bucket) - cap]
        return True

    def match_midterms(self, npc_id: int, query_vec: list[float]) -> list[MidTerm]:
        """用当前消息向量匹配中期线索，返回命中的完整中期条目。"""
        scored: list[tuple[float, MidTerm]] = []
        for item in self._midterms.get(npc_id, []):
            if not item.vectors:
                continue
            score = max(sum(a * b for a, b in zip(query_vec, v)) for v in item.vectors)
            if score >= settings.midterm_min_score:
                scored.append((score, item))
        scored.sort(key=lambda pair: -pair[0])
        return [item for _, item in scored[: max(1, settings.midterm_top_k)]]

    def _schedule_longterm(self, db: Database, npc_id: int, turns: list[dict], *,
                           ts_game: float, npc_name: str, player_name: str,
                           generation: int) -> None:
        if self._closed or self._draining or generation != self._generation:
            logger.info("drop longterm schedule npc=%d gen=%d current=%d "
                        "draining=%s closed=%s",
                        npc_id, generation, self._generation,
                        self._draining, self._closed)
            return
        self._ensure_worker()
        self._queue.put_nowait(LongTermJob(
            db=db, generation=generation, npc_id=npc_id, turns=turns,
            ts_game=ts_game, npc_name=npc_name, player_name=player_name,
        ))

    async def _run_longterm(self, job: LongTermJob) -> None:
        result = await summary_mod.summarize_longterm(
            self, job.npc_id, job.npc_name, job.player_name, job.turns)
        if job.generation != self._generation:
            logger.info("drop stale longterm result npc=%d gen=%d current=%d",
                        job.npc_id, job.generation, self._generation)
            return
        row_id = await job.db.execute(
            "INSERT INTO longterm_memory(npc_id, ts_game, content, turn_count,"
            " created_at_real) VALUES(?,?,?,?,?)",
            (job.npc_id, job.ts_game, result["summary"], len(job.turns), time.time()),
        )
        if job.generation != self._generation:
            await job.db.execute(
                "DELETE FROM longterm_memory WHERE id = ?", (row_id,))
            logger.info("drop stale longterm row npc=%d gen=%d current=%d",
                        job.npc_id, job.generation, self._generation)
            return
        logger.info("longterm memory stored npc=%d turns=%d chars=%d",
                    job.npc_id, len(job.turns), len(result["summary"]))

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
        self._midterms.clear()
        self._longterm_turns.clear()
        self.recent_events.clear()
        self._draining = False

    def debug_midterms(self, npc_id: int) -> list[dict]:
        return [
            {"summary": item.summary, "clues": item.clues,
             "ts_game": item.ts_game, "turn_count": item.turn_count}
            for item in self._midterms.get(npc_id, [])
        ]

    def pending_longterm_turns(self, npc_id: int) -> int:
        return len(self._longterm_turns.get(npc_id, []))

    def query(self, npc_id: int, query_vec: list[float], n_results: int = 20) -> dict:
        """ANN search over this NPC's memories (vector similarity only)."""
        return self._coll.query(
            query_embeddings=[query_vec], n_results=n_results,
            where={"npc_id": str(npc_id)},
        )

    async def reconcile(self, db: Database) -> bool:
        """Ensure the Chroma index matches the SQLite memory SSOT; True if rebuilt.

        Chroma is a derived index. If it's out of sync with `memory`, or its
        on-disk format is unreadable (e.g. an unpinned chromadb got bumped and
        changed the internal seq_id encoding), drop the collection and re-embed
        every memory row from SQLite (AGENTS.md: Chroma 恒可从 SQLite 全量重建).
        Cheap no-op when the counts already agree.
        """
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

    def close(self) -> None:
        # chromadb 0.5.0 PersistentClient 无 close()，且持久化在每次 upsert 即落盘，
        # 无需 flush。关机收敛只做资源清理；此处保持空实现以免 teardown 崩溃进程。
        pass