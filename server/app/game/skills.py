import json

from ..db import Database

# Skill catalog per docs/M2-PLAN.md §2.1 (originally DESIGN.md §5.2/§5.3).
SKILLS = [
    {"key": "lieyang_jue", "name": "烈阳剑诀", "sect": "jianyang", "kind": "passive", "tier": 1,
     "mp_cost": 0, "multiplier": 0, "effect": {"stat": "str", "per_level": 1}},
    {"key": "chiyang_zhan", "name": "赤阳斩", "sect": "jianyang", "kind": "active", "tier": 1,
     "mp_cost": 8, "multiplier": 1.6, "effect": {}},
    {"key": "fenglei_shi", "name": "风雷剑势", "sect": "jianyang", "kind": "active", "tier": 2,
     "mp_cost": 14, "multiplier": 1.9, "effect": {"crit_add": 0.20}},
    {"key": "xuri_sheng", "name": "旭日东升", "sect": "jianyang", "kind": "active", "tier": 3,
     "mp_cost": 26, "multiplier": 2.6, "effect": {"cooldown": 2}},
    {"key": "xuantie_dao", "name": "玄铁刀法", "sect": "xuantie", "kind": "passive", "tier": 1,
     "mp_cost": 0, "multiplier": 0, "effect": {"stat": "con", "per_level": 1}},
    {"key": "nulang_dao", "name": "怒浪刀", "sect": "xuantie", "kind": "active", "tier": 1,
     "mp_cost": 8, "multiplier": 1.5, "effect": {"buff_self": {"def_pct": 1.10, "rounds": 1}}},
    {"key": "tiebu_shan", "name": "铁布衫", "sect": "xuantie", "kind": "active", "tier": 2,
     "mp_cost": 12, "multiplier": 0.8, "effect": {"buff_self": {"def_pct": 1.40, "rounds": 2}}},
    {"key": "kaishan_dao", "name": "开山刀", "sect": "xuantie", "kind": "active", "tier": 3,
     "mp_cost": 26, "multiplier": 2.4, "effect": {"pierce_def": 0.5}},
    {"key": "yanyu_xin", "name": "烟雨心法", "sect": "yanyu", "kind": "passive", "tier": 1,
     "mp_cost": 0, "multiplier": 0, "effect": {"stat": "agi", "per_level": 1}},
    {"key": "mizong_bu", "name": "迷踪步", "sect": "yanyu", "kind": "active", "tier": 1,
     "mp_cost": 6, "multiplier": 1.2, "effect": {"buff_self": {"dodge_add": 0.15, "rounds": 2}}},
    {"key": "xiyu_ci", "name": "细雨十三刺", "sect": "yanyu", "kind": "active", "tier": 2,
     "mp_cost": 15, "multiplier": 1.8, "effect": {"ignore_dodge": True}},
    {"key": "feihong_guan", "name": "飞虹贯云", "sect": "yanyu", "kind": "active", "tier": 3,
     "mp_cost": 22, "multiplier": 2.2, "effect": {"double_if_faster": 1.5}},
]

SECTS = {
    "jianyang": {"name": "剑阳派", "poi": "剑阳道馆", "bonus": {"str": 3, "int": 2}, "passive": "lieyang_jue"},
    "xuantie": {"name": "玄铁堡", "poi": "玄铁堂", "bonus": {"con": 3, "int": 2}, "passive": "xuantie_dao"},
    "yanyu": {"name": "烟雨楼", "poi": "烟雨小筑", "bonus": {"agi": 3, "int": 2}, "passive": "yanyu_xin"},
}

STAT_COLS = {"str": "str", "con": "con", "agi": "agi", "int": "int_"}
STAT_NAMES = {"str": "臂力", "con": "根骨", "agi": "身法", "int": "悟性"}

RANKS = {1: "入门弟子", 2: "亲传弟子", 3: "关门弟子"}
# 位阶门槛（DESIGN §5.2；与 agent/affinity.RANK_THRESHOLDS 同源，各自内联免循环 import）
RANK_THRESHOLDS = [(60, 1), (75, 2), (85, 3)]
JOIN_LEVEL_REQ = 3
APPRENTICE_AFFINITY = 60  # 掌门收徒好感门槛（DESIGN §5.2）
MAX_SKILL_LEVEL = 5
UPGRADE_BASE = 20  # cost to go L -> L+1 is 20 * L (20/40/60/80)
MAX_EQUIPPED_ACTIVES = 3

_skills_by_key: dict[str, dict] = {}


class SkillError(Exception):
    pass


