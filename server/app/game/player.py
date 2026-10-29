import json
from datetime import datetime

from ..db import Database
from . import arts, character as char_mod, data, skills


class PlayerError(Exception):
    pass


exp_next = char_mod.exp_next


def derived(p: dict, mods: dict | None = None) -> dict:
    return char_mod.derived(p, mods)


async def modifiers(db: Database, p: dict) -> dict:
    return await char_mod.modifiers(db, p)


async def stats(db: Database, p: dict) -> dict:
    return await char_mod.stats(db, p)


async def get(db: Database) -> dict | None:
    prow = await db.fetch_one("SELECT * FROM player LIMIT 1")
    if prow is None:
        return None
    cid = prow.get("character_id")
    if cid is None:
        crow = await db.fetch_one(
            "SELECT id FROM character WHERE kind = 'player' AND name = ? LIMIT 1",
            (prow["name"],),
        )
        if crow is None:
            return None
        cid = crow["id"]
        await db.execute(
            "UPDATE player SET character_id = ? WHERE id = ?", (cid, prow["id"])
        )
    crow = await db.fetch_one("SELECT * FROM character WHERE id = ?", (cid,))
    if crow is None:
        return None
    p = dict(crow)
    p["id"] = prow["id"]
    p["character_id"] = cid
    p["pos_x"] = prow["pos_x"]
    p["pos_y"] = prow["pos_y"]
    p["clock_offset"] = prow["clock_offset"]
    return p


async def create(db: Database, name: str) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    await db.execute("DELETE FROM battle")
    await db.execute("DELETE FROM character_skill"
                     " WHERE character_id IN (SELECT id FROM character WHERE kind = 'player')")
    await db.execute("DELETE FROM character_inventory"
                     " WHERE character_id IN (SELECT id FROM character WHERE kind = 'player')")
    await db.execute("DELETE FROM character_art"
                     " WHERE character_id IN (SELECT id FROM character WHERE kind = 'player')")
    await db.execute("DELETE FROM character WHERE kind = 'player'")
    await db.execute("DELETE FROM player_skill")
    await db.execute("DELETE FROM inventory")
    await db.execute("DELETE FROM player")
    pid = await db.execute(
        "INSERT INTO player(name, pos_x, pos_y, created_at, last_saved_at)"
        " VALUES(?,?,?,?,?)",
        (name, data.DEFAULT_SPAWN[0], data.DEFAULT_SPAWN[1], now, now),
    )
    d = char_mod.derived({
        "level": 1, "str": 5, "con": 5, "agi": 5, "int_": 5,
    })
    cid = await db.execute(
        "INSERT INTO character(kind, name, level, exp, cultivation, hp, mp, gold,"
        " str, con, agi, int_, free_points, art_id, sect_id, map_id,"
        " created_at, last_saved_at)"
        " VALUES('player',?,1,0,0,?,?,100,5,5,5,5,0,NULL,NULL,'qingxi',?,?)",
        (name, d["hp_max"], d["mp_max"], now, now),
    )
    await db.execute(
        "UPDATE player SET character_id = ? WHERE id = ?", (cid, pid)
    )
    return await get(db)


async def save(db: Database, p: dict) -> None:
    p["last_saved_at"] = datetime.now().isoformat(timespec="seconds")
    await db.execute(
        "UPDATE character SET sect_id=?, level=?, exp=?, cultivation=?, hp=?, mp=?,"
        " gold=?, str=?, con=?, agi=?, int_=?, free_points=?, art_id=?, map_id=?,"
        " last_saved_at=? WHERE id=?",
        (p.get("sect_id"), p["level"], p["exp"], p["cultivation"], p["hp"], p["mp"],
         p["gold"], p["str"], p["con"], p["agi"], p["int_"], p["free_points"],
         p.get("art_id"), p.get("map_id", "qingxi"), p["last_saved_at"],
         p["character_id"]),
    )
    await db.execute(
        "UPDATE player SET pos_x=?, pos_y=?, clock_offset=? WHERE id=?",
        (p["pos_x"], p["pos_y"], p.get("clock_offset", 0), p["id"]),
    )


def gain_exp(p: dict, exp: int) -> list[int]:
    return char_mod.gain_exp(p, exp)


