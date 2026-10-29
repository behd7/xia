"""功法目录、属性克制与同源/异源转换（docs/DESIGN.md §5.1/§5.2/§5.8）。

本模块只依赖 DB 与 config，不 import player/skills/agent，避免循环依赖。
角色卡状态由 game/character.py 负责；这里只做功法规则与 character 行更新。
"""

from __future__ import annotations

import json
import time

from ..config import settings
from ..db import Database

ELEMENTS = (
    "metal", "wood", "water", "fire", "earth",
    "wind", "thunder", "ice", "light", "dark", "demon",
)
ELEMENT_NAMES = {
    "metal": "金", "wood": "木", "water": "水", "fire": "火", "earth": "土",
    "wind": "风", "thunder": "雷", "ice": "冰", "light": "光", "dark": "暗",
    "demon": "魔",
}
# 五行环 + 异变环；跨环/魔功中立。
COUNTERS = {
    "metal": "wood", "wood": "earth", "earth": "water", "water": "fire", "fire": "metal",
    "wind": "thunder", "thunder": "ice", "ice": "light", "light": "dark", "dark": "wind",
}
GUILDS = ("jianyang", "xuantie", "yanyu")
GUILD_AFFINITY = 20
GUILD_TUITION = 50

# 武功目录：Phase A 只投放清溪镇三馆 + 南风城外传/城主占位；
# 真传与招式在 Phase C 补齐。name 必须与 docs/DESIGN.md §5.2 一致。
ARTS = [
    {"key": "art_lieyang", "name": "烈阳剑诀", "element": "fire", "cap": 15,
     "sect": "jianyang", "is_true": False,
     "passive": {"stat": "str", "per_levels": 3, "amount": 1},
     "desc": "烈阳武馆外传，走刚猛一路。"},
    {"key": "art_xuantu", "name": "玄土刀法", "element": "earth", "cap": 15,
     "sect": "xuantie", "is_true": False,
     "passive": {"stat": "con", "per_levels": 3, "amount": 1},
     "desc": "玄土武馆外传，沉稳厚重。"},
    {"key": "art_yanyu", "name": "烟雨诀", "element": "water", "cap": 15,
     "sect": "yanyu", "is_true": False,
     "passive": {"stat": "agi", "per_levels": 3, "amount": 1},
     "desc": "烟雨武馆外传，身法轻灵。"},
    {"key": "art_dafeng", "name": "大风诀", "element": "wind", "cap": 45,
     "sect": "fengchi", "is_true": False,
     "passive": {"stat": "agi", "per_levels": 3, "amount": 1},
     "desc": "风池派外传，御风而行。"},
    {"key": "art_chiyang", "name": "赤阳功", "element": "fire", "cap": 40,
     "sect": "chidi", "is_true": False,
     "passive": {"stat": "str", "per_levels": 3, "amount": 1},
     "desc": "赤地门外传，赤阳灼体。"},
    {"key": "art_nantian", "name": "南天诀", "element": "thunder", "cap": 55,
     "sect": None, "is_true": True,
     "passive": {"stat": "int", "per_levels": 3, "amount": 1},
     "desc": "城主绝学，暂定雷系。"},
]

ART_BY_KEY = {a["key"]: a for a in ARTS}
_arts_by_key: dict[str, dict] = {}
_arts_by_id: dict[int, dict] = {}


class ArtError(Exception):
    def __init__(self, message: str, *, requires_reset: bool = False,
                 requires_clamp: bool = False, clamp_to: int | None = None):
        super().__init__(message)
        self.requires_reset = requires_reset
        self.requires_clamp = requires_clamp
        self.clamp_to = clamp_to


async def seed_and_load(db: Database) -> None:
    for a in ARTS:
        passive = json.dumps(a.get("passive", {}), ensure_ascii=False)
        row = await db.fetch_one("SELECT id FROM art WHERE name = ?", (a["name"],))
        if row:
            await db.execute(
                "UPDATE art SET element=?, cap_level=?, sect_id=?, is_true=?,"
                " passive_json=?, desc=? WHERE id=?",
                (a["element"], a["cap"], a["sect"], 1 if a["is_true"] else 0,
                 passive, a.get("desc", ""), row["id"]),
            )
        else:
            await db.execute(
                "INSERT INTO art(name, element, cap_level, sect_id, is_true,"
                " passive_json, desc) VALUES(?,?,?,?,?,?,?)",
                (a["name"], a["element"], a["cap"], a["sect"],
                 1 if a["is_true"] else 0, passive, a.get("desc", "")),
            )
    rows = await db.fetch_all("SELECT * FROM art")
    by_name = {r["name"]: r for r in rows}
    _arts_by_key.clear()
    _arts_by_id.clear()
    for key, a in ART_BY_KEY.items():
        row = by_name.get(a["name"])
        if row:
            _arts_by_key[key] = row
            _arts_by_id[row["id"]] = row


def art(key: str) -> dict:
    if not _arts_by_key:
        raise RuntimeError("art catalog not seeded")
    return _arts_by_key[key]


