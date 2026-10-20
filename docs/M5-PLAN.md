# M5 执行计划：世界公设、议程与感知

> 状态：**待审阅，未执行**。本文件由需求稿改写为执行计划；确认后再按 Phase 顺序改代码。
>
> 执行方式：一个 Phase 一个 commit，每个 Phase 独立可回退；每个 Phase 完成即跑 `server/tests/smoke.py` 与 `xia-ai-town/tools/smoke_test.py`，全绿才提交。
>
> 已拍板（2026-10-20）：
> - 神器是刀，名“断岳刀”，做成**唯一武器**；
> - “取回神器 → 得张馒头认可 → 三派重开山门收徒”只作为**镇上的说法**，拜师仍按好感≥60、等级≥3，不做硬门槛；
> - 玩家真正击败黑旋风、杀尽三名山贼并取得断岳刀后，这件事作为世界事实**通告所有 NPC**；
> - 不保留阶段 1，世界只有两种状态：`0` 传闻期、`2` 全镇皆知；
> - 知识分层按原方案执行：L0 公开、L1 传闻、L2 机密，张馒头独占核心真相。

## 0. 不可变约束

改动必须继续满足以下约束，任何一条被破坏就停下来改方案：

1. **服务器权威**：世界阶段、背包、战斗、好感、记忆只在服务器结算；客户端只渲染。
2. **NPC 位置零同步**：位置仍是日程表的确定性函数；偏离只改阶段边界，不新增自由路径、不做位置轮询。
3. **不引入任务系统**：没有任务栏、目标追踪、强制流程；世界公设只是背景与谈资。
4. **LLM 永不阻塞玩法**：新增 LLM 调用都走“校验 → 重试一次(temperature 0.3) → 模板/空值兜底”。
5. **好感规则不变**：世界阶段、心事、议程、情绪都不改好感数值；只有现有 LLM `affinity_delta`、送礼、切磋、反思按原规则生效。
6. **记忆噪声控制**：世界公设与全镇通告**不逐人写记忆**，只作为常驻 prompt 上下文；只有具体互动/目击才按原规则入忆。
7. **SQLite 单写队列**：新表、新字段都走现有 `db.execute/fetch_*`，不新增连接。
8. **4B 稳定性**：新增结构化输出全部 `format:json`、`think:false`、失败兜底；新 purpose 必须登记到 `llm.PRIORITY`。
9. **不在代码里写死模型名**：新配置只放数值/开关，模型 tag 继续走 `.env`。

## 1. 变更地图

| 文件 | 变更 | Phase |
|---|---|---|
| `server/app/game/world.py` | 新增：公开事实、NPC 立场、知识分层、议程关键词、`brief_for` 纯函数 | W |
| `server/app/game/world_state.py` | 新增：`world.stage` kv、阶段推进判定、神器取得标记 | W |
| `server/app/game/plan.py` | 新增：当日偏离读取（纯 DB 辅助，避免 `npc`/`heart` 循环 import） | A |
| `server/app/game/data.py` | 新增断岳刀 item、`unsellable` 描述 | W |
| `server/app/game/inventory.py` | 卖出时拒绝 `unsellable` | W |
| `server/app/game/battle.py` | 黑旋风首掉断岳刀、胜利后推进世界阶段、战斗日志 | W |
| `server/app/game/mobs.py` | 暴露 `kill_times()` 给世界状态判定 | W |
| `server/app/main.py` | `game/state` 返回 `world`，debug 返回立场/知识/阶段；启动补算 | W |
| `server/app/agent/dialogue.py` | 注入世界简报、心事、议程、情绪、口癖 | W/B/A |
| `server/app/agent/greet.py` | 注入世界简报、心事、情绪 | W/B/A |
| `server/app/agent/npc.py` | 花名册加口癖示例；送礼设情绪；debug 辅助 | B |
| `server/app/agent/agenda.py` | 新增：按立场抽议程、防重复、会话内保持 | B |
| `server/app/agent/mood.py` | 新增：轻情绪读写 | B |
| `server/app/agent/heart.py` | 新增：每日心事 + 偏离选择 | A |
| `server/app/game/schedule.py` | `phase_at/position_for/poi_label_for/is_idle` 支持 `deviation` 阶段平移 | A |
| `server/app/agent/scheduler.py` | 新增心事巡检循环 | A |
| `server/app/agent/events.py` | 新增：短期事件环形缓冲（C v1） | C |
| `server/app/game/shop.py`、`server/app/agent/npc.py` | 大额消费/送礼/拜师记录短期事件 | C |
| `server/app/config.py` | 心事开关/偏离时长/事件 TTL | A/C |
| `server/app/llm.py` | `PRIORITY["heart"] = 3` | A |
| `server/app/db.py` | 新增 `npc_plan`、`npc_mood` 表（新表，无 ALTER 风险） | A/B |
| `xia-ai-town/scripts/main.gd` | 镇志面板、世界状态、断岳刀通告、心事显示 | W/A |
| `xia-ai-town/scripts/town_world.gd` | 偏离阶段平移（与服务端同算法） | A |
| `server/tests/smoke.py` | W/B/A/C 的确定性断言 | 全部 |
| `xia-ai-town/tools/smoke_test.gd` | 镇志、偏离镜像、对话提示的 UI 断言 | 全部 |
| `docs/DESIGN.md` | 每 Phase 完成后同步 §6/§9/§10/§13/§14 | 全部 |

---

# Phase W：世界公设与断岳刀

**目标**：任何 NPC 都能围绕“后山、神器、张馒头、三派”给出有共同事实、有个人立场的回答；玩家取回断岳刀后全镇知晓，但拜师规则不变。

## W1. 新增 `server/app/game/world.py`

纯静态数据 + 纯函数，不 import DB、不 import agent，避免循环依赖。

### W1.1 常量

```python
STAGE_KEY = "world.stage"                       # kv，值 "0" 或 "2"
ARTIFACT_ACQUIRED_KEY = "world.artifact_acquired"  # kv，"1" 表示已取得
ANNOUNCED_AT_KEY = "world.announced_at_game"    # kv，游戏时间戳
ARTIFACT_ITEM_KEY = "duanyue_dao"

PREMISE_KEYWORDS = (
    "后山", "山贼", "黑旋风", "寨子", "神器", "断岳刀", "宝刀", "那把刀",
    "张馒头", "破庙", "山外", "外面", "拜师", "武馆", "三派",
    "剑阳", "玄铁", "烟雨", "门派",
)

# 话题 tag -> 需要注入的公开事实（按 PUBLIC_FACTS 固定顺序去重）
TOPIC_FACTS = {
    "bandit": ("bandits", "artifact"),
    "artifact": ("artifact", "bandits", "zhang"),
    "zhang": ("zhang", "artifact"),
    "sect": ("sects", "zhang"),
}

PUBLIC_FACTS = {
    "bandits": "半月前，黑旋风带一伙山贼占了后山崖旧寨，镇上货郎、猎户都不敢往那边走。",
    "artifact": "传闻黑旋风从外头劫来一把神器宝刀“断岳刀”，藏在寨子里；没人亲眼见过。",
    "zhang": "破庙的张馒头是镇上唯一出过远门的人，疯话里总念“山外有山”；传说取回神器的人才配听他说真话。",
    "sects": "剑阳道馆、玄铁堂、烟雨小筑都盯着后山；镇上流传神器现世之日，三派会重开山门收徒。",
}

STAGE2_FACT_TEMPLATE = "{player}杀了黑旋风，清了后山那伙山贼，取回了断岳刀；这件事已经传遍清溪镇。"
AMBIENT_LINE = "近日镇上都在议论后山的事。"
```

### W1.2 立场与知识（精确文案）

`NPC_STANCE[slug]` 字段：

- `stance`：该 NPC 对神器的固定立场，作为 prompt 的“你的立场”；
- `avoid`：不愿提的点，B 阶段议程直接复用；
- `knowledge`：`[{text, min_stage, min_affinity}]`，按层级和交情过滤；
- `stage2`：阶段 2 后该 NPC 的说法变化；
- `stage2_truth`（仅张馒头）：阶段 2 才注入的核心真相。

| slug | stance | avoid | knowledge（含门槛） | stage2 |
|---|---|---|---|---|
| gu_changfeng | 你认为断岳刀该归剑阳，但嘴上不屑外物；你厌恶山贼，也不愿向张馒头低头。 | 不愿承认自己在意那把刀，也不愿提剑阳这些年被另外两派压过一头。 | 本派打算借神器压过玄铁堂与烟雨楼，但你不肯明说。（min_stage=0, min_affinity=60） | 你仍不承认自己看走眼，只说刀是死物、取刀的人才活着。 |
| tie_wushuang | 你想带人上山硬抢，把这事当成喝酒前的热身；你觉得张馒头是装疯。 | 不愿承认自己没先上山，也不愿提玄铁堡先前折了几个弟子。 | 猎户说过寨子后崖有条窄路，黑旋风每晚都从那里巡一遍；你没亲自去过。（min_stage=0, min_affinity=50） | 你拍桌大笑，要玩家请你喝酒，嘴上说刀归他、山贼归你。 |
| su_wanqing | 你怀疑“神器”是有人做的局，按兵不动，爱套别人的话。 | 不会说出自己查到了什么，也不愿让人看出烟雨楼在等谁先出手。 | 你隐约知道另外两派都派了人上后山，但不确定是谁。（min_stage=0, min_affinity=60） | 你先问玩家信不信那是神器，再问张馒头说了什么；仍留三分。 |
| wang_dazhu | 你不关心神器归谁，只关心消息能卖几个钱。 | 不会白送消息；也不愿承认自己手里的布防图有一半是猜的。 | 你手里有一份寨子布防，真假参半：后崖窄路、夜里换岗、黑旋风喝酒的习惯。（min_stage=0, min_affinity=0） | 你立刻想打听细节、转卖消息，劝玩家别把刀露出来。 |
| chen_bansong | 你只担心上山的人送命，避谈宝物。 | 不愿提当年为一件宝物丢命的那个人，也不愿说那人是谁。 | 你亲眼见过有人为宝物死在山上；你记得那人的伤，却不愿再提名字。（min_stage=0, min_affinity=60） | 你先看玩家伤势，念叨宝物害人，确认人没事才松口气。 |
| liu_xiaodie | 你把这事当全镇最热闹的谈资，说法最夸张。 | 不愿承认自己多半只是听来的，没亲眼见过。 | 你听来的说法比谁都多：有人说刀会发光，有人说黑旋风三头六臂；你都没亲眼见过。（min_stage=0, min_affinity=0） | 你把玩家当英雄，追问一路细节，说法越传越夸张。 |
| zhang_mantou | 你知道断岳刀的真正来历和山外的世界，但在等一个取回刀的人；在此之前你只用疯话点人。 | 阶段 2 之前不说破；之后也只说七分。 | 无（核心真相单列） | 你认了玩家，开始用疯话提“山外”，但只说七分。 |

张馒头核心真相（`stage2_truth`，仅 `stage==2` 且被问及神器/山外时注入）：

> 断岳刀是山外旧朝的信物；持刀的人才能听懂你疯话里的地名；山外还有更大的江湖。

知识分层落地对应：

| 层 | 内容 | 注入条件 |
|---|---|---|
| L0 公开 | `PUBLIC_FACTS` 四条 | 命中话题即注入；阶段 0 无话题只给氛围句 |
| L1 传闻 | 王大柱的布防图、柳小蝶的夸张说法 | 命中话题即注入；前者带“真假参半”限定 |
| L2 机密 | 三位掌门、陈半松的 `knowledge` | 命中话题且 `affinity >= min_affinity`；掌门门槛 50~60 |
| 核心真相 | 张馒头 `stage2_truth` | 仅阶段 2 且话题命中神器/张馒头/山外 |

### W1.3 纯函数

