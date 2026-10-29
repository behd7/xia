import json

from ..db import Database


class InvError(Exception):
    pass


SLOTS = ("weapon", "armor", "accessory")
SLOT_NAMES = {"weapon": "武器", "armor": "衣服", "accessory": "饰品"}


async def add(db: Database, character_id: int, item_id: int, qty: int = 1) -> None:
    row = await db.fetch_one(
        "SELECT qty FROM character_inventory WHERE character_id = ? AND item_id = ?",
        (character_id, item_id),
    )
    if row:
        await db.execute(
            "UPDATE character_inventory SET qty = ? WHERE character_id = ? AND item_id = ?",
            (row["qty"] + qty, character_id, item_id),
        )
    else:
        await db.execute(
            "INSERT INTO character_inventory(character_id, item_id, qty) VALUES(?,?,?)",
            (character_id, item_id, qty),
        )


async def consume(db: Database, character_id: int, item_id: int, qty: int = 1) -> bool:
    row = await db.fetch_one(
        "SELECT qty FROM character_inventory WHERE character_id = ? AND item_id = ?",
        (character_id, item_id),
    )
    if row is None or row["qty"] < qty:
        return False
    if row["qty"] == qty:
        await db.execute(
            "DELETE FROM character_inventory WHERE character_id = ? AND item_id = ?",
            (character_id, item_id),
        )
    else:
        await db.execute(
            "UPDATE character_inventory SET qty = ? WHERE character_id = ? AND item_id = ?",
            (row["qty"] - qty, character_id, item_id),
        )
    return True


async def equip(db: Database, character_id: int, item_id: int) -> None:
    row = await db.fetch_one(
        "SELECT it.type FROM character_inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.character_id = ? AND inv.item_id = ?",
        (character_id, item_id),
    )
    if row is None:
        raise InvError("背包里没有此物")
    if row["type"] not in SLOTS:
        raise InvError("此物不可装备")
    await db.execute(
        "UPDATE character_inventory SET equipped_slot = NULL"
        " WHERE character_id = ? AND equipped_slot = ?",
        (character_id, row["type"]),
    )
    await db.execute(
        "UPDATE character_inventory SET equipped_slot = ?"
        " WHERE character_id = ? AND item_id = ?",
        (row["type"], character_id, item_id),
    )


async def unequip(db: Database, character_id: int, slot: str) -> None:
    if slot not in SLOTS:
        raise InvError("未知槽位")
    await db.execute(
        "UPDATE character_inventory SET equipped_slot = NULL"
        " WHERE character_id = ? AND equipped_slot = ?",
        (character_id, slot),
    )


async def sell(db: Database, character_id: int, item_id: int, qty: int) -> int:
    if qty < 1:
        raise InvError("数量须为正")
    row = await db.fetch_one(
        "SELECT it.price, it.effect_json, inv.qty, inv.equipped_slot"
        " FROM character_inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.character_id = ? AND inv.item_id = ?",
        (character_id, item_id),
    )
    if row is None:
        raise InvError("背包里没有此物")
    if json.loads(row["effect_json"] or "{}").get("unsellable"):
        raise InvError("此物不可出售")
    if row["equipped_slot"] is not None:
        raise InvError("装备中的物品须先卸下")
    if row["qty"] < qty:
        raise InvError("数量不足")
    gain = (row["price"] // 2) * qty
    await consume(db, character_id, item_id, qty)
    return gain
