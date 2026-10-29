"""玩家/NPC 共用的角色卡派生与经验逻辑（docs/M6-PLAN.md Phase A）。"""

from __future__ import annotations

import json
from typing import Any

from ..config import settings
from ..db import Database
from . import arts, skills


class CharacterError(Exception):
    pass


def exp_next(level: int) -> int:
    return level * level * 20


def derived(c: dict, mods: dict | None = None) -> dict:
    m = mods or {}
    str_ = c["str"] + m.get("str", 0)
    con = c["con"] + m.get("con", 0)
    agi = c["agi"] + m.get("agi", 0)
    int_ = c["int_"] + m.get("int", 0)
    eq_int = int_ + m.get("eq_int", 0)
    return {
        "hp_max": 80 + con * 12 + c["level"] * 8,
        "mp_max": 50 + eq_int * 8 + c["level"] * 5 + m.get("eq_mp_max", 0),
        "atk": 8 + str_ * 2 + m.get("eq_atk", 0),
        "def": 4 + con + m.get("eq_def", 0),
        "spd": 10 + agi * 1.5,
        "dodge": min(agi * 0.008, 0.25),
        "crit": min(0.05 + eq_int * 0.003 + m.get("eq_crit", 0), 0.30),
    }


async def modifiers(db: Database, c: dict) -> dict[str, Any]:
    mods: dict[str, Any] = {
        "str": 0, "con": 0, "agi": 0, "int": 0,
        "eq_atk": 0, "eq_def": 0, "eq_crit": 0, "eq_int": 0, "eq_mp_max": 0,
    }
    eq_rows = await db.fetch_all(
        "SELECT it.type, it.effect_json FROM character_inventory inv"
        " JOIN item it ON it.id = inv.item_id"
        " WHERE inv.character_id = ? AND inv.equipped_slot IS NOT NULL",
        (c["character_id"],),
    )
    for r in eq_rows:
        eff = json.loads(r["effect_json"])
        mods["eq_atk"] += eff.get("atk", 0)
        mods["eq_def"] += eff.get("def", 0)
        mods["eq_crit"] += eff.get("crit", 0)
        mods["eq_int"] += eff.get("int", 0)
        mods["eq_mp_max"] += eff.get("mp_max", 0)
    mods.update(arts.passive_mods(arts.row_for(c), int(c["level"])))
    return mods


async def stats(db: Database, c: dict) -> dict:
    return derived(c, await modifiers(db, c))


def level_cap(c: dict) -> int:
    return min(settings.max_level, arts.cap_of(c))


def gain_exp(c: dict, exp: int) -> list[int]:
    if exp <= 0:
        return []
    cap = level_cap(c)
    if c["level"] >= settings.max_level or c["level"] >= cap:
        return []
    c["exp"] += exp
    ups: list[int] = []
    while c["level"] < cap and c["level"] < settings.max_level:
        need = exp_next(c["level"])
        if c["exp"] < need:
            break
        c["exp"] -= need
        c["level"] += 1
        c["str"] += 1
        c["con"] += 1
        c["agi"] += 1
        c["int_"] += 1
        c["free_points"] += 3
        ups.append(c["level"])
    return ups


def allocate(c: dict, stats: dict[str, int]) -> None:
    total = 0
    for key, val in stats.items():
        if key not in skills.STAT_COLS:
            raise CharacterError(f"未知属性：{key}")
        if not isinstance(val, int) or val < 0:
            raise CharacterError("分配点须为非负整数")
        total += val
    if total == 0:
        raise CharacterError("未分配任何点数")
    if total > c["free_points"]:
        raise CharacterError("自由点不足")
    for key, val in stats.items():
        c[skills.STAT_COLS[key]] += val
    c["free_points"] -= total