```python
def topic_tags(text: str) -> list[str]:
    """返回命中的 tag：bandit/artifact/zhang/sect；无命中返回 []。"""

def knowledge_for(slug: str, stage: int, affinity: int, tags: list[str]) -> list[str]:
    """按 min_stage/min_affinity 过滤；阶段 2 且 tags 含 artifact/zhang 时追加 stage2_truth。"""

def brief_for(npc: dict, text: str, stage: int, affinity: int, player_name: str) -> str:
    """生成 120~260 字的 prompt 片段；无公设关键词且阶段 0 时只返回氛围句。"""
```

`brief_for` 选取公开事实的规则：先按 `topic_tags` 收集 tag，再按 tag 顺序合并 `TOPIC_FACTS[tag]`，去重后按 `PUBLIC_FACTS` 的固定顺序输出前 3 条；命中多个 tag 时仍保持这个顺序，避免同一句话在不同 NPC 处顺序漂移。

`brief_for` 输出格式固定为下列结构，便于测试断言：

```
【清溪镇人人都知道】
- <命中的 2~3 条公开事实>
【你的立场】
<stance>
【你知道的细节】
- <过滤后的 knowledge；没有则省略>
【分寸】
只依据以上事实与你的见闻说话；不知道的岔开、装糊涂；不要编造新人物、新宝物、新门派；不要复述设定。
```

- 阶段 2：第一段追加 `STAGE2_FACT_TEMPLATE.format(player=player_name)`，并追加一行 `stage2`。
- 张馒头被问及神器/山外且阶段 2：追加 `stage2_truth`，同时追加“只说七分，不把山外一次说尽”。
- 非公设话题且阶段 0：返回 `AMBIENT_LINE`，不注入立场和知识，避免每句话都谈后山。
- 总长度超过 260 字时，按 `bandits → artifact → zhang → sects` 顺序截断公开事实，保留立场与分寸。

## W2. 新增 `server/app/game/world_state.py`

```python
BANDIT_IDS = ("bandit_1", "bandit_2", "bandit_3")
BOSS_ID = "boss_1"

async def stage(db) -> int: ...
async def artifact_acquired(db) -> bool: ...
async def mark_artifact_acquired(db) -> None: ...
async def announced_at(db) -> float | None: ...
async def check_and_advance(db, now_game: float) -> bool: ...
async def reset(db) -> None: ...
```

`check_and_advance` 规则（**不保留阶段 1**）：

1. 已是阶段 2 → 返回 `False`；
2. `mob_kills` 中必须有 `boss_1`；
3. `mob_kills` 中必须有 `bandit_1`、`bandit_2`、`bandit_3` 三条记录（各自至少击杀过一次，不看是否已重生）；
4. `world.artifact_acquired == "1"`；
5. 全部满足 → 写 `world.stage = "2"`、`world.announced_at_game = str(now_game)`，返回 `True`。

`mob_kills` 只保留每个 spawn 最近一次击杀时间戳，不清理，因此“曾经杀过”可用键存在性判断。

`reset(db)` 删除 `world.stage`、`world.artifact_acquired`、`world.announced_at_game` 三个 kv，并清空 `mob_kills`。`POST /api/game/new` 在 `player_mod.create` 之后调用它：否则上一个存档的阶段 2 会带到新档，NPC 会对着新角色讲旧角色的事。`reset_on_start=true` 已由 `db.reset()` 覆盖，不需要额外调用。

`main.lifespan` 在 `clock.load()` 与 NPC/数据 seed 之后调用一次 `await world_state.check_and_advance(db, clock.now())`：若上一次进程在“最后一名山贼胜利结算”之后、写阶段之前退出，重启时能补上；该函数幂等，正常启动返回 `False` 不产生写。

## W3. 断岳刀与世界推进

### W3.1 `game/data.py`

在 `ITEMS` 增加：

```python
{"key": "duanyue_dao", "name": "断岳刀", "type": "weapon", "tier": "rare", "price": 0,
 "effect": {"atk": 28, "crit": 0.08, "unsellable": True}},
```

- 品质沿用现有 `rare`，避免新增品质档位影响 UI。
- `price=0` 且 `unsellable`，双重防止卖店。
- `effect_desc` 末尾追加：`if effect.get("unsellable"): parts.append("神器，不可出售")`。

### W3.2 `game/inventory.py`

`sell()` 的查询增加 `it.effect_json`；解析后若 `unsellable` 为真：

```python
raise InvError("此物不可出售")
```

### W3.3 `game/mobs.py`

新增公开函数：

```python
async def kill_times(db) -> dict[str, float]:
    return await _kills(db)
```

### W3.4 `game/battle.py`

在 `_finalize` 的 `result == "win"` 分支：

1. 通用掉落循环之前，若 `state["enemy"]["type"] == "boss"` 且 `not await world_state.artifact_acquired(db)`：
   - `row = data.item(world_mod.ARTIFACT_ITEM_KEY)`；
   - `await inventory.add(db, p["id"], row["id"])`；
   - `await world_state.mark_artifact_acquired(db)`；
   - `drops.append({"name": row["name"], "qty": 1})`；
   - `add("drop", "拾获 断岳刀——刀身沉黑，隐有寒光。")`。
2. 通用掉落继续保留（玄铁剑仍按原概率掉落）。
3. `await mobs.mark_killed(...)` 之后：
   ```python
   advanced = False
   if mem_store is not None:
       advanced = await world_state.check_and_advance(db, mem_store.clock.now())
       if advanced:
           add("world", "你取回断岳刀的消息，已经传遍了清溪镇。")
   ```
4. `_finalize` 的胜利返回值增加 `"world_announced": advanced`；切磋分支补 `"world_announced": False`；失败/逃跑仍按现状返回 `None`，**不要**为凑字段把 `None` 改成空字典，客户端已按 `rewards != null` 判断。
5. `apply_action` 返回的 `rewards` 形状保持稳定：胜利时始终有 `exp/gold/cultivation/drops/level_ups/world_announced`；`world_announced` 只在本次战斗真正触发阶段推进会时为 `True`。

