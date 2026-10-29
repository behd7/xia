# M6 执行计划：功法境界、NPC 同构与南风城

> 状态：**Phase A/B 已实施（2026-10-29），Phase C 待续**。本文件是执行拆解，不是已实现记录；实施按 Phase 分次提交，完成后回写状态。
>
> Phase A 交付：`game/arts.py`、`game/character.py`、角色卡 schema 迁移、同源/异源转换与软散功、属性克制、招式解锁/升级费、`/api/game/arts`、`/api/game/art/learn`、三馆学艺与武学面板。
>
> Phase B 交付：花名册角色卡预设、seed/reset、NPC 切磋统一 `character.stats` + 装备/招式、敌方规则 AI 使用已装备招式、debug/prompt 注入功法上下文。Phase C（南风城/多地图）尚未开始。
>
> 执行方式：一个 Phase 一个 commit，每个 Phase 独立可回退；每个 Phase 完成即跑 `server/tests/smoke.py` 与 `xia-ai-town/tools/smoke_test.py`（`SCHEDULER_ENABLED=false`），全绿才提交。
>
> 已拍板：
> - 单等级：人物等级 = 当前功法的修为等级，1~100；功法上限就是人物等级上限。
> - 软散功：换异属性功法时等级/经验归 1/0；四维、自由点、装备、背包、银两、已学功法图鉴保留；旧招式封存，新招式从 L1 练。
> - 身份与功法解耦：清溪镇三馆是武馆，可兼学；南风城两派是正式门派，同一时间只入一派。
> - 真传/外传：门主 50 级修 cap50 真传，弟子学 cap45/40 外传；真传靠位阶 3/好感 90/任务。
> - 属性克制：五行环 + 异变环，克制 ×1.20、被克 ×0.85；魔功留 `demon` 枚举，V1 不投放。
> - NPC 同构：NPC 与玩家共用角色卡；V1 用花名册预设，工具调用后续接。
> - 多地图：POI 键带 `map_id`；V1 NPC 不跨图。
> - 城主功法暂定（雷/光，cap55）。

## 0. 不可变约束

1. **服务器权威**：等级、经验、功法切换、散功、装备、背包只在服务器结算；客户端只发意图。
2. **SQLite 单写队列**：所有新表/迁移都走现有 `db.execute/fetch_*`；不新增连接，不绕过 `_call_locked` 的取消语义。
3. **NPC 位置零同步**：V1 NPC 不跨图；位置仍是日程表的确定性函数，不因角色卡同构新增轮询或自由路径。
4. **LLM 不阻塞玩法**：NPC 功法/等级只作为 prompt 文本上下文，不新增 LLM 调用、不新增 purpose；对话/送礼/切磋仍走现有链路。
5. **记忆链路不变**：`conversation` / 中期内存 / `longterm_memory` / Chroma 规则不动；`memory` 只加 `map_id` 列，Chroma 索引范围仍是 `memory` 的 event/reflection/gossip。
6. **旧档可迁移**：分阶段迁移，旧表在新链路验证前保留只读；`server/tests/smoke.py` 覆盖迁移与新局重置。
7. **SCHEMA 顺序**：先 `CREATE TABLE IF NOT EXISTS`，再 `ALTER TABLE` 补列，最后建引用新列的索引（AGENTS.md 已知坑）。
8. **不写死模型名**：新配置只放数值/开关；不新增依赖。
9. **测试/冒烟必须 `SCHEDULER_ENABLED=false`**，避免后台反思/心事调用真实 Ollama。

## 1. 变更地图

