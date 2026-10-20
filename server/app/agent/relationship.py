"""关系滚动摘要（docs/DESIGN.md §6.8）。

每对 (NPC, 对象) 一段 ≤100 字印象，供对话 prompt 使用。触发口径统一用现实
时间 `created_at_real >= relationship.updated_at` 统计新增素材；参与者过滤在
Python 侧做，不依赖 SQLite JSON1。target_id 一律用名字（玩家用玩家名、NPC 用
NPC 名），与 participants_json 里的值同口径。
"""

import json
import re
import time

from ..config import settings
from ..db import Database
from . import npc as npc_mod


async def summary_for(db: Database, npc_id: int, target_kind: str, target_id: str) -> str:
    row = await db.fetch_one(
        "SELECT summary FROM relationship WHERE npc_id = ? AND target_kind = ? AND target_id = ?",
        (npc_id, target_kind, target_id),
    )
    return str(row["summary"]) if row else ""


def _parse_summary(result_text: str) -> str:
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    summary = str(data.get("summary", "")).strip() if isinstance(data, dict) else ""
    if not summary:
        raise ValueError("missing summary")
    return summary


def _messages(n: dict, target: str, old_summary: str, material: list[dict]) -> list[dict]:
    lines = "\n".join(f"- {m['content']}" for m in material)
    system = (
        f"你是{n['name']}。{n['persona']}\n"
        f"你对{target}的旧印象：{old_summary or '（尚无）'}\n"
        f"近来与{target}有关的事：\n{lines}\n"
        f"请把旧印象与这些事合成一段不超过{settings.relationship_max_chars}字的印象"
        "（你的视角，第三人称，不虚构）。\n"
        '仅输出JSON：{"summary":"..."}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请开始归纳。"}]


async def maybe_update(db: Database, store, npc_id: int, target_kind: str, target_id: str,
                       *, force: bool = False) -> bool:
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        return False
    old_row = await db.fetch_one(
        "SELECT summary, updated_at FROM relationship"
        " WHERE npc_id = ? AND target_kind = ? AND target_id = ?",
        (npc_id, target_kind, target_id),
    )
    old_summary = str(old_row["summary"]) if old_row else ""
    since = float(old_row["updated_at"]) if old_row else 0.0

    rows = await db.fetch_all(
        "SELECT id, content, kind, importance, participants_json, created_at_real"
        " FROM memory WHERE npc_id = ? AND created_at_real >= ?"
        " AND kind IN ('event','gossip','reflection')"
        " ORDER BY ts_game DESC LIMIT 200",
        (npc_id, since),
    )
    events: list[dict] = []
    reflections: list[dict] = []
    for r in rows:
        try:
            participants = json.loads(r["participants_json"])
        except (TypeError, ValueError):
            participants = []
        if target_id not in participants:
            continue
        if r["kind"] == "reflection":
            reflections.append(r)
        else:
            events.append(r)

    if not force:
        if (len(events) < settings.relationship_update_events
                and len(reflections) < settings.relationship_mention_reflections):
            return False

    material = events[:20] + reflections[:10]
    if not material:
        return False

    summary: str | None = None
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                "relationship", _messages(n, target_id, old_summary, material),
                json_format=True, temperature=temperature,
                metadata={"npc_id": npc_id, "target": target_id},
            )
            summary = _parse_summary(cr.text)
            break
        except Exception:
            continue
    if not summary:
        tail = "；".join(e["content"] for e in events[:2])
        summary = f"{old_summary}；{tail}" if old_summary and tail else (old_summary or tail)
        summary = summary.replace("\n", " ").strip()[-settings.relationship_max_chars:]
        if not summary:
            summary = str(rows[0]["content"])[:settings.relationship_max_chars]

    summary = summary.strip().replace("\n", " ")[:settings.relationship_max_chars]
    if not summary:
        return False
    await db.execute(
        "INSERT INTO relationship(npc_id, target_kind, target_id, summary, updated_at)"
        " VALUES(?,?,?,?,?)"
        " ON CONFLICT(npc_id, target_kind, target_id)"
        " DO UPDATE SET summary = excluded.summary, updated_at = excluded.updated_at",
        (npc_id, target_kind, target_id, summary, time.time()),
    )
    return True
