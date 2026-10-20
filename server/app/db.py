import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Sequence

# Full data model per docs/DESIGN.md §8.
# Memory embeddings intentionally absent: they live in the ChromaDB
# `memories` collection, which is a derived index rebuildable from here.
SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sect_id TEXT,
    level INTEGER NOT NULL DEFAULT 1,
    exp INTEGER NOT NULL DEFAULT 0,
    cultivation INTEGER NOT NULL DEFAULT 0,
    hp REAL NOT NULL DEFAULT 140,
    mp REAL NOT NULL DEFAULT 90,
    gold INTEGER NOT NULL DEFAULT 100,
    str INTEGER NOT NULL DEFAULT 5,
    con INTEGER NOT NULL DEFAULT 5,
    agi INTEGER NOT NULL DEFAULT 5,
    int_ INTEGER NOT NULL DEFAULT 5,
    free_points INTEGER NOT NULL DEFAULT 0,
    pos_x REAL NOT NULL DEFAULT 792,
    pos_y REAL NOT NULL DEFAULT 448,
    clock_offset REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'common',
    price INTEGER NOT NULL DEFAULT 0,
    effect_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES player(id),
    item_id INTEGER NOT NULL REFERENCES item(id),
    qty INTEGER NOT NULL DEFAULT 1,
    equipped_slot TEXT
);

CREATE TABLE IF NOT EXISTS skill (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sect_id TEXT,
    kind TEXT NOT NULL,
    tier_req INTEGER,
    mp_cost INTEGER NOT NULL DEFAULT 0,
    multiplier REAL NOT NULL DEFAULT 1.0,
    cooldown INTEGER NOT NULL DEFAULT 0,
    effect_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS player_skill (
    player_id INTEGER NOT NULL REFERENCES player(id),
    skill_id INTEGER NOT NULL REFERENCES skill(id),
    level INTEGER NOT NULL DEFAULT 1,
    equipped INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, skill_id)
);

CREATE TABLE IF NOT EXISTS npc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sect_id TEXT,
    role TEXT NOT NULL,
    init_affinity INTEGER NOT NULL DEFAULT 20,
    home_poi TEXT NOT NULL,
    work_poi TEXT NOT NULL
);

-- 说明：人格/口吻/路径等"静态人格"字段（原 persona_json/speech_style/waypoints_json）
-- 运行时始终以 NPC_ROSTER（agent/npc.py）为准且被其覆盖，库中从未被消费，属死数据，
-- 已随 connect() 迁移 DROP，定义只留在代码花名册里。此表仅负责稳定 id/role/poi 锚点
-- 与 npc_state 的动态状态配对。

CREATE TABLE IF NOT EXISTS npc_state (
    npc_id INTEGER PRIMARY KEY REFERENCES npc(id),
    affinity INTEGER NOT NULL,
    last_reflection_at REAL,
    mem_since_reflection INTEGER NOT NULL DEFAULT 0,
    paused_until REAL,
    last_greet_real REAL,
    interacts_since_summary INTEGER NOT NULL DEFAULT 0,
    last_summary_conv_id INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL REFERENCES npc(id),
    ts_game REAL NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    participants_json TEXT NOT NULL DEFAULT '[]',
    poi TEXT,
    importance INTEGER NOT NULL DEFAULT 3,
    created_at_real REAL NOT NULL,
    source_memory_id INTEGER REFERENCES memory(id)
);
CREATE INDEX IF NOT EXISTS idx_memory_npc_ts ON memory(npc_id, ts_game);
CREATE INDEX IF NOT EXISTS idx_memory_npc_importance ON memory(npc_id, importance);
-- idx_memory_source must wait for the additive migration below: old DBs lack the column.

CREATE TABLE IF NOT EXISTS relationship (
    npc_id INTEGER NOT NULL REFERENCES npc(id),
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (npc_id, target_kind, target_id)
);

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL REFERENCES npc(id),
    ts_game REAL NOT NULL,
    turns_json TEXT NOT NULL,
    affinity_delta_total INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS battle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES player(id),
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    in_tokens INTEGER,
    out_tokens INTEGER,
    ms INTEGER,
    ok INTEGER NOT NULL,
    created_at REAL NOT NULL
);
"""


class Database:
    """Single-connection SQLite in WAL mode.

    One asyncio.Lock serializes every statement, so concurrent dialogue
    writes and background agent jobs never interleave (AGENTS.md #6).
    The connection is created with check_same_thread=False because
    statements run in a worker thread via asyncio.to_thread; that is
    safe only because the lock is held for the whole call.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        def _open() -> sqlite3.Connection:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            # Idempotent migrations (CREATE IF NOT EXISTS bakes old shapes; new columns
            # must be added explicitly). Early drafts added this same intent under
            # events_pending / unsummarized_turns; superseded now.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(npc_state)")}
            for col in ("interacts_since_summary", "last_summary_conv_id"):
                if col not in cols:
                    conn.execute(
                        f"ALTER TABLE npc_state ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                    )
            mem_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory)")}
            if "source_memory_id" not in mem_cols:
                conn.execute("ALTER TABLE memory ADD COLUMN source_memory_id INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_source"
                " ON memory(npc_id, source_memory_id)"
            )
            # Legacy npc table carried static personality columns (persona_json/speech_style/
            # waypoints_json) that runtime never reads — NPC_ROSTER supplies & overrides them.
            # Drop as dead data. DROP COLUMN needs SQLite >= 3.35; fail-open so a leftover
            # column can never block startup.
            npc_cols = {r[1] for r in conn.execute("PRAGMA table_info(npc)")}
            for col in ("persona_json", "speech_style", "waypoints_json"):
                if col in npc_cols:
                    try:
                        conn.execute(f"ALTER TABLE npc DROP COLUMN {col}")
                    except sqlite3.OperationalError as e:
                        print(f"[db] skip drop npc.{col}: {e}")
            conn.commit()
            return conn

        self._conn = await asyncio.to_thread(_open)

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self.lock:
            def _run() -> int:
                cur = self.conn.execute(sql, params)
                self.conn.commit()
                return cur.lastrowid or 0

            return await asyncio.to_thread(_run)

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self.lock:
            def _run() -> list[dict[str, Any]]:
                cur = self.conn.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

            return await asyncio.to_thread(_run)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with self.lock:
            def _run() -> dict[str, Any] | None:
                cur = self.conn.execute(sql, params)
                cols = [d[0] for d in cur.description]
                row = cur.fetchone()
                return dict(zip(cols, row)) if row else None

            return await asyncio.to_thread(_run)

    async def reset(self) -> None:
        """清空全部业务表，等价于全新开档。

        枚举 sqlite_sequence 之外的所有表逐张 DELETE，再把 AUTOINCREMENT
        计数复位，让新档从 id=1 重新开始。仅删 SQLite 行；Chroma 是派生索引，
        由调用方之后的 MemoryStore.reconcile 对空库自动整集合重建（幂等）。
        需在单写锁内执行，避免与并发语句交错。
        """

        async with self.lock:
            def _run() -> None:
                tables = [
                    r[0]
                    for r in self.conn.execute(
                        "SELECT name FROM sqlite_master"
                        " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                ]
                for t in tables:
                    self.conn.execute(f"DELETE FROM {t}")
                self.conn.execute("DELETE FROM sqlite_sequence")
                self.conn.commit()

            await asyncio.to_thread(_run)

    async def kv_get(self, key: str) -> str | None:
        row = await self.fetch_one("SELECT v FROM kv WHERE k = ?", (key,))
        return row["v"] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )
