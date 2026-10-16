import json
import math
import random
import time

from ..db import Database
from . import data, inventory, mobs, player as player_mod


class BattleError(Exception):
    pass


async def active_row(db: Database) -> dict | None:
    return await db.fetch_one("SELECT * FROM battle WHERE active = 1 ORDER BY id DESC LIMIT 1")


def _snapshot(bid: int, state: dict, target_id: str, p: dict, result: str | None) -> dict:
    d = player_mod.derived(p)
    e = state["enemy"]
    return {
        "id": bid,
        "enemy": {
            "id": target_id, "type": e["type"], "name": e["name"], "level": e["level"],
            "hp": e["hp"], "hp_max": e["hp_max"], "atk": e["atk"], "def": e["def"], "spd": e["spd"],
        },
        "player": {
            "name": p["name"], "level": p["level"],
            "hp": p["hp"], "mp": p["mp"], "hp_max": d["hp_max"], "mp_max": d["mp_max"],
        },
        "player_first": state["player_first"],
        "round": state["round"],
        "potions_used": state["potions_used"],
        "active": result is None,
        "result": result,
    }


async def active_snapshot(db: Database) -> dict | None:
    brow = await active_row(db)
    if brow is None:
        return None
    p = await player_mod.get(db)
    if p is None:
        return None
    return _snapshot(brow["id"], json.loads(brow["state_json"]), brow["target_id"], p, None)


async def start(db: Database, p: dict, mob_id: str, pos: dict | None) -> dict:
    if await active_row(db) is not None:
        raise BattleError("已有战斗进行中")

    spawn = mobs.find_spawn(mob_id)
    if spawn is None:
        raise BattleError("unknown mob")
    st = {m["id"]: m for m in await mobs.states(db)}
    if not st[mob_id]["alive"]:
        raise BattleError("mob not available")

    t = data.MOB_TYPES[spawn["type"]]
    d = player_mod.derived(p)
    state = {
        "mob_id": mob_id,
        "enemy": {
            "type": spawn["type"], "name": t["name"], "level": t["level"],
            "hp": t["hp"], "hp_max": t["hp"], "atk": t["atk"], "def": t["def"],
            "spd": t["spd"], "potions": t["potions"],
        },
        "player_first": d["spd"] >= t["spd"],
        "round": 1, "enemy_acted": False, "potions_used": 0,
    }
    if pos:
        p["pos_x"], p["pos_y"] = pos["x"], pos["y"]
    bid = await db.execute(
        "INSERT INTO battle(player_id, target_kind, target_id, state_json, active, created_at)"
        " VALUES(?,?,?,?,1,?)",
        (p["id"], "mob", mob_id, json.dumps(state, ensure_ascii=False), time.time()),
    )
    await player_mod.save(db, p)
    return _snapshot(bid, state, mob_id, p, None)


