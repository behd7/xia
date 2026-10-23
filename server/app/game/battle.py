import json
import logging
import math
import random
import time

from ..db import Database
from . import data, inventory, mobs, player as player_mod, skills as skills_mod
from . import world as world_mod, world_state as world_state_mod
from ..agent import events as events_mod
from ..agent import mood as mood_mod
from ..agent import npc as npc_mod

logger = logging.getLogger(__name__)


class BattleError(Exception):
    pass

# 跨层受控注入：main lifespan 装配时赋值。避免 game -> agent 的模块级 import 环，
# 仅此两处；None 表示 LLM/反思层未装配（正常开发恒装配，测试可不配）。
mem_store = None     # injected: 切磋写该 NPC 记忆

SPAR_EXP_BASE = 50        # 切磋经验基数，结算后 ×0.3（DESIGN §5.6）
SPAR_END_PCT = 0.30       # 任一方气血降至 30% 即结束
SPAR_AFFINITY_WIN = 5     # 切磋表现 → 好感（DESIGN §6.5）
SPAR_AFFINITY_LOSE = 3


async def active_row(db: Database) -> dict | None:
    return await db.fetch_one("SELECT * FROM battle WHERE active = 1 ORDER BY id DESC LIMIT 1")


def _snapshot(bid: int, state: dict, target_id: str, p: dict, result: str | None, d: dict) -> dict:
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
        "buffs": state.get("buffs", {"player": {}, "enemy": {}}),
        "skill_cd": state.get("skill_cd", {}),
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
    d = await player_mod.stats(db, p)
    return _snapshot(brow["id"], json.loads(brow["state_json"]), brow["target_id"], p, None, d)


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
    d = await player_mod.stats(db, p)
    state = {
        "mob_id": mob_id,
        "enemy": {
            "type": spawn["type"], "name": t["name"], "level": t["level"],
            "hp": t["hp"], "hp_max": t["hp"], "atk": t["atk"], "def": t["def"],
            "spd": t["spd"], "potions": t["potions"],
        },
        "player_first": d["spd"] >= t["spd"],
        "round": 1, "enemy_acted": False, "potions_used": 0,
        "buffs": {"player": {}, "enemy": {}}, "skill_cd": {},
    }
    if pos:
        p["pos_x"], p["pos_y"] = pos["x"], pos["y"]
    bid = await db.execute(
        "INSERT INTO battle(player_id, target_kind, target_id, state_json, active, created_at)"
        " VALUES(?,?,?,?,1,?)",
        (p["id"], "mob", mob_id, json.dumps(state, ensure_ascii=False), time.time()),
    )
    await player_mod.save(db, p)
    return _snapshot(bid, state, mob_id, p, None, d)


async def start_spar(db: Database, p: dict, npc_id: int, pos: dict | None) -> dict:
    """切磋战斗（DESIGN §5.6）：任一方 HP<30% 即止，无死亡/无掉落，经验 ×0.3。"""
    if await active_row(db) is not None:
        raise BattleError("已有战斗进行中")
    n = await npc_mod.by_id(db, npc_id)
    if n is None:
        raise BattleError("查无此人")
    c = n.get("combat")
    if not c:
        raise BattleError("此人不肯比试")
    d = await player_mod.stats(db, p)
    state = {
        "npc_id": npc_id, "spar": True,
        "enemy": {
            "type": "npc", "name": n["name"], "level": c["level"],
            "hp": c["hp"], "hp_max": c["hp"], "atk": c["atk"], "def": c["def"],
            "spd": c["spd"], "potions": c.get("potions", 0),
        },
        "player_first": d["spd"] >= c["spd"],
        "round": 1, "enemy_acted": False, "potions_used": 0,
        "buffs": {"player": {}, "enemy": {}}, "skill_cd": {},
    }
    if pos:
        p["pos_x"], p["pos_y"] = pos["x"], pos["y"]
    bid = await db.execute(
        "INSERT INTO battle(player_id, target_kind, target_id, state_json, active, created_at)"
        " VALUES(?,?,?,?,1,?)",
        (p["id"], "npc", str(npc_id), json.dumps(state, ensure_ascii=False), time.time()),
    )
    await player_mod.save(db, p)
    return _snapshot(bid, state, str(npc_id), p, None, d)


def _tick_round_end(state: dict) -> None:
    for side in ("player", "enemy"):
        b = state.get("buffs", {}).get(side)
        if not b:
            continue
        for key in ("def", "dodge"):
            rk = f"{key}_rounds"
            if b.get(rk):
                b[rk] -= 1
                if b[rk] <= 0:
                    b.pop(f"{key}_pct", None)
                    b.pop(f"{key}_add", None)
                    b.pop(rk, None)
    cd = state.get("skill_cd", {})
    for k in list(cd):
        cd[k] = max(0, cd[k] - 1)


