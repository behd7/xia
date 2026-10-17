import json
from datetime import datetime
from typing import Any

from ..db import Database
from . import data, skills


class PlayerError(Exception):
    pass


def exp_next(level: int) -> int:
    return level * level * 20


def derived(p: dict, mods: dict | None = None) -> dict:
    m = mods or {}
    str_ = p["str"] + m.get("str", 0)
    con = p["con"] + m.get("con", 0)
    agi = p["agi"] + m.get("agi", 0)
    int_ = p["int_"] + m.get("int", 0)
    eq_int = int_ + m.get("eq_int", 0)
    return {
        "hp_max": 80 + con * 12 + p["level"] * 8,
        "mp_max": 50 + eq_int * 8 + p["level"] * 5 + m.get("eq_mp_max", 0),
        "atk": 8 + str_ * 2 + m.get("eq_atk", 0),
        "def": 4 + con + m.get("eq_def", 0),
        "spd": 10 + agi * 1.5,
        "dodge": min(agi * 0.008, 0.25),
        "crit": min(0.05 + eq_int * 0.003 + m.get("eq_crit", 0), 0.30),
    }


async def modifiers(db: Database, p: dict) -> dict[str, Any]:
    mods: dict[str, Any] = {
        "str": 0, "con": 0, "agi": 0, "int": 0,
        "eq_atk": 0, "eq_def": 0, "eq_crit": 0, "eq_int": 0, "eq_mp_max": 0,
    }
    eq_rows = await db.fetch_all(
        "SELECT it.type, it.effect_json FROM inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.player_id = ? AND inv.equipped_slot IS NOT NULL",
        (p["id"],),
    )
    for r in eq_rows:
        eff = json.loads(r["effect_json"])
        mods["eq_atk"] += eff.get("atk", 0)
        mods["eq_def"] += eff.get("def", 0)
        mods["eq_crit"] += eff.get("crit", 0)
        mods["eq_int"] += eff.get("int", 0)
        mods["eq_mp_max"] += eff.get("mp_max", 0)
    psv = await db.fetch_one(
        "SELECT sk.effect_json, ps.level FROM player_skill ps JOIN skill sk ON sk.id = ps.skill_id"
        " WHERE ps.player_id = ? AND sk.kind = 'passive'",
        (p["id"],),
    )
    if psv:
        eff = json.loads(psv["effect_json"])
        col = skills.STAT_COLS.get(eff.get("stat", ""))
        if col:
            key = "int" if col == "int_" else col
            mods[key] += eff.get("per_level", 1) * psv["level"]
    return mods


async def stats(db: Database, p: dict) -> dict:
    return derived(p, await modifiers(db, p))


async def get(db: Database) -> dict | None:
    return await db.fetch_one("SELECT * FROM player LIMIT 1")


async def create(db: Database, name: str) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    await db.execute("DELETE FROM battle")
    await db.execute("DELETE FROM player_skill")
    await db.execute("DELETE FROM inventory")
    await db.execute("DELETE FROM player")
    p = {
        "name": name, "sect_id": None, "level": 1, "exp": 0, "cultivation": 0,
        "gold": 100, "str": 5, "con": 5, "agi": 5, "int_": 5, "free_points": 0,
        "pos_x": data.DEFAULT_SPAWN[0], "pos_y": data.DEFAULT_SPAWN[1],
    }
    d = derived(p)
    pid = await db.execute(
        "INSERT INTO player(name, sect_id, level, exp, cultivation, hp, mp, gold,"
        " str, con, agi, int_, free_points, pos_x, pos_y, created_at, last_saved_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (p["name"], None, 1, 0, 0, d["hp_max"], d["mp_max"], 100,
         5, 5, 5, 5, 0, p["pos_x"], p["pos_y"], now, now),
    )
    return await db.fetch_one("SELECT * FROM player WHERE id = ?", (pid,))


async def save(db: Database, p: dict) -> None:
    p["last_saved_at"] = datetime.now().isoformat(timespec="seconds")
    await db.execute(
        "UPDATE player SET sect_id=?, level=?, exp=?, cultivation=?, hp=?, mp=?, gold=?,"
        " str=?, con=?, agi=?, int_=?, free_points=?, pos_x=?, pos_y=?, last_saved_at=? WHERE id=?",
        (p["sect_id"], p["level"], p["exp"], p["cultivation"], p["hp"], p["mp"], p["gold"],
         p["str"], p["con"], p["agi"], p["int_"], p["free_points"], p["pos_x"], p["pos_y"],
         p["last_saved_at"], p["id"]),
    )


