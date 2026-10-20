"""Deterministic NPC schedule (docs/DESIGN.md §6.10).

Position is a pure function of the game clock. The server owns the phase
table, anchors, routes and pacing constants; the Godot client mirrors the
same pure functions locally (zero position polling). No DB, no agent imports.
"""

from __future__ import annotations

GAME_SEC_PER_DAY = 86400
GATHER_ANCHOR = (768.0, 440.0)          # 镇口，竖路中央，避开井与墙
PACING = {"slot": 600.0, "fade": 120.0, "x": 8.0, "y": 6.0}

PHASES = (
    ("home", 0, 23400),
    ("walk_home_to_work", 23400, 25200),
    ("work", 25200, 39600),
    ("walk_work_to_gather", 39600, 40200),
    ("gather", 40200, 49800),
    ("walk_gather_to_work", 49800, 50400),
    ("work", 50400, 68400),
    ("walk_work_to_home", 68400, 70200),
    ("home", 70200, 86400),
)

# slug -> (家→工作地折线, 工作地→镇口折线)。折线绕开实心建筑，不能改直线。
ROUTES = {
    "gu_changfeng": ([(144, 24), (240, 24), (240, 183), (144, 183)],
                     [(144, 183), (768, 183), (768, 440)]),
    "tie_wushuang": ([(500, 430), (384, 430), (384, 183), (368, 183)],
                     [(368, 183), (768, 183), (768, 440)]),
    "su_wanqing": ([(592, 24), (688, 24), (688, 183), (592, 183)],
                   [(592, 183), (768, 183), (768, 440)]),
    "chen_bansong": ([(128, 214), (208, 214), (208, 375), (128, 375)],
                     [(128, 375), (128, 440), (768, 440)]),
    "wang_dazhu": ([(304, 214), (384, 214), (384, 375), (304, 375)],
                   [(304, 375), (304, 440), (768, 440)]),
    "liu_xiaodie": ([(620, 430), (566, 407)],
                    [(566, 407), (768, 440)]),
    "zhang_mantou": ([(1360, 444), (1300, 456), (1120, 456), (1120, 427), (752, 427)],
                     [(752, 427), (768, 440)]),
}


def phase_at(sec_of_day: float) -> tuple[str, float, float]:
    for kind, start, end in PHASES:
        if start <= sec_of_day < end:
            return kind, float(start), float(end)
    return "home", 70200.0, 86400.0


def _distance(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def point_along(points, t: float) -> tuple[float, float]:
    """按累计长度在折线上取点；t 会被 clamp 到 [0,1]。"""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) == 1:
        return pts[0]
    total = sum(_distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    if total <= 0:
        return pts[0]
    d = max(0.0, min(1.0, t)) * total
    for i in range(len(pts) - 1):
        seg = _distance(pts[i], pts[i + 1])
        if d <= seg or i == len(pts) - 2:
            r = d / seg if seg else 0.0
            return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * r,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * r)
        d -= seg
    return pts[-1]


def _lcg(npc_id: int, day: int, slot: int, axis: int) -> int:
    s = (npc_id * 1103515245 + day * 12345 + slot * 1013904223 + axis * 2654435761) & 0xFFFFFFFF
    return (s * 1664525 + 1013904223) & 0xFFFFFFFF


def _slot_offset(npc_id: int, day: int, slot: int) -> tuple[float, float]:
    sx = _lcg(npc_id, day, slot, 0)
    sy = _lcg(npc_id, day, slot, 1)
    x = ((sx >> 8) & 0xFFFF) / 65535.0 * 16.0 - 8.0
    y = ((sy >> 8) & 0xFFFF) / 65535.0 * 12.0 - 6.0
    return x, y


def _pacing(npc_id: int, day: int, sec_of_day: float) -> tuple[float, float]:
    slot = int(sec_of_day // PACING["slot"])
    prev = slot if slot == 0 else slot - 1
    cur_x, cur_y = _slot_offset(npc_id, day, slot)
    prev_x, prev_y = _slot_offset(npc_id, day, prev)
    t = min(1.0, (sec_of_day % PACING["slot"]) / PACING["fade"])
    t = t * t * (3.0 - 2.0 * t)  # smoothstep
    return prev_x + (cur_x - prev_x) * t, prev_y + (cur_y - prev_y) * t


def position_for(npc: dict, now_game: float) -> tuple[float, float]:
    routes = ROUTES.get(str(npc.get("slug", "")))
    if routes is None:
        return (792.0, 440.0)  # 未知花名册条目的兜底，正常不会发生
    home_work, work_gather = routes
    sod = now_game % GAME_SEC_PER_DAY
    day = int(now_game // GAME_SEC_PER_DAY)
    kind, start, end = phase_at(sod)
    t = (sod - start) / (end - start) if end > start else 0.0
    if kind == "home":
        base = home_work[0]
    elif kind == "work":
        base = home_work[-1]
    elif kind == "gather":
        base = GATHER_ANCHOR
    elif kind == "walk_home_to_work":
        return point_along(home_work, t)
    elif kind == "walk_work_to_home":
        return point_along(list(reversed(home_work)), t)
    elif kind == "walk_work_to_gather":
        return point_along(work_gather, t)
    else:  # walk_gather_to_work
        return point_along(list(reversed(work_gather)), t)
    ox, oy = _pacing(int(npc["id"]), day, sod)
    return base[0] + ox, base[1] + oy


def poi_label_for(npc: dict, now_game: float) -> str:
    kind, _, _ = phase_at(now_game % GAME_SEC_PER_DAY)
    if kind == "home":
        return str(npc.get("home") or "家中")
    if kind == "work":
        return str(npc.get("work") or "外头")
    if "gather" in kind:
        return "镇口"
    return "路上"


def is_idle(now_game: float, paused_until: float | None, now_real: float) -> bool:
    kind, _, _ = phase_at(now_game % GAME_SEC_PER_DAY)
    return kind in ("work", "gather") and not (paused_until and paused_until > now_real)