注意：玩家先杀黑旋风拿刀、后杀三名山贼时，最后一次山贼胜利也会触发 `check_and_advance`；不要只在 boss 分支检查。

## W4. Prompt 注入

### W4.1 `agent/dialogue.py`

- import `world as world_mod`、`world_state`。
- `talk()` 中读取 `stage = await world_state.stage(db)`，并生成 `world_brief = world_mod.brief_for(n, message, stage, current_affinity, p["name"])`。
- `_build_messages` 增加 `world_brief: str` 参数，system prompt 顺序固定：

```
你是{n['name']}。{n['persona']}你说话{n['speech_style']}。
{world_brief}
{_warmth(...)}
你对{p['name']}的印象：...
你记得的一些事：...
你们最近的往来：...
此刻{now_str}，你正在{place}。
以你的口吻应答{p['name']}（≤80字）……
```

- 末尾追加两条硬约束：
  - `不要复述上面的设定，只在相关或你想推进话题时提起。`
  - `不知道的事就岔开、装糊涂或反问，不要编造新人物、新宝物、新门派。`

### W4.2 `agent/greet.py`

`_messages` 增加 `world_brief`、`heart`、`mood_text`；搭话上限仍 40 字。`greet()` 里用 `world_mod.brief_for(n, "", stage, aff, p["name"])` 生成世界简报（搭话没有玩家原话，按无话题处理，只保留氛围句与阶段 2 通告）。示例：

```
你是{name}。{persona}你说话{speech_style}。
{world_brief}
{warmth}
你记得的一些事：...
你眼下挂心的事：...（有则加）
此刻...看见玩家走近。以你的口吻主动招呼一句（≤40字），不要每句都提后山。
```

### W4.3 `agent/reflection.py` / `gossip.py`（可选，W 不做）

W 阶段不改反思与八卦 prompt；等 B/A 完成后再决定是否加入世界简报。避免一次改动面过大。

## W5. API 与客户端

### W5.1 `GET /api/game/state`

新增：

```json
"world": {
  "stage": 0,
  "artifact_acquired": false,
  "announced_at_game": null,
  "announcement": null,
  "premise": [
    "半月前，黑旋风带一伙山贼占了后山崖旧寨……",
    "传闻黑旋风从外头劫来一把神器宝刀“断岳刀”……",
    "破庙的张馒头是镇上唯一出过远门的人……",
    "剑阳道馆、玄铁堂、烟雨小筑都盯着后山……"
  ]
}
```

`world.public_facts_for_client()` 由 `world.py` 提供，返回固定顺序的四条；`announcement` 在阶段 2 返回 `STAGE2_FACT_TEMPLATE.format(player=p["name"])`，否则为 `null`。客户端不复制服务器文案常量。

### W5.2 `GET /api/debug/npc/{id}`

新增：

```json
"world": {
  "stage": 0,
  "stance": "...",
  "knowledge": ["..."],
  "stage2": "..."
}
```

### W5.3 客户端 `main.gd`

- 新增 `var world_info: Dictionary = {}`；`refresh()` 里 `world_info = data.get("world", {})`。
- 左侧“江湖小笺”面板改为两段：
  - 第一段保留原新手提示；
  - 第二段显示 `world_info.premise[0]`（或“后山传闻”一句话）+ 按钮“清溪镇志”；
  - 面板高度从 `156` 调到 `208` 左右，避免按钮被裁切（`_build_scene()` 里 `_panel(self, Vector2(24, 420), Vector2(274, 156), ...)` 这一行）。
- `open_chronicle()`：用现有 `_new_modal(..., kind="chronicle")` 展示四条 `premise`，末尾固定提示：
  > 镇上都说，神器现世之日三派会重开山门；但拜师仍要看掌门对你的交情与你的境界。
  - 阶段 2 时追加 `world_info.announcement`（服务端已给全文，客户端不硬编码）。
- **不要自动弹窗**：Godot 冒烟断言新档创建后 `app.modal == null`，自动弹镇志会破坏既有断言。首次进入只更新面板文案，玩家点按钮才展开。
- `battle_action()` 读取 `rewards.get("world_announced", false)`，为真时 `notify("你取回断岳刀的消息，已经传遍了清溪镇。")`。
- 断岳刀进背包后，现有行囊/装备 UI 自动显示，无需新增代码。

### W5.4 客户端 `town_world.gd`

W 阶段不改。

## W6. 测试

### W6.1 `server/tests/smoke.py`

在现有流程**末尾**追加（先跑完 M4 断言，避免新档/阶段推进影响已有变量）：

1. **世界初始状态**：`GET /api/game/state` 中 `world.stage == 0`、`len(world.premise) == 4`、`artifact_acquired is False`。
2. **debug 立场**：`GET /api/debug/npc/1` 的 `world.stance` 非空，`world.stage == 0`。
3. **黑旋风掉落**：直接构造 boss 胜利状态调用 `battle._finalize`（测试里先 `battle_mod.mem_store = main_mod.mem_store`，否则 `world_announced` 不会触发）；断言背包出现断岳刀、`world.artifact_acquired` 为真、再次击杀不再掉落。
4. **不可出售**：用上一步拿到的断岳刀 `POST /api/shop/clinic/sell`，断言 400；不要为了测试再 `inventory.add` 一把，避免出现两把神器。
5. **推进条件**（按顺序）：
   - 只杀 boss + 拿到刀、未杀满三山贼 → `check_and_advance` 返回 `False`；
   - `mobs.mark_killed` 三名山贼后再检查 → 返回 `True`，`world.stage == 2`；
   - 再次检查 → `False`（幂等）。
