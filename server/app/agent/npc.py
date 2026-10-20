"""NPC roster, seeding and interaction rules (对话式拜师/切磋/送礼).

Personal data mirrors docs/DESIGN.md §6.2. Server stores each NPC as a row
in `npc` + `npc_state`; ids are the numeric primary keys. The client learns
the id/slug/affinity mapping from /api/game/state `npcs` and always addresses
an NPC by its numeric id.
"""

from ..db import Database
from ..game import inventory as inventory_mod
from ..game import player as player_mod
from ..game import skills as skills_mod
from . import summary as summary_mod
from . import trace as trace_mod

# slug -> roster entry. `work` is the daytime POI, `home` the night POI.
NPC_ROSTER: dict[str, dict] = {
    "gu_changfeng": {
        "name": "顾长风", "slug": "gu_changfeng", "sect": "jianyang", "role": "master",
        "speech_style": "冷硬简短",
        "persona": "剑阳派掌门，性傲而严苛，重悟性，最厌庸钝之人。一言不合便以剑论道，话少而锋利。",
        "init_affinity": 25, "work": "剑阳道馆", "home": "道馆后屋",
        "combat": {"level": 6, "hp": 320, "atk": 30, "def": 8, "spd": 12, "potions": 0},
    },
    "tie_wushuang": {
        "name": "铁无双", "slug": "tie_wushuang", "sect": "xuantie", "role": "master",
        "speech_style": "大嗓门，爱称'小子'",
        "persona": "玄铁堡堡主，豪爽嗜酒，重品性，貌似粗鲁实则义气过顶。高兴了便拍人后颈。",
        "init_affinity": 25, "work": "玄铁堂", "home": "客栈",
        "combat": {"level": 6, "hp": 340, "atk": 32, "def": 10, "spd": 9, "potions": 0},
    },
    "su_wanqing": {
        "name": "苏婉清", "slug": "su_wanqing", "sect": "yanyu", "role": "master",
        "speech_style": "半文半白，带调侃",
        "persona": "烟雨楼楼主，高深戏谑，重机敏，看似玩世不恭实则洞察入微。说话总留三分。",
        "init_affinity": 30, "work": "烟雨小筑", "home": "小筑内室",
        "combat": {"level": 6, "hp": 290, "atk": 28, "def": 7, "spd": 16, "potions": 0},
    },
    "chen_bansong": {
        "name": "陈半松", "slug": "chen_bansong", "sect": None, "role": "clinic",
        "speech_style": "缓慢絮叨",
        "persona": "药铺大夫，慈祥啰嗦，爱忆当年。见人先问寒暖，一言三叹，望闻问切样样慢。",
        "init_affinity": 30, "work": "药铺", "home": "铺后家",
        "combat": {"level": 3, "hp": 150, "atk": 14, "def": 5, "spd": 6, "potions": 0},
    },
    "wang_dazhu": {
        "name": "王大柱", "slug": "wang_dazhu", "sect": None, "role": "smith",
        "speech_style": "热络市侩",
        "persona": "铁匠铺主，爱财爱打听，满镇消息贩子。见白花花的银子和奇闻异事最是来劲。",
        "init_affinity": 25, "work": "铁匠铺", "home": "铺后家",
        "combat": {"level": 3, "hp": 170, "atk": 18, "def": 6, "spd": 7, "potions": 0},
    },
    "liu_xiaodie": {
        "name": "柳小蝶", "slug": "liu_xiaodie", "sect": None, "role": "inn",
        "speech_style": "清脆快语",
        "persona": "客栈掌柜之女，活泼好奇，最爱看热闹。说话清脆连珠，镇上有甚风吹草动必先问她。",
        "init_affinity": 40, "work": "客栈", "home": "客栈",
        "combat": {"level": 2, "hp": 110, "atk": 12, "def": 4, "spd": 12, "potions": 0},
    },
    "zhang_mantou": {
        "name": "张馒头", "slug": "zhang_mantou", "sect": None, "role": "temple",
        "speech_style": "疯话押韵",
        "persona": "破庙疯丐，癫三倒四，偶尔语出惊人。爱念押韵的疯话，世人皆笑他痴。",
        "init_affinity": 50, "work": "镇口水井", "home": "破庙",
        "combat": {"level": 4, "hp": 200, "atk": 21, "def": 7, "spd": 9, "potions": 0},
    },
}

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
                "INSERT INTO npc(name, sect_id, role, init_affinity, home_poi, work_poi)"
                " VALUES(?,?,?,?,?,?)",
                (n["name"], n["sect"], n["role"], n["init_affinity"], n["home"], n["work"]),
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


CHALLENGE_AFFINITY = 40  # 掌门应切磋好感门槛（DESIGN §5.6）


