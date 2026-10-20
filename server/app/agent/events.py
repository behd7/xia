"""附近显著事件（docs/DESIGN.md §6.12）：进程内环形缓冲，不落库。

只记录玩家造成的显著事件（战斗、大额消费、送礼、拜师），供对话/搭话
prompt 提及；不写记忆流、不新增 LLM 调用。重启丢失可接受。
"""

from __future__ import annotations

MAX_EVENTS = 20
TTL_GAME_SEC = 1800  # 30 游戏分钟


def record(store, kind: str, text: str, poi: str, participants: list[str]) -> None:
    store.recent_events.append({
        "kind": kind,
        "text": text,
        "poi": poi,
        "participants": list(participants),
        "ts_game": store.clock.now(),
    })
    if len(store.recent_events) > MAX_EVENTS:
        del store.recent_events[:-MAX_EVENTS]


def recent(store, now_game: float, poi: str | None = None,
           limit: int = 3) -> list[dict]:
    out = [
        event for event in store.recent_events
        if now_game - float(event["ts_game"]) <= TTL_GAME_SEC
        and (poi is None or event["poi"] == poi)
    ]
    return list(reversed(out))[:max(0, limit)]