async def apply_action(db: Database, action: str, item_id: int | None) -> dict:
    brow = await active_row(db)
    if brow is None:
        raise BattleError("no active battle")
    p = await player_mod.get(db)
    if p is None:
        raise BattleError("no player")

    state = json.loads(brow["state_json"])
    enemy = state["enemy"]
    d = await player_mod.stats(db, p)
    log: list[dict] = []
    result: str | None = None
    rewards: dict | None = None

    def add(kind: str, text: str, **kw) -> None:
        log.append({"kind": kind, "text": text, **kw})

    def player_buff() -> dict:
        return state.setdefault("buffs", {}).setdefault("player", {})

    def enemy_buff() -> dict:
        return state.setdefault("buffs", {}).setdefault("enemy", {})

    def player_hit(name: str, mult: float, eff: dict) -> None:
        defv = enemy["def"] * enemy_buff().get("def_pct", 1.0)
        defv *= 1 - eff.get("pierce_def", 0)
        crit = random.random() < (d["crit"] + eff.get("crit_add", 0))
        dmg = max(1, round(d["atk"] * mult - defv))
        if crit:
            dmg = round(dmg * 1.5)
        enemy["hp"] = max(0, enemy["hp"] - dmg)
        base = f"你使出{name}，" if name else f"你攻向{enemy['name']}，"
        suffix = "（会心一击）" if crit else ""
        add("player_attack", f"{base}造成 {dmg} 点伤害{suffix}。", value=dmg, crit=crit, enemy_hp=enemy["hp"])

    def try_player_attack() -> None:
        player_hit("", 1.0, {})

    def try_enemy_attack() -> None:
        pbv = player_buff()
        dodge = d["dodge"] + pbv.get("dodge_add", 0.0)
        if random.random() < dodge:
            add("enemy_attack", f"你身形一晃，避开了{enemy['name']}的攻击。", dodged=True, player_hp=p["hp"])
            return
        defv = d["def"] * pbv.get("def_pct", 1.0)
        dmg = max(1, round(enemy["atk"] - defv))
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
        elif action == "skill":
            if item_id is None:
                raise BattleError("missing skill id")
            srow = await skills_mod.by_id(db, item_id)
            ps = await db.fetch_one(
                "SELECT level, equipped FROM player_skill WHERE player_id = ? AND skill_id = ?",
                (p["id"], item_id),
            )
            if srow is None or ps is None:
                raise BattleError("尚未习得此技")
            if srow["kind"] != "active":
                raise BattleError("内功为被动心法，无需施展")
            if not ps["equipped"]:
                raise BattleError("此技未装备")
            if p["mp"] < srow["mp_cost"]:
                raise BattleError("内力不足")
            cd_map = state.setdefault("skill_cd", {})
            if cd_map.get(str(item_id), 0) > 0:
                raise BattleError("此技尚在冷却")
            eff = json.loads(srow["effect_json"])
            p["mp"] -= srow["mp_cost"]
            if srow["cooldown"]:
                cd_map[str(item_id)] = srow["cooldown"]
            mult = skills_mod.effective_multiplier(srow["multiplier"], ps["level"])

            player_hit(srow["name"], mult, eff)
            faster = eff.get("double_if_faster")
            if faster and d["spd"] > enemy["spd"] * faster:
                add("double", "身法占尽先机，再补一击！")
                player_hit(srow["name"], mult, eff)
            b = eff.get("buff_self")
            if b:
                pbv = player_buff()
                if "def_pct" in b:
                    pbv["def_pct"] = b["def_pct"]
                    pbv["def_rounds"] = max(pbv.get("def_rounds", 0), b["rounds"])
                    add("buff", "真气护体，防御提升。")
                if "dodge_add" in b:
                    pbv["dodge_add"] = b["dodge_add"]
                    pbv["dodge_rounds"] = max(pbv.get("dodge_rounds", 0), b["rounds"])
                    add("buff", "身形飘忽，闪避提升。")
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
        else:
            raise BattleError("unknown action")

    if result is None and state.get("spar"):
        # 切磋：任一方 HP <= 30% 即结束（无死亡）。
        e_pct = enemy["hp"] <= enemy["hp_max"] * SPAR_END_PCT
        p_pct = p["hp"] <= d["hp_max"] * SPAR_END_PCT
        if e_pct:
            result = "win"          # 技压对方，即止；不再吃对方一回合
        elif not state["enemy_acted"]:
            enemy_turn()
            if p["hp"] <= d["hp_max"] * SPAR_END_PCT:
                result = "lose"
    else:
        if result is None and enemy["hp"] <= 0:
            result = "win"
        if result is None and not state["enemy_acted"]:
            enemy_turn()
        if result is None and p["hp"] <= 0:
            result = "lose"

    if result is None:
        state["round"] += 1
        state["enemy_acted"] = False
        _tick_round_end(state)
    else:
        rewards = await _finalize(db, p, state, add, result)
        state["buffs"] = {"player": {}, "enemy": {}}
        state["skill_cd"] = {}

    await db.execute(
        "UPDATE battle SET state_json = ?, active = ? WHERE id = ?",
        (json.dumps(state, ensure_ascii=False), 0 if result else 1, brow["id"]),
    )
    await player_mod.save(db, p)
    return {
        "battle": _snapshot(brow["id"], state, brow["target_id"], p, result, d),
        "log": log,
        "result": result,
        "rewards": rewards,
        "player": await player_mod.api_dict(db, p),
    }


