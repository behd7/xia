# M2 成长系统 — 实施拆解

> 本文档是 M2 的直接执行清单：数值、规则、接口形状、文件落点、验证步骤全部内联，实施时**不需要回查 DESIGN.md**（但改设计仍须同步回 DESIGN.md）。
> 动手前先读根目录 `AGENTS.md` 的「已知坑」——尤其是：Phaser 场景字段跨重启存活（新面板类同样要防）、battle.state_json 与 player 行的数据分工、http_proxy 需 NO_PROXY、E2E 按键要按住 ≥150ms。

- **验收标准（DESIGN §13）：入门剑阳派并在战斗中用出赤阳斩。**
- 交付物：属性分配、装备穿卸/卖店、技能学习/升级/装备/战斗结算（含 Buff/CD/特效）、拜师（M2 纯条件判定版）。

## 0. 现状复用清单（M1 已就位，不要重做）

| 已有 | 位置 | M2 怎么用 |
|---|---|---|
| `skill` / `player_skill` / `inventory.equipped_slot` / `player.free_points·cultivation·sect_id` 表 | db.py §8 全量 schema | 直接写入；**唯一缺口**：`player_skill` 没有"已装备"列，见 §6 迁移 |
| 升级逻辑（四维+1、自由点+3）、修炼点累积（胜场 exp×0.6） | `player.gain_exp` / battle `_finalize` | 属性分配只差入口 |
| 商店购买、战斗四指令+药剂子菜单、浮字播放、断线重入 | Shop.ts / BattleScene | 技能子菜单、卖店、面板全部照抄既有模式 |
| 静态数据模式（key→行，启动种子+内存目录） | `data.py` `seed_and_load`/`item()` | SKILLS/SECTS 沿用同一模式 |
| 面板 UI 样式（列表+按钮+Esc 关闭+`uiBlocked` 门控） | `ui/Shop.ts` + FieldScene.update | 三个新面板复用此骨架 |
| 怪物/物品/商店全量表 | `data.py` ITEMS/MOB_TYPES/MOB_SPAWNS/SHOPS | 装备效果从 ITEMS 的 effect_json 读，不另建表 |

## 1. 需求拆解（四块）

### 1.1 属性分配
- 玩家在角色面板把 `free_points` 一次性分配到四维（可多次），server 校验：总和 ≤ free_points、值为正整数。
- 分配后**立即生效**于派生属性与 HUD（客户端刷新 player）。

### 1.2 装备（§5.4）
- 3 槽位：武器 weapon / 衣服 armor / 饰品 accessory，槽位由 item.type 推导（weapon/armor/accessory）。
- 背包面板点"装"→ `inventory.equipped_slot` 置槽；该槽原装备自动回到背包（equipped_slot 置 NULL）。饰品没有"替换冲突"以外的规则。
- 卖店：任意商店可卖，**半价**（price//2 向下取整）；装备中的物品必须先卸下才能卖；卖杂货（狼皮）同路径。
- 派生属性吃装备聚合（公式见 §2.3），玉佩的 `int+1` 参与暴击/内力公式。

### 1.3 技能（§5.3）
- 目录：3 派 ×（1 被动内功 + 3 主动）。等级 L1~L5；主动倍率 `base + 0.15×(L−1)`；被动每级给主属性 +1。
- 升级花修炼点：L→L+1 耗 `20×L`（即 20/40/60/80），被动主动同曲线；5 级封顶。
- 最多装备 3 个主动技能（战斗中才能选用）；城内随时改。
- 战斗中"技能"指令：列出已装备主动技能（名字+MP+CD 状态），MP 不足或 CD>0 灰显；选用消耗 MP、进入冷却、按倍率与特效结算（§4）。

