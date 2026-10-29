import json

from ..config import settings
from ..db import Database
from . import arts

# 主动招式目录：art 指向 art.key，unlock 是修为等级门槛。
# 旧内功被动不再作为 skill 行，效果统一在 art.passive_json。
SKILLS = [
    {"key": "chiyang_zhan", "name": "赤阳斩", "art": "art_lieyang", "sect": "jianyang",
     "kind": "active", "tier": 1, "unlock": 1, "mp_cost": 8, "multiplier": 1.6,
     "effect": {}},
    {"key": "fenglei_shi", "name": "风雷剑势", "art": "art_lieyang", "sect": "jianyang",
     "kind": "active", "tier": 2, "unlock": 5, "mp_cost": 14, "multiplier": 1.9,
     "effect": {"crit_add": 0.20}},
    {"key": "xuri_sheng", "name": "旭日东升", "art": "art_lieyang", "sect": "jianyang",
     "kind": "active", "tier": 3, "unlock": 10, "mp_cost": 26, "multiplier": 2.6,
     "effect": {"cooldown": 2}},
    {"key": "nulang_dao", "name": "怒浪刀", "art": "art_xuantu", "sect": "xuantie",
     "kind": "active", "tier": 1, "unlock": 1, "mp_cost": 8, "multiplier": 1.5,
     "effect": {"buff_self": {"def_pct": 1.10, "rounds": 1}}},
    {"key": "tiebu_shan", "name": "铁布衫", "art": "art_xuantu", "sect": "xuantie",
     "kind": "active", "tier": 2, "unlock": 5, "mp_cost": 12, "multiplier": 0.8,
     "effect": {"buff_self": {"def_pct": 1.40, "rounds": 2}}},
    {"key": "kaishan_dao", "name": "开山刀", "art": "art_xuantu", "sect": "xuantie",
     "kind": "active", "tier": 3, "unlock": 10, "mp_cost": 26, "multiplier": 2.4,
     "effect": {"pierce_def": 0.5}},
    {"key": "mizong_bu", "name": "迷踪步", "art": "art_yanyu", "sect": "yanyu",
     "kind": "active", "tier": 1, "unlock": 1, "mp_cost": 6, "multiplier": 1.2,
     "effect": {"buff_self": {"dodge_add": 0.15, "rounds": 2}}},
    {"key": "xiyu_ci", "name": "细雨十三刺", "art": "art_yanyu", "sect": "yanyu",
     "kind": "active", "tier": 2, "unlock": 5, "mp_cost": 15, "multiplier": 1.8,
     "effect": {"ignore_dodge": True}},
    {"key": "feihong_guan", "name": "飞虹贯云", "art": "art_yanyu", "sect": "yanyu",
     "kind": "active", "tier": 3, "unlock": 10, "mp_cost": 22, "multiplier": 2.2,
     "effect": {"double_if_faster": 1.5}},
]

# 清溪镇三馆是 guild；南风城两派（Phase C）才用 kind='sect'。
SECTS = {
    "jianyang": {"name": "剑阳道馆", "poi": "剑阳道馆", "kind": "guild",
                 "art": "art_lieyang"},
    "xuantie": {"name": "玄铁堂", "poi": "玄铁堂", "kind": "guild",
                "art": "art_xuantu"},
    "yanyu": {"name": "烟雨小筑", "poi": "烟雨小筑", "kind": "guild",
              "art": "art_yanyu"},
}

STAT_COLS = {"str": "str", "con": "con", "agi": "agi", "int": "int_"}
STAT_NAMES = {"str": "臂力", "con": "根骨", "agi": "身法", "int": "悟性"}

RANKS = {1: "入门弟子", 2: "亲传弟子", 3: "关门弟子"}
RANK_THRESHOLDS = [(60, 1), (75, 2), (85, 3)]
JOIN_LEVEL_REQ = 15
APPRENTICE_AFFINITY = 60
MAX_SKILL_LEVEL = 5
MAX_EQUIPPED_ACTIVES = 3

