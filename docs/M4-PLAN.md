# M4 反思与传播 — 实施拆解

> 本文件是 M4 的唯一执行入口。数值、prompt、算法、API 形状、文件落点、schema 迁移、验证步骤全部内联；实施时不要回头翻 DESIGN 与 M3 代码做考古。仅当实现中发现设计必须变更时，才回改 `docs/DESIGN.md` 并同步本文件（AGENTS.md「实施顺序」）。
>
> **验收标准（DESIGN §13）：隔天 NPC 主动提起你昨天的事。**
> 拆成可观察的四件事：① 后台反思产出 `kind=reflection` 记忆；② `relationship` 出现滚动摘要并进入对话 prompt；③ 两个 NPC 在镇口交换 `kind=gossip` 记忆；④ 好感≥70 的 NPC 在玩家靠近时主动冒泡，且内容能引用旧记忆。

## 0. 现状复用清单（不要重做）

| 已有能力 | 位置 | M4 用法 |
|---|---|---|
| 双库记忆写入（SQLite SSOT → Chroma） | `server/app/agent/memory.py` | 只加 `source_memory_id`，其余照用 |
| RAG top20→三因子重排 top6 | `server/app/agent/rag.py` | 搭话直接复用；反思/关系/八卦走 SQLite（Chroma 不能排序/过滤列表） |
| 花名册、好感、送礼、拜师 | `server/app/agent/npc.py` | 加 `ids()`、`names_in()`，修 `list_for_client` 的 POI bug |
| 对话 + 窗口批总结 | `agent/dialogue.py` + `agent/reflect.py` | **把 `reflect.py` 改名为 `summary.py`**，腾出 reflection 名字 |
| 游戏时钟（偏移语义） | `server/app/game/clock.py` | 日程/记忆/八卦日界用游戏时间；冷却/反思间隔用现实时间 |
| 全局 LLM 信号量 | `server/app/llm.py` | 换成优先级限流器 |
| `npc_state` 已有字段 | `server/app/db.py` | `last_reflection_at / mem_since_reflection / paused_until / last_greet_real` 全部启用，不新增状态列 |
| 关键路径冒烟 | `server/tests/smoke.py` | 扩展，仍是改完必须全绿的底线 |
| Godot NPC 静态标记 | `xia-ai-town/scripts/town_world.gd` | 改为按日程推算位置 + 头顶气泡 |

## 1. 六个必须先拍板的设计缺口（本文件已裁定，实施时照此）

### 1.1 `agent/reflect.py` 改名 `agent/summary.py`

现状：`reflect.py` 做的是 DESIGN §6.6 的「对话窗口批总结」，不是 §6.7 的反思。M4 要新增真正的 reflection，名字必须让位。

改动：

- `git mv server/app/agent/reflect.py server/app/agent/summary.py`。
- 函数名保持 `bump()`（调用点多），模块 docstring 里把「反思」改为「批总结」。
- 更新 import：`dialogue.py`、`npc.py`、`main.py` 中的 `reflect as reflect_mod` → `summary as summary_mod`。
- `game/battle.py`、`game/shop.py` 的注入属性 `reflect_bump` → `summary_bump`；`main.py` 装配处同步。
- DESIGN §6.6 中「触发一次"反思"」改为「触发一次批总结」，避免与 §6.7 冲突。

### 1.2 八卦需要共同场所：午间镇口聚集

DESIGN §6.10 只有「家/工作地」两段，而 7 个 NPC 的工作 POI 各不相同，白天永远配不出「同一 POI」的一对，§6.9 会永远扫不到人。

裁定：沿用 DESIGN §4 已写明的「镇口（八卦聚集点）」，在日程中插入**午间聚集段**：

| 游戏时段 | 行为 |
|---|---|
| 00:00–06:30 | 在家 |
| 06:30–07:00 | 家 → 工作地 |
| 07:00–11:00 | 工作地 |
| 11:00–11:10 | 工作地 → 镇口 |
| **11:10–13:50** | **镇口聚集（可八卦）** |
| 13:50–14:00 | 镇口 → 工作地 |
| 14:00–19:00 | 工作地 |
| 19:00–19:30 | 工作地 → 家 |
| 19:30–24:00 | 在家 |

聚集窗口 9600 游戏秒 = 160 现实秒（`TIME_SCALE=60`），大于八卦扫描周期 120 现实秒，保证每个游戏日至少扫到一次。默认每对每天最多一次、每次扫描最多 2 对，避免背景 LLM 把队列打满。

### 1.3 八卦去重需要来源溯源 `memory.source_memory_id`

「对方不知道」无法只靠参与者判断（A 听 B 说过、又听 C 说过同一件事）。给 `memory` 加一列可空 `source_memory_id`，传播链始终指向**根记忆 id**：

- 原始 event/对话总结：`NULL`。
- B 听 A 说后写的 gossip：`source_memory_id = root`（若 A 的记忆本身是 gossip，则取它的 `source_memory_id`，否则取 A 记忆的 `id`）。
- 选材时排除：B 已有 `source_memory_id = root` 的记忆；B 的名字出现在原记忆 `participants` 里（亲历者不需要别人转述）。

同场独立目击产生的两条不同 id 记忆无法互相识别，这是已知局限，不额外处理。

### 1.4 关系摘要的触发用现实时间字段

`memory.ts_game` 是游戏时间，`relationship.updated_at` 是现实时间，不能直接比较。统一口径：

- `relationship.updated_at` 存 `time.time()`；
- 统计「新增交互/反思提及」一律用 `memory.created_at_real >= COALESCE(updated_at, 0)`；
- 参与者过滤在 Python 侧做（`json.loads(participants_json)`），不依赖 SQLite JSON1，避免版本差异。

### 1.5 用优先级限流器替换裸 `Semaphore`

AGENTS #7 要求 对话 > 搭话 > 关系摘要 > 反思 > 八卦。`asyncio.Semaphore` 是 FIFO，后台任务会堵住对话。改用 `PriorityLimiter`（§3.1），`chat()` 增加 `priority` 参数；`embed` 恒为最高优先级 0。

### 1.6 日程数据只有服务器一份，算法两端各实现

服务端在 `game/schedule.py` 定义相位、锚点、折线路径、踱步参数，通过 `GET /api/npcs/state` 原样下发；Python 与 GDScript 各实现一次相同的纯函数。**客户端不轮询位置**，只轮询静态参数与 `paused_until`。任何数值调整只改服务端常量并同步本文件，不在 GDScript 里另写一套。

## 2. 配置、schema 与常量

### 2.1 `server/app/config.py` 新增字段

```python
# M4 background agents / schedule
scheduler_enabled: bool = True          # 测试与 Godot 冒烟置 false
scheduler_tick_real: int = 60           # 反思巡检周期（现实秒）
reflection_take_n: int = 30             # 反思取最近 N 条记忆
reflection_min_memories: int = 3        # 少于 3 条不反思
max_reflection_delta: int = 2           # 反思修正好感上限 ±2
relationship_update_events: int = 5     # 5 次交互触发关系摘要
relationship_mention_reflections: int = 3  # 反思提及 3 次触发
relationship_max_chars: int = 100       # 摘要长度
gossip_importance_min: int = 5          # 可传播记忆最低重要度
gossip_rewrite_importance: int = 7      # ≥7 才用 LLM 改写
gossip_max_pairs_per_scan: int = 2      # 每次扫描最多八卦对数
npc_sense_radius: float = 48.0          # 主动搭话感应半径（3 tile）
```

