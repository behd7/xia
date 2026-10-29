import json
import logging
import math
import random
import time

from ..db import Database
from . import arts, character as character_mod, data, inventory, mobs, player as player_mod, skills as skills_mod
from . import world as world_mod, world_state as world_state_mod
from ..agent import dialogue as dialogue_mod
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


async def active_row(db: Database) -> dict | None:
    return await db.fetch_one("SELECT * FROM battle WHERE active = 1 ORDER BY id DESC LIMIT 1")


def _snapshot(bid: int, state: dict, target_id: str, p: dict, result: str | None, d: dict) -> dict:
    e = state["enemy"]
    return {
        "id": bid,
        "enemy": {
            "id": target_id, "type": e["type"], "name": e["name"], "level": e["level"],
            "hp": e["hp"], "hp_max": e["hp_max"], "atk": e["atk"], "def": e["def"],
            "spd": e["spd"], "element": e.get("element"),
            "mp": e.get("mp"), "mp_max": e.get("mp_max"),
        },
        "player": {
            "name": p["name"], "level": p["level"],
            "hp": p["hp"], "mp": p["mp"], "hp_max": d["hp_max"], "mp_max": d["mp_max"],
            "element": arts.element_for_id(p.get("art_id")),
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
            "spd": t["spd"], "potions": t["potions"], "element": t.get("element"),
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
    c = n.get("character")
    if not c:
        raise BattleError("此人不肯比试")
    d = await player_mod.stats(db, p)
    de = await character_mod.stats(db, c)
    npc_skills = await skills_mod.combat_skills(
        db, c["character_id"], c.get("art_id"), int(c["level"]))
    hp = float(c["hp"]) if c["hp"] and float(c["hp"]) > 0 else de["hp_max"]
    state = {
        "npc_id": npc_id, "spar": True,
        "enemy": {
            "type": "npc", "name": n["name"], "level": c["level"],
            "hp": min(hp, de["hp_max"]), "hp_max": de["hp_max"],
            "atk": de["atk"], "def": de["def"], "spd": de["spd"],
            "crit": de["crit"], "dodge": de["dodge"],
            "potions": int(n.get("potions", 0)),
            "element": arts.element_for_id(c.get("art_id")),
            "mp": float(c["mp"]), "mp_max": de["mp_max"],
            "skills": npc_skills,
        },
        "player_first": d["spd"] >= de["spd"],
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
    player_el = arts.element_for_id(p.get("art_id"))
    enemy_el = enemy.get("element")
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
        ebv = enemy_buff()
        e_dodge = enemy.get("dodge", 0.0) + ebv.get("dodge_add", 0.0)
        if random.random() < e_dodge:
            add("player_attack",
                f"{enemy['name']}身形一晃，避开了你的攻击。",
                dodged=True, enemy_hp=enemy["hp"])
            return
        defv = enemy["def"] * ebv.get("def_pct", 1.0)
        defv *= 1 - eff.get("pierce_def", 0)
        crit = random.random() < (d["crit"] + eff.get("crit_add", 0))
        dmg = max(1, round(d["atk"] * mult - defv))
        if crit:
            dmg = round(dmg * 1.5)
        elem = arts.counter_label(player_el, enemy_el)
        elem_mult = arts.counter_multiplier(player_el, enemy_el)
        dmg = max(1, round(dmg * elem_mult))
        enemy["hp"] = max(0, enemy["hp"] - dmg)
        base = f"你使出{name}，" if name else f"你攻向{enemy['name']}，"
        suffix = "（会心一击）" if crit else ""
        if elem == "advantage":
            suffix += "（属性克制）"
        elif elem == "disadvantage":
            suffix += "（属性被克）"
        add("player_attack", f"{base}造成 {dmg} 点伤害{suffix}。", value=dmg,
            crit=crit, enemy_hp=enemy["hp"], element_counter=elem,
            element_mult=round(elem_mult, 2))

    def try_player_attack() -> None:
        player_hit("", 1.0, {})

    def enemy_hit(name: str, mult: float, eff: dict) -> None:
        pbv = player_buff()
        if not eff.get("ignore_dodge"):
            dodge = d["dodge"] + pbv.get("dodge_add", 0.0)
            if random.random() < dodge:
                add("enemy_attack",
                    f"你身形一晃，避开了{enemy['name']}的{'这一招' if name else '攻击'}。",
                    dodged=True, player_hp=p["hp"])
                return
        defv = d["def"] * pbv.get("def_pct", 1.0)
        defv *= 1 - eff.get("pierce_def", 0)
        crit = random.random() < (enemy.get("crit", 0) + eff.get("crit_add", 0))
        dmg = max(1, round(enemy["atk"] * mult - defv))
        if crit:
            dmg = round(dmg * 1.5)
        elem = arts.counter_label(enemy_el, player_el)
        elem_mult = arts.counter_multiplier(enemy_el, player_el)
        dmg = max(1, round(dmg * elem_mult))
        p["hp"] = max(0, p["hp"] - dmg)
        base = f"{enemy['name']}使出{name}，" if name else f"{enemy['name']}扑将过来，"
        suffix = "（会心一击）" if crit else ""
        if elem == "advantage":
            suffix += "（属性克制）"
        elif elem == "disadvantage":
            suffix += "（属性被克）"
        add("enemy_skill" if name else "enemy_attack",
            f"{base}你受到 {dmg} 点伤害{suffix}。", value=dmg, crit=crit,
            player_hp=p["hp"], element_counter=elem,
            element_mult=round(elem_mult, 2))

    def pick_enemy_skill() -> dict | None:
        best: dict | None = None
        cd_map = state.setdefault("skill_cd", {})
        for s in enemy.get("skills", []):
            if float(enemy.get("mp", 0)) < float(s.get("mp_cost", 0)):
                continue
            if cd_map.get(f"e:{s['id']}", 0) > 0:
                continue
            if best is None or float(s.get("multiplier", 1)) > float(best.get("multiplier", 1)):
                best = s
        return best

    def enemy_use_skill(s: dict) -> None:
        eff = s.get("effect") or {}
        enemy["mp"] = max(0, float(enemy.get("mp", 0)) - float(s.get("mp_cost", 0)))
        if int(s.get("cooldown", 0)):
            state.setdefault("skill_cd", {})[f"e:{s['id']}"] = int(s["cooldown"])
        enemy_hit(str(s["name"]), float(s.get("multiplier", 1.0)), eff)
        faster = eff.get("double_if_faster")
        if faster and enemy["spd"] > d["spd"] * float(faster):
            add("double", f"{enemy['name']}身法奇快，再补一招！")
            enemy_hit(str(s["name"]), float(s.get("multiplier", 1.0)), eff)
        b = eff.get("buff_self")
        if b:
            ebv = enemy_buff()
            if "def_pct" in b:
                ebv["def_pct"] = b["def_pct"]
                ebv["def_rounds"] = max(ebv.get("def_rounds", 0), b["rounds"])
            if "dodge_add" in b:
                ebv["dodge_add"] = b["dodge_add"]
                ebv["dodge_rounds"] = max(ebv.get("dodge_rounds", 0), b["rounds"])

    def enemy_turn() -> None:
        # Rule AI per docs/DESIGN.md §5.6; NPCs use their equipped moves.
        if enemy["hp"] < enemy["hp_max"] * 0.35 and enemy["potions"] > 0:
            enemy["potions"] -= 1
            heal = min(data.ENEMY_POTION_HEAL, enemy["hp_max"] - enemy["hp"])
            enemy["hp"] += heal
            add("enemy_item", f"{enemy['name']}服下药剂，恢复了 {heal} 点气血。", value=heal, enemy_hp=enemy["hp"])
        else:
            skill = pick_enemy_skill()
            if skill and (p["hp"] <= d["hp_max"] * 0.4 or random.random() < 0.7):
                enemy_use_skill(skill)
            else:
                enemy_hit("", 1.0, {})
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
                "SELECT level, equipped FROM character_skill"
                " WHERE character_id = ? AND skill_id = ?",
                (p["character_id"], item_id),
            )
            if srow is None or ps is None:
                raise BattleError("尚未习得此技")
            if srow["art_id"] != p.get("art_id"):
                raise BattleError("此招不属于当前功法")
            if p["level"] < int(srow["unlock_level"] or 1):
                raise BattleError("修为未至，此招尚未解锁")
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
                "SELECT it.name, it.type, it.effect_json FROM character_inventory inv"
                " JOIN item it ON it.id = inv.item_id"
                " WHERE inv.character_id = ? AND inv.item_id = ?",
                (p["character_id"], item_id),
            )
            if inv is None or inv["type"] != "potion":
                raise BattleError("not a usable potion")
            effect = json.loads(inv["effect_json"])
            state["potions_used"] += 1
            await inventory.consume(db, p["character_id"], item_id)
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
        npc_name = state["enemy"]["name"]
        verdict = {"win": "你技高一筹", "lose": "你略逊半招"}[result]
        add(result, f"切磋结束——{verdict}，获经验 {exp}。")

        aff_before = await npc_mod.affinity(db, npc_id)
        message = (
            f"你我切磋一场，{'我胜了半招' if result == 'win' else '我输了一招'}。"
        )
        payload = await dialogue_mod.talk(
            db, mem_store, npc_id, message, purpose="spar", pause=False)
        if payload.get("stale"):
            # 对话期间重开了新档：切磋事件/情绪不属于新局。
            return {"exp": exp, "gold": 0, "cultivation": 0, "drops": [],
                    "level_ups": ups, "world_announced": False}
        add("npc_reaction", f"{npc_name}：{payload['reply']}")
        events_mod.record(
            mem_store, "spar",
            f"{p['name']}与{npc_name}切磋，"
            f"{'胜了半招' if result == 'win' else '输了一招'}",
            payload["place"], [p["name"], npc_name])
        new_aff = int(payload["affinity"])
        try:
            if new_aff < aff_before:
                mood = "impatient"
            elif new_aff > aff_before:
                mood = "respectful" if result == "win" else "pleased"
            else:
                mood = None
            if mood is not None:
                await mood_mod.set(db, npc_id, mood, "与你切磋", 30)
        except Exception:
            logger.exception("mood update failed for spar npc=%d", npc_id)
        return {"exp": exp, "gold": 0, "cultivation": 0, "drops": [], "level_ups": ups,
                "world_announced": False}

    if result == "win":
        t = data.MOB_TYPES[state["enemy"]["type"]]
        drops = []
        if state["enemy"]["type"] == "boss" and not await world_state_mod.artifact_acquired(db):
            row = data.item(world_mod.ARTIFACT_ITEM_KEY)
            await inventory.add(db, p["character_id"], row["id"])
            await world_state_mod.mark_artifact_acquired(db)
            drops.append({"name": row["name"], "qty": 1})
            add("drop", "拾获 断岳刀——刀身沉黑，隐有寒光。")
        for dr in t["drops"]:
            if random.random() < dr["rate"]:
                row = data.item(dr["key"])
                await inventory.add(db, p["character_id"], row["id"])
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
