import json

from ..db import Database
from . import data, inventory, player as player_mod


class ShopError(Exception):
    pass


def info(shop_id: str) -> dict | None:
    s = data.SHOPS.get(shop_id)
    if s is None:
        return None
    items = []
    for key in s["items"]:
        row = data.item(key)
        items.append({
            "item_id": row["id"], "name": row["name"], "type": row["type"],
            "tier": row["tier"], "price": row["price"],
            "desc": data.effect_desc(json.loads(row["effect_json"])),
        })
    return {"id": shop_id, "name": s["name"], "items": items}


async def buy(db: Database, p: dict, shop_id: str, item_id: int, qty: int, pos: dict | None) -> dict:
    s = data.SHOPS.get(shop_id)
    if s is None:
        raise ShopError("unknown shop")
    row = next((data.item(k) for k in s["items"] if data.item(k)["id"] == item_id), None)
    if row is None:
        raise ShopError("此店不售此物")
    cost = row["price"] * qty
    if p["gold"] < cost:
        raise ShopError("银两不足")
    p["gold"] -= cost
    await inventory.add(db, p["id"], item_id, qty)
    if pos:
        p["pos_x"], p["pos_y"] = pos["x"], pos["y"]
    await player_mod.save(db, p)
    return await player_mod.api_dict(db, p)