async def seed_and_load(db: Database) -> None:
    for s in SKILLS:
        await db.execute(
            "INSERT OR IGNORE INTO skill(name, sect_id, kind, tier_req, mp_cost, multiplier, cooldown, effect_json)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (s["name"], s["sect"], s["kind"], s["tier"], s["mp_cost"], s["multiplier"],
             s["effect"].get("cooldown", 0), json.dumps(s["effect"], ensure_ascii=False)),
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


def upgrade_cost(level: int) -> int:
    return UPGRADE_BASE * level


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
    """拜入门派掌门对该玩家的好感。Querying the npc table directly keeps
    this game module free of agent imports."""
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
    """位阶按拜师掌门好感度推出（M3 取代 M2 的 MAX_RANK 常量）。"""
    return _rank_of(await master_affinity(db, p))


async def tier_unlocked(db: Database, p: dict, tier: int) -> bool:
    return tier <= await current_rank(db, p)


async def upgrade(db: Database, p: dict, skill_id: int) -> None:
    srow = await by_id(db, skill_id)
    if srow is None:
        raise SkillError("查无此技")
    ps = await db.fetch_one(
        "SELECT level FROM player_skill WHERE player_id = ? AND skill_id = ?",
        (p["id"], skill_id),
    )
    if ps is None:
        raise SkillError("尚未习得此技")
    if ps["level"] >= MAX_SKILL_LEVEL:
        raise SkillError("已至最高境界")
    cost = upgrade_cost(ps["level"])
    if p["cultivation"] < cost:
        raise SkillError(f"修炼点不足（需 {cost}）")
    p["cultivation"] -= cost
    await db.execute(
        "UPDATE player_skill SET level = ? WHERE player_id = ? AND skill_id = ?",
        (ps["level"] + 1, p["id"], skill_id),
    )


async def set_equip(db: Database, p: dict, skill_id: int, equip: bool) -> None:
    srow = await by_id(db, skill_id)
    if srow is None:
        raise SkillError("查无此技")
    if srow["kind"] != "active":
        raise SkillError("内功为被动心法，无需装备")
    ps = await db.fetch_one(
        "SELECT level, equipped FROM player_skill WHERE player_id = ? AND skill_id = ?",
        (p["id"], skill_id),
    )
    if ps is None:
        raise SkillError("尚未习得此技")
    if equip and not ps["equipped"]:
        if not await tier_unlocked(db, p, srow["tier_req"]):
            raise SkillError("位阶未至，此技未解锁")
        count = await db.fetch_one(
            "SELECT COUNT(*) AS n FROM player_skill ps JOIN skill sk ON sk.id = ps.skill_id"
            " WHERE ps.player_id = ? AND ps.equipped = 1 AND sk.kind = 'active'",
            (p["id"],),
        )
        if count["n"] >= MAX_EQUIPPED_ACTIVES:
            raise SkillError(f"至多同时装备 {MAX_EQUIPPED_ACTIVES} 门主动技")
    await db.execute(
        "UPDATE player_skill SET equipped = ? WHERE player_id = ? AND skill_id = ?",
        (1 if equip else 0, p["id"], skill_id),
    )


async def join_sect(db: Database, p: dict, sect_id: str) -> None:
    sect = SECTS.get(sect_id)
    if sect is None:
        raise SkillError("查无此派")
    if p["sect_id"]:
        raise SkillError("一生只入一派，你已有师承")
    if p["level"] < JOIN_LEVEL_REQ:
        raise SkillError(f"入门需等级 ≥{JOIN_LEVEL_REQ}")
    aff = await _master_affinity(db, sect_id)
    if aff < APPRENTICE_AFFINITY:
        raise SkillError(f"掌门日渐看重方肯收你入门（好感 ≥{APPRENTICE_AFFINITY}）")
    p["sect_id"] = sect_id
    for stat, add in sect["bonus"].items():
        p[STAT_COLS[stat]] += add
    passive = skill(sect["passive"])
    await db.execute(
        "INSERT OR IGNORE INTO player_skill(player_id, skill_id, level, equipped) VALUES(?,?,1,0)",
        (p["id"], passive["id"]),
    )
    tier1 = next(s for s in SKILLS if s["sect"] == sect_id and s["kind"] == "active" and s["tier"] == 1)
    t1row = skill(tier1["key"])
    await db.execute(
        "INSERT OR IGNORE INTO player_skill(player_id, skill_id, level, equipped) VALUES(?,?,1,1)",
        (p["id"], t1row["id"]),
    )
