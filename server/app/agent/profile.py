"""人物画像（docs/MEMORY-V2-PLAN.md §5.3 / P4）：确定性聚合，不调 LLM。

画像是「关系摘要」的量化补充：摘要描述印象，画像只数事实——给过几回东西、
回绝过几回、头回照面在什么时候。数据来自 `tool_call_log`（行动层唯一的结构化
账本），因此零幻觉、可测试、随动作增量更新；没有行动就不注入该板块。
"""

from __future__ import annotations

from ..config import settings
from ..db import Database

# 每个工具对应的量化说法；新增工具时在这里登记，否则画像里看不见。
TOOL_CLAUSES: dict[str, str] = {
    "give_item": "给过他 {n} 回东西",
    "take_item": "收过他 {n} 回",
    "teach_art": "传过他 {n} 门功法",
    "refuse": "回绝过他 {n} 回",
}

_GAME_DAY = 86400.0
_GAME_HOUR = 3600.0


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 600))} 刻钟前"
    if seconds < _GAME_DAY:
        return f"{max(1, int(seconds // 1800))} 个时辰前"
    return f"{int(seconds // _GAME_DAY)} 日前"


def _when(prefix: str, seconds: float) -> str:
    if seconds < 600:
        return f"{prefix}就在方才"
    return f"{prefix}在 {_ago(seconds)}"


async def build(db: Database, npc_id: int, player_name: str,
                now_game: float) -> str:
    """把行动史压成一段 ≤PROFILE_MAX_CHARS 的短句；没有行动返回空串。"""
    rows = await db.fetch_all(
        "SELECT tool, COUNT(*) AS n, MIN(ts_game) AS first_at,"
        " MAX(ts_game) AS last_at FROM tool_call_log"
        " WHERE npc_id = ? AND ok = 1 GROUP BY tool ORDER BY tool",
        (npc_id,))
    if not rows:
        return ""
    counts = {row["tool"]: int(row["n"]) for row in rows}
    clauses = [TOOL_CLAUSES[tool].format(n=counts[tool])
               for tool in TOOL_CLAUSES if counts.get(tool)]
    if not clauses:
        return ""
    first_at = min(float(row["first_at"] or 0) for row in rows)
    last_at = max(float(row["last_at"] or 0) for row in rows)
    text = (f"你与{player_name}打过的交道：{'、'.join(clauses)}；"
            f"{_when('头回照面', now_game - first_at)}，"
            f"{_when('最近一次', now_game - last_at)}。")
    return text[:max(40, settings.profile_max_chars)]