async def by_id(db: Database, art_id: int) -> dict | None:
    return await db.fetch_one("SELECT * FROM art WHERE id = ?", (art_id,))


def row_for(c: dict) -> dict | None:
    return _arts_by_id.get(c.get("art_id")) if c.get("art_id") else None


def element_for_id(art_id: int | None) -> str | None:
    row = _arts_by_id.get(art_id) if art_id else None
    return row["element"] if row else None


def element_name(element: str | None) -> str:
    return ELEMENT_NAMES.get(element or "", "无")


def cap_of(c: dict) -> int:
    row = row_for(c)
    return int(row["cap_level"]) if row else settings.artless_level_cap


def passive_mods(row: dict | None, level: int) -> dict[str, int]:
    if not row:
        return {}
    try:
        p = json.loads(row.get("passive_json") or "{}")
    except (TypeError, ValueError):
        return {}
    stat = p.get("stat")
    per = max(1, int(p.get("per_levels", 1)))
    amount = int(p.get("amount", 1))
    if stat not in ("str", "con", "agi", "int") or level < per:
        return {}
    return {stat: (level // per) * amount}


def passive_desc(row: dict | None) -> str:
    if not row:
        return ""
    try:
        p = json.loads(row.get("passive_json") or "{}")
    except (TypeError, ValueError):
        return ""
    names = {"str": "臂力", "con": "根骨", "agi": "身法", "int": "悟性"}
    stat = p.get("stat")
    if stat not in names:
        return ""
    return f"每 {int(p.get('per_levels', 1))} 级{names[stat]}+{int(p.get('amount', 1))}"


def counter_multiplier(attacker_el: str | None, defender_el: str | None) -> float:
    if not attacker_el or not defender_el:
        return 1.0
    if attacker_el == "demon" or defender_el == "demon":
        return 1.0
    if COUNTERS.get(attacker_el) == defender_el:
        return settings.element_advantage
    if COUNTERS.get(defender_el) == attacker_el:
        return settings.element_disadvantage
    return 1.0


def counter_label(attacker_el: str | None, defender_el: str | None) -> str:
    mult = counter_multiplier(attacker_el, defender_el)
    if mult > 1.0:
        return "advantage"
    if mult < 1.0:
        return "disadvantage"
    return "neutral"


def check_switch(c: dict, art_row: dict) -> dict:
    cur = row_for(c)
    reset = bool(cur and cur["element"] != art_row["element"])
    clamp = int(c.get("level", 1)) > int(art_row["cap_level"])
    return {
        "requires_reset": reset,
        "requires_clamp": clamp,
        "clamp_to": int(art_row["cap_level"]) if clamp else None,
        "same": bool(cur and cur["id"] == art_row["id"]),
    }


async def ensure_art_skills(db: Database, character_id: int, art_row: dict) -> None:
    rows = await db.fetch_all(
        "SELECT id FROM skill WHERE art_id = ? AND kind = 'active'"
        " ORDER BY unlock_level, id",
        (art_row["id"],),
    )
    for r in rows:
        await db.execute(
            "INSERT OR IGNORE INTO character_skill(character_id, skill_id, level, equipped)"
            " VALUES(?,?,1,0)",
            (character_id, r["id"]),
        )


async def switch_art(db: Database, c: dict, art_row: dict, *,
                     confirm_reset: bool = False, confirm_clamp: bool = False,
                     source: str = "switch") -> dict:
    cur = row_for(c)
    if cur and cur["id"] == art_row["id"]:
        await ensure_art_skills(db, c["character_id"], art_row)
        return {"art": art_row, "reset": False, "clamped": False,
                "level": int(c["level"]), "cap": int(art_row["cap_level"])}

    reset = bool(cur and cur["element"] != art_row["element"])
    clamp = int(c["level"]) > int(art_row["cap_level"])
    if reset and not confirm_reset:
        raise ArtError(
            f"散功重修：{cur['name']}（{element_name(cur['element'])}）与"
            f"{art_row['name']}（{element_name(art_row['element'])}）属性相冲，"
            "修为将归 1 级，四维/装备/图鉴保留。",
            requires_reset=True,
        )
    if clamp and not confirm_clamp:
        raise ArtError(
            f"新功法上限为 {art_row['cap_level']} 级，低于你当前修为 "
            f"{c['level']} 级；确认后将压至上限。",
            requires_clamp=True, clamp_to=int(art_row["cap_level"]),
        )

    if reset:
        c["level"] = 1
        c["exp"] = 0
    elif clamp:
        c["level"] = int(art_row["cap_level"])
        c["exp"] = 0
    c["art_id"] = art_row["id"]

    await db.execute(
        "UPDATE character SET art_id=?, level=?, exp=? WHERE id=?",
        (c["art_id"], c["level"], c["exp"], c["character_id"]),
    )
    await db.execute(
        "INSERT OR IGNORE INTO character_art(character_id, art_id, learned_at, source)"
        " VALUES(?,?,?,?)",
        (c["character_id"], art_row["id"], time.time(), source),
    )
    await ensure_art_skills(db, c["character_id"], art_row)
    await db.execute(
        "UPDATE character_skill SET equipped = 0 WHERE character_id = ?",
        (c["character_id"],),
    )
    first = await db.fetch_one(
        "SELECT sk.id FROM character_skill cs JOIN skill sk ON sk.id = cs.skill_id"
        " WHERE cs.character_id = ? AND sk.art_id = ? AND sk.kind = 'active'"
        " AND sk.unlock_level <= ? ORDER BY sk.unlock_level, sk.id LIMIT 1",
        (c["character_id"], art_row["id"], c["level"]),
    )
    if first:
        await db.execute(
            "UPDATE character_skill SET equipped = 1"
            " WHERE character_id = ? AND skill_id = ?",
            (c["character_id"], first["id"]),
        )
    return {"art": art_row, "reset": reset, "clamped": clamp,
            "level": int(c["level"]), "cap": int(art_row["cap_level"])}


async def learn_guild_art(db: Database, p: dict, art_key: str,
                          master_affinity: int, *,
                          confirm_reset: bool = False,
                          confirm_clamp: bool = False) -> dict:
    art_row = art(art_key)
    if master_affinity < GUILD_AFFINITY:
        raise ArtError(f"馆主尚未认可你（好感 ≥{GUILD_AFFINITY} 才肯传功）。")
    learned = await db.fetch_one(
        "SELECT 1 AS ok FROM character_art WHERE character_id = ? AND art_id = ?",
        (p["character_id"], art_row["id"]),
    )
    tuition = 0 if learned else GUILD_TUITION
    if p["gold"] < tuition:
        raise ArtError(f"学费不足（需 {tuition} 两）。")
    result = await switch_art(
        db, p, art_row, confirm_reset=confirm_reset,
        confirm_clamp=confirm_clamp, source="guild",
    )
    p["gold"] -= tuition
    await db.execute(
        "UPDATE character SET gold = ? WHERE id = ?",
        (p["gold"], p["character_id"]),
    )
    return {**result, "tuition": tuition, "already": bool(learned)}


async def teacher_affinity(db: Database, art_row: dict, npc_id: int) -> int | None:
    row = await db.fetch_one(
        "SELECT ns.affinity FROM npc n JOIN npc_state ns ON ns.npc_id = n.id"
        " WHERE n.id = ? AND n.role = 'master' AND n.sect_id = ?",
        (npc_id, art_row.get("sect_id")),
    )
    return int(row["affinity"]) if row else None


async def learn_from_npc(db: Database, p: dict, art_row: dict, npc_id: int, *,
                         confirm_reset: bool = False,
                         confirm_clamp: bool = False) -> dict:
    aff = await teacher_affinity(db, art_row, npc_id)
    if aff is None:
        raise ArtError("此人不传这门功法。")
    return await learn_guild_art(
        db, p, next(k for k, a in ART_BY_KEY.items() if a["name"] == art_row["name"]),
        aff, confirm_reset=confirm_reset, confirm_clamp=confirm_clamp,
    )


async def list_for_client(db: Database, c: dict) -> list[dict]:
    rows = await db.fetch_all("SELECT * FROM art ORDER BY element, cap_level, id")
    learned_rows = await db.fetch_all(
        "SELECT art_id FROM character_art WHERE character_id = ?",
        (c["character_id"],),
    )
    learned = {r["art_id"] for r in learned_rows}
    out = []
    for r in rows:
        flags = check_switch(c, r)
        out.append({
            "id": r["id"], "name": r["name"], "element": r["element"],
            "element_name": element_name(r["element"]),
            "cap_level": r["cap_level"], "is_true": bool(r["is_true"]),
            "sect_id": r.get("sect_id"), "desc": r.get("desc", ""),
            "passive_desc": passive_desc(r),
            "learned": r["id"] in learned,
            "current": r["id"] == c.get("art_id"),
            "requires_reset": flags["requires_reset"],
            "requires_clamp": flags["requires_clamp"],
            "clamp_to": flags["clamp_to"],
        })
    return out


async def migrate_legacy(db: Database) -> None:
    """旧档：三馆旧 sect_id 迁成对应功法，并清掉正式门派身份。"""
    rows = await db.fetch_all(
        "SELECT c.id AS character_id, c.sect_id, c.level, c.exp, c.art_id"
        " FROM character c WHERE c.kind = 'player' AND c.art_id IS NULL"
        " AND c.sect_id IN ('jianyang','xuantie','yanyu')"
    )
    for r in rows:
        art_row = next((a for a in _arts_by_id.values()
                        if a.get("sect_id") == r["sect_id"]), None)
        if art_row is None:
            continue
        c = {"character_id": r["character_id"], "level": r["level"],
             "exp": r["exp"], "art_id": None}
        await switch_art(db, c, art_row, confirm_reset=True,
                         confirm_clamp=True, source="legacy")
        await db.execute(
            "UPDATE character SET sect_id = NULL WHERE id = ?",
            (r["character_id"],),
        )