### 1.4 拜师（M2 特化 = 纯条件判定，不涉对话）
- 三派据点是 M1 地图上已有的建筑：剑阳道馆 / 玄铁堂 / 烟雨小筑。走到门口区按 E → 弹确认面板（门派名、定位、入门加成、条件校验结果）→ 确认即入派。
- **M2 条件**：无门派 且 等级≥3（好感度系统 M3 才有，先用等级替位；M3 换成"好感≥60 且 等级≥3 + 对话式"）。
- 入派效果（一次性，写死 player 行）：入门加成四维上调（§2.2）+ 插入 `player_skill`：内功 L1 + 本派一档主动 L1，且**一档主动自动装备**（equipped=1）。
- **一生只入一派**：已入派再请求 → 400。
- 位阶（入门/亲传/关门）M2 只会到"入门弟子"（亲传关门需好感，M3）；技能面板对二档三档显示锁定原因（"需亲传/关门弟子（M3 好感系统）"），目录与 tier_req 字段先行落库。

## 2. 数据常量（全部内联，勿再查设计文档）

### 2.1 技能目录（写入 `data.py` 或新建 `game/skills.py`，启动种子进 skill 表）

| key | 名称 | 派派 | kind | tier | MP | 倍率 | effect_json |
|---|---|---|---|---|---|---|---|
| lieyang_jue | 烈阳剑诀 | jianyang | passive | 1 | 0 | — | `{"stat":"str","per_level":1}` |
| chiyang_zhan | 赤阳斩 | jianyang | active | 1 | 8 | 1.6 | `{}` |
| fenglei_shi | 风雷剑势 | jianyang | active | 2 | 14 | 1.9 | `{"crit_add":0.20}` |
| xuri_sheng | 旭日东升 | jianyang | active | 3 | 26 | 2.6 | `{"cooldown":2}` |
| xuantie_dao | 玄铁刀法 | xuantie | passive | 1 | 0 | — | `{"stat":"con","per_level":1}` |
| nulang_dao | 怒浪刀 | xuantie | active | 1 | 8 | 1.5 | `{"buff_self":{"def_pct":1.10,"rounds":1}}` |
| tiebu_shan | 铁布衫 | xuantie | active | 2 | 12 | 0.8 | `{"buff_self":{"def_pct":1.40,"rounds":2}}` |
| kaishan_dao | 开山刀 | xuantie | active | 3 | 26 | 2.4 | `{"pierce_def":0.5}` |
| yanyu_xin | 烟雨心法 | yanyu | passive | 1 | 0 | — | `{"stat":"agi","per_level":1}` |
| mizong_bu | 迷踪步 | yanyu | active | 1 | 6 | 1.2 | `{"buff_self":{"dodge_add":0.15,"rounds":2}}` |
| xiyu_ci | 细雨十三刺 | yanyu | active | 2 | 15 | 1.8 | `{"ignore_dodge":true}` |
| feihong_guan | 飞虹贯云 | yanyu | active | 3 | 22 | 2.2 | `{"double_if_faster":1.5}` |

- `tier`：1=一档(入门弟子) 2=二档(亲传) 3=三档(关门)；`multiplier` 列存 base 倍率。
- 等级倍率公式（battle 结算时现算，不落库）：`mult = base + 0.15*(level-1)`。

### 2.2 门派（SECTS）

| slug | 名称 | 据点 POI（FieldScene BUILDINGS.label） | 入门加成（一次性直改四维） | 内功 key |
|---|---|---|---|---|
| jianyang | 剑阳派 | 剑阳道馆 | 臂力+3、悟性+2 | lieyang_jue |
| xuantie | 玄铁堡 | 玄铁堂 | 根骨+3、悟性+2 | xuantie_dao |
| yanyu | 烟雨楼 | 烟雨小筑 | 身法+3、悟性+2 | yanyu_xin |

位阶表（解锁判定用，M2 只实现 rank=1）：入门弟子=好感≥60 且 lv≥3（M2 以 lv≥3 替位）解锁内功+一档；亲传=75/5 解锁二档；关门=85/10 解锁三档。