| 文件 | 变更 | Phase |
|---|---|---|
| `server/app/game/arts.py` | 新增：属性枚举/相克表/功法目录/上限/切换/内功被动/招式解锁 | A |
| `server/app/game/character.py` | 新增：玩家/NPC 共用角色卡、派生属性、经验、分配、功法切换、软散功 | A |
| `server/app/game/player.py` | 改为玩家锚点/API 视图；`get()` 返回 player + character join；保留 `battle.player_id` 语义 | A |
| `server/app/game/skills.py` | `skill` 挂 `art_id/unlock_level`；内功被动移到 `art.passive_json`；招式升级费公式；正式门派与武馆分流 | A |
| `server/app/game/battle.py` | 属性克制倍率；玩家/NPC 统一 `character.stats()`；NPC 招式 AI；快照含 element | A/B |
| `server/app/game/inventory.py` / `shop.py` | `player_id` → `character_id`；玩家 API 传玩家 character_id，NPC 工具调用传 NPC character_id | A/B |
| `server/app/game/data.py` | 怪物加 `element`；清溪镇后山深处怪物；南风城物品/怪物/POI 数据（可分阶段） | A/C |
| `server/app/game/mobs.py` | spawn `map_id`；击杀键按图唯一；南风城刷怪 | C |
| `server/app/game/world_state.py` / `world.py` | 阶段键按图预留；清溪镇仍用 `world.stage`；三馆/门派文案 | C |
| `server/app/game/schedule.py` | NPC 日程仍按本地 POI；`/api/npcs/state` 按 `map_id` 过滤 | C |
| `server/app/agent/npc.py` | 花名册加等级/功法/装备/招式预设；seed `character`；去掉硬编码 `combat`；debug/prompt 接角色卡 | B |
| `server/app/agent/dialogue.py` / `greet.py` | prompt 注入当前功法/属性/等级/上限/瓶颈（纯文本，不新增调用） | B |
| `server/app/agent/memory.py` / `events.py` | 写记忆/附近事件带 `map_id` | C |
| `server/app/db.py` | `character`/`art`/`character_art`/`character_skill`/`character_inventory`；`npc`/`memory` 加 `map_id`；旧档迁移 | A/C |
| `server/app/main.py` | `/api/game/arts`、`/api/game/art/learn`、`/api/game/travel`；state/debug/new_game/travel 适配 | A/C |
| `server/app/config.py` | `MAX_LEVEL/ARTLESS_LEVEL_CAP/ELEMENT_ADVANTAGE/ELEMENT_DISADVANTAGE/SKILL_UPGRADE_EXP_RATE` | A |
| `xia-ai-town/scripts/api.gd` | 新路由；`art`/`map_id` 字段 | A/C |
| `xia-ai-town/scripts/main.gd` | HUD `Lv.x/cap`、武学面板、切换确认、属性徽记、舆图旅行 | A/C |
| `xia-ai-town/scripts/town_world.gd` / `town_map.gd` | 地图管理器、南风城数据、按 `map_id` 过滤 NPC | C |
| `xia-ai-town/tools/smoke_test.gd` | 功法面板/切换弹窗/战斗克制/地图旅行 UI 断言 | A/C |
| `server/tests/smoke.py` | 功法/等级/散功/克制/NPC 同构/迁移/新局 | 全部 |
| `docs/DESIGN.md` | 已按 M6 设计更新；实施后回写状态 | 全部 |
| `AGENTS.md` | M6 计划/状态 | 全部 |

## 2. 目标数据模型与迁移

### 2.1 目标表

```sql
character(id INTEGER PK, kind TEXT, name TEXT, level INTEGER, exp INTEGER,
          cultivation INTEGER, hp REAL, mp REAL, gold INTEGER,
          str INTEGER, con INTEGER, agi INTEGER, int_ INTEGER,
          free_points INTEGER, art_id INTEGER NULL, sect_id TEXT NULL,
          map_id TEXT NOT NULL DEFAULT 'qingxi',
          created_at TEXT, last_saved_at TEXT)

player(id INTEGER PK, character_id INTEGER UNIQUE, pos_x REAL, pos_y REAL, clock_offset REAL)
npc(id INTEGER PK, character_id INTEGER UNIQUE, name TEXT, sect_id TEXT NULL,
    role TEXT, init_affinity INTEGER, home_poi TEXT, work_poi TEXT,
    map_id TEXT NOT NULL DEFAULT 'qingxi')

art(id INTEGER PK, name TEXT UNIQUE, element TEXT, cap_level INTEGER,
    sect_id TEXT NULL, is_true INTEGER DEFAULT 0, passive_json TEXT,
    desc TEXT)
character_art(character_id, art_id, learned_at, source,
              PRIMARY KEY(character_id, art_id))
skill(id INTEGER PK, art_id INTEGER, name TEXT UNIQUE, kind TEXT,
      unlock_level INTEGER, mp_cost INTEGER, multiplier REAL,
      cooldown INTEGER, effect_json TEXT)
character_skill(character_id, skill_id, level, equipped,
                PRIMARY KEY(character_id, skill_id))
character_inventory(character_id, item_id, qty, equipped_slot,
                    PRIMARY KEY(character_id, item_id))
memory(..., poi TEXT, map_id TEXT NOT NULL DEFAULT 'qingxi', ...)
```

- `art.element`：`metal|wood|water|fire|earth|wind|thunder|ice|light|dark|demon`。
- `character.kind`：`player|npc`；玩家一行，NPC 每人一行。
- `npc.id` 仍是记忆外键锚点；`npc.character_id` 指向可变角色卡。
- `player.id` 仍是 `battle.player_id` 的锚点；`player.character_id` 指向角色卡。
- `map_id` 旧数据默认 `qingxi`。

### 2.2 旧档迁移步骤