def gain_exp(p: dict, exp: int) -> list[int]:
    p["exp"] += exp
    ups: list[int] = []
    while p["exp"] >= exp_next(p["level"]):
        p["exp"] -= exp_next(p["level"])
        p["level"] += 1
        p["str"] += 1
        p["con"] += 1
        p["agi"] += 1
        p["int_"] += 1
        p["free_points"] += 3
        ups.append(p["level"])
    return ups


async def allocate(db: Database, p: dict, stats: dict[str, int]) -> None:
    total = 0
    for key, val in stats.items():
        if key not in skills.STAT_COLS:
            raise PlayerError(f"未知属性：{key}")
        if not isinstance(val, int) or val < 0:
            raise PlayerError("分配点须为非负整数")
        total += val
    if total == 0:
        raise PlayerError("未分配任何点数")
    if total > p["free_points"]:
        raise PlayerError("自由点不足")
    for key, val in stats.items():
        p[skills.STAT_COLS[key]] += val
    p["free_points"] -= total


def item_view(r: dict) -> dict:
    return {
        "item_id": r["item_id"], "name": r["name"], "type": r["type"], "tier": r["tier"],
        "qty": r["qty"], "price": r["price"], "desc": data.effect_desc(json.loads(r["effect_json"])),
    }


async def api_dict(db: Database, p: dict) -> dict:
    inv_rows = await db.fetch_all(
        "SELECT it.id AS item_id, it.name, it.type, it.tier, it.price, it.effect_json,"
        " inv.qty, inv.equipped_slot"
        " FROM inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.player_id = ? ORDER BY it.id",
        (p["id"],),
    )
    eq_rows = [r for r in inv_rows if r["equipped_slot"] is not None]
    equipped: dict[str, dict | None] = {"weapon": None, "armor": None, "accessory": None}
    for r in eq_rows:
        equipped[r["equipped_slot"]] = item_view(r)

    sect = None
    skills_view: list[dict] = []
    if p["sect_id"]:
        s = skills.SECTS[p["sect_id"]]
        sect = {"id": p["sect_id"], "name": s["name"], "rank": skills.RANKS[skills.MAX_RANK]}
        ps_rows = await db.fetch_all(
            "SELECT sk.*, ps.level, ps.equipped FROM player_skill ps"
            " JOIN skill sk ON sk.id = ps.skill_id WHERE ps.player_id = ? ORDER BY sk.tier_req, sk.id",
            (p["id"],),
        )
        for r in ps_rows:
            eff = json.loads(r["effect_json"])
            active = r["kind"] == "active"
            locked = r["tier_req"] > skills.MAX_RANK
            skills_view.append({
                "skill_id": r["id"], "name": r["name"], "kind": r["kind"],
                "tier": r["tier_req"], "level": r["level"],
                "mp_cost": r["mp_cost"],
                "multiplier": skills.effective_multiplier(r["multiplier"], r["level"]) if active else 0,
                "effect_desc": skills.effect_desc(eff),
                "equipped": bool(r["equipped"]), "locked": locked,
                "upgradable": r["level"] < skills.MAX_SKILL_LEVEL and not locked,
                "upgrade_cost": skills.upgrade_cost(r["level"]),
            })

    d = derived(p, await modifiers(db, p))
    return {
        "id": p["id"], "name": p["name"], "sect": sect,
        "level": p["level"], "exp": p["exp"], "exp_next": exp_next(p["level"]),
        "cultivation": p["cultivation"], "gold": p["gold"],
        "hp": p["hp"], "mp": p["mp"], "hp_max": d["hp_max"], "mp_max": d["mp_max"],
        "stats": {"str": p["str"], "con": p["con"], "agi": p["agi"], "int": p["int_"]},
        "free_points": p["free_points"],
        "atk": d["atk"], "def": d["def"], "spd": d["spd"],
        "dodge": d["dodge"], "crit": d["crit"],
        "pos": {"x": p["pos_x"], "y": p["pos_y"]},
        "equipped": equipped,
        "skills": skills_view,
        "inventory": [
            {
                "item_id": r["item_id"], "name": r["name"], "type": r["type"], "qty": r["qty"],
                "slot": r["equipped_slot"], "price": r["price"],
                "desc": data.effect_desc(json.loads(r["effect_json"])),
            }
            for r in inv_rows
        ],
    }