### 2.3 派生属性（§5.1，含装备与被动聚合）

```
mods = {str,con,agi,int 各 += 被动内功等级×per_level}          # 装备前聚合
HP_max  = 80 + (con+mods.con)×12 + level×8                      # 无装备项
MP_max  = 50 + (int+mods.int+eq.int)×8 + level×5 + eq.mp_max
atk     = 8 + (str+mods.str)×2 + eq.atk
def     = 4 + (con+mods.con)×1 + eq.def
spd     = 10 + (agi+mods.agi)×1.5                               # 无装备项
dodge   = min((agi+mods.agi)×0.008, 0.25)                       # 战斗中再 + buff
crit    = min(0.05 + (int+mods.int+eq.int)×0.003 + eq.crit, 0.30)
```

- `eq.*` 来自三件已装备物品的 effect_json 聚合（atk/def/crit/mp_max/int）。
- **实现落点**：`player.derived()` 改为 `derived(p, mods)` 纯函数 + 新增 `async def modifiers(db, p)`（读装备 + player_skill 被动等级）一次聚合；所有调用方（battle/shop/game state）改走 `player_mod.full_view(db, p)` 这类单一入口，避免漏聚合。注意入门加成是直改 player 行四维（一次性 UPDATE），不进 mods。

### 2.4 装备效果（M1 ITEMS 已有，无需改）
铁剑攻+8 / 精钢剑攻+15 / 精铁匕首攻+6 / 玄铁剑攻+24且暴击+5% / 布袍防+4 / 精革甲防+9 / 玉佩 MP上限+40且悟性+1。

## 3. API 新增（全部需同步回 DESIGN.md §9）

统一：错误用 HTTPException(400/409, 中文)；成功响应附最新 `player` 全量视图（含 `sect`/`rank`/`equipped`/`skills`，见 §5 game/state 扩展）。

| Method & Path | Body | 说明 |
|---|---|---|
| POST `/api/game/allocate` | `{stats:{str,con,agi,int}}` | 校验总和 ≤ free_points；扣点、四维上调 |
| POST `/api/game/equip` | `{item_id}` | 槽位按 item.type 推导；原槽装备自动卸回背包 |
| POST `/api/game/unequip` | `{slot}` | slot ∈ weapon/armor/accessory |
| POST `/api/game/skill/equip` | `{skill_id, equip}` | 装/卸主动，装备数 ≤3；被动不可 equip |
| POST `/api/game/skill/upgrade` | `{skill_id}` | 校验已拥有、等级<5、修炼点 ≥ 20×L；扣点升级 |
| POST `/api/sect/join` | `{sect_id}` | M2 条件：无门派 且 lv≥3；入派发内功+一档（自动装备） |
| POST `/api/shop/{shop_id}/sell` | `{item_id, qty}` | 半价 `price//2`；装备中不可卖；校验库存 |

战斗既有接口扩展：`POST /api/battle/action {action:"skill", id: skill_id}`（M1 目前返回"技能系统将于 M2 解锁"，改为真结算）。

## 4. 战斗技能结算语义（battle.py，严格按此顺序实现）

前置校验：player_skill 拥有该技能、kind=active、equipped=1、MP ≥ mp_cost、`state_json.skill_cd[skill_id]` 为 0 或不存在。

