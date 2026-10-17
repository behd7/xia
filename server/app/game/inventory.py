from ..db import Database


class InvError(Exception):
    pass


SLOTS = ("weapon", "armor", "accessory")
SLOT_NAMES = {"weapon": "武器", "armor": "衣服", "accessory": "饰品"}


async def add(db: Database, player_id: int, item_id: int, qty: int = 1) -> None:
    row = await db.fetch_one(
        "SELECT id, qty FROM inventory WHERE player_id = ? AND item_id = ?",
        (player_id, item_id),
    )
    if row:
        await db.execute("UPDATE inventory SET qty = ? WHERE id = ?", (row["qty"] + qty, row["id"]))
    else:
        await db.execute(
            "INSERT INTO inventory(player_id, item_id, qty) VALUES(?,?,?)",
            (player_id, item_id, qty),
        )


async def consume(db: Database, player_id: int, item_id: int, qty: int = 1) -> bool:
    row = await db.fetch_one(
        "SELECT id, qty FROM inventory WHERE player_id = ? AND item_id = ?",
        (player_id, item_id),
    )
    if row is None or row["qty"] < qty:
        return False
    if row["qty"] == qty:
        await db.execute("DELETE FROM inventory WHERE id = ?", (row["id"],))
    else:
        await db.execute("UPDATE inventory SET qty = ? WHERE id = ?", (row["qty"] - qty, row["id"]))
    return True


async def equip(db: Database, player_id: int, item_id: int) -> None:
    row = await db.fetch_one(
        "SELECT inv.id, it.type FROM inventory inv JOIN item it ON it.id = inv.item_id"
        " WHERE inv.player_id = ? AND inv.item_id = ?",
        (player_id, item_id),
    )
    if row is None:
        raise InvError("背包里没有此物")
    if row["type"] not in SLOTS:
        raise InvError("此物不可装备")
    await db.execute(
        "UPDATE inventory SET equipped_slot = NULL WHERE player_id = ? AND equipped_slot = ?",
        (player_id, row["type"]),
    )
    await db.execute(
        "UPDATE inventory SET equipped_slot = ? WHERE player_id = ? AND item_id = ?",
        (row["type"], player_id, item_id),
    )


async def unequip(db: Database, player_id: int, slot: str) -> None:
    if slot not in SLOTS:
        raise InvError("未知槽位")
    await db.execute(
        "UPDATE inventory SET equipped_slot = NULL WHERE player_id = ? AND equipped_slot = ?",
        (player_id, slot),
    )


async def sell(db: Database, player_id: int, item_id: int, qty: int) -> int:
    if qty < 1:
        raise InvError("数量须为正")
    row = await db.fetch_one(
        "SELECT it.price, inv.qty, inv.equipped_slot FROM inventory inv"
        " JOIN item it ON it.id = inv.item_id WHERE inv.player_id = ? AND inv.item_id = ?",
        (player_id, item_id),
    )
    if row is None:
        raise InvError("背包里没有此物")
    if row["equipped_slot"] is not None:
        raise InvError("装备中的物品须先卸下")
    if row["qty"] < qty:
        raise InvError("数量不足")
    gain = (row["price"] // 2) * qty
    await consume(db, player_id, item_id, qty)
    return gain
