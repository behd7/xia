import time
from typing import Any

from ..db import Database

GAME_SEC_PER_DAY = 86400


class GameClock:
    """Server-authoritative game clock (docs/DESIGN.md §5.7).

    game_now = anchor_game + (real_now - anchor_real) * time_scale

    The anchor pair is persisted in kv so the clock keeps running
    across restarts, matching the "时钟偏移持久化" decision.
    """

    def __init__(self, db: Database, time_scale: int, start_sec: int) -> None:
        self._db = db
        self._scale = time_scale
        self._start_sec = start_sec
        self._anchor_real = 0.0
        self._anchor_game = 0.0

    async def load(self) -> None:
        real = await self._db.kv_get("clock_anchor_real")
        game = await self._db.kv_get("clock_anchor_game")
        if real is None or game is None:
            self._anchor_real = time.time()
            self._anchor_game = float(self._start_sec)
            await self._save_anchor()
        else:
            self._anchor_real = float(real)
            self._anchor_game = float(game)

    async def _save_anchor(self) -> None:
        await self._db.kv_set("clock_anchor_real", repr(self._anchor_real))
        await self._db.kv_set("clock_anchor_game", repr(self._anchor_game))

    def now(self) -> float:
        return self._anchor_game + (time.time() - self._anchor_real) * self._scale

    def snapshot(self) -> dict[str, Any]:
        g = self.now()
        sec_of_day = g % GAME_SEC_PER_DAY
        return {
            "game_sec": round(g, 3),
            "day": int(g // GAME_SEC_PER_DAY) + 1,
            "hour": int(sec_of_day // 3600),
            "minute": int(sec_of_day % 3600 // 60),
            "time_scale": self._scale,
            "real_ts": time.time(),
        }
