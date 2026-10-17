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
    ) -> int:
        """Persist one memory: SQLite row + Chroma vector, atomically-ordered."""
        ts_game = self.clock.now()
        async with self._lock:
            mid = await db.execute(
                "INSERT INTO memory(npc_id, ts_game, kind, content, participants_json, poi,"
                " importance, created_at_real) VALUES(?,?,?,?,?,?,?,?)",
                (npc_id, ts_game, kind, content,
                 json.dumps(participants or [], ensure_ascii=False),
                 poi, importance, time.time()),
            )
            vec = (await self.llm.embed([content]))[0]
            self._coll.add(
                ids=[str(mid)], embeddings=[vec], documents=[content],
                metadatas=[{
                    "npc_id": str(npc_id), "kind": kind, "importance": importance,
                    "ts_game": ts_game, "poi": poi or "",
                }],
            )
        # Service column for M4 reflection trigger; M3 only accumulates it.
        await db.execute(
            "UPDATE npc_state SET mem_since_reflection = mem_since_reflection + 1 WHERE npc_id = ?",
            (npc_id,),
        )
        return mid

    def query(self, npc_id: int, query_vec: list[float], n_results: int = 20) -> dict:
        """ANN search over this NPC's memories (vector similarity only)."""
        return self._coll.query(
            query_embeddings=[query_vec], n_results=n_results,
            where={"npc_id": str(npc_id)},
        )

    def close(self) -> None:
        self._client.close()