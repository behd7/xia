import json

TILE = 16

# All static game data mirrors docs/DESIGN.md §4/§5.4/§5.5.
ITEMS = [
    {"key": "xiao_huichun", "name": "小回春散", "type": "potion", "tier": "common", "price": 15, "effect": {"hp": 60}},
    {"key": "da_huichun", "name": "大回春散", "type": "potion", "tier": "common", "price": 60, "effect": {"hp": 200}},
    {"key": "ningshen_wan", "name": "凝神丸", "type": "potion", "tier": "common", "price": 20, "effect": {"mp": 40}},
    {"key": "lang_pi", "name": "狼皮", "type": "misc", "tier": "common", "price": 40, "effect": {}},
    {"key": "tie_jian", "name": "铁剑", "type": "weapon", "tier": "common", "price": 80, "effect": {"atk": 8}},
    {"key": "jinggang_jian", "name": "精钢剑", "type": "weapon", "tier": "fine", "price": 250, "effect": {"atk": 15}},
    {"key": "jingtie_bishou", "name": "精铁匕首", "type": "weapon", "tier": "fine", "price": 120, "effect": {"atk": 6}},
    {"key": "xuantie_jian", "name": "玄铁剑", "type": "weapon", "tier": "rare", "price": 500, "effect": {"atk": 24, "crit": 0.05}},
    {"key": "duanyue_dao", "name": "断岳刀", "type": "weapon", "tier": "rare", "price": 0,
     "effect": {"atk": 28, "crit": 0.08, "unsellable": True}},
    {"key": "bu_pao", "name": "布袍", "type": "armor", "tier": "common", "price": 50, "effect": {"def": 4}},
    {"key": "jingge_jia", "name": "精革甲", "type": "armor", "tier": "fine", "price": 200, "effect": {"def": 9}},
    {"key": "yu_pei", "name": "玉佩", "type": "accessory", "tier": "fine", "price": 150, "effect": {"mp_max": 40, "int": 1}},
]

MOB_TYPES = {
    "wolf": {
        "name": "野狼", "level": 1, "hp": 60, "atk": 12, "def": 3, "spd": 11,
        "exp": 15, "gold": 5, "potions": 0,
        "drops": [{"key": "lang_pi", "rate": 0.4}, {"key": "xiao_huichun", "rate": 0.3}],
    },
    "bandit": {
        "name": "山贼", "level": 3, "hp": 120, "atk": 20, "def": 6, "spd": 10,
        "exp": 35, "gold": 20, "potions": 1,
        "drops": [{"key": "xiao_huichun", "rate": 0.5}, {"key": "jingtie_bishou", "rate": 0.08}],
    },
    "boss": {
        "name": "黑旋风", "level": 5, "hp": 260, "atk": 30, "def": 9, "spd": 12,
        "exp": 120, "gold": 100, "potions": 2,
        "drops": [{"key": "xuantie_jian", "rate": 1.0}],
    },
}

# Spawn points sit on the wilderness trail rows so they are guaranteed
# walkable and reachable (tree/cliff scattering never touches road tiles).
MOB_POI = {"wolf": "东山林地", "bandit": "后山崖", "boss": "后山崖"}

MOB_SPAWNS = [
    {"id": "wolf_1", "type": "wolf", "x": 14, "y": 38},
    {"id": "wolf_2", "type": "wolf", "x": 26, "y": 39},
    {"id": "wolf_3", "type": "wolf", "x": 36, "y": 38},
    {"id": "bandit_1", "type": "bandit", "x": 60, "y": 39},
    {"id": "bandit_2", "type": "bandit", "x": 70, "y": 38},
    {"id": "bandit_3", "type": "bandit", "x": 80, "y": 39},
    {"id": "boss_1", "type": "boss", "x": 88, "y": 38},
]

SHOPS = {
    "clinic": {"name": "药铺 · 陈半松", "items": ["xiao_huichun", "da_huichun", "ningshen_wan"]},
    "smith": {"name": "铁匠铺 · 王大柱", "items": ["tie_jian", "jinggang_jian", "bu_pao", "jingge_jia", "yu_pei"]},
}

# Town gate spawn for a new character; inn front for death revival.
DEFAULT_SPAWN = (792.0, 440.0)
INN_SPAWN = (520.0, 376.0)

MOB_RESPAWN_REAL = 60
ENEMY_POTION_HEAL = 60
BATTLE_POTION_LIMIT = 3
WIN_CULTIVATION_RATE = 0.6

_items_by_key: dict[str, dict] = {}


async def seed_and_load(db) -> None:
    for it in ITEMS:
        await db.execute(
            "INSERT OR IGNORE INTO item(name, type, tier, price, effect_json) VALUES(?,?,?,?,?)",
            (it["name"], it["type"], it["tier"], it["price"], json.dumps(it["effect"], ensure_ascii=False)),
        )
    rows = await db.fetch_all("SELECT * FROM item")
    by_name = {r["name"]: r for r in rows}
    _items_by_key.clear()
    _items_by_key.update({it["key"]: by_name[it["name"]] for it in ITEMS})


def item(key: str) -> dict:
    if not _items_by_key:
        raise RuntimeError("item catalog not seeded")
    return _items_by_key[key]


def effect_desc(effect: dict) -> str:
    parts = []
    if "hp" in effect:
        parts.append(f"恢复 {effect['hp']} 气血")
    if "mp" in effect:
        parts.append(f"恢复 {effect['mp']} 内力")
    if "atk" in effect:
        parts.append(f"攻击 +{effect['atk']}")
    if "def" in effect:
        parts.append(f"防御 +{effect['def']}")
    if "crit" in effect:
        parts.append(f"暴击 +{int(effect['crit'] * 100)}%")
    if "mp_max" in effect:
        parts.append(f"内力上限 +{effect['mp_max']}")
    if "int" in effect:
        parts.append(f"悟性 +{effect['int']}")
    if effect.get("unsellable"):
        parts.append("神器，不可出售")
    return "，".join(parts) if parts else "杂货，可卖店换钱"