`.env` 键名由 pydantic-settings 自动大写匹配（如 `SCHEDULER_ENABLED=false`），无需改 `.env` 样例；DESIGN §14 同步补表。

### 2.2 `server/app/db.py` 迁移

`SCHEMA` 的 `memory` 表直接加列（新库生效）：

```sql
source_memory_id INTEGER REFERENCES memory(id),
```

`connect()` 的幂等迁移追加（旧库生效）：

```python
mem_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory)")}
if "source_memory_id" not in mem_cols:
    conn.execute("ALTER TABLE memory ADD COLUMN source_memory_id INTEGER")
conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_memory_source"
    " ON memory(npc_id, source_memory_id)"
)
```

不新增表：八卦「每对每天一次」用 kv 键 `gossip_pair:{小id}:{大id}` 存游戏日序号。`Database.reset()` 逐表 DELETE 已覆盖新列，无需改。

### 2.3 日程常量（`server/app/game/schedule.py`，唯一真源）

```python
GAME_SEC_PER_DAY = 86400
GATHER_ANCHOR = (768.0, 440.0)          # 镇口，竖路中央，避开井与墙
PACING = {"slot": 600.0, "fade": 120.0, "x": 8.0, "y": 6.0}

PHASES = (
    ("home",              0,     23400),
    ("walk_home_to_work", 23400, 25200),
    ("work",              25200, 39600),
    ("walk_work_to_gather", 39600, 40200),
    ("gather",            40200, 49800),
    ("walk_gather_to_work", 49800, 50400),
    ("work",              50400, 68400),
    ("walk_work_to_home", 68400, 70200),
    ("home",              70200, 86400),
)
```

锚点与折线（`slug -> (家→工作地, 工作地→镇口)`），全部避开实心建筑；折线是绕楼路线，不能改成直线：

```python
ROUTES = {
    "gu_changfeng": ([(144, 24), (240, 24), (240, 183), (144, 183)],
                     [(144, 183), (768, 183), (768, 440)]),
    "tie_wushuang": ([(500, 430), (384, 430), (384, 183), (368, 183)],
                     [(368, 183), (768, 183), (768, 440)]),
    "su_wanqing":   ([(592, 24), (688, 24), (688, 183), (592, 183)],
                     [(592, 183), (768, 183), (768, 440)]),
    "chen_bansong": ([(128, 214), (208, 214), (208, 375), (128, 375)],
                     [(128, 375), (128, 440), (768, 440)]),
    "wang_dazhu":   ([(304, 214), (384, 214), (384, 375), (304, 375)],
                     [(304, 375), (304, 440), (768, 440)]),
    "liu_xiaodie":  ([(620, 430), (566, 407)],
                     [(566, 407), (768, 440)]),
    "zhang_mantou": ([(1360, 444), (1300, 456), (1120, 456), (1120, 427), (752, 427)],
                     [(752, 427), (768, 440)]),
}
```

锚点自检记录（改数值前必须重新核对）：

- 工作锚点沿用 M3 的 NPC 站位：`(144,183) (368,183) (592,183) (128,375) (304,375) (566,407) (752,427)`，均在三派/店铺建筑 `rect.grow(32)` 交互区之外（门口区）。
- 家锚点都在建筑外侧可走格：道馆/小筑北侧 `y=24`，药铺/铁匠铺北侧 `y=214`，铁无双/柳小蝶客栈南侧 `y=430`，破庙南侧 `y=444`。
- 镇口 `(768,440)` 落在 x=48 的竖直土路（ROAD）上，避开 `(704,400,32,32)` 水井与 `y=480` 镇墙。
- 张馒头家→工作地**不能走直线**：直线会穿过水塘 `(1152,384,112,64)`（瓦片 72–78 列、24–27 行）。折线先沿 `y=456` 从 `x=1300` 绕到 `x=1120`（水塘南、镇墙北），再回到 `y=427` 西行。
- 树/崖只生成在第 34–58 行（`y≥544`），所有锚点不受影响。

踱步（确定性，两端可复现）：600 游戏秒一个槽，槽首 120 秒内在上一槽与当前槽偏移间平滑过渡。LCG 必须逐位照抄：

```python
def _lcg(npc_id: int, day: int, slot: int, axis: int) -> int:
    s = (npc_id * 1103515245 + day * 12345 + slot * 1013904223 + axis * 2654435761) & 0xFFFFFFFF
    return (s * 1664525 + 1013904223) & 0xFFFFFFFF

def _slot_offset(npc_id: int, day: int, slot: int) -> tuple[float, float]:
    sx = _lcg(npc_id, day, slot, 0)
    sy = _lcg(npc_id, day, slot, 1)
    x = ((sx >> 8) & 0xFFFF) / 65535.0 * 16.0 - 8.0
    y = ((sy >> 8) & 0xFFFF) / 65535.0 * 12.0 - 6.0
    return x, y
```

槽 0 的上一槽取当前槽（不做跨日回看），避免负 slot 的补码歧义。GDScript 侧 64 位整数乘以上述系数不会溢出，最后一步 `& 0xFFFFFFFF` 即可。

### 2.4 LLM 优先级映射（`server/app/llm.py`）

```python
PRIORITY = {
    "dialogue": 0, "dialogue_summary": 0, "embed": 0,
    "greet": 1, "relationship": 2, "reflection": 3, "gossip": 4,
}
DEFAULT_PRIORITY = 3
```

`dialogue_summary` 虽然名字像后台任务，但它发生在一次前台对话请求内，必须与对话同优先级。

## 3. 后端实现

### 3.1 `llm.py`：PriorityLimiter

在 `OllamaClient` 内把 `asyncio.Semaphore` 换成：

```python
import heapq
from contextlib import asynccontextmanager

class PriorityLimiter:
    """并发上限内的优先级队列；数值越小越先拿到槽位。"""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._seq = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []

    @asynccontextmanager
    async def slot(self, priority: int = DEFAULT_PRIORITY):
        await self._acquire(priority)
        try:
            yield
        finally:
            self._release()

    async def _acquire(self, priority: int) -> None:
        if self._active < self._limit and not self._waiters:
            self._active += 1
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, self._seq, fut))
        self._seq += 1
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self._release()  # 槽位已分配但调用方在返回前被取消
            raise

    def _release(self) -> None:
        self._active -= 1
        while self._active < self._limit and self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if fut.done():
                continue
            self._active += 1
            fut.set_result(True)
```

`chat()` 增加 `priority: int | None = None`：

```python
p = priority if priority is not None else PRIORITY.get(purpose, DEFAULT_PRIORITY)
async with self._limiter.slot(p):
    ...
```

`embed()` 用 `slot(0)`。`llm_log` 已记录 purpose，无需新增列；`trace` 的 generation metadata 里带 `priority` 便于回放。

### 3.2 新文件 `server/app/game/schedule.py`

职责：相位、锚点、折线、踱步与 POI 文案。纯函数，不碰 DB、不 import agent，避免环。

```python
from __future__ import annotations

GAME_SEC_PER_DAY = 86400
GATHER_ANCHOR = (768.0, 440.0)
PACING = {"slot": 600.0, "fade": 120.0, "x": 8.0, "y": 6.0}
PHASES = (...)          # 见 §2.3
ROUTES = {...}          # 见 §2.3


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


def _lcg(npc_id: int, day: int, slot: int, axis: int) -> int: ...
def _slot_offset(npc_id: int, day: int, slot: int) -> tuple[float, float]: ...


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
```