_skills_by_key: dict[str, dict] = {}


class SkillError(Exception):
    pass


async def seed_and_load(db: Database) -> None:
    for s in SKILLS:
        art_row = arts.art(s["art"])
        effect_json = json.dumps(s["effect"], ensure_ascii=False)
        row = await db.fetch_one("SELECT id FROM skill WHERE name = ?", (s["name"],))
        if row:
            await db.execute(
                "UPDATE skill SET sect_id=?, kind=?, tier_req=?, art_id=?,"
                " unlock_level=?, mp_cost=?, multiplier=?, cooldown=?, effect_json=?"
                " WHERE id=?",
                (s.get("sect"), s["kind"], s["tier"], art_row["id"], s["unlock"],
                 s["mp_cost"], s["multiplier"], s["effect"].get("cooldown", 0),
                 effect_json, row["id"]),
            )
        else:
            await db.execute(
                "INSERT INTO skill(name, sect_id, kind, tier_req, art_id, unlock_level,"
                " mp_cost, multiplier, cooldown, effect_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (s["name"], s.get("sect"), s["kind"], s["tier"], art_row["id"],
                 s["unlock"], s["mp_cost"], s["multiplier"],
                 s["effect"].get("cooldown", 0), effect_json),
            )
    rows = await db.fetch_all("SELECT * FROM skill")
    by_name = {r["name"]: r for r in rows}
    _skills_by_key.clear()
    _skills_by_key.update({s["key"]: by_name[s["name"]] for s in SKILLS})


def skill(key: str) -> dict:
    if not _skills_by_key:
        raise RuntimeError("skill catalog not seeded")
    return _skills_by_key[key]


async def by_id(db: Database, skill_id: int) -> dict | None:
    return await db.fetch_one("SELECT * FROM skill WHERE id = ?", (skill_id,))


def upgrade_cost(character_level: int, skill_level: int) -> int:
    base = max(20, round(character_level * character_level * 20
                         * settings.skill_upgrade_exp_rate))
    return base * skill_level


def effective_multiplier(base: float, level: int) -> float:
    return base + 0.15 * (level - 1)


def effect_desc(eff: dict) -> str:
    if "stat" in eff:
        return f"每级{STAT_NAMES[eff['stat']]}+{eff.get('per_level', 1)}"
    if "crit_add" in eff:
        return f"本次暴击率+{int(eff['crit_add'] * 100)}%"
    if "cooldown" in eff:
        return f"冷却 {eff['cooldown']} 回合"
    b = eff.get("buff_self")
    if b:
        if "def_pct" in b:
            return f"自身防御+{int((b['def_pct'] - 1) * 100)}% {b['rounds']}回合"
        return f"自身闪避+{int(b['dodge_add'] * 100)}% {b['rounds']}回合"
    if "pierce_def" in eff:
        return f"无视目标 {int(eff['pierce_def'] * 100)}% 防御"
    if "ignore_dodge" in eff:
        return "此击无视闪避"
    if "double_if_faster" in eff:
        return "速度远胜对手时攻击两次"
    return "—"


MASTER_SQL = (
    "SELECT ns.affinity FROM npc n JOIN npc_state ns ON ns.npc_id = n.id"
    " WHERE n.sect_id = ? AND n.role = 'master'"
)


async def _master_affinity(db: Database, sect_id: str | None) -> int:
    if not sect_id:
        return 0
    row = await db.fetch_one(MASTER_SQL, (sect_id,))
    return row["affinity"] if row else 0


async def master_affinity(db: Database, p: dict) -> int:
    return await _master_affinity(db, p["sect_id"])


def _rank_of(affinity: int) -> int:
    for thr in sorted(RANK_THRESHOLDS, reverse=True):
        if affinity >= thr[0]:
            return thr[1]
    return 0


async def current_rank(db: Database, p: dict) -> int:
    return _rank_of(await master_affinity(db, p))