1. **停服备份**：复制 `server/data/xia.db`（WAL 模式下先确认无进程写入）与 `server/data/chroma/`；迁移代码必须幂等，允许重复启动。
2. **建新表**：`character`/`art`/`character_art`/`character_skill`/`character_inventory`。
3. **补列**：`npc.character_id`、`npc.map_id`、`memory.map_id`、`skill.art_id`、`skill.unlock_level`、`player.character_id`。索引放到补列之后。
4. **迁移玩家**：把旧 `player` 行写入 `character(kind='player')`，保留 `player.id` 与 `pos_x/pos_y/clock_offset`；`player.character_id` 回填。
5. **迁移 NPC**：按 `NPC_ROSTER` 预设为每个 `npc` 建 `character(kind='npc')`，回填 `npc.character_id`；没有预设的旧 NPC 给保守默认（1 级、无功法）。
6. **迁移技能/背包**：`player_skill` → `character_skill`；`inventory` → `character_inventory`；`kind='passive'` 的旧心法行跳过（效果已进 `art.passive_json`）；按技能名映射 `art_id`。
7. **迁移地点**：旧 `memory.poi` 保留，`map_id='qingxi'`；旧 `npc`/`player` 位置同理。
8. **验证**：旧表只读保留；跑 smoke 的迁移断言；确认玩家/记忆/背包/技能数量一致后，再切换服务层读新表。
9. **清理**：M6 不删旧表；至少保留一个版本周期，确认回滚不需要后再删。

迁移注意：

- `ALTER TABLE ADD COLUMN ... NOT NULL DEFAULT` 对旧库安全；`UNIQUE` 约束用唯一索引补，不要依赖 ALTER 加 UNIQUE。
- `skill.name` 继续唯一；旧 `sect_id/tier_req` 列保留但不再读，避免旧代码/调试工具报错。
- `db.connect()` 先 `executescript(SCHEMA)`，再跑 ALTER；任何引用新列的索引必须在 ALTER 之后创建。
- `seed_and_load` 必须幂等：`art`/`skill` 用 `INSERT OR IGNORE` + 按 name 回填 `art_id`；NPC 角色卡只在缺失时创建，不覆盖玩家/NPC 的运行态。

### 2.3 玩家/NPC 读取形状

`player.get(db)` 返回 join 后的字典，保留旧字段名，避免大面积改调用方：

```python
{
  "id": player.id, "character_id": character.id, "name": ...,
  "level": ..., "exp": ..., "cultivation": ..., "hp": ..., "mp": ...,
  "gold": ..., "str": ..., "con": ..., "agi": ..., "int_": ...,
  "free_points": ..., "art_id": ..., "sect_id": ..., "map_id": ...,
  "pos_x": ..., "pos_y": ..., "clock_offset": ...,
}
```

- `battle.player_id` 仍用 `player.id`；装备/技能/功法查询一律用 `character_id`。
- `npc_mod.by_id(db, npc_id)` 返回 `npc` 行 + `character` 行，`character` 键保留角色卡；`npc["combat"]` 删除。
- `character.stats(db, c)` 与 `player.stats(db, p)` 共用实现；`p` 有 `character_id` 即可。

## 3. Phase A：功法境界与角色卡地基

**目标**：玩家等级受当前功法上限约束；同源/异源转换、软散功、属性克制、招式解锁与升级费全部在服务端生效；清溪镇现有内容可玩；NPC 暂时仍用旧战斗数值，Phase B 再接。

### A1. `server/app/game/arts.py`

```python
ELEMENTS = ("metal","wood","water","fire","earth","wind","thunder","ice","light","dark")
ELEMENT_NAMES = {...}
COUNTERS = {
    "metal":"wood", "wood":"earth", "earth":"water", "water":"fire", "fire":"metal",
    "wind":"thunder", "thunder":"ice", "ice":"light", "light":"dark", "dark":"wind",
}
# demon 不在表内，永远中立

ARTS = [
    {"key":"art_lieyang","name":"烈阳剑诀","element":"fire","cap":15,
     "sect":"jianyang","is_true":False,
     "passive":{"stat":"str","per_levels":3,"amount":1}, "desc":"..."},
    {"key":"art_xuantu","name":"玄土刀法","element":"earth","cap":15,
     "sect":"xuantie","is_true":False,
     "passive":{"stat":"con","per_levels":3,"amount":1}},
    {"key":"art_yanyu","name":"烟雨诀","element":"water","cap":15,
     "sect":"yanyu","is_true":False,
     "passive":{"stat":"agi","per_levels":3,"amount":1}},
    {"key":"art_dafeng","name":"大风诀","element":"wind","cap":45,
     "sect":"fengchi","is_true":False,
     "passive":{"stat":"agi","per_levels":3,"amount":1}},
    {"key":"art_dafeng_true","name":"大风诀·真传","element":"wind","cap":50,
     "sect":"fengchi","is_true":True,
     "passive":{"stat":"agi","per_levels":2,"amount":1}},
    {"key":"art_chiyang","name":"赤阳功","element":"fire","cap":40,
     "sect":"chidi","is_true":False,
     "passive":{"stat":"str","per_levels":3,"amount":1}},
    {"key":"art_chiyang_true","name":"赤阳功·真传","element":"fire","cap":50,
     "sect":"chidi","is_true":True,
     "passive":{"stat":"str","per_levels":2,"amount":1}},
    {"key":"art_nantian","name":"南天诀","element":"thunder","cap":55,
     "sect":None,"is_true":True,
     "passive":{"stat":"int","per_levels":3,"amount":1}},  # 城主，暂定
]
```

