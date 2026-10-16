import json
from datetime import datetime

from ..db import Database
from . import data


class PlayerError(Exception):
    pass


def exp_next(level: int) -> int:
    return level * level * 20


def derived(p: dict) -> dict:
    return {
        "hp_max": 80 + p["con"] * 12 + p["level"] * 8,
        "mp_max": 50 + p["int_"] * 8 + p["level"] * 5,
        "atk": 8 + p["str"] * 2,
        "def": 4 + p["con"],
        "spd": 10 + p["agi"] * 1.5,
        "dodge": min(p["agi"] * 0.008, 0.25),
        "crit": min(0.05 + p["int_"] * 0.003, 0.30),
    }


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


async def api_dict(db: Database, p: dict) -> dict:
    inv_rows = await db.fetch_all(
        "SELECT it.id AS item_id, it.name, it.type, it.tier, it.effect_json, inv.qty"
        " FROM inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.player_id = ? ORDER BY it.id",
        (p["id"],),
    )
    d = derived(p)
    return {
        "id": p["id"], "name": p["name"], "sect": p["sect_id"],
        "level": p["level"], "exp": p["exp"], "exp_next": exp_next(p["level"]),
        "cultivation": p["cultivation"], "gold": p["gold"],
        "hp": p["hp"], "mp": p["mp"], "hp_max": d["hp_max"], "mp_max": d["mp_max"],
        "stats": {"str": p["str"], "con": p["con"], "agi": p["agi"], "int": p["int_"]},
        "free_points": p["free_points"],
        "atk": d["atk"], "def": d["def"], "spd": d["spd"],
        "dodge": d["dodge"], "crit": d["crit"],
        "pos": {"x": p["pos_x"], "y": p["pos_y"]},
        "inventory": [
            {
                "item_id": r["item_id"], "name": r["name"], "type": r["type"], "qty": r["qty"],
                "desc": data.effect_desc(json.loads(r["effect_json"])),
            }
            for r in inv_rows
        ],
    }