1. **扣 MP、置 CD**（effect_json 有 cooldown 则 `skill_cd[key] = cooldown`）。
2. **命中**：effect 有 `ignore_dodge` → 必中；否则对目标 dodge 掷闪避（玩家闪避含 `buffs.player.dodge_add`）。
3. **伤害**：`dmg = max(1, round(atk × mult − def_eff))`，其中 `mult = base + 0.15×(L−1)`、`def_eff = 目标def × 目标buff.def_pct × (有pierce_def ? (1−pierce_def) : 1)`。
4. **暴击**：掷 `crit + effect.crit_add`（风雷），命中后 ×1.5。
5. **双击**（飞虹贯云）：`spd > 敌spd × double_if_faster` → 立刻再完整结算一次攻击（独立闪避/暴击），两条 log。
6. **自 Buff**（怒浪/铁布/迷踪）：写入 `state_json.buffs.player = {def_pct 或 dodge_add, rounds}`；同类刷新时 rounds 取 max；铁布衫自身倍率 ×0.8 照打伤害。
7. **回合末**：双方 buffs 各 rounds−1，归零删除；`skill_cd` 全部 −1（下限 0）；战斗结束（胜/负/逃）清空 buffs 与 CD。
8. **文案**：`你使出赤阳斩，造成 24 点伤害（会心一击）。` 技能名+浮字同攻击路径；Buff 生效打一条 `真气护体，防御提升。`；CD 中/MP 不足在子菜单灰显并给原因。
9. 敌方 AI 不动（M1 决定：怪不配技能、不掷暴击；规则分支 2/3 保持落空即可）。

`state_json` 增量字段（沿用 M1 断线重入约定，重启不丢）：
```
"buffs": {"player": {"def_pct": 1.40, "dodge_add": 0.15, "rounds": 2}, "enemy": {}},
"skill_cd": {"chiyang_zhan": 0}
```

## 5. Server 文件落点

| 文件 | 改动 |
|---|---|
| `app/game/skills.py`（新建） | SKILLS/SECTS 目录与种子、tier/rank 判定、upgrade/equip/learn 的校验与写库 |
| `app/game/data.py` | 不动技能目录（进 skills.py 避免数据文件膨胀）；如需共享常量从这里 import |
| `app/game/player.py` | `modifiers(db,p)`、`derived(p,mods)` 改签名、`full_view`（api_dict 扩展：sect/rank/equipped/skills）、`allocate` |
| `app/game/inventory.py` | `equip/unequip`（改 equipped_slot）、`sell`（校验+扣库存+半价入账） |
| `app/game/battle.py` | skill action 全结算（§4）、buffs/CD 进 state_json、api_dict 出 buffs 供 UI |
| `app/game/shop.py` | sell 端点逻辑（复用库存校验） |
| `app/main.py` | 接线 §3 全部路由；lifespan 里 seed SKILLS |
| `app/db.py` | SCHEMA：`player_skill` 加 `equipped INTEGER NOT NULL DEFAULT 0`（见 §6 迁移） |

`game/state` 的 player 视图新增字段：
```json
"sect": {"id":"jianyang","name":"剑阳派","rank":"入门弟子"} | null,
"equipped": {"weapon": {…item…} | null, "armor": …, "accessory": …},
"skills": [{"skill_id":3,"name":"赤阳斩","kind":"active","tier":1,"level":1,
            "mp_cost":8,"multiplier":1.6,"effect_desc":"—","equipped":true,
            "upgradable":true,"upgrade_cost":20,"locked":false}]
```

## 6. Schema 迁移与 DESIGN.md 同步义务

- **唯一 schema 变更**：`player_skill` 加 `equipped`。`CREATE TABLE IF NOT EXISTS` 不会改旧表——dev 库直接删 `server/data/xia.db*` 重建（gitignore、可弃）。
- DESIGN.md 需同步三处：§8（player_skill.equipped）、§9（§3 的 7 个新路由）、§9 备注里"拜师 M2 纯条件判定、M3 换对话式"。
- 提交规范照 AGENTS.md（一个任务一个 commit，三段式）。

## 7. Client 文件落点

