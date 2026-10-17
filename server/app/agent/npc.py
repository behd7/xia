"""NPC roster, seeding and interaction rules (对话式拜师/切磋/送礼).

Personal data mirrors docs/DESIGN.md §6.2. Server stores each NPC as a row
in `npc` + `npc_state`; ids are the numeric primary keys. The client learns
the id/slug/affinity mapping from /api/game/state `npcs` and always addresses
an NPC by its numeric id.
"""

import json

from ..db import Database

# slug -> roster entry. `work` is the daytime POI, `home` the night POI.
NPC_ROSTER: dict[str, dict] = {
    "gu_changfeng": {
        "name": "顾长风", "slug": "gu_changfeng", "sect": "jianyang", "role": "master",
        "speech_style": "冷硬简短",
        "persona": "剑阳派掌门，性傲而严苛，重悟性，最厌庸钝之人。一言不合便以剑论道，话少而锋利。",
        "catchphrase": "且试锋芒。",
        "init_affinity": 25, "work": "剑阳道馆", "home": "道馆后屋",
        "combat": {"level": 6, "hp": 320, "atk": 30, "def": 8, "spd": 12, "potions": 0},
    },
    "tie_wushuang": {
        "name": "铁无双", "slug": "tie_wushuang", "sect": "xuantie", "role": "master",
        "speech_style": "大嗓门，爱称'小子'",
        "persona": "玄铁堡堡主，豪爽嗜酒，重品性，貌似粗鲁实则义气过顶。高兴了便拍人后颈。",
        "catchphrase": "好小子！",
        "init_affinity": 25, "work": "玄铁堂", "home": "客栈",
        "combat": {"level": 6, "hp": 340, "atk": 32, "def": 10, "spd": 9, "potions": 0},
    },
    "su_wanqing": {
        "name": "苏婉清", "slug": "su_wanqing", "sect": "yanyu", "role": "master",
        "speech_style": "半文半白，带调侃",
        "persona": "烟雨楼楼主，高深戏谑，重机敏，看似玩世不恭实则洞察入微。说话总留三分。",
        "catchphrase": "有趣。",
        "init_affinity": 30, "work": "烟雨小筑", "home": "小筑内室",
        "combat": {"level": 6, "hp": 290, "atk": 28, "def": 7, "spd": 16, "potions": 0},
    },
    "chen_bansong": {
        "name": "陈半松", "slug": "chen_bansong", "sect": None, "role": "clinic",
        "speech_style": "缓慢絮叨",
        "persona": "药铺大夫，慈祥啰嗦，爱忆当年。见人先问寒暖，一言三叹，望闻问切样样慢。",
        "catchphrase": "想当年啊……",
        "init_affinity": 30, "work": "药铺", "home": "铺后家",
        "combat": {"level": 3, "hp": 150, "atk": 14, "def": 5, "spd": 6, "potions": 0},
    },
    "wang_dazhu": {
        "name": "王大柱", "slug": "wang_dazhu", "sect": None, "role": "smith",
        "speech_style": "热络市侩",
        "persona": "铁匠铺主，爱财爱打听，满镇消息贩子。见白花花的银子和奇闻异事最是来劲。",
        "catchphrase": "消息灵通着呢。",
        "init_affinity": 25, "work": "铁匠铺", "home": "铺后家",
        "combat": {"level": 3, "hp": 170, "atk": 18, "def": 6, "spd": 7, "potions": 0},
    },
    "liu_xiaodie": {
        "name": "柳小蝶", "slug": "liu_xiaodie", "sect": None, "role": "inn",
        "speech_style": "清脆快语",
        "persona": "客栈掌柜之女，活泼好奇，最爱看热闹。说话清脆连珠，镇上有甚风吹草动必先问她。",
        "catchphrase": "快说说！",
        "init_affinity": 40, "work": "客栈", "home": "客栈",
        "combat": {"level": 2, "hp": 110, "atk": 12, "def": 4, "spd": 12, "potions": 0},
    },
    "zhang_mantou": {
        "name": "张馒头", "slug": "zhang_mantou", "sect": None, "role": "temple",
        "speech_style": "疯话押韵",
        "persona": "破庙疯丐，癫三倒四，偶尔语出惊人。爱念押韵的疯话，世人皆笑他痴。",
        "catchphrase": "馒头歌，歌馒头。",
        "init_affinity": 50, "work": "镇口水井", "home": "破庙",
        "combat": {"level": 4, "hp": 200, "atk": 21, "def": 7, "spd": 9, "potions": 0},
    },
}

