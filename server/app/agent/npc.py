"""NPC roster, seeding and interaction rules (对话式拜师/切磋/送礼).

Personal data mirrors docs/DESIGN.md §6.2. Server stores each NPC as a row
in `npc` + `npc_state`; ids are the numeric primary keys. The client learns
the id/slug/affinity mapping from /api/game/state `npcs` and always addresses
an NPC by its numeric id.
"""

import logging

from ..db import Database
from ..game import inventory as inventory_mod
from ..game import plan as plan_mod
from ..game import player as player_mod
from ..game import schedule as schedule_mod
from ..game import skills as skills_mod
from . import events as events_mod
from . import mood as mood_mod
from . import reaction as reaction_mod
from . import trace as trace_mod

logger = logging.getLogger(__name__)

# slug -> roster entry. `work` is the daytime POI, `home` the night POI.
NPC_ROSTER: dict[str, dict] = {
    "gu_changfeng": {
        "name": "顾长风", "slug": "gu_changfeng", "sect": "jianyang", "role": "master",
        "speech_style": "冷硬简短",
        "speech_examples": ["话不必多。", "你握剑的手，先稳三分。", "庸才。"],
        "persona": "剑阳派掌门，性傲而严苛，重悟性，最厌庸钝之人。一言不合便以剑论道，话少而锋利。",
        "init_affinity": 25, "work": "剑阳道馆", "home": "道馆后屋",
        "combat": {"level": 6, "hp": 320, "atk": 30, "def": 8, "spd": 12, "potions": 0},
    },
    "tie_wushuang": {
        "name": "铁无双", "slug": "tie_wushuang", "sect": "xuantie", "role": "master",
        "speech_style": "大嗓门，爱称'小子'",
        "speech_examples": ["小子，先喝一碗！", "这事俺老铁给你兜着。", "哈哈，痛快！"],
        "persona": "玄铁堡堡主，豪爽嗜酒，重品性，貌似粗鲁实则义气过顶。高兴了便拍人后颈。",
        "init_affinity": 25, "work": "玄铁堂", "home": "客栈",
        "combat": {"level": 6, "hp": 340, "atk": 32, "def": 10, "spd": 9, "potions": 0},
    },
    "su_wanqing": {
        "name": "苏婉清", "slug": "su_wanqing", "sect": "yanyu", "role": "master",
        "speech_style": "半文半白，带调侃",
        "speech_examples": ["哦？这话是谁教你的。", "你猜。", "急什么，茶还没凉。"],
        "persona": "烟雨楼楼主，高深戏谑，重机敏，看似玩世不恭实则洞察入微。说话总留三分。",
        "init_affinity": 30, "work": "烟雨小筑", "home": "小筑内室",
        "combat": {"level": 6, "hp": 290, "atk": 28, "def": 7, "spd": 16, "potions": 0},
    },
    "chen_bansong": {
        "name": "陈半松", "slug": "chen_bansong", "sect": None, "role": "clinic",
        "speech_style": "缓慢絮叨",
        "speech_examples": ["唉，年轻人哪……", "先把脉，再说别的。", "当年我也见过这样的伤。"],
        "persona": "药铺大夫，慈祥啰嗦，爱忆当年。见人先问寒暖，一言三叹，望闻问切样样慢。",
        "init_affinity": 30, "work": "药铺", "home": "铺后家",
        "combat": {"level": 3, "hp": 150, "atk": 14, "def": 5, "spd": 6, "potions": 0},
    },
    "wang_dazhu": {
        "name": "王大柱", "slug": "wang_dazhu", "sect": None, "role": "smith",
        "speech_style": "热络市侩",
        "speech_examples": ["客官，这消息可不白给。", "您再加点，我再说一句。", "嗨，都是听来的。"],
        "persona": "铁匠铺主，爱财爱打听，满镇消息贩子。见白花花的银子和奇闻异事最是来劲。",
        "init_affinity": 25, "work": "铁匠铺", "home": "铺后家",
        "combat": {"level": 3, "hp": 170, "atk": 18, "def": 6, "spd": 7, "potions": 0},
    },
    "liu_xiaodie": {
        "name": "柳小蝶", "slug": "liu_xiaodie", "sect": None, "role": "inn",
        "speech_style": "清脆快语",
        "speech_examples": ["真的假的？！", "我跟你讲，我亲眼看见的！", "哎哟，那可热闹了。"],
        "persona": "客栈掌柜之女，活泼好奇，最爱看热闹。说话清脆连珠，镇上有甚风吹草动必先问她。",
        "init_affinity": 40, "work": "客栈", "home": "客栈",
        "combat": {"level": 2, "hp": 110, "atk": 12, "def": 4, "spd": 12, "potions": 0},
    },
    "zhang_mantou": {
        "name": "张馒头", "slug": "zhang_mantou", "sect": None, "role": "temple",
        "speech_style": "疯话押韵",
        "speech_examples": ["山外山，楼外楼，一把刀儿换馒头。", "疯话不疯，听的人聋。", "刀归你，路归我。"],
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


def ids() -> list[int]:
    return sorted(_npc_meta_by_id)


def names_in(text: str, player_name: str | None = None) -> list[str]:
    names = ([player_name] if player_name else []) + [n["name"] for n in NPC_ROSTER.values()]
    return [x for x in dict.fromkeys(names) if x and x in text]


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


async def list_for_client(db: Database, clock,
                          deviations: dict[int, dict] | None = None) -> list[dict]:
    rows = await db.fetch_all(
        "SELECT n.id, n.name, n.role, n.work_poi, n.home_poi, ns.affinity, ns.paused_until"
        " FROM npc n JOIN npc_state ns ON ns.npc_id = n.id ORDER BY n.id"
    )
    now = clock.now()
    deviations = deviations or {}
    out = []
    for r in rows:
        meta = _npc_meta_by_id.get(r["id"])
        label_src = meta or {"home": r["home_poi"], "work": r["work_poi"]}
        out.append({
            "id": r["id"], "slug": (meta or {}).get("slug", r["name"]), "name": r["name"],
            "role": r["role"], "affinity": r["affinity"],
            "poi": schedule_mod.poi_label_for(label_src, now, deviations.get(r["id"])),
            "work_poi": r["work_poi"], "home_poi": r["home_poi"],
            "paused_until": r["paused_until"],
        })
    return out


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
        now_game = mem_store.clock.now()
        dev = await plan_mod.deviation_for(db, npc_id, now_game)
        poi = schedule_mod.poi_label_for(n, now_game, dev)
        await mem_store.add_memory(
            db, npc_id, content=f"你将{p['name']}收归{sect_name}门下，授以入门剑诀。",
            kind="event", participants=[p["name"]], poi=poi, importance=6,
        )
        events_mod.record(
            mem_store, "apprentice", f"拜入{sect_name}", poi, [p["name"], n["name"]])
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
    """送礼（DESIGN §6.5）：LLM 生成感受与好感变化，作为特殊对话轮进入窗口。"""
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
    now_game = mem_store.clock.now() if mem_store is not None else 0.0
    gift_day = int(now_game // 86400)
    count = await gifts_today(db, npc_id, gift_day) if mem_store is not None else 0
    event_text = (
        f"{p['name']}送给你一份{inv['name']}"
        f"（价值{inv['price']}两，今日第{count + 1}次送礼）。"
    )
    conversation_user = (
        f"我将一份{inv['name']}送给你"
        f"（价值{inv['price']}两，今日第{count + 1}次）。"
    )
    poi = str(n.get("work") or "外头")
    if mem_store is not None:
        dev = await plan_mod.deviation_for(db, npc_id, now_game)
        poi = schedule_mod.poi_label_for(n, now_game, dev)
    tr = await trace_mod.trace_turn(
        "gift", input_={"npc": n["name"], "player": p["name"], "item": inv["name"]},
        tags=[f"npc:{n['name']}", f"npc_id:{npc_id}", f"player:{p['name']}"],
        metadata={"affinity_before": aff, "place": poi},
    )
    reaction = await reaction_mod.generate(
        db, mem_store, n, p, event_text=event_text, purpose="gift",
        fallback_reply=f"{n['name']}收下{inv['name']}，神色缓和。",
    )
    await inventory_mod.consume(db, p["id"], item_id)
    delta = int(reaction["affinity_delta"])
    new_aff = await apply_delta(db, npc_id, delta)
    reply = reaction["reply"]
    if mem_store is not None:
        await _mark_gift(db, npc_id, gift_day)
        await mem_store.add_conversation_turn(
            db, npc_id, user=conversation_user, npc=reply,
            player_name=p["name"], place=poi, affinity_delta=delta,
        )
        events_mod.record(
            mem_store, "gift", f"向{n['name']}送了{inv['name']}", poi,
            [p["name"], n["name"]])
    try:
        if delta > 0:
            await mood_mod.set(db, npc_id, "pleased", f"收到{inv['name']}", 30)
        elif delta < 0:
            await mood_mod.set(db, npc_id, "impatient", f"收到{inv['name']}", 20)
    except Exception:
        logger.exception("mood update failed for gift npc=%d", npc_id)
    await player_mod.save(db, p)
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


def _gift_count_key(npc_id: int, day: int) -> str:
    return f"gift_count:{npc_id}:{day}"


async def gifts_today(db: Database, npc_id: int, day: int) -> int:
    """今日已送礼次数（按 NPC、游戏日独立，存 kv）。"""
    raw = await db.kv_get(_gift_count_key(npc_id, day))
    return int(raw) if raw else 0


async def _mark_gift(db: Database, npc_id: int, day: int) -> None:
    await db.execute(
        "INSERT INTO kv(k, v) VALUES(?, '1')"
        " ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + 1",
        (_gift_count_key(npc_id, day),),
    )