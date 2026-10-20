"""NPC-NPC 八卦传播（docs/DESIGN.md §6.9）。

午间镇口聚集窗口内，同一 POI、距离 ≤64px 且空闲的 NPC 两两配对。每条传播
选 A 的一条 importance≥5 且 B 未亲历、未听说过的记忆，双方各写一条 kind=gossip；
importance≥7 才用 LLM 改写以省 token。传播链用 source_memory_id 恒指向根记忆，
避免同一件事被反复转述。每对每天最多一次（kv 键），每次扫描最多 2 对。
"""

import json
import logging
import re
import time

from ..config import settings
from ..db import Database
from ..game import plan as plan_mod
from ..game import schedule as schedule_mod
from . import npc as npc_mod
from . import relationship as relationship_mod

logger = logging.getLogger(__name__)


def _parse_line(result_text: str) -> str:
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    line = str(data.get("line", "")).strip() if isinstance(data, dict) else ""
    if not line:
        raise ValueError("missing line")
    return line


async def _rewrite(a: dict, b: dict, poi: str, content: str, store) -> str:
    system = (
        f"你是{a['name']}。{a['persona']}你说话{a['speech_style']}。\n"
        f"你在{poi}遇见{b['name']}，想把下面这件事说给他听：\n{content}\n"
        "用你的口吻转述成一句不超过60字的话，保留事实，不要添加新内容。\n"
        '仅输出JSON：{"line":"..."}'
    )
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": "请开始转述。"}]
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                "gossip", msgs, json_format=True, temperature=temperature,
                metadata={"npc_id": a["id"], "target": b["name"]},
            )
            return _parse_line(cr.text)
        except Exception:
            continue
    return content


async def gossip_between(db: Database, store, a_id: int, b_id: int, *,
                         poi: str | None = None) -> bool:
    a = await npc_mod.by_id(db, a_id)
    b = await npc_mod.by_id(db, b_id)
    if a is None or b is None:
        return False
    poi = poi or schedule_mod.poi_label_for(a, store.clock.now())

    rows = await db.fetch_all(
        "SELECT id, content, participants_json, importance, source_memory_id"
        " FROM memory WHERE npc_id = ? AND kind IN ('event','gossip') AND importance >= ?"
        " ORDER BY importance DESC, ts_game DESC LIMIT 50",
        (a_id, settings.gossip_importance_min),
    )
    known_rows = await db.fetch_all(
        "SELECT source_memory_id FROM memory"
        " WHERE npc_id = ? AND source_memory_id IS NOT NULL",
        (b_id,),
    )
    known = {int(r["source_memory_id"]) for r in known_rows}

    source: dict | None = None
    root = 0
    seen_roots: set[int] = set()
    for r in rows:
        try:
            participants = json.loads(r["participants_json"])
        except (TypeError, ValueError):
            participants = []
        if b["name"] in participants:
            continue  # 亲历者不需要别人转述
        candidate_root = int(r["source_memory_id"] or r["id"])
        if candidate_root in known or candidate_root in seen_roots:
            continue
        seen_roots.add(candidate_root)
        if source is None:
            source, root = r, candidate_root
    if source is None:
        return False

    content = str(source["content"])
    if int(source["importance"]) >= settings.gossip_rewrite_importance:
        line = await _rewrite(a, b, poi, content, store)
    else:
        line = content

    try:
        base_parts = json.loads(source["participants_json"])
    except (TypeError, ValueError):
        base_parts = []
    importance = max(1, int(source["importance"]) - 1)

    await store.add_memory(
        db, b_id, content=f"{a['name']}与我闲聊：{line}", kind="gossip",
        participants=list(dict.fromkeys([*base_parts, a["name"]])),
        poi=poi, importance=importance, source_memory_id=root,
    )
    await store.add_memory(
        db, a_id, content=f"我将此事说与{b['name']}听：{line}", kind="gossip",
        participants=list(dict.fromkeys([*base_parts, b["name"]])),
        poi=poi, importance=importance, source_memory_id=root,
    )

    for npc_id, other in ((a_id, b["name"]), (b_id, a["name"])):
        try:
            await relationship_mod.maybe_update(db, store, npc_id, "npc", other)
        except Exception:
            pass
    return True


async def scan(db: Database, store) -> int:
    now_game = store.clock.now()
    now_real = time.time()
    states = await db.fetch_all("SELECT npc_id, paused_until FROM npc_state")
    paused = {r["npc_id"]: r["paused_until"] for r in states}
    deviations = await plan_mod.deviations_for_day(db, now_game)

    active: list[tuple[dict, tuple[float, float], str]] = []
    for npc_id in npc_mod.ids():
        n = await npc_mod.by_id(db, npc_id)
        dev = deviations.get(npc_id)
        if n is None or not schedule_mod.is_idle(
                now_game, paused.get(npc_id), now_real, dev):
            continue
        active.append((n, schedule_mod.position_for(n, now_game, dev),
                       schedule_mod.poi_label_for(n, now_game, dev)))

    pairs: list[tuple[dict, dict, str]] = []
    for i in range(len(active)):
        a, a_pos, a_poi = active[i]
        for j in range(i + 1, len(active)):
            b, b_pos, b_poi = active[j]
            if a_poi != b_poi:
                continue
            dx, dy = a_pos[0] - b_pos[0], a_pos[1] - b_pos[1]
            if (dx * dx + dy * dy) ** 0.5 <= 64.0:
                pairs.append((a, b, a_poi))
    if not pairs:
        return 0

    rotate = int(time.time() // settings.gossip_interval_real) % len(pairs)
    pairs = pairs[rotate:] + pairs[:rotate]
    day = int(now_game // schedule_mod.GAME_SEC_PER_DAY)

    done = 0
    for a, b, poi in pairs:
        if done >= settings.gossip_max_pairs_per_scan:
            break
        key = f"gossip_pair:{min(a['id'], b['id'])}:{max(a['id'], b['id'])}"
        if await db.kv_get(key) == str(day):
            continue
        try:
            ok = await gossip_between(db, store, a["id"], b["id"], poi=poi)
        except Exception:
            logger.exception("gossip pair %d->%d failed", a["id"], b["id"])
            continue
        if ok:
            await db.kv_set(key, str(day))
            done += 1
    return done
