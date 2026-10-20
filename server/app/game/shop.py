import json

from ..db import Database
from . import data, inventory, player as player_mod


class ShopError(Exception):
    pass


# Owner NPC per shop (only smith discounts per DESIGN §6.5 王大柱).
SHOP_OWNER_ROLE = {"clinic": "clinic", "smith": "smith"}
# 跨层受控注入：main lifespan 装配时赋值。避免 game -> agent 的模块级 import 环，
# 仅此两处；None 表示 LLM/反思层未装配（正常开发恒装配，测试可不配）。
mem_store = None    # injected: 大额消费写店主记忆
summary_bump = None  # injected: summary.bump（互动判断值+1, 达阈值蒸馏窗口）


async def _owner(db: Database, shop_id: str) -> dict | None:
    role = SHOP_OWNER_ROLE.get(shop_id)
    if not role:
        return None
    return await db.fetch_one("SELECT id AS npc_id, name FROM npc WHERE role = ?", (role,))


async def _discount(db: Database, shop_id: str) -> int:
    """价格折扣百分比。DESIGN §6.5：王大柱 好感热络 95 折、亲近 9 折。"""
    if shop_id != "smith":
        return 100
    owner = await _owner(db, shop_id)
    if owner is None:
        return 100
    row = await db.fetch_one("SELECT affinity FROM npc_state WHERE npc_id = ?", (owner["npc_id"],))
    aff = row["affinity"] if row else 0
    if aff >= 70:
        return 90
    if aff >= 60:
        return 95
    return 100


async def info(db: Database, shop_id: str) -> dict | None:
    s = data.SHOPS.get(shop_id)
    if s is None:
        return None
    disc = await _discount(db, shop_id)
    items = []
    for key in s["items"]:
        row = data.item(key)
        items.append({
            "item_id": row["id"], "name": row["name"], "type": row["type"],
            "tier": row["tier"], "price": row["price"] * disc // 100,
            "desc": data.effect_desc(json.loads(row["effect_json"])),
        })
    return {"id": shop_id, "name": s["name"], "items": items, "discount_pct": disc}


async def buy(db: Database, p: dict, shop_id: str, item_id: int, qty: int, pos: dict | None) -> dict:
    s = data.SHOPS.get(shop_id)
    if s is None:
        raise ShopError("unknown shop")
    row = next((data.item(k) for k in s["items"] if data.item(k)["id"] == item_id), None)
    if row is None:
        raise ShopError("此店不售此物")
    disc = await _discount(db, shop_id)
    unit = row["price"] * disc // 100
    cost = unit * qty
    if p["gold"] < cost:
        raise ShopError("银两不足")
    p["gold"] -= cost
    await inventory.add(db, p["id"], item_id, qty)
    if pos:
        p["pos_x"], p["pos_y"] = pos["x"], pos["y"]
    # 记忆控制噪声：大额消费（≥100 两）才写店主记忆（AGENTS.md #6.9 记忆控制噪声）;
    # 与 NPC 店主的任何购买都累计互动判断值（不逐轮入忆，见 §6.6）。
    if mem_store is not None:
        owner = await _owner(db, shop_id)
        if owner:
            if cost >= 100:
                await mem_store.add_memory(
                    db, owner["npc_id"],
                    content=f"你在本店花{cost}两买下{row['name']}，出手阔绰。",
                    kind="event", participants=[p["name"]], poi=s["name"], importance=3,
                )
            if summary_bump is not None:
                await summary_bump(db, mem_store, owner["npc_id"], p["name"], s["name"])
    await player_mod.save(db, p)
    return await player_mod.api_dict(db, p)