服务端所有取当前位置/当前地点的代码统一走这两个函数；不要再读 `n["work"]` 当「此刻所在」。

### 3.3 新文件 `server/app/agent/reflection.py`

触发与产出（DESIGN §6.7）：

```python
async def run_once(db, store, npc_id: int, *, force: bool = False) -> bool:
```

1. 取 `npc_mod.by_id`、`player_mod.get` 与 `npc_state`（`last_reflection_at, mem_since_reflection`）。NPC 或玩家不存在返回 False（无玩家的空档不产出反思）。
2. 冷却：非 force 且 `last_reflection_at` 非空且 `time.time() - last_reflection_at < settings.scheduler_tick_real * 0.75` → False（失败退避也靠它）。乘 0.75 是为了避开循环调度抖动：若卡在 60.0s 整，`asyncio.sleep(60)` 实际可能 59.99s，严格的 `< tick` 会让该 NPC 每两拍才反思一次。
3. 触发条件（非 force）：
   - `mem_since_reflection >= settings.reflect_mem_threshold`；或
   - `mem_since_reflection > 0` 且 `last_reflection_at` 非空且已过 `settings.reflect_interval_real`。
4. 取记忆（SQLite，按游戏时间倒序）：
   ```sql
   SELECT id, content, kind, importance, participants_json
   FROM memory WHERE npc_id = ? ORDER BY ts_game DESC LIMIT ?
   ```
   条数 < `reflection_min_memories` → False。
5. 组装 prompt（第一人称身份，不出现「记忆/重要度/系统」等词）：

   ```text
   你是{name}。{persona}你说话{speech_style}。
   以下是你近来的经历与见闻（由近到远）：
   - {content}
   - ...
   请以你的视角归纳 1~3 条更高层的洞察：这些人这些事说明了什么、你对{player_name}的观感有何变化。
   每条一句话，第三人称叙述，不超过50字；只依据上述记录，不要虚构。
   若这些经历让你对{player_name}的观感明显改变，另给 -2~+2 的整数；否则给 0。
   仅输出JSON：{"insights":[{"content":"..."}],"affinity_delta":0}
   ```

6. 调用 `store.llm.chat("reflection", msgs, json_format=True, temperature=0.7)`，解析失败重试一次 `temperature=0.3`。
7. 兜底（必须实现）：两次都失败时，取最近一条记忆生成单条洞察 `{"content": f"近来常想起：{content}", "affinity_delta": 0}`。**不跳过**，保证后台循环不会无限重试同一批记忆。
8. 过滤：每条 insight 去空白、截断 80 字；最多 3 条；空则走兜底。
9. `participants = npc_mod.names_in(" ".join(insight 内容), player_name)`。
10. 逐条 `await store.add_memory(db, npc_id, content=..., kind="reflection", participants=..., poi=schedule.poi_label_for(n, now), importance=8)`。**importance 固定 8，不采信模型给的数值**（4B 恒偏高）。
11. 好感修正：仅当玩家名字出现在任一 insight 内容里时才应用；`delta = clamp(模型值, -max_reflection_delta, +max_reflection_delta)`，走 `npc_mod.apply_delta`。
12. 复位计数（必须用快照扣减，不能直接清零，否则会吞掉并发写入）：
    ```sql
    UPDATE npc_state
       SET mem_since_reflection = MAX(0, mem_since_reflection - ?),
           last_reflection_at = ?
     WHERE npc_id = ?
    ```
    第一个参数是第 3 步读到的 `count` 快照，第二个是 `time.time()`。
13. 调 `relationship.maybe_update(db, store, npc_id, "player", player_name)`（失败吞掉，不影响反思结果）。
14. 返回 True。

解析器写法与 `summary.py` 一致：剥 ```json 围栏、`json.loads`、校验 `insights` 为非空 list。

### 3.4 新文件 `server/app/agent/relationship.py`

```python
async def summary_for(db, npc_id: int, target_kind: str, target_id: str) -> str
async def maybe_update(db, store, npc_id: int, target_kind: str, target_id: str,
                       *, force: bool = False) -> bool
