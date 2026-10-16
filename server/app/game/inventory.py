from ..db import Database


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
