"""记忆双库（docs/DESIGN.md §6.3 / §8）：SQLite 为记忆 SSOT，Chroma 为派生向量索引。

写入选句：先 SQLite 落库分配 id -> 同一写锁内 embed -> Chroma upsert
（AGENTS.md #6：先 SQLite 后 Chroma）。换 embedding 模型或索引损坏时，
删 Chroma 集合从 SQLite 全量重建即可——向量不进 SQLite 正是为留这条退路。
Chroma 集合恒建为 embedding_function=None + hnsw:space=cosine，add/query
恒定传 embeddings（AGENTS.md ChromaDB 三坑）。
"""

import asyncio
import json
import time

import chromadb
from chromadb.config import Settings

from ..db import Database


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