async def tier_unlocked(db: Database, p: dict, tier: int) -> bool:
    return tier <= await current_rank(db, p)


async def upgrade(db: Database, p: dict, skill_id: int) -> None:
    srow = await by_id(db, skill_id)
    if srow is None:
        raise SkillError("查无此技")
    if srow["art_id"] != p.get("art_id"):
        raise SkillError("此招不属于当前功法")
    ps = await db.fetch_one(
        "SELECT level FROM character_skill WHERE character_id = ? AND skill_id = ?",
        (p["character_id"], skill_id),
    )
    if ps is None:
        raise SkillError("尚未习得此技")
    if ps["level"] >= MAX_SKILL_LEVEL:
        raise SkillError("已至最高境界")
    cost = upgrade_cost(p["level"], ps["level"])
    if p["cultivation"] < cost:
        raise SkillError(f"修炼点不足（需 {cost}）")
    p["cultivation"] -= cost
    await db.execute(
        "UPDATE character_skill SET level = ?"
        " WHERE character_id = ? AND skill_id = ?",
        (ps["level"] + 1, p["character_id"], skill_id),
    )


async def set_equip(db: Database, p: dict, skill_id: int, equip: bool) -> None:
    srow = await by_id(db, skill_id)
    if srow is None:
        raise SkillError("查无此技")
    if srow["kind"] != "active":
        raise SkillError("内功为被动心法，无需装备")
    if srow["art_id"] != p.get("art_id"):
        raise SkillError("此招不属于当前功法")
    if p["level"] < int(srow["unlock_level"] or 1):
        raise SkillError("修为未至，此招尚未解锁")
    ps = await db.fetch_one(
        "SELECT level, equipped FROM character_skill"
        " WHERE character_id = ? AND skill_id = ?",
        (p["character_id"], skill_id),
    )
    if ps is None:
        raise SkillError("尚未习得此技")
    if equip and not ps["equipped"]:
        count = await db.fetch_one(
            "SELECT COUNT(*) AS n FROM character_skill cs"
            " JOIN skill sk ON sk.id = cs.skill_id"
            " WHERE cs.character_id = ? AND cs.equipped = 1"
            " AND sk.kind = 'active' AND sk.art_id = ?",
            (p["character_id"], p["art_id"]),
        )
        if count["n"] >= MAX_EQUIPPED_ACTIVES:
            raise SkillError(f"至多同时装备 {MAX_EQUIPPED_ACTIVES} 门主动技")
    await db.execute(
        "UPDATE character_skill SET equipped = ?"
        " WHERE character_id = ? AND skill_id = ?",
        (1 if equip else 0, p["character_id"], skill_id),
    )


async def join_sect(db: Database, p: dict, sect_id: str) -> None:
    sect = SECTS.get(sect_id)
    if sect is None:
        raise SkillError("查无此派")
    if sect.get("kind") != "sect":
        raise SkillError("武馆不设门派位阶，请直接学艺。")
    if p.get("sect_id"):
        raise SkillError("一生只入一派，你已有师承")
    if p["level"] < JOIN_LEVEL_REQ:
        raise SkillError(f"入门需等级 ≥{JOIN_LEVEL_REQ}")
    aff = await _master_affinity(db, sect_id)
    if aff < APPRENTICE_AFFINITY:
        raise SkillError(f"掌门日渐看重方肯收你入门（好感 ≥{APPRENTICE_AFFINITY}）")
    art_row = arts.art(sect["art"])
    try:
        await arts.switch_art(db, p, art_row, confirm_clamp=True, source="sect")
    except arts.ArtError as e:
        if e.requires_reset:
            raise SkillError("入派需先散功重修本派功法，请在武学面板确认。")
        raise SkillError(str(e))
    p["sect_id"] = sect_id
    for stat, add in sect.get("bonus", {}).items():
        p[STAT_COLS[stat]] += add
