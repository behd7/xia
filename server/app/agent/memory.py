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


class MemoryStore:
    """Owns one Chroma 'memories' collection plus the single memory write lock."""

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
        self._bg_tasks: set[asyncio.Task] = set()
        # Phase C：附近显著事件的进程内环形缓冲，不落库。
        self.recent_events: list[dict] = []

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
    ) -> TurnOutcome:
        """写入短期窗口；满窗生成中期内存摘要，攒满 36 轮调度长期总结。"""
        lock = self._window_locks.setdefault(npc_id, asyncio.Lock())
        async with lock:
            now_game = self.clock.now()
            await db.execute(
                "INSERT INTO conversation(npc_id, ts_game, turns_json, affinity_delta_total)"
                " VALUES(?,?,?,?)",
                (npc_id, now_game,
                 json.dumps({"user": user, "npc": npc, "place": place},
                            ensure_ascii=False),
                 affinity_delta),
            )
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
                await self._store_midterm(npc_id, result, ts_game=now_game)
                if ids:
                    marks = ",".join("?" for _ in ids)
                    await db.execute(
                        f"DELETE FROM conversation WHERE id IN ({marks})", ids)
                outcome.midterm_created = True

            interval = max(1, settings.longterm_turn_interval)
            buffer = self._longterm_turns.get(npc_id, [])
            if len(buffer) >= interval:
                batch = buffer[:interval]
                del buffer[:interval]
                self._schedule_longterm(
                    db, npc_id, batch, ts_game=now_game,
                    npc_name=npc_name, player_name=player_name)
                outcome.longterm_scheduled = True
            return outcome

    async def _store_midterm(self, npc_id: int, result: dict, *, ts_game: float) -> None:
        clues = result.get("clues") or []
        vectors: list[list[float]] = []
        if clues:
            try:
                vectors = await self.llm.embed(clues)
            except Exception:
                logger.warning("midterm clue embed failed npc=%d clues=%d",
                               npc_id, len(clues))
        bucket = self._midterms.setdefault(npc_id, [])
        bucket.append(MidTerm(
            summary=result["summary"], clues=clues, vectors=vectors,
            ts_game=ts_game, turn_count=int(result.get("turn_count", 0)),
        ))
        cap = max(1, settings.midterm_capacity)
        if len(bucket) > cap:
            del bucket[:len(bucket) - cap]

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
                           ts_game: float, npc_name: str, player_name: str) -> None:
        task = asyncio.create_task(
            self._run_longterm(
                db, npc_id, turns, ts_game=ts_game,
                npc_name=npc_name, player_name=player_name),
            context=contextvars.Context(),  # 不继承请求侧 Langfuse trace
            name=f"longterm-summary-{npc_id}",
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _run_longterm(self, db: Database, npc_id: int, turns: list[dict], *,
                            ts_game: float, npc_name: str, player_name: str) -> None:
        try:
            result = await summary_mod.summarize_longterm(
                self, npc_id, npc_name, player_name, turns)
            await db.execute(
                "INSERT INTO longterm_memory(npc_id, ts_game, content, turn_count,"
                " created_at_real) VALUES(?,?,?,?,?)",
                (npc_id, ts_game, result["summary"], len(turns), time.time()),
            )
            logger.info("longterm memory stored npc=%d turns=%d chars=%d",
                        npc_id, len(turns), len(result["summary"]))
        except asyncio.CancelledError:
            logger.info("longterm summary cancelled npc=%d turns=%d",
                        npc_id, len(turns))
            raise
        except Exception:
            logger.exception("longterm summary failed npc=%d turns=%d",
                             npc_id, len(turns))

    async def wait_background(self) -> None:
        """测试/收尾用：等待已调度的长期总结完成，不取消。"""
        tasks = list(self._bg_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        """取消并等待后台长期总结；shutdown/new game 前调用。"""
        tasks = list(self._bg_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._bg_tasks.clear()

    def reset_runtime(self) -> None:
        self._midterms.clear()
        self._longterm_turns.clear()

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