```

`summary_for` 供对话 prompt 读取；无行返回 `""`。

`maybe_update`：

1. 读旧摘要与 `updated_at`（无行时 `since = 0.0`）。
2. 取该 NPC 自 `since` 起的记忆：
   ```sql
   SELECT id, content, kind, importance, participants_json, created_at_real
   FROM memory
   WHERE npc_id = ? AND created_at_real >= ?
     AND kind IN ('event','gossip','reflection')
   ORDER BY ts_game DESC LIMIT 200
   ```
3. Python 过滤 `target_id in json.loads(participants_json)`，分成：
   - `events`：`kind in ("event","gossip")`
   - `reflections`：`kind == "reflection"`
4. 非 force 时，`len(events) < settings.relationship_update_events` 且 `len(reflections) < settings.relationship_mention_reflections` → False。
5. 素材取 events 最近 20 条 + reflections 最近 10 条；都没有 → False。
6. Prompt：

   ```text
   你是{name}。{persona}
   你对{target}的旧印象：{old_summary 或 "（尚无）"}
   近来与{target}有关的事：
   - {content}
   - ...
   请把旧印象与这些事合成一段不超过100字的印象（你的视角，第三人称，不虚构）。
   仅输出JSON：{"summary":"..."}
   ```

   purpose=`relationship`，温度 0.7 → 0.3。
7. 兜底（必须实现）：`(old_summary + "；" + "；".join(e["content"] for e in events[:2]))[-100:]`；去掉换行，空则用最后一条事件内容。
8. 写入：

   ```sql
   INSERT INTO relationship(npc_id, target_kind, target_id, summary, updated_at)
   VALUES(?,?,?,?,?)
   ON CONFLICT(npc_id, target_kind, target_id)
   DO UPDATE SET summary = excluded.summary, updated_at = excluded.updated_at
   ```

   长度按字符截断到 `settings.relationship_max_chars`。

调用点：
- 反思完成后：`("player", player_name)`。
- 每次八卦成功后：双方各更新一次 `("npc", 对方 name)`，失败吞掉。**target_id 一律用名字**（玩家用玩家名、NPC 用 NPC 名），因为素材过滤是拿 `target_id` 去比对 `participants_json` 里的名字；传数字 id 会永远匹配不到。

### 3.5 新文件 `server/app/agent/gossip.py`

```python
async def scan(db, store) -> int
async def gossip_between(db, store, a_id: int, b_id: int, *, poi: str | None = None) -> bool
```

`scan`：

1. `now_game = store.clock.now()`，`now_real = time.time()`。
2. 遍历花名册：`schedule.is_idle(now_game, paused_until, now_real)` 为真者参与；计算 `pos = schedule.position_for(npc, now_game)` 与 `poi = schedule.poi_label_for(...)`。
3. 两两配对：`poi` 相同且距离 ≤ 64px。
4. 配对数超过 `gossip_max_pairs_per_scan` 时轮转，避免永远只喂低 id：
   ```python
   rotate = int(time.time() // settings.gossip_interval_real) % len(pairs)
   pairs = pairs[rotate:] + pairs[:rotate]
   ```
5. 对每对：kv `gossip_pair:{min}:{max}` 等于当前游戏日则跳过；`gossip_between` 成功才写 kv（先 gossip 后写 cap，失败下一轮可重试）。
6. 返回成功对数。

`gossip_between`：

1. `a = npc_mod.by_id(a_id)`、`b = npc_mod.by_id(b_id)`；`poi = poi or schedule.poi_label_for(a, clock.now())`。
2. 候选查询（A 的、可传播的）：
   ```sql
   SELECT id, content, participants_json, importance, source_memory_id
   FROM memory
   WHERE npc_id = ? AND kind IN ('event','gossip') AND importance >= ?
   ORDER BY importance DESC, ts_game DESC LIMIT 50
   ```
3. 过滤（Python）：
   - `b["name"] in participants` → 亲历者，跳过；
   - `root = row["source_memory_id"] or row["id"]` 已在 B 的已知集合 → 跳过。已知集合：
     ```sql
     SELECT source_memory_id FROM memory
     WHERE npc_id = ? AND source_memory_id IS NOT NULL
     ```
   - 同一 root 只保留第一条（按上面的排序即为最重要/最新）。
4. 没有候选 → False。
5. 转述文本：
   - `importance < settings.gossip_rewrite_importance`：直接用原 content。
   - 否则调 LLM（purpose=`gossip`）：
     ```text
     你是{a.name}。{a.persona}你说话{a.speech_style}。
     你在{poi}遇见{b.name}，想把下面这件事说给他听：
     {content}
     用你的口吻转述成一句不超过60字的话，保留事实，不要添加新内容。
     仅输出JSON：{"line":"..."}
     ```
     失败重试 0.3，再失败用原 content。
6. 写入两条记忆（importance = `max(1, 原 importance - 1)`）：
   - B：`content=f"{a.name}与我闲聊：{line}"`，`participants = 原参与人 + [a.name]`，`source_memory_id=root`
   - A：`content=f"我将此事说与{b.name}听：{line}"`，`participants = 原参与人 + [b.name]`，`source_memory_id=root`
   - 两条都 `kind="gossip"`、`poi=poi`；`add_memory` 会各自累加 `mem_since_reflection`（正确，八卦是新记忆）。
7. 调双方 `relationship.maybe_update(..., "npc", 另一方的 name)`，异常吞掉。
8. 返回 True。

### 3.6 新文件 `server/app/agent/greet.py`

```python
_locks: dict[int, asyncio.Lock] = {}

async def greet(db, store, npc_id: int) -> dict
```

流程（全程在 `_locks.setdefault(npc_id, asyncio.Lock())` 内，防客户端连点）：

1. `n = npc_mod.by_id`；无 → `LookupError`。
2. `p = player_mod.get`；无 → `ValueError("无存档")`（路由转 409）。
3. `aff = npc_mod.affinity`；`aff < settings.greet_threshold` → `{"greet": False, "reason": "affinity", "affinity": aff}`。
4. 冷却：`last_greet_real` 非空且 `time.time() - last < settings.greet_cooldown_real` → `{"greet": False, "reason": "cooldown", "affinity": aff}`。
5. `paused_until > time.time()` → `{"greet": False, "reason": "paused", "affinity": aff}`。
6. RAG 取记忆：`rag_mod.retrieve(db, store, npc_id, f"{p['name']}近来", settings=settings)`；异常则空列表（搭话不能因为检索失败而不搭）。
7. Prompt：

   ```text
   你是{name}。{persona}你说话{speech_style}。
   {交情档位，复用 dialogue._warmth}
   你记得的一些事：
   {mem_lines 或 （暂无）}
   此刻{时辰}，你正在{place}，看见{player}走近。
   以你的口吻主动招呼他一句（≤40字），可顺带提起一件你记得的事，不要说套话。
   仅输出JSON：{"reply":"..."}
   ```

   `place = schedule.poi_label_for(n, clock.now())`；`时辰` 直接复用 `dialogue.shichen(clock)`（`from .dialogue import shichen`，无环：dialogue 不 import greet）。温度 0.7 → 0.3。
8. 兜底：
   ```python
   if aff >= 85:
       reply = f"{n['name']}远远便朝你招手：“{p['name']}，可算见着你了。”"
   else:
       reply = f"{n['name']}朝你略一颔首：“{p['name']}，别来无恙。”"
   ```
9. 成功后 `UPDATE npc_state SET last_greet_real = ? WHERE npc_id = ?`（`time.time()`）。
10. 返回 `{"greet": True, "reply": reply, "affinity": aff, "npc_id": npc_id}`。

搭话不改好感——避免站在 NPC 旁边反复进出刷好感。冷却只在成功搭话后消耗。

### 3.7 新文件 `server/app/agent/scheduler.py`

```python
async def start(db, store) -> list[asyncio.Task]
async def stop(tasks: list[asyncio.Task]) -> None
```

- `settings.scheduler_enabled` 为 false 时 `start` 返回 `[]`（测试/Godot 冒烟用）。
- 两个任务（都**先 sleep 再工作**，避免启动瞬间与 reconcile/首个请求抢 LLM）：
  - `_reflect_loop`：每 `scheduler_tick_real` 秒，对 `npc_mod.ids()` 逐个 `reflection.run_once`。
  - `_gossip_loop`：每 `gossip_interval_real` 秒 `gossip.scan`。
- 循环体必须自行兜异常，避免任务静默死掉：
  ```python
  except asyncio.CancelledError:
      raise
  except Exception as e:
      print(f"[scheduler] reflection tick failed: {e!r}")
  ```
  打印文案不要出现 `ERROR:`/`Traceback`，否则 Godot 冒烟的 `ERROR_LOG` 正则会误判。
- `stop`：逐个 `cancel()`，`await asyncio.gather(*tasks, return_exceptions=True)`。

`main.py` lifespan 装配顺序：

```python
mem_store = memory_mod.MemoryStore(...)
await mem_store.reconcile(db)
battle_mod.mem_store = mem_store
shop_mod.mem_store = mem_store
battle_mod.summary_bump = summary_mod.bump
shop_mod.summary_bump = summary_mod.bump
scheduler_tasks = await scheduler_mod.start(db, mem_store)
yield
await scheduler_mod.stop(scheduler_tasks)   # 必须先于 close mem_store/llm/db
```

### 3.8 现有模块的具体改动

**`agent/memory.py`**

- `add_memory(..., source_memory_id: int | None = None)`；INSERT 增加该列。
- 末尾计数改为：
  ```python
  if kind != "reflection":
      await db.execute(
          "UPDATE npc_state SET mem_since_reflection = mem_since_reflection + 1"
          " WHERE npc_id = ?", (npc_id,))
  ```
- `reconcile` 的 SELECT 不必取 `source_memory_id`（Chroma 元数据不需要它）。

**`agent/dialogue.py`**

- import 改为 `summary as summary_mod`、`relationship as relationship_mod`、`game.schedule as schedule_mod`。
- `place = schedule_mod.poi_label_for(n, mem_store.clock.now())`。
- 并行取三类上下文（M3 是串行，M4 顺手改）：
  ```python
  memories, turns, impression = await asyncio.gather(
      rag_mod.retrieve(db, mem_store, npc_id, message, settings=settings),
      rag_mod.recent_turns(db, npc_id, n=settings.dialogue_window_size),
      relationship_mod.summary_for(db, npc_id, "player", p["name"]),
  )
  ```
- prompt 在「交情档位」后加一行；空摘要不输出该行：
  `你对{p['name']}的印象：{impression}`
- 进入 `talk` 校验完 message 后立刻暂停：
  ```sql
  UPDATE npc_state SET paused_until = ? WHERE npc_id = ?
  ```
  值 `time.time() + 300`（DESIGN §6.10 超时 5 现实分）。
- 结果 `end=true` 时清空 `paused_until`；在 `summary_mod.bump(...)` 之前清，确保总结失败也不影响恢复。
- 调用改名：`summary_mod.bump(...)`。

**`agent/npc.py`**

- `list_for_client` 修正 POI（当前实现把 `home_poi` 当 work，见 `SELECT n.home_poi AS work`）：
  ```python
  rows = await db.fetch_all(
      "SELECT n.id, n.name, n.role, n.work_poi, n.home_poi, ns.affinity, ns.paused_until"
      " FROM npc n JOIN npc_state ns ON ns.npc_id = n.id ORDER BY n.id")
  ```
  返回字段：`id, slug, name, role, affinity, poi(当前), work_poi, home_poi, paused_until`。
  `poi` 用 `schedule.poi_label_for({"home": r["home_poi"], "work": r["work_poi"]}, clock.now())`——**注意 `poi_label_for` 读的是 `home`/`work` 两个键，不是 `home_poi`/`work_poi`**；也可以直接取 `_npc_meta_by_id[r["id"]]`（它同时含两套键）。这里需要时钟，函数签名改为 `list_for_client(db, clock)`，`main.py` 同步传 `clock`。
- 新增：
  ```python
  def ids() -> list[int]:
      return sorted(_npc_meta_by_id)

  def names_in(text: str, player_name: str | None = None) -> list[str]:
      names = ([player_name] if player_name else []) + [n["name"] for n in NPC_ROSTER.values()]
      return [x for x in dict.fromkeys(names) if x and x in text]
  ```
- `gift` / `apprentice` 的记忆 `poi` 改用 `schedule.poi_label_for(n, mem_store.clock.now())`（无 mem_store 时退回 `n["work"]`）；`gift` 末尾 `summary_mod.bump(...)` 的 `place` 参数同步传同一个 poi（否则批总结出的记忆地点仍写工作地）。
- `reflect_mod` import 改名 `summary_mod`。

**`game/battle.py` / `game/shop.py`**

- 仅重命名注入属性 `reflect_bump` → `summary_bump`，行为不变。切磋记忆的 `poi="练武场"` 保持不变（战斗发生在练武场/野外，不按日程地点）。

**`main.py`**

- import：`summary`、`reflection`、`relationship`、`gossip`、`greet`、`scheduler`、`game.schedule`。
- lifespan 见 §3.7。
- 新路由见 §4；`/api/game/state` 的 `npcs` 传 `clock`。
- `/api/debug/npc/{id}` 扩展：
  - memory SELECT 增加 `source_memory_id`；
  - `reflection` 块增加 `last_reflection_real`、`seconds_since_reflection`、`paused_until`、`last_greet_real`；
  - 增加 `schedule`: `{"phase": ..., "poi": ..., "pos": [x, y], "paused": bool}`；
  - `relationship` 已全量返回，保留。

## 4. API 形状（同步回 DESIGN §9）

### 4.1 `GET /api/npcs/state`（新增）

不需要玩家存档。静态日程参数 + 当前暂停/搭话状态，客户端只拉它、不拉位置：

```json
{
  "clock": {"game_sec": 40210.5, "day": 2, "hour": 11, "minute": 10,
            "time_scale": 60, "real_ts": 1760000000.0},
  "sense_radius": 48.0,
  "greet_threshold": 70,
  "phases": [
    {"kind": "home", "start": 0, "end": 23400},
    {"kind": "walk_home_to_work", "start": 23400, "end": 25200}
  ],
  "pacing": {"slot": 600.0, "fade": 120.0, "x": 8.0, "y": 6.0},
  "gather_anchor": [768.0, 440.0],
  "npcs": [
    {
      "id": 1, "slug": "gu_changfeng", "name": "顾长风",
      "home": "道馆后屋", "work": "剑阳道馆",
      "home_anchor": [144.0, 24.0], "work_anchor": [144.0, 183.0],
      "home_to_work": [[144, 24], [240, 24], [240, 183], [144, 183]],
      "work_to_gather": [[144, 183], [768, 183], [768, 440]],
      "affinity": 25, "paused_until": null, "greet_ready": false,
      "pos": [146.2, 184.1], "poi": "剑阳道馆"
    }
  ]
}
```

`phases` 不能直接返回 tuple（FastAPI 会序列化成数组），路由里转成 `[{"kind": k, "start": s, "end": e} for k, s, e in schedule.PHASES]`；`pacing`、`gather_anchor` 同理。`pos`/`poi` 是**服务端按当前时刻算出的值**，只用于调试与降级显示；客户端正常帧仍本地推算，避免每 10 秒跳位置。

`greet_ready` 的计算与 `greet.greet()` 的判定保持一致：`affinity >= settings.greet_threshold and (last_greet_real is null or now - last_greet_real >= settings.greet_cooldown_real) and (paused_until is null or paused_until <= now)`。本路由不需要玩家存档，但需要 `npc_state` 的 `affinity/last_greet_real/paused_until` 三列。

### 4.2 `POST /api/npc/{id}/greet`（新增）

无请求体。空闲/冷却时不报错，HTTP 200 返回布尔，客户端据此决定是否冒泡：

```json
{"greet": false, "reason": "affinity", "affinity": 42}
{"greet": false, "reason": "cooldown", "affinity": 78}
{"greet": false, "reason": "paused", "affinity": 78}
{"greet": true, "reply": "别来无恙？上回你说要寻的那把剑，可有眉目？",
 "affinity": 78, "npc_id": 5}
```

`reason` 只用 `affinity|cooldown|paused`。NPC 不存在 404；无存档 409。

### 4.3 `GET /api/game/state` 的 `npcs`（修改）

保留现有字段，修正并新增：

```json
{"id": 1, "slug": "gu_changfeng", "name": "顾长风", "role": "master",
 "affinity": 25, "poi": "剑阳道馆", "work_poi": "剑阳道馆",
 "home_poi": "道馆后屋", "paused_until": null}
```

`poi` 是**当前**地点（由日程算），`work_poi`/`home_poi` 是锚点名。客户端人物录与对话副标题用 `poi`。

### 4.4 `GET /api/debug/npc/{id}`（扩展）

```json
{
  "npc": {"id": 5, "name": "王大柱", "role": "smith", "sect": null},
  "affinity": 78,
  "reflection": {"last_reflection_at": 1760000000.0, "seconds_since_reflection": 61.3,
                 "mem_since_reflection": 2, "interacts_since_summary": 1,
                 "paused_until": null, "last_greet_real": 1760000000.0},
  "schedule": {"phase": "gather", "poi": "镇口", "pos": [770.4, 438.1], "paused": false},
  "memories": [{"id": 42, "ts_game": 140000.0, "kind": "gossip", "importance": 7,
                "poi": "镇口", "source_memory_id": 31, "content": "..."}],
  "relationship": [{"npc_id": 5, "target_kind": "player", "target_id": "冒烟侠",
                    "summary": "此人常来走动，出手也大方。", "updated_at": 1760000000.0}]
}
```

## 5. Godot 客户端

### 5.1 `town_world.gd`：NPC 改为日程驱动

1. 把 `const NPCS` 改为默认样式表 + 运行时数组：
   ```gdscript
   const NPC_DEFAULTS := {
       "gu_changfeng": {"name": "顾长风", "robe": Color("647e83"), "pos": Vector2(144, 183)},
       "tie_wushuang": {"name": "铁无双", "robe": Color("8b7061"), "pos": Vector2(368, 183)},
       "su_wanqing":  {"name": "苏婉清", "robe": Color("7b8c83"), "pos": Vector2(592, 183)},
       "chen_bansong":{"name": "陈半松", "robe": Color("91a68b"), "pos": Vector2(128, 375)},
       "wang_dazhu":  {"name": "王大柱", "robe": Color("93725a"), "pos": Vector2(304, 375)},
       "liu_xiaodie": {"name": "柳小蝶", "robe": Color("b68b8b"), "pos": Vector2(566, 407)},
       "zhang_mantou":{"name": "张馒头", "robe": Color("858578"), "pos": Vector2(752, 427)},
   }
   var npcs: Array[Dictionary] = []
   var schedule: Dictionary = {}
   var sense_radius: float = 48.0
   ```
   `_ready()` 里先用 `NPC_DEFAULTS` 填出一份，保证断网也能画。**每个运行时条目必须带 `slug` 键**（遍历 `NPC_DEFAULTS` 的 key 写入，如 `entry["slug"] = key`），因为 `nearby()` 的 `id` 和 `apply_state` 的合并都靠它；默认条目没有 `npc_id`、没有折线，位置为工作锚点。

2. `apply_state(state: Dictionary)`：
   - 保存 `schedule = state`、`sense_radius`；
   - 按 `slug` 合并服务端 `npcs`，写入 `npc_id`、`poi`、`paused_until`、`greet_ready`，颜色/名字沿用 defaults；服务端的 `pos` 只存成 `server_pos` 作降级/初始值，**不要覆盖运行时 `pos`**，否则每 10 秒刷新会闪一下服务端坐标——有折线的条目每帧都由 `tick_schedule` 重算；
   - 把 `home_to_work`/`work_to_gather` 的 JSON 数组转成 `Array[Vector2]` 缓存在条目里（`home_to_work_pts` / `work_to_gather_pts`），`gather_anchor` 转成 `Vector2` 存 `gather_anchor_pos`，避免每帧转换；
   - 折线为空的条目直接跳过合并，保持 defaults，防止 `[0]` 越界。

3. `tick_schedule(now_game: float, freeze_slug: String)`：遍历 `npcs`，`slug == freeze_slug` 的条目跳过（对话冻结）；**没有 `home_to_work_pts` 的条目也跳过**（`_ready()` 的默认项、离线状态、服务端旧响应），否则下面 `home_work[0]` 会越界；其余 `entry["pos"] = schedule_position(entry, now_game)`。

4. 纯函数照 §3.2 一比一实现：
   ```gdscript
   func schedule_position(entry: Dictionary, now_game: float) -> Vector2:
       var sod: float = fposmod(now_game, 86400.0)
       var day: int = int(now_game / 86400.0)
       var phase: Dictionary = _phase_at(sod)
       var kind: String = str(phase.get("kind", "home"))
       var start: float = float(phase.get("start", 0.0))
       var end: float = float(phase.get("end", 86400.0))
       var t: float = (sod - start) / (end - start) if end > start else 0.0
       var home_work: Array = entry.get("home_to_work_pts", [])
       var work_gather: Array = entry.get("work_to_gather_pts", [])
       var nid: int = int(entry.get("npc_id", 0))
       match kind:
           "home":
               return home_work[0] + _pacing(nid, day, sod)
           "work":
               return home_work[-1] + _pacing(nid, day, sod)
           "gather":
               return entry.get("gather_anchor_pos", Vector2(768, 440)) + _pacing(nid, day, sod)
           "walk_home_to_work":
               return _point_along(home_work, t)
           "walk_work_to_home":
               return _point_along(_reversed(home_work), t)
           "walk_work_to_gather":
               return _point_along(work_gather, t)
           _:
               return _point_along(_reversed(work_gather), t)
   ```

   `_phase_at` 读服务端下发的 `phases`；没有 state 时退回固定 home 段（配合第 3 条的跳过，默认 NPC 保持静止）：
   ```gdscript
   func _phase_at(sod: float) -> Dictionary:
       for p in schedule.get("phases", []):
           if float(p["start"]) <= sod and sod < float(p["end"]):
               return p
       return {"kind": "home", "start": 70200.0, "end": 86400.0}
   ```

   `_lcg` 逐位照抄；注意 GDScript 的 `>>` 对正数是逻辑右移（值已 `& 0xFFFFFFFF`）：
   ```gdscript
   func _lcg(npc_id: int, day: int, slot: int, axis: int) -> int:
       var s: int = (npc_id * 1103515245 + day * 12345 + slot * 1013904223 + axis * 2654435761) & 0xFFFFFFFF
       return (s * 1664525 + 1013904223) & 0xFFFFFFFF
   ```

   `_slot_offset`、`_pacing` 同样照抄 §2.3 的 Python 版；`_pacing` 用 `schedule.get("pacing", {...})` 的四个值；槽 0 的 prev 取当前槽；`smoothstep` 用 `t*t*(3-2t)`。折线辅助函数：
   ```gdscript
   func _reversed(points: Array) -> Array:
       var out: Array = points.duplicate()
       out.reverse()
       return out

   func _point_along(points: Array, t: float) -> Vector2:
       if points.is_empty():
           return Vector2(768, 440)
       if points.size() == 1:
           return points[0]
       var total: float = 0.0
       for i in range(points.size() - 1):
           total += points[i].distance_to(points[i + 1])
       if total <= 0.0:
           return points[0]
       var d: float = clampf(t, 0.0, 1.0) * total
       for i in range(points.size() - 1):
           var seg: float = points[i].distance_to(points[i + 1])
           if d <= seg or i == points.size() - 2:
               var r: float = d / seg if seg > 0.0 else 0.0
               return points[i].lerp(points[i + 1], r)
           d -= seg
       return points[-1]
   ```
   `apply_state` 里的 `home_to_work_pts` 必须是 `Array[Vector2]`（逐项 `Vector2(float(p[0]), float(p[1]))`），否则 `points[i].distance_to` 会在运行时报错。

5. `nearby()` 改用 `npcs` 的 `pos`（逻辑不变：建筑优先、NPC 距离 ≤38）；NPC 分支返回 `{"id": entry["slug"], "name": entry["name"], "kind": "npc"}`，`id` 必须是 slug 而不是数字 `npc_id`——`main.interact()` 用它调 `open_dialogue()`，后者按 slug 在 `people_npcs` 里找人。

6. 气泡：
   ```gdscript
   func show_bubble(slug: String, text: String, seconds: float = 6.0) -> void
   ```
   给条目写 `bubble` 与 `bubble_until`（`Time.get_ticks_msec()/1000.0 + seconds`）。`_draw()` 的 `"npc"` 分支在名字上方画一个纸色小牌，最多两行、每行约 14 个汉字；文本为空或过期不画。不要新增大图资源。

### 5.2 `main.gd`

1. 新变量：`dialogue_slug: String = ""`、`npc_state: Dictionary = {}`、`greet_inside: Dictionary = {}`、`greet_busy: Dictionary = {}`。
2. `refresh()` 在 `/api/game/state` 成功后追加：
   ```gdscript
   var detail: Dictionary = await api.request("/api/npcs/state")
   if detail.ok:
       npc_state = detail.data
       world.apply_state(detail.data)
   ```
   `people_npcs` 仍来自 `game/state`（好感/角色）；两者按 slug 对应。
3. `_process` 里算出本地 `sec` 后：
   ```gdscript
   world.tick_schedule(sec, dialogue_slug)
   _check_greet()
   ```
   注意现有 `sec` 声明在 `if not clock.is_empty():` 块内，要先提到块外（`var sec: float = 0.0`），或把这两行放进同一个块；没有时钟时不要 tick，保持默认位置。
   `_check_greet()` 只在 `modal == null and not busy and battle.is_empty()` 时执行：对每个有 `npc_id` 的条目做「进入 48px 半径」的边沿检测，`was == false && inside == true && greet_ready` 时发 `POST /api/npc/{id}/greet`。请求不置 `busy`（不阻塞走动），用 `greet_busy[nid]` 防重入；响应 `greet=true` 时 `world.show_bubble(slug, reply)`，可选择同时 `notify`。
4. `open_dialogue(slug)`：`dialogue_slug = slug`（`_new_modal` 之后设置，避免被清）；`world.frozen_slug` 可省略，统一由 `tick_schedule` 的 `freeze_slug` 参数控制。
5. `_remove_modal()`：清 `dialogue_slug = ""`，NPC 下一帧回到日程位置（单机下允许瞬移回日程点；不做插值）。
6. 人物录 `open_people()`：行尾显示当前 `poi`；`paused_until` 为未来时间时加「（正忙）」。
7. 删除/改写陈旧文案：`open_sect()` 里「NPC 好感度与对话式拜师将在后端开放后接入。」改为「也可走近掌门对话，交情到了再提拜师。」。
8. 对话副标题用 `npc.get("poi", ...)`，不再用 `work_poi`。

### 5.3 日程与暂停的职责边界

- 服务端 `paused_until` 只用于：① 八卦扫描排除对话中的 NPC；② 调试观察。**不驱动客户端位置**。
- 客户端只冻结「当前打开的对话框对应的 NPC」；关闭对话框立即恢复日程。单人单客户端下不会出现不一致。
- 服务端的 5 分钟暂停会让该 NPC 在玩家离开后一段时间内仍不参与八卦，这是可接受的行为，不做 leave 接口。
- 相位切换处，静止段的踱步偏移与行走段端点不带偏移，可能出现 ≤10px 的位置跳变；这是预期表现，不做插值补偿。

## 6. 文件落点总表

| 文件 | 动作 | 内容 |
|---|---|---|
| `server/app/agent/reflect.py` | 改名 | → `summary.py`，只改模块名/docstring |
| `server/app/agent/summary.py` | 改名后 | 对话窗口批总结（原样） |
| `server/app/agent/reflection.py` | 新建 | 反思触发、prompt、兜底、计数复位 |
| `server/app/agent/relationship.py` | 新建 | 关系摘要触发/压缩/upsert |
| `server/app/agent/gossip.py` | 新建 | 配对扫描、选材、转述、双写 |
| `server/app/agent/greet.py` | 新建 | 主动搭话判定、RAG、模板兜底 |
| `server/app/agent/scheduler.py` | 新建 | 反思/八卦后台循环生命周期 |
| `server/app/agent/memory.py` | 修改 | `source_memory_id`；reflection 不计数 |
| `server/app/agent/dialogue.py` | 修改 | 并行取上下文、关系印象、暂停/恢复、改名 |
| `server/app/agent/npc.py` | 修改 | POI bug、`ids()`、`names_in()`、当前地点 |
| `server/app/game/schedule.py` | 新建 | 相位/锚点/折线/踱步/POI |
| `server/app/game/battle.py` | 修改 | `reflect_bump` → `summary_bump` |
| `server/app/game/shop.py` | 修改 | 同上 |
| `server/app/llm.py` | 修改 | `PriorityLimiter`、`chat(priority=...)` |
| `server/app/config.py` | 修改 | §2.1 新字段 |
| `server/app/db.py` | 修改 | `source_memory_id` 迁移与索引 |
| `server/app/main.py` | 修改 | lifespan、greet/npcs 路由、debug、注入改名 |
| `server/tests/smoke.py` | 修改 | stub 新 purpose、M4 直调检查 |
| `xia-ai-town/tools/smoke_test.py` | 修改 | 后端 env 加 `SCHEDULER_ENABLED=false` |
| `xia-ai-town/scripts/town_world.gd` | 修改 | 动态 NPC、日程推算、气泡 |
| `xia-ai-town/scripts/main.gd` | 修改 | 拉 `/api/npcs/state`、搭话边沿、冻结、文案 |
| `docs/DESIGN.md` | 修改 | §6.6/§6.7–6.10/§8/§9/§10/§13/§14 同步 |
| `README.md` | 修改 | 里程碑与运行说明 |

## 7. 验证计划

### 7.1 确定性冒烟（必须全绿，优先写）

`server/tests/smoke.py` 顶部早于 `app.main` import 加：

```python
os.environ["SCHEDULER_ENABLED"] = "false"
```

同时给 `smoke.py` 补 `import json`。stub 签名加 `priority=None`，按 purpose 返回确定 JSON，并把 purpose 记进列表（stub 替换了 `chat` 后 `_log` 不会被调用，`llm_log` 表不会新增行，所以第 7 条检查断言的是 stub 调用记录）：

```python
CALLED: list[str] = []

async def fake_chat(purpose, messages, *, json_format=False, temperature=0.7,
                    metadata=None, priority=None):
    CALLED.append(purpose)
    if purpose == "dialogue":
    end = "告辞" in messages[-1]["content"]
    text = json.dumps({"reply": "阁下所问，某自有计较。", "affinity_delta": 2, "end": end},
                      ensure_ascii=False)
elif purpose == "dialogue_summary":
    text = '{"events":[{"content":"与玩家交谈甚欢。","importance":3}]}'
elif purpose == "reflection":
    # 必须带上玩家名，反思的好感修正才会落地，才能验证 clamp
    text = '{"insights":[{"content":"冒烟侠屡次来问剑，心性倒是坚定。"}],"affinity_delta":1}'
elif purpose == "relationship":
    text = '{"summary":"此人常来走动，是个有心人。"}'
elif purpose == "gossip":
    text = '{"line":"那小子昨日在后山崖办了黑旋风。"}'
elif purpose == "greet":
    text = '{"reply":"别来无恙？今日可有好酒？"}'
else:
    text = "{}"
```

新增检查（沿用 `check()`）：

1. `GET /api/npcs/state`：7 个 NPC；`phases` 9 段；每人的两条折线非空；`pos` 在 1600×960 内；`greet_threshold == settings.greet_threshold`（不要写死 70，本机 `.env` 可覆盖）。
2. 对话暂停：`POST /talk` 后查 debug，`paused_until` 非空；再发 `{"message":"告辞"}`（stub 返 `end=true`）后 `paused_until` 为空。
3. 反思：在 lifespan 块内用 `import app.main as main_mod` 取 `main_mod.db` / `main_mod.mem_store`（**不要 `from app.main import mem_store`**，那是 import 时的 `None`）；对 npc 5 连写 8 条 `add_memory(kind="event", participants=["冒烟侠"])`；`await reflection.run_once(main_mod.db, main_mod.mem_store, 5, force=True)`；debug 最近记忆出现 `kind="reflection"`、`importance=8`；调用前后 affinity 差为 +1（stub 固定给 1，验证名字命中与 clamp 落地）。
4. 关系摘要：第 3 步的反思末尾可能已按 5 次交互阈值顺带写过摘要；为确定命中 force 路径，先再写一条 `participants=["冒烟侠"]` 的 event，再 `await relationship.maybe_update(main_mod.db, main_mod.mem_store, 5, "player", "冒烟侠", force=True)`；debug 的 `relationship` 出现 `target_id="冒烟侠"` 且 summary 非空。
5. 八卦：给 npc 5 写一条 `importance=8`、`participants=["冒烟侠"]` 的 event；`await gossip.gossip_between(main_mod.db, main_mod.mem_store, 5, 6, poi="镇口")` 返回 True；debug npc 6 出现 `kind="gossip"` 且 `source_memory_id` 指向根记忆；再调一次返回 False（B 已知）。
6. 搭话：`await npc_mod.set_affinity(main_mod.db, 5, 75)`；`POST /api/npc/5/greet` → `greet=true` 且 reply 非空；立刻再调 → `greet=false, reason="cooldown"`。
7. `{"reflection","relationship","gossip","greet"} <= set(CALLED)`（stub 记录；真实 `llm_log` 由 `OllamaClient.chat` 写，stub 绕过它）。
8. 原有 M1–M3 检查全绿（尤其送礼写记忆、对话几何、战斗收敛）。

运行：`cd server && .venv/bin/python tests/smoke.py`，最后一行必须是 `SMOKE PASS`。

### 7.2 真实 Ollama 快时钟（人工观察）

```bash
cd server
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
RESET_ON_START=true TIME_SCALE=300 SCHEDULER_TICK_REAL=5 \
REFLECT_INTERVAL_REAL=20 GOSSIP_INTERVAL_REAL=5 \
.venv/bin/uvicorn app.main:app --port 8000
```

`TIME_SCALE=300` 时一个游戏日 4.8 现实分，镇口聚集窗口 32 现实秒，足够扫 6 次。步骤：

1. 新档，找王大柱连聊 8 轮（或聊 4 轮 + 送礼），等 5–10 秒。
2. `curl --noproxy '*' http://127.0.0.1:8000/api/debug/npc/5` 应看到 reflection 记忆与 `seconds_since_reflection` 更新。
3. 等到游戏时间 11:10（约 1 分钟），再查两个集市 NPC 的 debug，应出现 gossip；`source_memory_id` 非空。
4. 把好感临时拉到 75（测试专用）：
   `sqlite3 data/xia.db "UPDATE npc_state SET affinity=75 WHERE npc_id=5;"`，重启不需要。
5. 走近王大柱（≤48px），接口应返回 `greet=true`；Godot 头顶出现气泡。等冷却 300 秒（测试可把 `GREET_COOLDOWN_REAL=10`）后离开再靠近，应再次搭话。
6. 验收句：跨过一个游戏日后再靠近，搭话内容能提到前一天的对话/战斗/送礼（RAG 命中旧记忆）。

### 7.3 Godot 冒烟

`xia-ai-town/tools/smoke_test.py` 的 `env.update({...})` 里加 `"SCHEDULER_ENABLED": "false"`，避免后台反思调用真实 Ollama 拖慢/污染日志。然后：

```bash
server/.venv/bin/python xia-ai-town/tools/smoke_test.py
```

必须仍然全绿，且 backend.log 无 `ERROR: / Traceback`。日程只改变 NPC 坐标，冒烟的商店/门派交互点都在建筑 `grow(32)` 内、建筑判定优先，不受影响。

### 7.4 回归清单

- 断线重入战斗仍靠 `battle.state_json`，M4 不改战斗状态。
- 切磋/送礼/大额消费仍写记忆并累加 `mem_since_reflection`；改名后注入不能断。
- Chroma 行数仍与 SQLite 一致（新增列不参与向量，`reconcile` 不受影响）。
- 时刻语义：`ts_game`/日程/八卦日界用游戏时间；`last_reflection_at`/`last_greet_real`/`updated_at`/冷却用现实时间。混用即为 bug。

## 8. 明确不做（防 scope 蔓延）

- 不做 A* / 导航网格；日程只用固定折线。
- 不做 NPC 自由行动、自由结伴、多轮 NPC-NPC 对话；八卦只有「一对一句」。
- 不做 WebSocket / 推送；仍是 HTTP 轮询 + 客户端本地推算。
- 不做关系摘要 UI；只进对话 prompt 与 debug。
- 不做记忆删除/衰减/容量上限；反思不压缩旧记忆。
- 不做新的图片资源；气泡与人物继续原生绘制。
- 不在舆图（M）上标 NPC；人物录足够。
- 不做多存档/多玩家；`target_id` 用玩家名，重开新档后旧关系行自然失效。

## 9. Git 提交建议（一个任务一 commit，照 AGENTS.md 三段式）

1. `refactor: 批总结模块改名 summary，腾出反思命名`
2. `feat: LLM 调用优先级限流，后台任务不再挤占对话`
3. `feat: 服务端确定性 NPC 日程与 /api/npcs/state`
4. `feat: 后台反思与关系滚动摘要`
5. `feat: NPC 间八卦传播与来源溯源`
6. `feat: 主动搭话接口`
7. `feat: Godot 客户端 NPC 日程移动、搭话气泡与冻结`
8. `docs: 同步 M4 设计与项目说明`

每个 commit 前 `git status` 复核；`.env`、`server/data/*.db`、`server/data/chroma/` 不入库。

## 10. 文档同步清单（实现完成时逐项打勾）

- `docs/DESIGN.md`
  - §6.6：把窗口蒸馏的「反思」措辞改为「批总结」。
  - §6.7：补「最近 30 条、少于 3 条不反思、importance 固定 8、失败模板兜底、计数快照扣减」。
  - §6.8：补「用 `created_at_real` 统计、target_kind/target_id 取值、100 字截断」。
  - §6.9：补午间镇口聚集窗口、`source_memory_id` 溯源、kv 每日上限、每次最多 2 对。
  - §6.10：把时段表替换为实际 9 段（含午间聚集），补折线绕楼与踱步 LCG 参数。
  - §8：`memory.source_memory_id`、索引 `idx_memory_source`、kv 八卦键。
  - §9：新增 `/api/npcs/state`、`/api/npc/{id}/greet`；更新 `game/state` 与 debug 字段。
  - §10：NPC 不再静态，日程驱动 + 搭话气泡 + 对话冻结。
  - §13：M4 状态改为 ✅，验收记录写实际观察。
  - §14：补 §2.1 全部新参数。
- `AGENTS.md`：删掉「NPC 暂为静态标记，尚无可调用的 NPC 后端接口」；在「已知坑」补一条「测试/冒烟启动后端必须 `SCHEDULER_ENABLED=false`，否则后台 LLM 任务干扰」；目录结构补 `schedule.py` 与新 agent 文件。
- `README.md`：里程碑改为 M0–M4 完成；运行说明补 `/api/npcs/state` 与主动搭话。
- 本文件：若实现中调整了常量、prompt 或 API，先改这里再改代码，避免下一会话拿到过期指引。

