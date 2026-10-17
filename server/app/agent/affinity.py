"""好感度规则（docs/DESIGN.md §6.5）：分段效果与移动/skill 解锁判定。

M3 用到的规则增益（切磋/送礼）与对话 LLM 判定在此统一 clamp，防止信任
侧面漂移（AGENTS.md #5）。rank 与位阶解锁由 affinity 推出，取代 M2 用
等级替位的纯条件版本。
"""

from ..db import Database
from . import npc as npc_mod

# 分段（DESIGN §6.5 表）：0~29 冷淡 / 30~59 客气 / 60~69 热络 / 70~84 亲近 / 85~100 挚交
RANK_THRESHOLDS = [(60, 1), (75, 2), (85, 3)]  # (affinity_min, rank)
RANKS = {1: "入门弟子", 2: "亲传弟子", 3: "关门弟子"}

APPRENTICE_AFFINITY = 60      # 掌门收徒好感门槛（另需 lv≥3，见 skills.join_sect）
CHALLENGE_AFFINITY = 40       # 掌门应切磋门槛（DESIGN §5.6）
MAX_DIALOGUE_DELTA = 3        # 对话单回合好感变化幅度上限（settings 亦有，双保险）


def clamp(value: int) -> int:
    return max(0, min(100, int(value)))


def rank_of(affinity: int) -> int:
    """最低达到该好感时的位阶。"""
    for thr in sorted(RANK_THRESHOLDS, reverse=True):
        if affinity >= thr[0]:
            return thr[1]
    return 0  # 未入派也可返回 0（仅辅助显示，不入派不显示 rank）


def rank_level_for(sect_affinity: int) -> int:
    return rank_of(sect_affinity)


async def apply_delta(db: Database, npc_id: int, delta: int) -> int:
    """按 ±delta 更新该 NPC 对玩家好感并写回、clamp 0~100。"""
    cur = await npc_mod.affinity(db, npc_id)
    return await npc_mod.set_affinity(db, npc_id, cur + int(delta))


def discount_pct(affinity: int, role: str) -> int:
    """王大柱（smith）按好感给折扣（DESIGN §6.5）：热络 95 折、亲近 9 折。"""
    if role != "smith":
        return 100
    if affinity >= 70:
        return 90
    if affinity >= 60:
        return 95
    return 100