6. **全镇通告**：阶段 2 后 `world.brief_for(npc_mod.NPC_ROSTER["gu_changfeng"], "后山那刀怎么回事", 2, 25, "冒烟侠")` 包含 `"冒烟侠杀了黑旋风"`；`GET /api/game/state` 的 `world.stage == 2` 且 `world.announcement` 含玩家名。
7. **拜师规则未变**：阶段 2 下，好感不足的掌门 `POST /api/npc/{id}/apprentice` 仍返回 400。
8. **新档清世界状态**：阶段 2 后带 `{"name":"新冒烟侠","confirm":true}` 再开新档，`world.stage == 0`、`world.artifact_acquired == false`、`mob_kills` 为空，boss/山贼全部存活。
9. 现有断言全绿；`CALLED` 不新增 purpose（W 没有新 LLM 调用）。

### W6.2 `xia-ai-town/tools/smoke_test.gd`

- 新档后断言 `app.world_info.premise.size() == 4`（`world_info` 由 `refresh()` 写入；若实现时改用别的变量名，测试同步改）。
- 直接调用 `app.open_chronicle()`，断言 `app.modal_kind == "chronicle"` 且 `modal_body` 文本包含“断岳刀”；`Esc` 关闭。
- 不模拟世界推进（服务器冒烟覆盖），避免改动测试 fixture 的复杂度。

## W7. 文档与提交

- `docs/DESIGN.md` 增补：
  - §6.2 花名册加“世界立场”一列或链接；
  - 新增 §6.11“世界公设与断岳刀世界事件”；
  - §9 `GET /api/game/state` 增加 `world` 字段；
  - §10 客户端增加镇志面板；
  - §13 增加 M5 行；
  - §14 增加相关配置（若有）。
- 提交：`feat: 实现 M5 世界公设与断岳刀世界事件`（三段式正文）。

---

# Phase B：对话议程、口癖与轻情绪

**目标**：同一 NPC 两场对话的开场/话题不重复；不同 NPC 的关注点不同；对话有“想推进的事”，但**不增加 LLM 调用次数**。

## B1. 花名册与静态议程

### B1.1 `agent/npc.py` 花名册增加口癖示例

每个 NPC 增加 `speech_examples: list[str]`（2~3 句，仅作语气参考，不逐字照抄）：

| NPC | speech_examples |
|---|---|
| 顾长风 | “话不必多。”“你握剑的手，先稳三分。”“庸才。” |
| 铁无双 | “小子，先喝一碗！”“这事俺老铁给你兜着。”“哈哈，痛快！” |
| 苏婉清 | “哦？这话是谁教你的。”“你猜。”“急什么，茶还没凉。” |
| 王大柱 | “客官，这消息可不白给。”“您再加点，我再说一句。”“嗨，都是听来的。” |
| 陈半松 | “唉，年轻人哪……”“先把脉，再说别的。”“当年我也见过这样的伤。” |
| 柳小蝶 | “真的假的？！”“我跟你讲，我亲眼看见的！”“哎哟，那可热闹了。” |
| 张馒头 | “山外山，楼外楼，一把刀儿换馒头。”“疯话不疯，听的人聋。”“刀归你，路归我。” |

### B1.2 新增 `agent/agenda.py`

议程是**预置池 + 确定性抽取**，不由 LLM 生成：

```python
AGENDAS = {
    "test_artifact": "你想知道玩家上没上过后山、见没见过黑旋风。",
    "probe_zhang": "你想试探玩家对张馒头的看法，或知不知道“山外”。",
    "urge_go": "你想怂恿玩家上山。",
    "warn_off": "你想劝玩家别去后山。",
    "boast_info": "你想炫耀自己知道的那点后山传闻。",
    "avoid_artifact": "你想避开神器这个话题。",
    "recruit": "你想替自家门派拉人。",
    "generic": "你想说点别的：炫耀、诉苦或赶时间。",
}
```

权重池：

| NPC | 议程池（id:权重） |
|---|---|
| gu_changfeng | test_artifact:3, recruit:2, warn_off:2, probe_zhang:1 |
| tie_wushuang | urge_go:3, boast_info:2, test_artifact:2, recruit:1 |
| su_wanqing | probe_zhang:3, avoid_artifact:2, warn_off:2, test_artifact:1 |
| wang_dazhu | boast_info:3, test_artifact:2, avoid_artifact:1, generic:1 |
| chen_bansong | warn_off:3, avoid_artifact:2, generic:1 |
| liu_xiaodie | test_artifact:3, boast_info:2, urge_go:1 |
| zhang_mantou | probe_zhang:3, avoid_artifact:2, generic:1 |

抽取规则：

- 用 `random.Random(npc_id * 1000003 + day * 1009 + agenda_seq)` 做确定性加权抽取（不依赖全局 `random` 状态，测试可复现）；
- `agenda_seq` 存 kv `agenda_seq:{npc_id}:{day}`，每场新对话 +1；
- 最近 3 个议程存 kv `agenda_recent:{npc_id}`，权重池中先排除最近用过的；只剩一个时允许重复；
- 当前议程存 kv `agenda_current:{npc_id}`：`{"day":..., "id":..., "text":..., "last_real":...}`；
- 同一场对话 5 现实分钟内复用当前议程；`talk()` 返回 `end=true` 时清除；
- 返回 `{"id":..., "text":..., "avoid": NPC_STANCE[slug]["avoid"]}`。

## B2. 轻情绪

### B2.1 新表 `npc_mood`

```sql
CREATE TABLE IF NOT EXISTS npc_mood (
    npc_id INTEGER PRIMARY KEY REFERENCES npc(id),
    mood TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    until_real REAL NOT NULL
);
```

新表直接写进 `SCHEMA`，不需要 ALTER 迁移。

### B2.2 新增 `agent/mood.py`

```python
MOOD_TEXT = {
    "pleased": "你心情不错，说话比平日热络。",
    "respectful": "你刚见识了玩家的身手，语气里多了几分认真。",
    "impatient": "你有些不耐烦，话比平日短。",
    "wary": "你心存戒备，不愿多说。",
}
async def set(db, npc_id, mood, reason, minutes=30) -> None: ...
async def current(db, npc_id, now_real) -> dict | None: ...
```

### B2.3 触发点