API：

```python
def art(key: str) -> dict
async def seed_and_load(db) -> None
def cap_of(c: dict) -> int            # art.cap / ARTLESS_LEVEL_CAP
def passive_mods(art_row, level) -> dict[str,int]
def counter_multiplier(attacker_el: str | None, defender_el: str | None) -> float
def check_switch(c, art_row) -> dict   # {requires_reset, requires_clamp, clamp_to, reason}
async def switch_art(db, c, art_row, confirm_reset=False, confirm_clamp=False) -> dict
```

`counter_multiplier`：

```python
if not a or not d or a == "demon" or d == "demon":
    return 1.0
if COUNTERS.get(a) == d:
    return config.ELEMENT_ADVANTAGE   # 1.20
if COUNTERS.get(d) == a:
    return config.ELEMENT_DISADVANTAGE  # 0.85
return 1.0
```

`switch_art`：

1. 当前无功法 → 直接设 `art_id`，等级/经验保留；首门功法不散功。
2. 同属性 → 等级/经验保留；若 `c.level > art.cap`：未确认则 `requires_clamp`，确认后 `level=cap, exp=0`。
3. 异属性 → 未确认则 `requires_reset`；确认后 `level=1, exp=0`，保留四维/自由点/装备/背包/银两/图鉴。
4. 写入 `character.art_id`，upsert `character_art(learned_at, source)`。
5. 清空 `character_skill.equipped`，只保留新功法已解锁的招式；可自动装备第一招。
6. 同一事务内完成；返回新等级/上限/是否散功。

### A2. `server/app/game/character.py`

```python
def exp_next(level: int) -> int: return level * level * 20
def derived(c, mods=None) -> dict   # 复制 player.derived，但 level 来自 c
async def modifiers(db, c) -> dict  # 按 c["character_id"] 查 character_inventory/character_skill/art
async def stats(db, c) -> dict
def level_cap(c, art_row=None) -> int
def gain_exp(c, exp, art_row) -> list[int]
def allocate(c, stats) -> None
```

`gain_exp`：

```python
if c["level"] >= MAX_LEVEL:
    return []
cap = level_cap(c, art_row)
if c["level"] >= cap:
    return []          # 到顶：经验不涨，修炼点由调用方照常加
c["exp"] += exp
while c["level"] < cap and c["level"] < MAX_LEVEL and c["exp"] >= exp_next(c["level"]):
    c["exp"] -= exp_next(c["level"])
    c["level"] += 1
    c["str"] += 1; c["con"] += 1; c["agi"] += 1; c["int_"] += 1
    c["free_points"] += 3
return ups
```

- 到顶时经验不累积；调用方仍按 `exp * WIN_CULTIVATION_RATE` 加修炼点。
- `level_cap`：无功法 = `ARTLESS_LEVEL_CAP`（15）；有功法 = `art.cap_level`；全局 `MAX_LEVEL` 兜底。
- `modifiers`：装备查询 `character_inventory.character_id`；被动从 `art.passive_json` 取，不再读 `player_skill` 的 passive。

### A3. `server/app/game/skills.py`

- `SKILLS` 每项加 `art`（art key）与 `unlock_level`；删除 3 个 passive 项（`lieyang_jue`/`xuantie_dao`/`yanyu_xin`）或标记 legacy，不再进 `character_skill`。
- `SECTS` 拆成 `SCHOOLS`（可保留别名 `SECTS`）：
  - `jianyang/xuantie/yanyu`: `kind='guild'`，对应三馆，`art` 指向 cap15 功法；`join_sect` 拒绝。
  - `fengchi`: `kind='sect'`，`bonus={"agi":3,"int":2}`，`art='art_dafeng'`。
  - `chidi`: `kind='sect'`，`bonus={"str":3,"int":2}`，`art='art_chiyang'`。