async def apply_action(db: Database, action: str, item_id: int | None) -> dict:
    brow = await active_row(db)
    if brow is None:
        raise BattleError("no active battle")
    p = await player_mod.get(db)
    if p is None:
        raise BattleError("no player")

    state = json.loads(brow["state_json"])
    enemy = state["enemy"]
    d = player_mod.derived(p)
    log: list[dict] = []
    result: str | None = None
    rewards: dict | None = None

    def add(kind: str, text: str, **kw) -> None:
        log.append({"kind": kind, "text": text, **kw})

    def try_player_attack() -> None:
        crit = random.random() < d["crit"]
        dmg = max(1, round(d["atk"] - enemy["def"]))
        if crit:
            dmg = round(dmg * 1.5)
        enemy["hp"] = max(0, enemy["hp"] - dmg)
        text = f"你攻向{enemy['name']}，造成 {dmg} 点伤害"
        if crit:
            text += "（会心一击）"
        add("player_attack", text + "。", value=dmg, crit=crit, enemy_hp=enemy["hp"])

    def try_enemy_attack() -> None:
        if random.random() < d["dodge"]:
            add("enemy_attack", f"你身形一晃，避开了{enemy['name']}的攻击。", dodged=True, player_hp=p["hp"])
            return
        dmg = max(1, round(enemy["atk"] - d["def"]))
        p["hp"] = max(0, p["hp"] - dmg)
        add("enemy_attack", f"{enemy['name']}扑将过来，你受到 {dmg} 点伤害。", value=dmg, player_hp=p["hp"])

    def enemy_turn() -> None:
        # Rule AI per docs/DESIGN.md §5.6; M1 mobs carry no skills,
        # so branches 2/3 fall through to a plain attack.
        if enemy["hp"] < enemy["hp_max"] * 0.35 and enemy["potions"] > 0:
            enemy["potions"] -= 1
            heal = min(data.ENEMY_POTION_HEAL, enemy["hp_max"] - enemy["hp"])
            enemy["hp"] += heal
            add("enemy_item", f"{enemy['name']}服下药剂，恢复了 {heal} 点气血。", value=heal, enemy_hp=enemy["hp"])
        else:
            try_enemy_attack()
        state["enemy_acted"] = True

    if not state["player_first"] and not state["enemy_acted"]:
        enemy_turn()
        if p["hp"] <= 0:
            result = "lose"

    if result is None:
        if action == "attack":
            try_player_attack()
        elif action == "item":
            if item_id is None:
                raise BattleError("missing item_id")
            if state["potions_used"] >= data.BATTLE_POTION_LIMIT:
                raise BattleError("本场药剂已用尽")
            inv = await db.fetch_one(
                "SELECT it.name, it.type, it.effect_json FROM inventory inv"
                " JOIN item it ON it.id = inv.item_id WHERE inv.player_id = ? AND inv.item_id = ?",
                (p["id"], item_id),
            )
            if inv is None or inv["type"] != "potion":
                raise BattleError("not a usable potion")
            effect = json.loads(inv["effect_json"])
            state["potions_used"] += 1
            await inventory.consume(db, p["id"], item_id)
            parts = []
            if "hp" in effect:
                heal = min(effect["hp"], d["hp_max"] - p["hp"])
                p["hp"] += heal
                parts.append(f"{heal} 点气血")
            if "mp" in effect:
                heal = min(effect["mp"], d["mp_max"] - p["mp"])
                p["mp"] += heal
                parts.append(f"{heal} 点内力")
            add("player_item", f"你服下{inv['name']}，恢复了{'、'.join(parts)}。",
                player_hp=p["hp"], player_mp=p["mp"])
        elif action == "flee":
            rate = min(0.9, max(0.1, 0.3 + (d["spd"] - enemy["spd"]) * 0.05))
            if random.random() < rate:
                add("flee", "你瞅准空当，抽身而退。")
                result = "fled"
            else:
                add("flee_fail", f"逃脱未成，反被{enemy['name']}占了先机！")
        elif action == "skill":
            raise BattleError("技能系统将于 M2 解锁")
        else:
            raise BattleError("unknown action")

    if result is None and enemy["hp"] <= 0:
        result = "win"

    if result is None and not state["enemy_acted"]:
        enemy_turn()

    if result is None and p["hp"] <= 0:
        result = "lose"

    if result is None:
        state["round"] += 1
        state["enemy_acted"] = False
    else:
        rewards = await _finalize(db, p, state, add, result)

    await db.execute(
        "UPDATE battle SET state_json = ?, active = ? WHERE id = ?",
        (json.dumps(state, ensure_ascii=False), 0 if result else 1, brow["id"]),
    )
    await player_mod.save(db, p)
    return {
        "battle": _snapshot(brow["id"], state, brow["target_id"], p, result),
        "log": log,
        "result": result,
        "rewards": rewards,
        "player": await player_mod.api_dict(db, p),
    }


async def _finalize(db: Database, p: dict, state: dict, add, result: str) -> dict | None:
    if result == "win":
        t = data.MOB_TYPES[state["enemy"]["type"]]
        drops = []
        for dr in t["drops"]:
            if random.random() < dr["rate"]:
                row = data.item(dr["key"])
                await inventory.add(db, p["id"], row["id"])
                drops.append({"name": row["name"], "qty": 1})
                add("drop", f"拾获 {row['name']}。")
        exp, gold = t["exp"], t["gold"]
        cult = round(exp * data.WIN_CULTIVATION_RATE)
        ups = player_mod.gain_exp(p, exp)
        p["gold"] += gold
        p["cultivation"] += cult
        add("win", f"战斗胜利！获得 经验 {exp}、银两 {gold}、修炼点 {cult}。")
        for lv in ups:
            add("levelup", f"境界提升！你升至 {lv} 级。")
        await mobs.mark_killed(db, state["mob_id"])
        return {"exp": exp, "gold": gold, "cultivation": cult, "drops": drops, "level_ups": ups}

    if result == "lose":
        d = player_mod.derived(p)
        p["hp"] = math.ceil(d["hp_max"] / 2)
        p["mp"] = math.ceil(d["mp_max"] / 2)
        p["gold"] = max(0, p["gold"] - math.floor(p["gold"] * 0.1))
        p["pos_x"], p["pos_y"] = data.INN_SPAWN
        add("lose", "你不支倒地，眼前一黑……")
        return None

    add("flee_end", "你摆脱了纠缠，退出了战斗。")
    return None