# A player's rank within a joined sect is gated by affinity with its master.
MASTER_BY_SECT = {"jianyang": "gu_changfeng", "xuantie": "tie_wushuang", "yanyu": "su_wanqing"}

_npc_by_slug: dict[str, dict] = {}
_npc_meta_by_id: dict[int, dict] = {}


def _load_rows(rows: list[dict]) -> None:
    _npc_by_slug.clear()
    _npc_meta_by_id.clear()
    for r in rows:
        entry: dict | None = None
        for n in NPC_ROSTER.values():
            if n["name"] == r["name"]:
                entry = n
                break
        meta = {**r, **(entry or {}), "id": r["id"], "name": r["name"]}
        if entry:
            _npc_by_slug[entry["slug"]] = meta
        _npc_meta_by_id[r["id"]] = meta


async def seed_and_load(db: Database) -> None:
    for n in NPC_ROSTER.values():
        row = await db.fetch_one("SELECT id FROM npc WHERE name = ?", (n["name"],))
        if row is None:
            nid = await db.execute(
                "INSERT INTO npc(name, sect_id, role, persona_json, speech_style, init_affinity,"
                " home_poi, work_poi, waypoints_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (n["name"], n["sect"], n["role"], json.dumps(n["persona"], ensure_ascii=False),
                 n["speech_style"], n["init_affinity"], n["home"], n["work"], "[]"),
            )
        else:
            nid = row["id"]
        await db.execute(
            "INSERT OR IGNORE INTO npc_state(npc_id, affinity, last_reflection_at, mem_since_reflection,"
            " paused_until, last_greet_real) VALUES(?,?,NULL,0,NULL,NULL)",
            (nid, n["init_affinity"]),
        )
    rows = await db.fetch_all("SELECT * FROM npc")
    _load_rows(rows)


async def by_id(db: Database, npc_id: int) -> dict | None:
    """Static NPC metadata + roster enrichments, from the seeded in-memory registry."""
    meta = _npc_meta_by_id.get(npc_id)
    if meta is None:
        return None
    return dict(meta)


async def affinity(db: Database, npc_id: int) -> int:
    row = await db.fetch_one("SELECT affinity FROM npc_state WHERE npc_id = ?", (npc_id,))
    return row["affinity"] if row else 0


async def set_affinity(db: Database, npc_id: int, value: int) -> int:
    value = max(0, min(100, int(value)))
    await db.execute(
        "UPDATE npc_state SET affinity = ? WHERE npc_id = ?", (value, npc_id)
    )
    return value


async def list_for_client(db: Database) -> list[dict]:
    rows = await db.fetch_all(
        "SELECT n.id, n.name, n.role, n.home_poi AS work, ns.affinity"
        " FROM npc n JOIN npc_state ns ON ns.npc_id = n.id ORDER BY n.id"
    )
    out = []
    for r in rows:
        entry = next((n for n in NPC_ROSTER.values() if n["name"] == r["name"]), {})
        out.append({
            "id": r["id"], "slug": entry.get("slug", r["name"]), "name": r["name"],
            "role": r["role"], "affinity": r["affinity"], "poi": r["work"],
        })
    return out


def gift_delta(price: int, same_day_count: int) -> int:
    """DESIGN §6.5: 按价值 5/10/15 两档 +5/+8/+12；同日第2次起减半。"""
    if price < 50:
        base = 5
    elif price < 150:
        base = 8
    else:
        base = 12
    if same_day_count >= 2:
        base //= 2
    return base


async def gifts_today(db: Database, npc_id: int, clock) -> int:
    """Gift events the player caused for this NPC on the current game day.

    Gift memories use a '送礼·' content prefix so they're counted reliably
    without polluting the locations field (DESIGN §6.5 同日衰减).
    """
    day_start = (float(clock.now()) // 86400) * 86400
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM memory WHERE npc_id = ? AND kind = 'event'"
        " AND content LIKE '送礼·%' AND ts_game >= ?",
        (npc_id, day_start),
    )
    return rows[0]["n"] if rows else 0