- `JOIN_LEVEL_REQ=15`；`APPRENTICE_AFFINITY=60`；`RANK_THRESHOLDS` 保持 60/75/85。
- `join_sect` 只允许 `kind='sect'`；入门授予外传 `character_art` + 一档主动招式；`sect.bonus` 只对正式门派生效。
- `upgrade_cost(character_level, skill_level) = max(20, round(exp_next(character_level) * SKILL_UPGRADE_EXP_RATE)) * skill_level`。
- `set_equip`：只允许当前功法、已解锁、active 招式；切功法后清空 equipped 由 `arts.switch_art` 负责。
- 旧 `current_rank`/`master_affinity` 保留给正式门派；武馆不参与位阶。
- `seed_and_load`：先 `arts.seed_and_load`，再写 `skill(art_id, unlock_level, ...)`；旧技能按 name 回填 `art_id`；旧 `tier_req` 不再读。

### A4. 玩家服务/API/战斗

- `player.get(db)` join `player` + `character`；`player.create(db,name)` 同时建 `character(kind='player')` 与 `player` 锚点；`player.save` 写 character + position。
- `main.game_new`：调用 `character` 清理/重建；旧的 `DELETE FROM player_skill/inventory` 改为 `DELETE FROM character_skill/character_inventory/character_art`（仅玩家角色）；不动 NPC 角色卡（Phase B 的 new_game 重置单独处理）。
- `GET /api/game/state`：加 `map_id`、`art`、`learned_arts`；`skills` 只返回当前功法的招式；`locked` 按 `unlock_level`。
- `GET /api/game/arts`：返回全量功法 + `current/learned/requires_reset/requires_clamp/cap`。
- `POST /api/game/art/learn`：
  - 校验 `art_id`、传授者/势力、好感、学费、真传资格；
  - 异源未确认 → `409 {requires_reset:true, consequences:{level:1, exp:0, keep:[...]}}`；
  - 同源降上限未确认 → `409 {requires_clamp:true, clamp_to:cap}`；
  - 确认后调用 `arts.switch_art`，返回新 state。
- `POST /api/game/skill/upgrade`：用新公式；`skill` 必须属于当前功法。
- `battle`：
  - 玩家 element = 当前功法 element；敌方 element = mob/NPC 当前功法 element；
  - 伤害 `max(1, round(atk * mult - def))` 后乘 `counter_multiplier`，日志加 `element_counter` 与 `element_mult`；
  - 玩家 `gain_exp` 传入当前 art，到顶停止；修炼点仍加。
- 配置：`MAX_LEVEL=100`、`ARTLESS_LEVEL_CAP=15`、`ELEMENT_ADVANTAGE=1.2`、`ELEMENT_DISADVANTAGE=0.85`、`SKILL_UPGRADE_EXP_RATE=0.05`。

### A5. 最小 UI（Godot）

- HUD：`Lv.10 / 15 · 烈阳剑诀（火）`；到顶显示“瓶颈”。
- K 武学面板：当前功法/内功/招式；已学功法列表；点选可学功法 → 切换确认弹窗。
- 弹窗三种文案：
  - 同源：`修为保留（10级），上限 15→40`
  - 异源：`散功重修：等级归1，经验清零；四维/装备/图鉴保留`
  - 降上限：`新功法上限低于当前等级，确认后压至 15 级`
- 战斗日志显示“克制/被克”；属性徽记可先用文字/颜色块，不新增图片。

### A6. Phase A 验收

- 新角色 1 级无功法，`cap=15`；学烈阳剑诀后仍 1 级，cap15。
- 练到 15 级后经验不再增长，修炼点仍增加。
- 学赤阳功（火 cap40）：等级 15 保留，cap 变 40，经验从 0 开始。
- 学大风诀（风 cap45）：未确认返回 `requires_reset`；确认后 1 级、经验 0；四维/装备/图鉴保留；旧招式不可用。
- 同源降到 cap15：高等级被压到 15，确认生效。
- 克制 ×1.20 / 被克 ×0.85 / 中立 ×1.00；魔功不参与。
- 旧档迁移后玩家等级/经验/背包/技能/记忆不丢；smoke 全绿。

## 4. Phase B：NPC 角色卡同构

**目标**：NPC 不再用花名册硬编码 `combat`，与玩家共用角色卡、装备、招式和战斗引擎；清溪镇馆主 15 级、南风城门主 50 级、城主 52 级可被同一套 `stats()` 推导。

### B1. 花名册预设

`agent/npc.py` 每人增加：

