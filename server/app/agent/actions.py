"""行动层（docs/DESIGN.md §6.14 / docs/MEMORY-V2-PLAN.md §6）：NPC 的原地工具调用。

三条硬规则：

1. **先全量校验，再逐条执行**。任一动作不合法就一个都不执行（原子性），把原因
   交给对话层做一次「改口」重生成。
2. **副作用走既有服务端函数**（inventory / arts），不给 NPC 开侧门：不直接写好感、
   不改日程、不跨地图。
3. **有副作用的动作立即入忆**（`memory.kind='action'`）并推一条附近事件，不等
   36 轮总结；工具内部不调 LLM（会破 2~5s 的对话延迟预算）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from ..db import Database
from ..game import arts as arts_mod
from ..game import inventory as inventory_mod
from ..game import player as player_mod
from . import events as events_mod

logger = logging.getLogger(__name__)

TOOL_NAMES = ("give_item", "take_item", "teach_art", "refuse")

MAX_COUNT = 3
TAKE_ITEM_MIN_AFFINITY = 50   # 能不能从玩家手里要东西
TEACH_ART_MIN_AFFINITY = 70   # 肯不肯传功（与拜师门槛一致）

_ACT_TEXT = {
    "give_item": "把{count}件{name}递给了{player}",
    "take_item": "收下了{player}的{count}件{name}",
    "teach_art": "把{name}传给了{player}",
    "refuse": "回绝了{player}：{reason}",
}


@dataclass
class ExecReport:
    """一次动作批的执行结果；ok=False 时 acted 恒为空。"""

    ok: bool = True
    acted: list[dict] = field(default_factory=list)
    error: str = ""
    observation: str = ""

    @property
    def texts(self) -> list[str]:
        return [act["text"] for act in self.acted if act.get("text")]


def acts_text(acts: list[dict], actor: str = "") -> str:
    """把动作渲染成一句括注：prompt 里 actor="你"，客户端气泡里传 NPC 名。"""
    parts = [str(act.get("text") or "").strip() for act in acts]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    return f"（{actor}{'；'.join(parts)}）"


def _clean_args(raw) -> dict:
    return raw if isinstance(raw, dict) else {}


def _clean_count(raw) -> int | None:
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return None
    if count < 1 or count > MAX_COUNT:
        return None
    return count


async def _item_by_name(db: Database, name) -> dict | None:
    text = str(name or "").strip()
    if not text:
        return None
    return await db.fetch_one(
        "SELECT id, name FROM item WHERE name = ?", (text,))


async def _holding(db: Database, character_id: int, item_id: int) -> int:
    row = await db.fetch_one(
        "SELECT qty FROM character_inventory WHERE character_id = ? AND item_id = ?",
        (character_id, item_id),
    )
    return int(row["qty"]) if row else 0


async def _plan_one(db: Database, npc: dict, player: dict, affinity: int,
                    act) -> tuple[dict | None, str]:
    """校验单个动作，返回 (执行计划, 失败原因)。"""
    if not isinstance(act, dict):
        return None, "动作格式不对"
    tool = str(act.get("tool") or "").strip()
    if tool not in TOOL_NAMES:
        return None, f"没有这个动作：{tool or '空'}"
    args = _clean_args(act.get("args"))

    if tool == "refuse":
        reason = str(args.get("reason") or "").strip()[:20] or "今日不便"
        return {"tool": tool, "reason": reason}, ""

    if tool in ("give_item", "take_item"):
        item = await _item_by_name(db, args.get("item"))
        if item is None:
            return None, f"身上没有叫做「{args.get('item')}」的东西"
        count = _clean_count(args.get("count", 1))
        if count is None:
            return None, "件数不对（一至三件）"
        holder = npc["character_id"] if tool == "give_item" else player["character_id"]
        if await _holding(db, holder, item["id"]) < count:
            who = "你" if tool == "give_item" else "对方"
            return None, f"{who}身上没有这么多{item['name']}"
        if tool == "take_item" and affinity < TAKE_ITEM_MIN_AFFINITY:
            return None, f"交情尚浅（好感 {affinity}/{TAKE_ITEM_MIN_AFFINITY}），开不了这个口"
        return {"tool": tool, "item": item, "count": count}, ""

    # teach_art
    art_name = str(args.get("art") or "").strip()
    art_row = await db.fetch_one("SELECT * FROM art WHERE name = ?", (art_name,))
    if art_row is None:
        return None, f"你不会「{art_name or '空'}」这门功法"
    if int(npc["character"].get("art_id") or 0) != int(art_row["id"]):
        return None, f"你眼下练的不是「{art_row['name']}」，教不了"
    if affinity < TEACH_ART_MIN_AFFINITY:
        return None, f"交情未到（好感 {affinity}/{TEACH_ART_MIN_AFFINITY}），不传外人"
    known = await db.fetch_one(
        "SELECT 1 AS ok FROM character_art WHERE character_id = ? AND art_id = ?",
        (player["character_id"], art_row["id"]),
    )
    if known:
        return None, f"对方已经学过「{art_row['name']}」"
    flags = arts_mod.check_switch(player, art_row)
    if flags["requires_reset"]:
        return None, "两门功法属性相冲，得对方自己拿主意散功重修"
    if flags["requires_clamp"]:
        return None, f"「{art_row['name']}」上限只有 {art_row['cap_level']} 级，会压掉对方修为"
    return {"tool": tool, "art": art_row}, ""


async def execute(db: Database, mem_store, *, npc: dict, player: dict,
                  affinity: int, acts: list[dict], place: str,
                  turn_id: str) -> ExecReport:
    """校验并执行一批动作；任何一条不合法就整批放弃。"""
    if not acts:
        return ExecReport()
    # 幂等：这一轮已经执行过就整批回放，不再校验——副作用已经发生，世界状态变了，
    # 再校验必然失败（比如东西已经给出去了）。
    replayed = await _replay(db, turn_id)
    if replayed is not None:
        return ExecReport(acted=replayed)
    if len(acts) > 2:
        return ExecReport(ok=False, error="一次最多两个动作",
                          observation="你一时手忙脚乱，什么也没做成。")

    plans: list[dict] = []
    for act in acts:
        plan, error = await _plan_one(db, npc, player, affinity, act)
        if plan is None:
            logger.info("act rejected npc=%d turn=%s tool=%s: %s",
                        npc["id"], turn_id, (act or {}).get("tool"), error)
            return ExecReport(ok=False, error=error,
                              observation=f"你没做成：{error}。")
        plans.append(plan)

    report = ExecReport()
    for index, plan in enumerate(plans):
        acted = await _run_one(db, mem_store, npc=npc, player=player,
                               plan=plan, place=place, turn_id=turn_id,
                               call_index=index)
        if acted is None:
            return ExecReport(ok=False, error="动作执行失败",
                              observation="你伸出的手又收了回来。")
        report.acted.append(acted)
    return report


async def _replay(db: Database, turn_id: str) -> list[dict] | None:
    """这一轮已执行过的动作结果（按 call_index 序）；从未执行过返回 None。"""
    if not turn_id:
        return None
    rows = await db.fetch_all(
        "SELECT result_json FROM tool_call_log WHERE turn_id = ?"
        " ORDER BY call_index", (turn_id,))
    if not rows:
        return None
    out: list[dict] = []
    for row in rows:
        try:
            out.append(json.loads(row["result_json"]))
        except (TypeError, ValueError):
            return None
    return out


async def _run_one(db: Database, mem_store, *, npc: dict, player: dict,
                   plan: dict, place: str, turn_id: str,
                   call_index: int) -> dict | None:
    """执行一个动作并记账；(turn_id, call_index) 唯一约束防止重复落账。"""
    tool = plan["tool"]
    player_name = player["name"]
    text = ""
    importance = 3
    if tool == "give_item":
        item, count = plan["item"], plan["count"]
        if not await inventory_mod.consume(db, npc["character_id"], item["id"], count):
            return None
        await inventory_mod.add(db, player["character_id"], item["id"], count)
        text = _ACT_TEXT[tool].format(count=count, name=item["name"],
                                      player=player_name)
        importance = 4
    elif tool == "take_item":
        item, count = plan["item"], plan["count"]
        if not await inventory_mod.consume(db, player["character_id"], item["id"], count):
            return None
        await inventory_mod.add(db, npc["character_id"], item["id"], count)
        text = _ACT_TEXT[tool].format(count=count, name=item["name"],
                                      player=player_name)
        importance = 5
    elif tool == "teach_art":
        art_row = plan["art"]
        try:
            await arts_mod.switch_art(db, player, art_row, source="npc")
        except arts_mod.ArtError:
            return None
        await player_mod.save(db, player)
        text = _ACT_TEXT[tool].format(name=art_row["name"], player=player_name)
        importance = 6
    else:  # refuse
        text = _ACT_TEXT[tool].format(reason=plan["reason"], player=player_name)
        importance = 3

    acted = {"tool": tool, "text": text, "ok": True}
    await db.execute(
        "INSERT INTO tool_call_log(turn_id, call_index, npc_id, tool, args_json,"
        " ok, result_json, created_at_real, ts_game) VALUES(?,?,?,?,?,1,?,?,?)",
        (turn_id, call_index, npc["id"], tool, _dump_args(plan),
         json.dumps(acted, ensure_ascii=False), time.time(),
         mem_store.clock.now()),
    )
    await mem_store.add_memory(
        db, npc["id"], content=f"你{text}。", kind="action",
        participants=[player_name], poi=place, importance=importance)
    events_mod.record(mem_store, f"act.{tool}", f"{npc['name']}{text}", place,
                      [npc["name"], player_name])
    return acted


def _dump_args(plan: dict) -> str:
    item = plan.get("item") or {}
    art_row = plan.get("art") or {}
    return json.dumps({
        "item": item.get("name", ""),
        "count": plan.get("count"),
        "art": art_row.get("name", ""),
        "reason": plan.get("reason", ""),
    }, ensure_ascii=False)