async def _finalize(db: Database, p: dict, state: dict, add, result: str) -> dict | None:
    if state.get("spar") and result in ("win", "lose"):
        npc_id = int(state["npc_id"])
        exp = round(SPAR_EXP_BASE * 0.3)
        ups = player_mod.gain_exp(p, exp)
        gain = SPAR_AFFINITY_WIN if result == "win" else SPAR_AFFINITY_LOSE
        await db.execute(
            "UPDATE npc_state SET affinity = MAX(0, MIN(100, affinity + ?)) WHERE npc_id = ?",
            (gain, npc_id),
        )
        verdict = {"win": "你技高一筹", "lose": "你略逊半招"}[result]
        add(result, f"切磋结束——{verdict}，获经验 {exp}。")
        try:
            await mood_mod.set(db, npc_id, "respectful" if result == "win" else "pleased",
                               "与你切磋", 30)
        except Exception:
            logger.exception("mood update failed for spar npc=%d", npc_id)
        if mem_store is not None:  # 本场切磋写入该 NPC 记忆（importance 6）
            await mem_store.add_memory(
                db, npc_id, content=f"在练武场与你切磋，{verdict}。",
                kind="event", participants=[p["name"]], poi="练武场", importance=6,
            )
        return {"exp": exp, "gold": 0, "cultivation": 0, "drops": [], "level_ups": ups,
                "world_announced": False}

    if result == "win":
        t = data.MOB_TYPES[state["enemy"]["type"]]
        drops = []
        if state["enemy"]["type"] == "boss" and not await world_state_mod.artifact_acquired(db):
            row = data.item(world_mod.ARTIFACT_ITEM_KEY)
            await inventory.add(db, p["id"], row["id"])
            await world_state_mod.mark_artifact_acquired(db)
            drops.append({"name": row["name"], "qty": 1})
            add("drop", "拾获 断岳刀——刀身沉黑，隐有寒光。")
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
        advanced = False
        if mem_store is not None:
            poi = data.MOB_POI.get(state["enemy"]["type"], "野外")
            events_mod.record(
                mem_store, "battle_win",
                f"在{poi}击败了{state['enemy']['name']}", poi, [p["name"]])
            advanced = await world_state_mod.check_and_advance(db, mem_store.clock.now())
            if advanced:
                add("world", "你取回断岳刀的消息，已经传遍了清溪镇。")
        # Nudge the player off the mob spawn so a reload after the 60s
        # respawn doesn't instantly re-trigger the battle while idle.
        spawn = mobs.find_spawn(state["mob_id"])
        if spawn and p["pos_x"] == spawn["x"] * data.TILE + 8 and p["pos_y"] == spawn["y"] * data.TILE + 8:
            p["pos_x"] += 48
            p["pos_y"] -= 32
        return {"exp": exp, "gold": gold, "cultivation": cult, "drops": drops, "level_ups": ups,
                "world_announced": advanced}

    if result == "lose":
        d = await player_mod.stats(db, p)
        p["hp"] = math.ceil(d["hp_max"] / 2)
        p["mp"] = math.ceil(d["mp_max"] / 2)
        p["gold"] = max(0, p["gold"] - math.floor(p["gold"] * 0.1))
        p["pos_x"], p["pos_y"] = data.INN_SPAWN
        add("lose", "你不支倒地，眼前一黑……")
        if mem_store is not None:
            poi = data.MOB_POI.get(state["enemy"]["type"], "野外")
            events_mod.record(
                mem_store, "battle_lose", f"在{poi}被打倒", poi, [p["name"]])
        return None

    add("flee_end", "你摆脱了纠缠，退出了战斗。")
    return None