| 事件 | 情绪 | 时长 |
|---|---|---|
| `npc.gift` 成功 | pleased | 30 现实分 |
| 切磋胜利（NPC 视角：玩家赢了） | respectful | 30 现实分 |
| 切磋失败（玩家输了） | pleased | 30 现实分 |
| `talk()` 返回 `end=true` 且好感 <30 | impatient | 20 现实分 |

情绪**只进 prompt 措辞，不改好感数值**，不参与任何结算。所有 `mood.set` 调用都包在 `try/except` 里 best-effort：写失败只打印，不影响送礼/战斗/对话主流程。

## B3. Prompt 组装

`dialogue._build_messages` 增加参数：`agenda`, `mood_text`, `speech_examples`，以及**默认空字符串**的 `heart`（B 阶段先留位，A 阶段再填值）。顺序：

```
你是{name}。{persona}你说话{speech_style}。
{world_brief}
{warmth}
你对玩家的印象：...
你记得的一些事：...
你们最近的往来：...
你眼下挂心的事：{heart}（有则加）
你此刻的盘算：{agenda.text}（有则加）
你不愿提：{agenda.avoid}（有则加）
你说话的样子：参考这些语气，不要逐字照抄：{speech_examples}
你此刻的心气：{mood_text}（有则加）
此刻...你正在...
以你的口吻应答（≤80字）...
```

`greet` 只加世界简报、心事、情绪，不加议程和口癖示例。

## B4. 调试与测试

- `/api/debug/npc/{id}` 增加 `agenda`、`mood`、`speech_examples`。
- 服务器冒烟：
  - 同一 NPC 连续两次 `talk`（未 end）返回相同 agenda id；
  - `end=true` 后下一次 `talk` agenda id 改变（池中 >1 时）；
  - 送礼后 debug 的 `mood.mood == "pleased"`；
  - 断言 stub chat 收到的 prompt 含“盘算”与“心气”；好感数值与没有情绪时一致。
- Godot 冒烟：对话面板仍正常收发；不新增 UI。

## B5. 提交

`feat: 为 NPC 增加对话议程、口癖与轻情绪`

---

# Phase A：每日心事与安全日程偏离

**目标**：每个 NPC 每天有一件心事，并最多一次安全的行程偏离；失败就当没有，行为保持现状。

## A1. 数据与配置

### A1.1 新表 `npc_plan`

```sql
CREATE TABLE IF NOT EXISTS npc_plan (
    npc_id INTEGER NOT NULL REFERENCES npc(id),
    game_day INTEGER NOT NULL,
    heart TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    deviation_json TEXT NOT NULL DEFAULT '{}',
    created_at_real REAL NOT NULL,
    PRIMARY KEY (npc_id, game_day)
);
```

### A1.2 `config.py`

```python
plan_enabled: bool = True            # 心事/偏离总开关
plan_deviation_shift_sec: int = 1200 # 阶段平移时长（游戏秒）= 20 游戏分钟
plan_heart_max_chars: int = 60
```

### A1.3 `llm.py`

```python
PRIORITY = {..., "heart": 3, ...}
```

## A2. 新增 `agent/heart.py`

JSON 契约：

```json
{
  "heart": "一句话，≤60字，第一人称或第三人称均可",
  "reason": "为什么是这件事，≤40字",
  "deviation": "none|gather_early|gather_late|stay_work|home_early",
  "minutes": 10
}
```

校验与兜底：

- `heart` 为空/超长 → 截断或丢弃；
- `deviation` 不在白名单 → `none`；
- `minutes` clamp 到 5~20；
- 失败重试一次（temperature 0.3），仍失败则写入 `heart=""`、`deviation={}` 的空行，**当天不再重试**。

生成素材：最近 10 条记忆、`relationship` 摘要、当前世界阶段、`NPC_STANCE[slug]`。Prompt 要求：

> 挑一件你今天挂在心上的事，必须与你的立场或最近见闻有关；不要写空泛的“想喝酒”。再从安全菜单里选一个今天的小偏离，没有就选 none。

`ensure_today(db, store, npc_id)`：

- `day = int(store.clock.now() // 86400)`；
- 已有行 → 直接返回；
- 无行 → 生成并 `INSERT OR IGNORE`；
- `ensure_all(db, store)` 遍历 `npc_mod.ids()`，单个失败不影响其他 NPC。

## A3. 安全偏离（服务端与客户端同算法）

**只平移现有日程阶段，不新增坐标、不做寻路、不穿墙。** 偏离对象：

```json
{"day": 12, "kind": "gather_early", "shift": 1200}
```

四种偏离：

| kind | 效果 |
|---|---|
| `none` | 原日程 |
| `gather_early` | 镇口聚集段整体提前 `shift` 游戏秒（午间提前去镇口） |
| `gather_late` | 镇口聚集段整体推迟 `shift` 游戏秒 |
| `stay_work` | 跳过聚集段，整个午间留在工作地 |
| `home_early` | 傍晚回家段整体提前 `shift` 游戏秒 |

### A3.1 `game/schedule.py`

`PHASES` 保持原样作为“无偏离日程”。新增 `_phases_for(deviation)` 返回当天的阶段元组，`phase_at(sec_of_day, deviation=None)` 只在 `deviation` 有效时遍历这份元组。