```python
"character": {
    "level": 15,
    "art": "art_lieyang",
    "attrs": {"str": 22, "con": 18, "agi": 16, "int": 14},
    "free_points": 0,
    "gold": 200,
    "equipment": ["jinggang_jian", "jingge_jia"],
    "skills": ["chiyang_zhan", "fenglei_shi", "xuri_sheng"],
}
```

- 删除 `combat` 字段；`battle.start_spar` 不再读它。
- 馆主：15 级、cap15 功法、精钢/精革甲级别装备、3 招。
- 南风城门主：50 级、真传 cap50、稀有装备；城主 52 级、cap55。
- 非战斗 NPC：2~8 级、无功法或低阶功法、普通装备；`spar` 以“无功法/不肯比试”拒绝。
- 张馒头：1 级、无功法、无装备；保留隐世高人伏笔。
- 具体数值在 Phase B/C 用现有掌门战力做回归，表里只是模板。

### B2. Seed 与读取

- `npc.seed_and_load`：
  - 建/复用 `npc` 行，补 `map_id`；
  - 为每个 NPC 建 `character(kind='npc')`，`art_id` 按 key 解析；
  - 按预设写 `character_art`/`character_skill`/`character_inventory`；
  - 已有角色卡不覆盖运行态，只在缺失时补默认；
  - `npc_state` 初始化逻辑不变。
- `npc_mod.by_id` 返回 `npc` + `character` join；`character` 里带 `art`/`element`/`cap/moves` 视图供战斗/调试。
- `/api/debug/npc/{id}` 加 `character` 字段；对话/搭话 prompt 加一行“你当前修 X（属性/等级/上限）”，不新增 LLM 调用。
- `/api/game/state` 的 `npcs` 花名册加 `level/art/element/cap`（客户端人物录/对话可用）。

### B3. 战斗统一

- `battle.start_spar`：
  - `npc_char = await npc_mod.character(db, npc_id)`；
  - `d = await character.stats(db, npc_char)`；
  - snapshot 加 `character_id/art_id/element/mp/mp_max/skills`；
  - 玩家先手按双方 `spd`。
- NPC 招式 AI（规则型，不调 LLM）：
  1. HP<35% 且有药剂 → 喝药；
  2. 玩家 HP<40% 且 MP 足够 → 用最高倍率已解锁招式；
  3. MP 足够 → 70% 用一档招式；
  4. 否则普攻。
- 玩家对 NPC 的属性克制同样按双方当前功法 element 结算。
- V1 NPC 不自动涨经验/升级；`character.gain_exp` 已可复用，后续工具调用 `train` 再开启。NPC 到 cap 时对话可提“瓶颈”。

### B4. 经济/背包

- `inventory.py`/`shop.py` 的 `player_id` 参数改为 `character_id`；玩家 API 传玩家 `character_id`，NPC 工具调用传 NPC `character_id`。
- 店铺库存仍走 `SHOPS` 静态表，不混入 NPC 个人背包。
- NPC 买装备进 `character_inventory`，装备后进入 `modifiers`；V1 不做 NPC 主动交易，只把服务函数留好。
- 新局 `new_game`：
  - 重置 NPC `character` 到花名册预设（等级/经验/功法/招式/装备/背包/银两）；
  - 保留 `conversation`/`longterm_memory`（沿用现有新局规则）；
  - 清空 `character_art` 后按预设重建，避免旧功法残留。

### B5. Phase B 验收

- 馆主切磋使用角色卡属性；15 级馆主比旧 6 级掌门强，但玩家 10~15 级可打。
- NPC 会用自己的招式、MP、冷却；无功法 NPC 拒绝切磋。
- `/api/debug/npc/{id}` 能看到等级/功法/属性/装备/招式。
- NPC 对话会提自己的功法/瓶颈；不新增 LLM 调用。
- `new_game` 后 NPC 回到预设等级/功法；记忆/对话保留规则不变。
- 旧档 NPC 迁移后切磋/日程/记忆不丢；smoke 全绿。

## 5. Phase C：南风城与多地图

**目标**：清溪镇 1~15 完整可玩，南风城 15~50 开放；两图各自 NPC/怪物/商店/记忆互不串；地图旅行走服务器。

### C1. 内容数据

- `arts.py` 补南风城：
  - 风池派：大风诀（外传 cap45）、大风诀·真传（cap50）；3 招。
  - 赤地门：赤阳功（外传 cap40）、赤阳功·真传（cap50）；3 招。
  - 城主府：南天诀（暂定雷/光 cap55）；3 招。
- `data.py`/`mobs.py`：
  - 清溪镇后山深处：6~14 级悍匪/毒蛇/独眼盗，击败黑旋风后开放（用 `world.stage==2` 或新 kv 标记）。
  - 南风城：城郊 15~22、风蚀谷 22~32、赤砂矿 32~42、城主试炼 42~50；怪物带 element，经验按“同级升级经验 1/10”。
  - 南风城商店/药剂/装备：沿用现有模板，新增 1~2 档装备，不新增品阶。