async def allocate(db: Database, p: dict, stats: dict[str, int]) -> None:
    try:
        char_mod.allocate(p, stats)
    except char_mod.CharacterError as e:
        raise PlayerError(str(e))


def item_view(r: dict) -> dict:
    return {
        "item_id": r["item_id"], "name": r["name"], "type": r["type"], "tier": r["tier"],
        "qty": r["qty"], "price": r["price"], "desc": data.effect_desc(json.loads(r["effect_json"])),
    }


async def api_dict(db: Database, p: dict) -> dict:
    inv_rows = await db.fetch_all(
        "SELECT it.id AS item_id, it.name, it.type, it.tier, it.price, it.effect_json,"
        " inv.qty, inv.equipped_slot"
        " FROM character_inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.character_id = ? ORDER BY it.id",
        (p["character_id"],),
    )
    eq_rows = [r for r in inv_rows if r["equipped_slot"] is not None]
    equipped: dict[str, dict | None] = {"weapon": None, "armor": None, "accessory": None}
    for r in eq_rows:
        equipped[r["equipped_slot"]] = item_view(r)

    sect = None
    if p.get("sect_id"):
        s = skills.SECTS.get(p["sect_id"])
        if s and s.get("kind") == "sect":
            rank = await skills.current_rank(db, p)
            sect = {"id": p["sect_id"], "name": s["name"],
                    "rank": skills.RANKS.get(rank, "入门弟子"), "kind": "sect"}

    art_row = arts.row_for(p)
    art = None
    if art_row:
        cap = int(art_row["cap_level"])
        art = {
            "id": art_row["id"], "name": art_row["name"],
            "element": art_row["element"],
            "element_name": arts.element_name(art_row["element"]),
            "cap_level": cap, "level": p["level"],
            "at_cap": p["level"] >= cap,
            "passive_desc": arts.passive_desc(art_row),
            "desc": art_row.get("desc", ""),
            "is_true": bool(art_row["is_true"]),
        }
    learned_arts = await arts.list_for_client(db, p)

    skills_view: list[dict] = []
    if art_row:
        ps_rows = await db.fetch_all(
            "SELECT sk.*, cs.level, cs.equipped FROM character_skill cs"
            " JOIN skill sk ON sk.id = cs.skill_id"
            " WHERE cs.character_id = ? AND sk.art_id = ?"
            " ORDER BY sk.unlock_level, sk.tier_req, sk.id",
            (p["character_id"], art_row["id"]),
        )
        for r in ps_rows:
            eff = json.loads(r["effect_json"])
            active = r["kind"] == "active"
            locked = p["level"] < int(r["unlock_level"] or 1)
            skills_view.append({
                "skill_id": r["id"], "name": r["name"], "kind": r["kind"],
                "tier": r["tier_req"], "level": r["level"],
                "mp_cost": r["mp_cost"],
                "multiplier": skills.effective_multiplier(r["multiplier"], r["level"]) if active else 0,
                "effect_desc": skills.effect_desc(eff),
                "equipped": bool(r["equipped"]), "locked": locked,
                "upgradable": r["level"] < skills.MAX_SKILL_LEVEL and not locked,
                "upgrade_cost": skills.upgrade_cost(p["level"], r["level"]),
            })

    d = char_mod.derived(p, await char_mod.modifiers(db, p))
    cap = arts.cap_of(p)
    return {
        "id": p["id"], "character_id": p["character_id"], "name": p["name"], "sect": sect,
        "level": p["level"], "exp": p["exp"], "exp_next": char_mod.exp_next(p["level"]),
        "level_cap": cap, "at_cap": p["level"] >= cap,
        "cultivation": p["cultivation"], "gold": p["gold"],
        "hp": p["hp"], "mp": p["mp"], "hp_max": d["hp_max"], "mp_max": d["mp_max"],
        "stats": {"str": p["str"], "con": p["con"], "agi": p["agi"], "int": p["int_"]},
        "free_points": p["free_points"],
        "atk": d["atk"], "def": d["def"], "spd": d["spd"],
        "dodge": d["dodge"], "crit": d["crit"],
        "pos": {"x": p["pos_x"], "y": p["pos_y"]},
        "map_id": p.get("map_id", "qingxi"),
        "art": art, "learned_arts": learned_arts,
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