```python
PHASES = (...)  # 原样保留

def _day_phases(deviation: dict | None):
    kind = str((deviation or {}).get("kind") or "none")
    shift = max(0, min(3600, int((deviation or {}).get("shift", 0) or 0)))
    if kind == "gather_early":
        return (
            ("home", 0, 23400),
            ("walk_home_to_work", 23400, 25200),
            ("work", 25200, 39600 - shift),
            ("walk_work_to_gather", 39600 - shift, 40200 - shift),
            ("gather", 40200 - shift, 49800 - shift),
            ("walk_gather_to_work", 49800 - shift, 50400 - shift),
            ("work", 50400 - shift, 68400),
            ("walk_work_to_home", 68400, 70200),
            ("home", 70200, 86400),
        )
    if kind == "gather_late":
        return (
            ("home", 0, 23400),
            ("walk_home_to_work", 23400, 25200),
            ("work", 25200, 39600 + shift),
            ("walk_work_to_gather", 39600 + shift, 40200 + shift),
            ("gather", 40200 + shift, 49800 + shift),
            ("walk_gather_to_work", 49800 + shift, 50400 + shift),
            ("work", 50400 + shift, 68400),
            ("walk_work_to_home", 68400, 70200),
            ("home", 70200, 86400),
        )
    if kind == "stay_work":
        return (
            ("home", 0, 23400),
            ("walk_home_to_work", 23400, 25200),
            ("work", 25200, 68400),
            ("walk_work_to_home", 68400, 70200),
            ("home", 70200, 86400),
        )
    if kind == "home_early":
        return (
            ("home", 0, 23400),
            ("walk_home_to_work", 23400, 25200),
            ("work", 25200, 39600),
            ("walk_work_to_gather", 39600, 40200),
            ("gather", 40200, 49800),
            ("walk_gather_to_work", 49800, 50400),
            ("work", 50400, 68400 - shift),
            ("walk_work_to_home", 68400 - shift, 70200 - shift),
            ("home", 70200 - shift, 86400),
        )
    return PHASES
```

关键点：`gather_early`/`gather_late` 必须同时改变**前一段 work 的结束**和**后一段 work 的开始**，否则 `phase_at` 按顺序命中前一个 `work`，平移不会生效。`stay_work` 是把两段 work 合并成 `25200..68400`，不是把 gather 段简单改名。

- `phase_at(sec_of_day, deviation=None)`：`for kind,start,end in _day_phases(deviation)`；
- `position_for(npc, now_game, deviation=None)`：用 `phase_at(sod, deviation)`；折线/踱步逻辑不变；
- `poi_label_for(npc, now_game, deviation=None)`：同样传 deviation；`stay_work` 在午间返回工作地；
- `is_idle(now_game, paused_until, now_real, deviation=None)`：同样用 deviated phase；
- 默认参数保持 `None`，现有调用方不传行为不变；shift clamp 到 `[0, 3600]`，保证 `39600-shift > 25200` 且 `50400+shift < 68400`。

### A3.2 偏离的读取与所有调用点

新增 `server/app/game/plan.py`（只 import `json` 与 `db`，不 import agent，避免循环依赖）：

```python
DEVIATION_KINDS = ("gather_early", "gather_late", "stay_work", "home_early")

async def deviation_for(db, npc_id: int, now_game: float) -> dict | None:
    day = int(now_game // 86400)
    row = await db.fetch_one(
        "SELECT deviation_json FROM npc_plan WHERE npc_id = ? AND game_day = ?",
        (npc_id, day),
    )
    dev = json.loads(row["deviation_json"]) if row else {}
    return dev if dev.get("kind") in DEVIATION_KINDS else None

async def deviations_for_day(db, now_game: float) -> dict[int, dict]:
    day = int(now_game // 86400)
    rows = await db.fetch_all(
        "SELECT npc_id, deviation_json FROM npc_plan WHERE game_day = ?", (day,)
    )
    out = {}
    for r in rows:
        dev = json.loads(r["deviation_json"] or "{}")
        if dev.get("kind") in DEVIATION_KINDS:
            out[r["npc_id"]] = dev
    return out
```

必须更新以下调用点，否则 NPC 会出现“人被画在工作地、prompt 却说在镇口”的不一致：

| 调用点 | 改法 |
|---|---|
| `main.npcs_state` | 先 `deviations_for_day`，每个 NPC 取 `devs.get(id)`，传给 `position_for` / `poi_label_for`，并把 `heart/deviation` 放进响应 |
| `main.npc_debug` | 取单个 `deviation_for`，`schedule.phase/poi/pos` 全部按偏离计算 |
| `npc.list_for_client` | 接收一个可选 `deviations` 参数（由 `main.game_state` 传），用于 `poi` 字段 |
| `dialogue.talk` | `place = poi_label_for(n, now, await deviation_for(...))` |
| `greet.greet` | 同上 |
| `npc.gift` / `npc.apprentice` | 写记忆前的 `poi` 用偏离后的地点 |
| `gossip.scan` | 一次 `deviations_for_day`，`is_idle` / `position_for` / `poi_label_for` 全部传对应偏离 |
| `reflection.run_once` | 反思记忆的 `poi` 用偏离后的地点 |

### A3.3 `town_world.gd`

- `apply_state` 保存 `entry["deviation"] = remote.get("deviation") if remote.get("deviation") is Dictionary else {}`；
- `schedule_position(entry, now_game)` 调用 `_phase_at(sod, entry.get("deviation", {}))`；
- `_phase_at(sod, deviation)`：
  - `deviation` 为空或 `kind == "none"` → 沿用现有 `schedule.phases` 遍历；
  - 否则用与 `schedule._day_phases` **完全相同的常量与分支**在 GDScript 里算出 `kind/start/end`，不读服务器 `phases`；
- `tick_schedule` 不做额外请求；偏离参数随 10 秒状态同步到达。

## A4. Prompt 与 UI

- `dialogue` / `greet` 注入 `heart`（“你眼下挂心的事：…”），为空则省略。
- `/api/debug/npc/{id}` 增加 `heart` 与 `deviation`。
- 客户端人物名录或对话标题旁显示当前心事（可选，先用 debug 验证；若要显示，读 `npc.heart`）。

## A5. 调度

`agent/scheduler.py` 新增 `_plan_loop`：

```python
while True:
    await asyncio.sleep(settings.scheduler_tick_real)
    if settings.plan_enabled:
        await heart_mod.ensure_all(db, store)
```

- 先 sleep 再工作；
- 单个 NPC 异常只打印，不拖死循环；
- `SCHEDULER_ENABLED=false` 时整条循环不启动，测试不受影响。

## A6. 测试