- `npc.py` 补南风城：
  - 风池派门主（50，风，真传 cap50）、赤地门门主（50，火，真传 cap50）、城主南天成（52，雷/光 cap55）；
  - 城门校尉（25，土/金 cap30）、药铺/铁匠/客栈（5~15，无/低功法）；
  - 日程 POI：两派驻地、城主府、城门、市集、客栈；V1 不跨图。
  - 门主姓名/支持 NPC 名单待定，先用占位名，数据表留 `TBD`。
- `world.py`/`world_state.py`：
  - 清溪镇阶段仍用 `world.stage`；
  - 南风城 V1 不做世界阶段；如后续要阶段，键改 `world.stage:{map_id}`；
  - 文案把清溪镇“三派/掌门”统一为“三馆/馆主”，南风城才用“门派/掌门”。

### C2. 多地图服务端

- `map_id`：
  - `character.map_id`（玩家当前位置/归属图）；
  - `npc.map_id`（NPC 所属图）；
  - `memory.map_id`（记忆发生地）；
  - 怪物 spawn `map_id`；`mob_kills` 的 spawn id 全局唯一（加图前缀）。
- `GET /api/game/state` 返回 `map_id`；`GET /api/npcs/state` 只返回当前图 NPC；`/api/npc/{id}/...` 校验 NPC 与玩家同图（跨图交互 409）。
- `POST /api/game/travel`：
  - `{map_id}`；校验在 `MAPS` 中、目标图已开放；
  - 更新 `character.map_id` + 目标出生点/城门落点；
  - 返回新 state；V1 无硬等级门槛，只在客户端提示“城外凶险”。
- `memory` 写入：`memory.add(...)` 带 `map_id`；附近事件 `events.add(...)` 带 `map_id`；RAG 仍按 `npc_id`，不加 map 过滤（NPC 不跨图）。
- `world_state.reset`/`new_game`：玩家回 `qingxi`；南风城进度（如后续有）按图清；NPC 角色卡重置。
- `schedule`：NPC 的 `home_poi/work_poi` 是本地 POI；`position_for` 不需要 map_id，但 `/api/npcs/state` 过滤和客户端渲染需要 `npc.map_id`。

### C3. 客户端

- `town_map.gd`/`town_world.gd`：
  - 抽出 `current_map` 与 `MAPS` 数据（尺寸/出生点/建筑/碰撞/怪物刷新/NPC 过滤）；
  - 清溪镇沿用现有 1600×960 地图；南风城先做灰盒（现有 atlas 拼贴），等新贴图再替换；
  - NPC 只在 `npc.map_id == current_map` 时渲染/推算；
  - 舆图显示两图与官道，点击/确认后 `POST /api/game/travel`。
- `main.gd`/`api.gd`：新 state 字段、`arts`/`art/learn`/`travel`；HUD/武学面板/弹窗；战斗属性徽记。
- 南风城新贴图：按 AGENTS.md，先在 `docs/ART-PROMPTS.md` 补建筑/地形/怪物提示词，等用户生成后再跑 `tools/build_art_assets.py`；不擅自手改 `assets/`。C 阶段可先用现有图集做灰盒验收。
- 客户端仍不轮询 NPC 坐标；地图切换后重新拉 `/api/npcs/state`，按本地日程推算。

### C4. 平衡与验收

- 清溪镇：1~5 现有怪；黑旋风后开后山深处 6~14；馆主 15；玩家能在 15 级前体验三馆功法。
- 南风城：15~22 城郊、22~32 风蚀谷、32~42 赤砂矿、42~50 城主试炼；门主 50/城主 52。
- 切磋：馆主 15/门主 50 的属性回归；切磋经验 ×0.3 + 每日递减，防止刷掌门。
- 招式升级费按新公式；高等级修炼点不溢出。
- 属性克制先在南风城带属性怪上验证；清溪镇低级怪保持无属性，避免新手困惑。
- 南风城两派功法：外传 cap45/40，真传 cap50；城主 cap55。玩家从清溪镇 cap15 到南风城同源/异源转换的完整链路可走通。

## 6. 测试与验收

### 6.1 `server/tests/smoke.py`

新增确定性断言：