async def apply_delta(db: Database, npc_id: int, delta: int) -> int:
    """好感按 ±delta 变化并 clamp 0~100 写回（原 agent/affinity.py，并入此处）。"""
    cur = await affinity(db, npc_id)
    return await set_affinity(db, npc_id, cur + int(delta))


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


CEREMONY = {
    "gu_changfeng": "今日起汝便是我剑阳门下。烈阳剑诀，且仔细看顾！",
    "tie_wushuang": "哈哈好小子！我玄铁堡认了你这份骨气，入门了！",
    "su_wanqing": "烟雨楼虽小，倒也容得下你这般机灵人。",
}


async def apprentice(db: Database, mem_store, npc_id: int) -> dict:
    """对话式拜师（DESIGN §5.2）：好感≥60 且 等级≥3，一生只入一派。"""
    n = await by_id(db, npc_id)
    if n is None:
        raise ValueError("查无此人")
    if n.get("role") != "master" or not n.get("sect"):
        raise ValueError("此人不收徒，另寻他路吧。")
    p = await player_mod.get(db)
    aff_before = await affinity(db, npc_id)
    if p["sect_id"]:
        raise ValueError("你已拜入门派，武德不容贰师。")
    try:
        await skills_mod.join_sect(db, p, n["sect"])
    except skills_mod.SkillError as e:
        raise ValueError(str(e))
    await player_mod.save(db, p)
    new_aff = await set_affinity(db, npc_id, aff_before + 3)
    sect_name = skills_mod.SECTS[n["sect"]]["name"]
    if mem_store is not None:
        await mem_store.add_memory(
            db, npc_id, content=f"你将{p['name']}收归{sect_name}门下，授以入门剑诀。",
            kind="event", participants=[p["name"]], poi=n["work"], importance=6,
        )
    reply = CEREMONY.get(n["slug"], f"{n['name']}缓缓点头，收你入门。")
    tr = await trace_mod.trace_turn(
        "apprentice", input_={"npc": n["name"], "player": p["name"]},
        tags=[f"npc:{n['name']}", f"npc_id:{npc_id}", f"player:{p['name']}"],
        metadata={"affinity_before": aff_before},
    )
    if tr is not None:
        await _close_trace(tr, reply, {"affinity_after": new_aff})
    return {
        "reply": reply,
        "joined": True, "sect": {"id": n["sect"], "name": sect_name},
        "affinity": new_aff, "player": await player_mod.api_dict(db, p),
    }


async def gift(db: Database, mem_store, npc_id: int, item_id: int) -> dict:
    """送礼（DESIGN §6.5）：按价值档 +5/+8/+12，同日第二次起减半；写记忆。"""
    n = await by_id(db, npc_id)
    if n is None:
        raise ValueError("查无此人")
    p = await player_mod.get(db)
    inv = await db.fetch_one(
        "SELECT it.name, it.price, inv.qty FROM inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.player_id = ? AND inv.item_id = ?",
        (p["id"], item_id),
    )
    if inv is None or inv["qty"] < 1:
        raise ValueError("行囊里没有此物，无从相赠。")
    aff = await affinity(db, npc_id)
    count = await gifts_today(db, npc_id, mem_store.clock)
    delta = gift_delta(inv["price"], count + 1)
    new_aff = await set_affinity(db, npc_id, aff + delta)
    await inventory_mod.consume(db, p["id"], item_id)
    if mem_store is not None:
        await mem_store.add_memory(
            db, npc_id, content=f"送礼·{p['name']}送来{inv['name']}。",
            kind="event", participants=[p["name"]], poi=n["work"], importance=3,
        )
        await summary_mod.bump(db, mem_store, npc_id, p["name"], n["work"])
    await player_mod.save(db, p)
    reply = f"{n['name']}收下{inv['name']}，神色缓和，欣喜以为所识非虚。"
    tr = await trace_mod.trace_turn(
        "gift", input_={"npc": n["name"], "player": p["name"], "item": inv["name"]},
        tags=[f"npc:{n['name']}", f"npc_id:{npc_id}", f"player:{p['name']}"],
        metadata={"affinity_before": aff},
    )
    if tr is not None:
        await _close_trace(tr, reply, {"affinity_after": new_aff})
    return {
        "reply": reply,
        "affinity": new_aff, "item": inv["name"],
        "player": await player_mod.api_dict(db, p),
    }


async def _close_trace(tr, output, metadata) -> None:
    """把轻量 trace 的 output 与收尾元数据补到根 trace 上（best-effort）。"""
    if tr is None:
        return
    try:
        tr.update(output=output, metadata=metadata)
    except Exception:
        pass


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