- 服务器冒烟：
  - stub `heart` 返回固定 JSON；`heart.ensure_today` 写入一行；
  - 同日再次调用不新增行（`COUNT(*) == 1`）；
  - 失败 stub 返回非法 JSON → 空心事行，且不重试；
  - `schedule.position_for` 在 `gather_early` 的边界前后符合预期：聚集开始时间提前 `shift`，位置仍在合法范围；
  - `/api/npcs/state` 输出 `heart/deviation`；
  - `GET /api/debug/npc/{id}` 可见。
- Godot 冒烟：
  - 手动给一个 NPC 设 `deviation = {"day": 当天, "kind": "gather_early", "shift": 1200}`，在聚集开始前后调用 `schedule_position`，断言位置与硬编码期望一致；
  - 恢复原状，确保后续断言不受影响。
- 好感断言：心事/偏离不改变好感。

## A7. 提交

`feat: 增加每日心事与安全日程偏离`

---

# Phase C：附近事件感知（精简版）

**目标**：NPC 能提起“方才那边动静不小”，但不扩大记忆噪声。**C+ NPC 可见闲聊不在本阶段实现。**

## C1. 新增 `agent/events.py`

进程内环形缓冲，不落库；重启丢失可接受：

```python
MAX_EVENTS = 20
TTL_GAME_SEC = 1800  # 30 游戏分钟

def record(store, kind: str, text: str, poi: str, participants: list[str]) -> None: ...
def recent(store, now_game: float, poi: str | None = None, limit: int = 3) -> list[dict]: ...
```

`MemoryStore.__init__` 增加 `self.recent_events: list[dict] = []`。

## C2. 记录点（只记显著事件，遵守噪声原则）

| 位置 | 事件 |
|---|---|
| `battle._finalize` 胜利 | `在{poi}击败了{enemy}` |
| `battle._finalize` 失败 | `在{poi}被打倒` |
| `shop.buy` 且 `cost >= 100` | `在{shop}一掷{cost}两` |
| `npc.gift` | `向{npc_name}送了{item}` |
| `npc.apprentice` | `拜入{sect}` |

POI 映射：`data.py` 增加 `MOB_POI = {"wolf": "东山林地", "bandit": "后山崖", "boss": "后山崖"}`；战斗用敌方 type 取 POI。

## C3. 注入

- `dialogue._build_messages` 增加 `events_text`：`近年附近发生的事：...`，最多 3 条；
- `greet._messages` 同样加；
- 不写入记忆流，不增加 LLM 调用。
- 阶段 2 的“全镇通告”仍来自世界简报，不重复放进事件流。

## C4. 客户端反应气泡（C2，默认不做）

- 若后续要做：`GET /api/game/state` 返回 `recent_events`；客户端只对玩家附近、空闲 NPC 显示模板气泡，模板按 NPC 立场分化，不调 LLM。
- 本阶段先不做，避免新增轮询与刷屏。

## C5. 测试

- 服务器冒烟：记录一条战斗事件后，`talk` 的 prompt 含该事件文本；超过 TTL 后不再出现；事件不写入 `memory` 表。
- Godot 冒烟：不新增断言。

## C6. 提交

`feat: 增加 NPC 附近事件感知（精简版）`

---

# 回归、验收与回退

## 回归命令

```bash
# 无 Ollama 后端回归
server/.venv/bin/python server/tests/smoke.py

# Godot 四阶段冒烟（隔离后端 + 临时库）
server/.venv/bin/python xia-ai-town/tools/smoke_test.py
```

手动验证：

```bash
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  server/.venv/bin/uvicorn app.main:app --reload --port 8000
godot --path xia-ai-town

curl --noproxy '*' http://127.0.0.1:8000/api/game/state
curl --noproxy '*' http://127.0.0.1:8000/api/debug/npc/1
```

## 验收标准

**W：**

- 同一句“后山那神器到底怎么回事？”问七个 NPC，得到七种立场、信息量、语气不同、事实不矛盾的回答；
- 张馒头以外的人不会说出核心真相；王大柱、柳小蝶可夸张但不新增关键事实；
- 问不知道的细节会岔开/装糊涂，不编造；
- 击杀三名山贼 + 黑旋风 + 取得断岳刀后，七个 NPC 下一次对话都承认这件事，且说法符合立场；
- 拿到刀但好感不足时，拜师仍被拒绝；
- 断岳刀唯一、不可出售、不重复掉落；老存档不凭空补发；
- 镇志面板可见，不自动弹窗、不破坏既有冒烟。

**B：**

- 同一 NPC 两场对话开场/议题不重复；
- 议程、口癖、情绪在 debug 可见；
- 情绪不改好感数值；
- 不增加 LLM 调用次数。

**A：**

- 每个 NPC 每天最多一次心事、最多一次偏离；
- 偏离不卡墙、不穿建筑，服务端与客户端位置一致；
- LLM 失败当天无心事、无偏离，玩法照常；
- 重启后当天心事不重新生成。

**C：**

- 附近显著事件能在对话/搭话中被提起；
- 事件不写入长期记忆；
- 不新增轮询、不刷屏。

## 回退

- Phase W：移除 `world.py`/`world_state.py`，删除断岳刀 item，回退 prompt 注入与 `world` API 字段。世界阶段只影响措辞，回退不影响存档。
- Phase B：删除 `agenda.py`/`mood.py` 与 `npc_mood` 表；prompt 参数有默认值，删除后对话恢复现状。
- Phase A：设置 `plan_enabled=false` 可立即停用；删除 `npc_plan` 表与 `heart` purpose；`deviation` 默认 `None`，日程恢复原状。
- Phase C：删除 `events.py` 与记录点；不落库，无数据迁移。

---

# 仍需确认

1. Phase B 的情绪触发点（送礼/切磋/冷落）是否按上表执行？默认只影响语气。
2. Phase A 的偏离只做“日程阶段平移”（提前/推迟去镇口、留工作地、早回家），不做真正的新路径寻路，是否可以接受？
3. Phase C 是否先只做“对话里能提起附近事件”，可见反应气泡留到下一版？
4. 四个 Phase 是分四次提交，还是先只做 W+B、观察效果后再做 A+C？建议 W → B → 停下观察一轮。