1. 无功法 cap15；首门功法保留等级。
2. 同源换功：等级/经验保留；cap 提升后继续升级。
3. 异源换功：未确认 409；确认后 1 级/0 经验；四维/装备/图鉴保留；旧招式不可用。
4. 同源降 cap：确认后压到 cap；未确认 409。
5. 到 cap 经验不涨、修炼点照加；换高 cap 同源后恢复。
6. 克制 1.20/0.85/1.00；魔功中立。
7. 招式解锁等级/升级费公式；跨功法招式不可装备。
8. NPC `stats()` 与玩家同源；切磋快照/招式 AI；无功法 NPC 拒绝。
9. `map_id`：旅行/NPC 过滤/记忆写入/同图交互校验。
10. 旧档迁移：旧 player/npc/player_skill/inventory/memory → 新表；数量一致；重复启动幂等。
11. `new_game`：玩家回 1 级/qingxi；NPC 回预设；conversation/longterm 保留规则不变。
12. 现有 M3/M4/M5 链路（对话/送礼/切磋/反思/八卦/心事/偏离）全绿。

### 6.2 `xia-ai-town/tools/smoke_test.gd`

- HUD `Lv.x / cap`、瓶颈文案。
- K 武学面板：当前功法/招式/已学图鉴。
- 切换弹窗三种文案；确认/取消。
- 战斗属性徽记与“克制/被克”日志。
- 舆图两图/旅行；切换后 NPC 列表变化。
- 旧 UI 回归：对话、送礼、切磋、日程、搭话不破。

### 6.3 手动验收

- 清溪镇开新档：三馆学艺 → 练到 15 → 打后山深处 → 南风城。
- 烈阳剑诀 → 赤阳功（同源，保留 15 级）→ 大风诀（散功，1 级）→ 再练。
- 与馆主/门主切磋，确认等级/功法/属性/招式与角色卡一致。
- 南风城两派拜师/真传/城主试炼（内容可先占位）。
- 两图来回旅行，确认 NPC/记忆/日程不串图。

## 7. 风险与取舍

- **软散功可能太轻**：保留四维/装备会让散功后仍强；先用“等级+经验+招式重练”作为代价，实测若太轻再回退等级自动成长的四维，或加散功冷却。
- **NPC 同构是跨表重构**：`player_skill`/`inventory`/`battle`/`shop` 的 owner 都要改；分 Phase、旧表只读、迁移测试是硬要求，不做大爆炸式替换。
- **NPC 自主成长/散功**：工具调用若让掌门散功变 1 级，会破坏门派；V1 NPC 保持预设，工具调用加“掌门不可轻易散功”的保护与剧情门槛。
- **多地图串数据**：裸 POI/怪物 key 会串；所有地点/刷怪/记忆带 `map_id`，spawn id 加图前缀。
- **南风城美术依赖**：新地图需要新贴图；先灰盒验收服务端/UI，贴图按 ART-PROMPTS 等用户生成，不擅自改 assets。
- **100 级数值**：M6 只验到 52 级；100 级曲线、怪物经验、血量/攻击膨胀留到后续里程碑做回归。
- **旧“三派”文案**：世界公设/NPC 人设/对话示例里的“三派/掌门”要统一为“三馆/馆主”；南风城才用“门派/掌门”，否则玩家身份混淆。
- **悟性减费**：现有设计表有“技能升级耗悟点-2%”，新公式暂未并入；M6 先不实现，留在开放项，避免公式双重折扣。

## 8. 提交切分

| Commit | 内容 | 验收 |
|---|---|---|
| `docs: 补齐功法境界、南风城与 NPC 同构设计` | DESIGN.md 相关章节 + 本计划 + AGENTS.md | 文档自洽，无代码改动 |
| `feat: 功法境界与角色卡地基` | Phase A | smoke A 全绿；旧档迁移通过 |
| `feat: NPC 角色卡同构与战斗统一` | Phase B | smoke B 全绿；切磋/日程/记忆不破 |
| `feat: 南风城与多地图` | Phase C | server + Godot smoke 全绿；手动清溪镇→南风城 |

每个 commit 按 AGENTS.md 三段式写：做了什么/怎么做的/为什么这样做。Phase 内可再拆小 commit，但每个 commit 必须可回退。

## 9. 开放项

1. 城主功法属性/名称（暂定雷/光，cap55）。
2. 南风城门主姓名、支持 NPC 名单、怪物表、地图布局/贴图。
3. NPC 成长是否跨局持久化；V1 默认 `new_game` 重置。
4. 武馆学费/好感门槛具体数值；叛门/改投代价。
5. 100 级内容曲线与怪物经验；M6 只到 52 级。
6. 悟性“招式升级费减免”是否并入新公式。
7. 旧 `player_skill`/`inventory` 表在一个版本周期后是否删除。
8. 清溪镇“三派/掌门”文案统一的具体清单（world.py、npc.py、对话示例、DESIGN 旧段落）。