| 文件 | 改动 |
|---|---|
| `src/api/client.ts` | §3 全部类型与方法；PlayerState 扩 sect/equipped/skills/free_points（已有） |
| `src/ui/CharacterPanel.ts`（新建） | I 键：四维+分配按钮（+1，消耗 free_points）、派生属性只读、门派/位阶 |
| `src/ui/InventoryPanel.ts`（新建） | B 键：物品列表，装备类有 装/卸，药类无操作（战斗内用），杂货/多余装备可去商店卖 |
| `src/ui/SkillsPanel.ts`（新建） | K 键：本派技能列表（锁定项灰显+原因）、升级按钮（修炼点余额）、主动装备开关（≤3） |
| `src/ui/JoinSectPanel.ts`（新建） | 道馆门口 E 弹出：门派卡（定位/加成/条件）→ 确认/取消 |
| `src/scenes/FieldScene.ts` | SECT_AREAS（照 SHOP_AREAS 模式，建筑外扩 2 格）；I/B/K/E 键盘互斥与 uiBlocked；面板开时移动冻结（照 shopOpen 既有逻辑） |
| `src/scenes/BattleScene.ts` | 技能按钮启用：子菜单列 equipped 主动（MP/CD 灰显）→ doAction("skill", id)；面板加 buffs 行（"御守：防御+40%（2回合）"）；**新 UI 对象全部在 create() 里重建，禁止类字段残留**（AGENTS.md 已知坑） |
| `src/ui/Hud.ts` | lvText 后缀门派名（`测试侠 · 3 级 · 剑阳派`） |

## 8. 验证计划（先 curl 冒烟，再浏览器 E2E）

**curl 冒烟**（server 起在 8000，注意 `--noproxy '*'`；fresh db）：
1. `POST /api/game/new {"name":"武测侠"}`；
2. 快速到 3 级：直接 `sqlite3 server/data/xia.db "UPDATE player SET exp=99"` → 打一只狼触发 gain_exp 到 lv3（或干脆 UPDATE level=3,free_points=9）；
3. `POST /api/game/allocate {"stats":{"str":3,"con":3,"agi":3,"int":0}}` → 校验 free_points 9→0、atk+6；
4. `POST /api/sect/join {"sect_id":"jianyang"}` → sect 写入、str+3 int+2、player_skill 4 行（内功 L1 + 赤阳斩 L1 equipped）；
5. `POST /api/game/equip {"item_id":<铁剑>}`（先 buy 或 UPDATE inventory）→ api_dict atk +8；
6. `POST /api/game/skill/upgrade` 修炼点不足 → 400；给足修炼点（UPDATE）→ 赤阳斩 L2（倍率 1.75）；
7. `POST /api/battle/start wolf_1` → `action skill id=赤阳斩` → log 含"赤阳斩"、MP −8、伤害按 1.6（L1）倍率；
8. 卖店：`POST /api/shop/clinic/sell {"item_id":<狼皮>,"qty":1}` → 银两 +20。

**浏览器 E2E**（复用 /tmp/pwtest 模式：playwright-core + 本地字体 + NO_PROXY + 按键按住 150ms）：
建档 → 打 2 只狼 → I 面板分配自由点 → 走到剑阳道馆 E 拜师（确认面板）→ K 面板看到赤阳斩已装备 → 撞狼 → 技能子菜单选赤阳斩 → 浮字与 log 出现技能名 → 胜利 → B 面板装备掉落物 → 药铺卖狼皮 → 全程零 jsErrors、截图核对三个新面板。

**回归**：不装技能时战斗技能按钮回到灰显；药剂上限 3、逃跑、死亡复活路径不回归。

## 9. 明确不做（防 scope 蔓延）

- 亲传/关门位阶与二档三档技能解锁（等 M3 好感）；技能目录先行落库、面板灰显即可。
- 对话式拜师/切磋/送礼（M3）；野外喝药（设计未定义，不做）；敌方技能与敌方暴击（M1 已定，保持）。
- 音效、存档槽、装备分解/附魔（V1 明确不做）。
- 修炼点不给杂货卖价之类的经济调整——数值表按 DESIGN 原样，实测调平是 M1 之后单独任务。
