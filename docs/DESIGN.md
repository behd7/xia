# 《侠》— 二维像素武侠 + 生成式 NPC 实验游戏 设计文档

- 版本：v1.0（草案）
- 日期：2026-10-16
- 状态：待评审
- 风格参照：《弈剑风云决》（像素武侠、门派成长、市井烟火气）
- AI 参照：Generative Agents（斯坦福小镇，Park et al. 2023）——记忆流 / 检索 / 反思

---

## 1. 项目概述

### 1.1 愿景

做一款**使用 Godot 4 客户端的二维像素武侠 RPG**，其核心实验点是**生成式 NPC**：

- NPC 拥有长期记忆流（Memory Stream），玩家的一切与之相关的行为（对话、送礼、切磋、在附近战斗、买东西）都会写入其记忆；
- NPC 对话基于 RAG 检索记忆生成，由本地 Ollama（qwen3:4b-instruct）驱动；
- NPC 定期**反思**记忆、维护与玩家/其他 NPC 的**关系摘要**，好感度随之演化；
- 好感度达到阈值后 NPC 才会主动搭话，低于阈值时玩家可以主动攀谈但对方态度冷淡。

游戏本体（门派、装备、药剂、属性、技能、回合制战斗）第一版**能简则简**，为 NPC 实验服务。暂无主线任务。

### 1.2 核心循环

```
野外打怪 → 获得经验/银两/掉落 → 属性与技能成长
    ↑                                ↓
与 NPC 交流/送礼/切磋 → 好感度提升 → 拜师入门 → 解锁门派技能 → 更强战斗
    ↘ NPC 记忆/反思/八卦 → NPC 态度与信息随时间演化 ↙
```

### 1.3 V1 范围

**包含**：一座小镇+两片野外的单张地图、3 门派、7 个智能 NPC、属性/等级、装备（3 槽位）、恢复药剂、技能、1v1 回合制战斗、自由文本对话、记忆流+RAG、好感度、反思、关系演化、NPC 八卦传播、NPC 主动搭话、单存档自动保存。

**不包含（留待后续版本）**：主线/支线任务、队友与多人战斗、交易所/摆摊、昼夜光照与天气、音效音乐、NPC 完整自主规划（V1 仅有固定日程：工作/午间镇口聚集/回家）、存档槽多开、移动端适配、存档回滚。

---

## 2. 已确认的技术决策

| 决策点 | 选择 |
|---|---|
| 客户端 | Godot 4 + GDScript（`xia-ai-town/`）；原 `client/` Phaser 前端已删除 |
| Agent 后端 | Python FastAPI（单进程，承载游戏逻辑 + NPC 智能体） |
| 存储 | SQLite（游戏状态 + 记忆记录，WAL 模式，记忆的 SSOT）+ ChromaDB（向量索引，PersistentClient 本地目录） |
| 向量检索 | **ChromaDB**：单集合 `memories`（cosine 空间）存 embedding 做 ANN 检索；记忆正文与元数据仍在 SQLite，Chroma 为可全量重建的派生索引 |
| 对话模型 | Ollama `qwen3:4b-instruct`（Q4 量化，禁用思考模式） |
| 向量模型 | Ollama `qwen3-embedding:0.6b`（Q8 量化，1024 维，向量归一化后存库） |
| 美术 | Godot 原生程序绘制像素地图、人物与纸墨 UI，无外部图片依赖，后期可替换 |
| 存档 | 单玩家单存档，事件后自动保存 |

> 模型名按 Ollama 本地 tag 配置（如 `qwen3:4b-instruct-q4_K_M`），走 `.env` 配置，代码不写死。

---

## 3. 总体架构

```
┌─────────────────────────┐        HTTP (JSON)         ┌──────────────────────────────┐
│  xia-ai-town (Godot 4)   │ ◄──────────────────────► │  server (FastAPI + uvicorn)   │
│  场景渲染 / 输入 / UI     │   /api/game /api/npc ...  │  ├─ game: 状态·战斗·商店·存档    │
│  NPC 行走由日程表本地推算  │                           │  ├─ agent: 记忆流·RAG·反思·关系  │
└─────────────────────────┘                           │  ├─ scheduler: 反思/八卦 后台任务│
                                                      │  └─ llm: Ollama 客户端(优先级限流)│
                                                      └──────────────┬───────────────┘
                                                                     │ HTTP
                                                              ┌──────▼──────┐
                                                              │   Ollama     │
                                                              │ qwen3:4b-it  │
                                                              │ qwen3-emb:0.6b│
                                                              └─────────────┘
                                                      ┌──────────────┐
                                                      │ xia.db SQLite + chroma/ (ChromaDB) │
                                                      └──────────────┘
```

关键原则：

1. **服务器权威**：属性、背包、战斗、好感度、记忆全部以服务器为准；客户端只负责渲染与输入。玩家移动为客户端本地，交互时上报位置。
2. **NPC 位置零同步**：NPC 位置 = 日程表(游戏时钟)的确定性函数，客户端用同一份日程数据本地推算，无需轮询（对话中 NPC 暂停，由服务器下发暂停标记）。
3. **战斗为同步请求-响应**：`POST /battle/action` 一次性返回双方回合结算结果，无 WebSocket，V1 不需要。

---

## 4. 地图与场景

单张确定性网格地图，尺寸 100×60，tile 16px，世界坐标 1600×960；Godot 客户端逻辑分辨率 1440×900、保持宽高比缩放，镜头跟随玩家，滚轮调整地图缩放。瓦片分布、建筑与怪物坐标沿用后端和原地图约定。

```
┌───────────────────────────────────────────────────────────┐
│  ❰清溪镇❱                                                  │
│  ┌────┐ ┌────┐ ┌────┐        ┌──────────┐                  │
│  │剑阳│ │玄铁│ │烟雨│        │ 练武场     │   镇口           │
│  │道馆│ │堂  │ │小筑│        │(开阔空地)  │  (水井)          │
│  └────┘ └────┘ └────┘        └──────────┘                  │
│  ┌────┐ ┌────┐ ┌────────┐        ┌───────┐                  │
│  │药铺│ │铁匠│ │  客栈    │        │破庙    │                  │
│  │(陈)│ │铺(王)││ (柳+柳) │        │(张馒头)│                  │
│  └────┘ └────┘ └────────┘        └───────┘                  │
│═══════════════ 镇墙·镇门 ═══════════════════════════════    │
│  ❰东山林地❱(弱怪: 野狼×3)      ❰后山崖❱(强怪: 山贼×3+头目)   │
└───────────────────────────────────────────────────────────┘
```

**POI 清单**：客栈（复活点/恢复）、药铺（药剂）、铁匠铺（装备）、练武场（NPC 日间聚集点、切磋）、剑阳道馆/玄铁堂/烟雨小筑（三馆，武馆性质；馆主所在，详见 §5.2）、破庙（张馒头）、镇口（八卦聚集点）、东山林地、后山崖、后山深处（M6 新增，击败黑旋风后开放）。

**野怪**（可见实体，碰撞触发战斗，击杀后 60 秒重生，区域上限 3 只）：

| 怪物 | 等级 | HP | 攻 | 防 | 速 | 经验 | 银两 | 掉落 |
|---|---|---|---|---|---|---|---|---|
| 野狼 | 1 | 60 | 12 | 3 | 11 | 15 | 5 | 狼皮(杂货,40%)、小回春散(30%) |
| 山贼 | 3 | 120 | 20 | 6 | 10 | 35 | 20 | 小回春散(50%)、精铁匕首(良品,8%) |
| 黑旋风(头目) | 5 | 260 | 30 | 9 | 12 | 120 | 100 | 玄铁剑(稀有,100%) |

**清溪镇等级带（M6 调整）**：现有野怪 1~5 级只覆盖新手期；击败黑旋风后开放“后山深处”，补 6~14 级精英怪（悍匪、毒蛇、独眼盗），让玩家在离开清溪镇前能练到功法上限 15。具体怪物表在 M6-C 内容阶段定。

**南风城（规划，M6-C 实施）**

南风城是第二张地图，推荐等级 15~50，是玩家第一次接触“正式门派 + 高上限功法”的地方。V1 做城门、城主府、两派驻地、药铺/铁匠铺/客栈与三片野外，不做城内自由民居。

| 区域 | 等级带 | 内容 |
|---|---|---|
| 城郊 | 15~22 | 流寇、野犬，开始出现带属性怪 |
| 风蚀谷 | 22~32 | 风系妖物，风池派试炼地 |
| 赤砂矿 | 32~42 | 火/土系矿盗，赤地门试炼地 |
| 城主试炼 | 42~50 | 城主府秘境，南天成守关 |

**势力与 NPC（V1 最低配置）**：

| NPC | 身份 | 等级 | 功法 | 作用 |
|---|---|---|---|---|
| 待定 | 风池派门主 | 50 | 大风诀·真传（风，cap50；外传 cap45） | 拜师/传功 |
| 待定 | 赤地门门主 | 50 | 赤阳功·真传（火，cap50；外传 cap40） | 拜师/传功 |
| 南天成 | 城主 | 52 | 南天诀（暂定，雷/光，cap55） | 城主试炼/高阶传功 |
| 待定 | 城门校尉 | 25 | 制式功法（土/金，cap30） | 指路/治安事件 |
| 待定 | 药铺/铁匠/客栈 | 5~15 | 无功法或低阶功法 | 市井功能位 |

- 南风城与清溪镇通过南门官道相连，不设硬等级门槛；等级不足时只给“城外凶险”提示。
- V1 NPC 不跨图：每个 NPC 属于一张地图，日程只在本地图内推算；`map_id` 进 `npc` 与 `memory` 的取址键，避免两张图的“客栈”串记忆。
- 南风城两派是正式门派，同一时间只能入一派；清溪镇三馆只是武馆，可兼学，详见 §5.2。
- 城主功法、门主姓名、支持 NPC 名单与具体怪物表都是 M6-C 的开放项，先留位。

---

## 5. 游戏系统

### 5.1 属性、等级与功法境界

**修为等级**是角色唯一的等级，范围 1~100。它同时是“人物等级”和“当前功法的修炼进度”：角色修哪门功法，等级就受哪门功法的上限约束。完整功法表见 §5.2，属性克制见 §5.8。

| 名词 | 定义 |
|---|---|
| 修为等级（人物等级） | 唯一等级，1~100；上限 = 当前功法 `cap_level` |
| 功法 | 属性 + 上限 + 内功被动 + 招式列表；同一时间只修一门 |
| 同源转换 | 换同属性功法，修为等级/经验保留 |
| 散功 | 换异属性功法，修为等级归 1 |

**一级属性**（升级各+1，每级另获 3 自由点）：

| 属性 | 作用 |
|---|---|
| 臂力 | 每 1 点 攻击+2 |
| 根骨 | 每 1 点 HP上限+12、防御+1 |
| 身法 | 每 1 点 速度+1.5、闪避+0.8% |
| 悟性 | 每 1 点 MP上限+8、暴击+0.3%、技能升级耗悟点-2% |

**二级属性（派生）**：
- HP上限 = 80 + 根骨×12 + 等级×8
- MP上限 = 50 + 悟性×8 + 等级×5
- 攻击 = 8 + 臂力×2 + 装备攻击
- 防御 = 4 + 根骨×1 + 装备防御
- 速度 = 10 + 身法×1.5
- 闪避 = 身法×0.8%（上限 25%）；暴击 = 5% + 悟性×0.3%（上限 30%），暴伤 ×1.5

新建角色：四维各 5、等级 1、银两 100。升级经验：`等级² × 20`。

**核心规则**：

1. **等级 ≤ 当前功法上限**；上限是硬天花板。**不设学习等级下限**：1 级也能学上限 100 的功法，但学完仍是 1 级，上限只是天花板，不是起始等级。
2. **未习功法视为“凡体”**：无属性，上限 15；第一门功法不触发散功，等级保留。
3. **同源转换**：换同属性功法，等级/经验保留，上限换成新功法的；新上限低于当前等级时需确认，确认后压到新上限，超出经验清零。
4. **异源转换（软散功）**：等级归 1、经验归 0；四维、已分配自由点、装备、背包、银两、已学功法图鉴保留；旧功法招式封存，新功法招式从 L1 开始。
5. **到达上限后**：人物经验停止增长，战斗仍照常给修炼点；UI 显示“瓶颈”。换一门更高上限的同属性功法后继续升级。
6. 100 级为全局硬上限；切换功法后已装备主动招式清空，只保留新功法已解锁的招式。

例：6 级火系·烈阳剑诀（cap15）→ 练到 10 级 → 得到赤阳功（火，cap40）：仍是 10 级，上限变 40；若得到大风诀（风，cap45）：散功，变 1 级，四维/装备/图鉴保留，风系招式从 L1 重练。“无缝”只指修为等级和经验，不指招式熟练度。

**修炼（技能货币）**：战斗胜利获 `经验×0.6` 修炼点，用于招式升级；招式升级费见 §5.3。

### 5.2 门派、武馆与功法

玩家初始**无门派**（仅“拳脚”普攻）。**身份与功法解耦**：清溪镇三馆是武馆，可兼学；南风城两派是正式门派，同一时间只入一派，保留“一生只入一派”。

**清溪镇三馆**（剑阳道馆/玄铁堂/烟雨小筑）：不占正式门派身份，交学费 + 馆主好感≥20 即可学功法，三馆可兼学。馆主均为 15 级，正好卡在功法上限 15 的瓶颈；对话会把玩家引向南风城。三馆不再写入 `character.sect_id`，学艺来源记在 `character_art.source`；馆主的好感、记忆、日程与八卦链路不变，好感仍影响学费、教学和对话。

**南风城两派**（风池派/赤地门）：正式门派，同一时间只能入一派；入门学外传，位阶 3 学真传；他派功法只能通过叛门、奇遇、城主藏经阁等特殊渠道。已学功法永久保留；叛门不清功法，但位阶、门派折扣清零，原门派好感大跌。

**功法表**：

| 地图 | 势力/传授者 | 功法 | 属性 | 上限 | 内功倾向 | 说明 |
|---|---|---|---|---|---|---|
| 清溪镇 | 剑阳道馆（烈阳武馆）· 顾长风 Lv15 | 烈阳剑诀 | 火 | 15 | 每 3 级臂力+1 | 武馆外传 |
| 清溪镇 | 玄铁堂（玄土武馆）· 铁无双 Lv15 | 玄土刀法 | 土 | 15 | 每 3 级根骨+1 | 武馆外传 |
| 清溪镇 | 烟雨小筑（烟雨武馆）· 苏婉清 Lv15 | 烟雨诀 | 水 | 15 | 每 3 级身法+1 | 武馆外传 |
| 南风城 | 风池派 · 门主 Lv50 | 大风诀（外传） | 风 | 45 | 每 3 级身法+1 | 真传上限 50 |
| 南风城 | 赤地门 · 门主 Lv50 | 赤阳功（外传） | 火 | 40 | 每 3 级臂力+1 | 真传上限 50 |
| 南风城 | 城主府 · 南天成 Lv52 | 南天诀（暂定） | 雷/光待定 | 55 | 待定 | 城主绝学 |

- **真传/外传**解释门主 50 级 > 外传上限 45/40：门主自己修真传（cap50），传给弟子的是删减版外传；真传通过位阶 3、好感≥90 或专属任务解锁。
- 赤阳功是火系，所以烈阳剑诀 → 赤阳功是无缝转换；烈阳剑诀 → 大风诀需要散功。
- `sect_id` 只记录正式门派（南风城）；武馆学艺记录在 `character_art.source`。
- 招式名是招式风格，不必逐字对应属性；属性只看功法，不看招式名。

**正式门派位阶**（南风城）：

| 位阶 | 条件 | 解锁 |
|---|---|---|
| 入门弟子 | 等级≥15、掌门好感≥60 | 外传功法 + 一档招式 |
| 亲传弟子 | 好感≥75 | 二档招式 |
| 关门弟子 | 好感≥85 | 三档招式 + 真传资格 |

招式本身的可用等级仍按 §5.3 的修为等级门槛；位阶只决定门派身份、真传与折扣。旧清溪镇三派的“拜师/位阶”在 M6 改为“武馆学艺”，`/api/npc/{id}/apprentice` 只保留给南风城正式门派。

### 5.3 招式与内功

每门功法 = 1 个内功被动 + 3 个主动招式。**内功被动随修为等级自动生效**（不再单独用修炼点升级，效果写在 `art.passive_json`）；主动招式 L1~L5，每级倍率 +0.15，最多同时装备 3 个。普攻人人可用（倍率×1.0，0 MP）。

**招式解锁**按功法品阶（由上限推导）：

- 黄阶（cap≤15）：1/5/10 级解锁三招；
- 玄阶（cap16~45）：1/15/30 级；
- 地阶（cap46~60）：1/25/45 级；
- 天阶/绝阶（cap61~100）：1/35/60 或按具体功法配置。

**招式升级费**改为随等级缩放：`max(20, 当前升级所需经验 × 5%) × 招式等级`，避免高等级修炼点溢出；系数可调（见 §14）。

| 功法 | 招式 | 档位 | MP | 倍率 | 特效 |
|---|---|---|---|---|---|
| 烈阳剑诀 | 赤阳斩 | 一档 | 8 | ×1.6 | — |
| 烈阳剑诀 | 风雷剑势 | 二档 | 14 | ×1.9 | 本次暴击率+20% |
| 烈阳剑诀 | 旭日东升 | 三档 | 26 | ×2.6 | 冷却 2 回合 |
| 玄土刀法 | 怒浪刀 | 一档 | 8 | ×1.5 | 自身防御+10% 1回合 |
| 玄土刀法 | 铁布衫 | 二档 | 12 | ×0.8 | 自身防御+40% 2回合 |
| 玄土刀法 | 开山刀 | 三档 | 26 | ×2.4 | 无视目标 50% 防御 |
| 烟雨诀 | 迷踪步 | 一档 | 6 | ×1.2 | 自身闪避+15% 2回合 |
| 烟雨诀 | 细雨十三刺 | 二档 | 15 | ×1.8 | 此击无视闪避 |
| 烟雨诀 | 飞虹贯云 | 三档 | 22 | ×2.2 | 速度>对方×1.5 时攻击两次 |

南风城三功法（大风诀/赤阳功/南天诀）各按同样三档模板补 3 招；具体名称、MP/倍率/特效在 M6-C 内容阶段定，不新增 LLM purpose。

战斗中最多同时装备 3 个主动技能（角色面板配置，城内可改）。切换功法后已装备主动招式清空，只保留新功法已解锁的招式。

### 5.4 装备

槽位 3 个：**武器 / 衣服 / 饰品**。品阶：凡品(白) / 良品(绿) / 稀有(蓝) / 传说(紫，V1 不可得)。

| 物品 | 槽位 | 品阶 | 效果 | 银两 |
|---|---|---|---|---|
| 铁剑 | 武器 | 凡品 | 攻+8 | 80 |
| 精钢剑 | 武器 | 良品 | 攻+15 | 250 |
| 玄铁剑(掉落) | 武器 | 稀有 | 攻+24、暴击+5% | 不可购 |
| 断岳刀(黑旋风首掉) | 武器 | 稀有 | 攻+28、暴击+8% | 神器，不可售 |
| 布袍 | 衣服 | 凡品 | 防+4 | 50 |
| 精革甲 | 衣服 | 良品 | 防+9 | 200 |
| 玉佩 | 饰品 | 良品 | MP上限+40、悟性+1 | 150 |

装备不可分解/附魔；替换即回背包，可卖店（半价），`unsellable` 神器除外。

### 5.5 恢复药剂与经济

| 物品 | 效果 | 银两 |
|---|---|---|
| 小回春散 | 恢复 60 HP | 15 |
| 大回春散 | 恢复 200 HP | 60 |
| 凝神丸 | 恢复 40 MP | 20 |

- 战斗中使用药剂**消耗本回合**；战斗内使用上限 3 次。
- 药铺（陈半松）售药剂；铁匠铺（王大柱）售装备。好感度折扣见 §6.5（仅王大柱）。
- 客栈住宿 20 两：恢复全满并推进游戏时间至次日清晨（跳过夜晚）。

### 5.6 战斗（回合制 1v1）

**进入方式**：① 野外撞见怪物（碰撞即进入，0.3s 转场）；② 对话中选择"切磋"（好感≥40 的馆主/掌门才应战）。

**回合流程**（服务器结算）：

1. 比速度定先手（相同则玩家先手），每回合双方各行动一次；
2. 玩家指令四选一：**攻击 / 技能 / 药剂 / 逃走**；
3. 伤害 = `max(1, round(攻击 × 技能倍率 − 防御))`；先掷闪避，再掷暴击；
4. 逃走成功率 = `clamp(0.3 + (自身速度−对方速度)×0.05, 0.1, 0.9)`，失败则白白挨打一回合；
5. Buff 按回合倒计时，战斗结束清空。

**切磋规则**：任一方 HP<30% 即结束，无死亡，双方获少量经验（×0.3）。结束后调用一次 LLM 生成 NPC 的当场感受与好感变化（clamp ±3），把「玩家切磋 + NPC 感受」作为一条特殊对话写入该 NPC 的 `conversation` 窗口；不即时写记忆，等窗口满批总结时入忆。

**敌方 AI（规则型，不耗 LLM）**：
1. HP<35% 且有药剂 → 喝药（NPC 药剂数按类型配置，馆主/掌门 2 瓶）；
2. 玩家 HP<40% 且 MP 足够且有大招 → 放大招；
3. MP 足够 → 70% 概率用小技能；
4. 否则普攻。

**胜负**：胜（经验/银两/掉落）、败（回客栈复活，HP/MP 减半，损失 10% 银两，战斗记录会传入周围 NPC 记忆——"那小子被人抬回来了"）。

### 5.7 时间系统

- 游戏时钟由服务器维护：默认 **1 现实秒 = 1 游戏分**（可配），即一个游戏日 = 24 现实分钟；
- 存档时持久化时钟偏移；
- 记忆时间戳用游戏时间（干支时辰显示：辰时/酉时……），recency 衰减按游戏日计。

### 5.8 功法属性与克制（M6）

十种属性分两环，跨环中立；魔功独立，V1 不投放：

| 环 | 相克链 | 记忆点 |
|---|---|---|
| 五行 | 金 → 木 → 土 → 水 → 火 → 金 | 传统五行相克 |
| 异变 | 风 → 雷 → 冰 → 光 → 暗 → 风 | 风散雷云、雷碎寒冰、冰晶折光、光破暗影、暗夜吞风 |

- 克制 ×1.20，被克 ×0.85，其余 ×1.00；只作用于伤害，不影响 buff/治疗。
- 属性来源是当前功法；无功法、拳脚、普通低级怪为无属性，中立。南风城开始多放带属性怪。
- 魔功在数据库 `element` 枚举中保留 `demon`，但不参与克制表，V1 不投放任何魔功内容。
- 克制是长期 build 选择，不是战斗中换属性；散功代价让它有分量。
- 战斗日志与 UI 显示“克制/被克”，伤害结算时先算防御再乘属性倍率。

---

## 6. NPC 智能系统（本项目核心）

### 6.1 总览

对 Generative Agents 的裁剪版：保留**记忆流 + 检索 + 反思 + 关系演化**，砍掉自由规划（V1 用固定日程 + 午间聚集 + 每日一次安全偏离）、砍掉 NPC 自由行动决策（移动按日程走）。所有 LLM 调用集中在：对话、送礼/切磋感受、主动搭话、反思、关系摘要、每日心事、少量八卦改写；议程/情绪/附近事件不调 LLM。

### 6.2 NPC 花名册（7 人）

| NPC | 身份 | 性格关键词 | 说话风格 | 日间 | 夜间 |
|---|---|---|---|---|---|
| 顾长风 | 剑阳道馆馆主（原剑阳派掌门） | 傲、严苛、重悟性 | 冷硬简短 | 剑阳道馆 | 道馆后屋 |
| 铁无双 | 玄铁堂馆主（原玄铁堡堡主） | 豪爽、嗜酒、重品性 | 大嗓门爱称"小子" | 玄铁堂 | 客栈 |
| 苏婉清 | 烟雨小筑馆主（原烟雨楼楼主） | 高深、戏谑、重机敏 | 半文半白带调侃 | 烟雨小筑 | 小筑内室 |
| 王大柱 | 铁匠铺主 | 爱财、爱打听、消息贩子 | 热络市侩 | 铁匠铺 | 铺后家 |
| 陈半松 | 药铺大夫 | 慈祥、啰嗦、爱忆当年 | 缓慢絮叨 | 药铺 | 铺后家 |
| 柳小蝶 | 客栈掌柜之女 | 活泼好奇、爱看热闹 | 清脆快语 | 客栈 | 客栈 |
| 张馒头 | 疯乞丐 | 癫三倒四、偶尔惊人 | 疯话押韵 | 镇口水井 | 破庙 |

（张馒头为后续"隐世高人"留口子，V1 只是怪话 NPC。）

> **M6 术语**：清溪镇三馆的“掌门”改称“馆主”，等级/功法见 §5.2（馆主均 15 级、功法上限 15）；花名册的人格、好感、记忆字段不变。南风城才使用“门派/掌门”。

人设（persona）字段：姓名、身份、门派、性格标签×3、说话风格、对玩家初始好感、拜师/商店等功能角色。每人另有 2~3 句 `speech_examples`，只作 prompt 语气参考、要求不逐字照抄。七人对后山神器的固定立场、避谈点与知识分层见 §6.11（代码 `game/world.py` 的 `NPC_STANCE`）。

### 6.3 记忆流（Memory Stream）

`memory` 表每条记忆：

```
{ npc_id, 游戏时间戳, kind: event|reflection|gossip,
  content: 自然语言一句话,
  participants: [玩家/其他NPC], poi: 地点,
  importance: 1~10, embedding: float32[1024]（存 Chroma，不入 SQLite） }
```

**三层分工**：

- 短期：`conversation` 表，每 NPC 独立窗口（默认 10 轮），最近 `DIALOGUE_WINDOW_SIZE`（默认 6）轮直接平铺进 prompt；
- 中期：`MemoryStore._midterms` 进程内列表，每 NPC 最多 10 条 `summary + 3~6 条线索`（不入库、不进 Chroma），窗口溢出时生成；
- 长期：`longterm_memory` 表，每 36 轮原始对话后台总结成 1 条（不写 `memory`/Chroma，暂不参与对话/RAG/反思/关系/八卦）。

`memory` 表本身继续只存 event（战斗/大额消费/拜师等即时事件）、reflection、gossip。

**事件入忆规则（"与我有关"的判定）**：

1. **直接交互**：送礼、切磋 → 把玩家动作合成为一条消息，复用 `dialogue.talk`
   （`purpose="gift"/"spar"`、`pause=False`）生成 NPC 感受与好感变化，并把这条
   特殊对话写入 `conversation` 窗口，**不即时入忆**；购买（≥100 两）仍即时入忆
   一条。普通对话每轮也只进同一窗口；该 NPC 的窗口条数超过
   `CONVERSATION_WINDOW_CAPACITY`（默认 10）时，按 §6.6 把最早一批
   （即 `CONVERSATION_WINDOW_CAPACITY` 轮）总结为一条**中期记忆**（纯内存），不再落 event；
2. **同场目击**：玩家战斗、大额消费（≥100两）等显著事件 → 发生时处于同一 POI 的 NPC 各自入忆一条（importance 按事件类型定值）；
3. **八卦传播**：见 §6.9，NPC 间扩散玩家事迹。

记忆内容示例（`memory` 表只列 event/reflection/gossip；对话/送礼/切磋的归纳在中期内存/长期表，不在此列）：

- event: `[酉时·铁匠铺] 秦侠客一掷二百两买下精钢剑，这小子手头真宽裕。`（importance 3，大额消费即时入忆）
- gossip: `[午时·镇口] 王大柱跟我说，秦侠客在后山崖把黑旋风给办了。`（importance 5）
- reflection: `这秦侠客一月来寻我三次，回回问剑，习剑之心比传闻里坚定，资质也不差。`（importance 8）
- midterm（纯内存，不落库）: `与秦侠客聊过后山黑旋风，他似有除害之意。`（线索：后山、黑旋风）
- longterm（`longterm_memory`，不进 Chroma）: `秦侠客近来常来问剑，先送药后切磋，性子倔，倒是个可教之才。`（每 36 轮后台总结）

### 6.4 RAG 检索

对话/反思/搭话前，取玩家输入（或反思主题）的 embedding，在 NPC 记忆中检索：

```
score = w_rel·余弦相似度 + w_rec·exp衰减 + w_imp·归一化重要度
w_rel=1.0, w_rec=0.8, w_imp=0.5（可配）
recency: 半衰期 = 3 游戏日
```

检索分两步：Chroma 以输入 embedding 查询（`where {"npc_id": id}` 过滤）取**相似度 top20**，再在应用侧算三因子综合分重排取 **top 6** 送入 prompt——Chroma 只算向量相似度，recency/importance 加权必须自己排。历史对话最近 `DIALOGUE_WINDOW_SIZE`（默认 6）轮直接平铺进 prompt（不走检索）。Chroma 集合只索引 `memory` 表（event/reflection/gossip）。

**中期线索检索**：当前消息的 embedding 同时用于 Chroma RAG 与中期线索匹配。每条中期记忆的 3~6 条线索各有一个向量；取该条目最高分线索，按分数降序取 top-k（默认 3）且 ≥ `MIDTERM_MIN_SCORE`（默认 0.45），命中后把**完整 summary** 放进 prompt 的独立「你想起一些旧事」板块。中期只存内存、不进 Chroma；长期记忆暂不参与检索。

### 6.5 好感度系统

- 范围 0~100，初始值按 NPC 设定（馆主/掌门们 20~30，柳小蝶 40，张馒头 50）；
- **增益**：对话、送礼、切磋都由 LLM 判定单次好感变化，统一 clamp ±3（`MAX_DIALOGUE_DELTA`，prompt 要求 −2~+3）；送礼价值/同日第 N 次、切磋胜负只作为 prompt 上下文，不再叠加固定规则增益；
- **送礼/切磋感受**：把礼物/胜负合成为一条玩家消息，用 `dialogue.talk(..., purpose="gift"/"spar", pause=False)` 复用对话 prompt/RAG/解析/重试链路；`pause=False` 只跳过 5 分钟对话暂停，议程/情绪/end 仍按对话逻辑处理；好感同样 clamp ±3，失败走模板兜底，不阻塞玩法；
- 无衰减（V1 不做时间衰减，反思可小幅修正）。

**分段效果**：

| 好感 | 态度 | 效果 |
|---|---|---|
| 0~29 | 冷淡 | 对话可能被提前结束（LLM 判 end） |
| 30~59 | 客气 | 正常对话 |
| 60~69 | 热络 | 正式门派收徒条件达成；王大柱 95 折 |
| 70~84 | 亲近 | **主动搭话**（阈值=70）；王大柱 9 折 |
| 85~100 | 挚交 | 关门弟子条件；馆主/掌门偶赠药剂（CD 1 游戏日） |

**主动搭话**：玩家进入 NPC 感应半径（`npc_sense_radius=48px`，约 3 tile）时，客户端边沿检测并询问 `/api/npc/{id}/greet`；若好感≥`greet_threshold`（70）且冷却（5 现实分）已过、NPC 不在对话暂停中，服务器检索记忆生成一句搭话（例如提你上次那场架），NPC 头顶冒气泡。低于阈值则只在 UI 上显示"E 交互"提示。

### 6.6 对话流程

玩家按 E 进入对话 → 输入框 + 快捷项：**[打招呼] [打听消息] [门派拜师] [切磋] [送礼]**（送礼弹背包选择）。

对话回复单次 LLM 调用（qwen3:4b-instruct，`format:"json"`，禁思考），响应以 SSE 流式返回（服务端为回复只调用一次模型；送礼/切磋复用同一对话链路，见 §6.5）：

**输入 prompt 结构**（第一人称身份句，不用"玩家/游戏/好感度 X"等机械设定词；交情以档位自然带出，不强制口头禅，让 NPC 自由发挥措辞）：
```
你是{NPC名}。{人设}你说话{说话风格}。
{世界简报：公开事实/你的立场/你知道的细节/分寸，见 §6.11}
你与{玩家名}的来往：{交情档位，由好感映射成第一人称描述}
你对{玩家名}的印象：{关系摘要}
你记得的一些事：
{检索 top6 记忆的内容}
你想起一些旧事（只作参考，不要逐字复述）：
{中期命中的完整 summary}
你们最近的往来：
{最近 dialogue_window_size 轮}
你眼下挂心的事：{当日心事，有则加}
你此刻的盘算：{当前议程}
你不愿提：{该 NPC 的避谈点}
你说话的样子：参考这些语气，不要逐字照抄：{speech_examples}
你此刻的心气：{轻情绪描述，有则加}
此刻{干支时辰}，你正在{地点}。
以你的口吻应答{玩家名}（≤80字）。并给出你与{玩家名}的关系变化(-2~+3)、
是否想就此收住话头(BOOL)。
仅输出JSON: {"reply","affinity_delta","end"}
```

**议程（`agent/agenda.py`）**：每人有预置议程池与权重（试探神器、套张馒头的话、怂恿/劝阻上山、炫耀消息、避谈、拉人、闲聊），**不调 LLM**。每次新对话用 `random.Random(npc_id*1000003 + day*1009 + seq)` 确定性加权抽取，排除最近 3 个议程（池中无可选时允许重复）；`agenda_seq:{id}:{day}` 每场 +1，`agenda_recent:{id}` 保留最近 3 个，`agenda_current:{id}` 存当前议程。同一场对话 5 现实分钟内复用当前议程，`end=true` 清除。

**轻情绪（`agent/mood.py` + `npc_mood` 表）**：只把一句语气描述注入 prompt，**不改好感、不参与任何结算**。送礼/切磋按 LLM 好感变化设情绪：delta<0 → `impatient`；delta>0 时送礼 `pleased`、玩家切磋获胜 `respectful`、玩家落败 `pleased`；delta=0 不改。对话 `end=true` 且好感 <30 → `impatient` 20 分。所有写入 best-effort，失败只打印。

**输出处理**：reply 显示；好感 clamp 后更新；end=true 则对话结束并清当前议程。
**失败策略**：JSON 解析失败→重试一次(temperature 0.3)→再失败用模板回复（"{NPC}沉吟片刻，不置可否。"，delta=0），永不阻塞游戏。

**流式输出（SSE）**：`POST /api/npc/{id}/talk` 返回 `text/event-stream`，事件序列 `start` → `token`* → `done`；重试或模板兜底前发 `reset`，客户端据此清空已显示的文字。模型仍只输出一个 JSON 对象，服务端边收边解码 `reply` 值（转义、代理对、UTF-8 分片都安全），`affinity_delta`/`end` 在流结束后解析，`done` 携带 `{reply, affinity, end, player, fallback, place}`。**流式路径不新增 LLM 调用**（M5 预算不变；送礼/切磋复用同一对话链路，见 §6.5）。Godot 的 `HTTPRequest` 会缓冲整包，客户端改用 `HTTPClient` 手动轮询读取（`api.gd::stream_request`）。

**三层记忆与批总结**：普通对话每轮由 `MemoryStore.add_conversation_turn` 写入短期窗口（`conversation` 表）；送礼/切磋调用 `dialogue.talk` 复用同一链路，`user` 是合成的玩家动作，`npc` 是同一对话 prompt 生成的回复。窗口按 **NPC 独立**统计，超过 `CONVERSATION_WINDOW_CAPACITY`（默认 10）时，取最早一批（默认 `CONVERSATION_WINDOW_CAPACITY=10` 轮）一次 LLM 归纳成 **1 条中期记忆**：`summary`（≤120 字）+ 3~6 条可检索线索；只存 `MemoryStore._midterms` 进程内存（每 NPC 最多 `MIDTERM_CAPACITY=10` 条，FIFO），线索向量也只存内存，**不写 `memory`、不写 Chroma**，随后删除这批 `conversation` 记录。

对话每轮把当前消息 embed 一次，同时用于 Chroma RAG 与中期线索匹配；命中线索时把该条目的完整 summary 放进 prompt 的「你想起一些旧事」独立板块（见 §6.4），不塞进「你们最近的往来」。

长期：`MemoryStore` 另维护每 NPC 的原始轮缓冲（含送礼/切磋，重启清零）；累计 `LONGTERM_TURN_INTERVAL`（默认 36）轮时，取最早 36 轮投入单 worker 队列，用同一条 `dialogue_summary` purpose（显式 `priority=3`）串行总结成 1 条长期记忆，写入独立的 `longterm_memory` 表。每个任务带 `generation`；`new_game` 先 `begin_reset()` 递增局代，再 `flush()` 等旧任务收尾/丢弃，`reset_runtime()` 清中期/缓冲；shutdown 用 `shutdown()` 丢弃排队任务并让当前任务收尾。长期记忆**不写 `memory`/Chroma**，也不参与对话/RAG/反思/关系摘要/八卦；本期只生成与入库。

对话入口在开始时捕获 `mem_store.generation`；LLM 返回后若局代已变，跳过好感/议程/情绪/记忆写入，`done` 带 `stale=true`（Godot 忽略该字段）。送礼/切磋看到 `stale` 时也不扣物品/不记事件。新局与在途对话、长期 worker 的这类竞态由 generation 屏障兜底，不要用裸 `drain()` 或 fire-and-forget task 集合替代。长期 prompt 由 `LLM_NUM_CTX`（默认 20480）覆盖，避免 36 轮原文被 Ollama 默认上下文截断。

两条总结都走「校验→重试一次(temperature 0.3)→模板兜底」链路，失败不阻塞玩法；中期兜底 `clues=[]`（该条暂不可检索），长期兜底用首尾玩家原话拼一句。中期与长期缓冲都在进程内，服务重启清零；短期窗口与 `longterm_memory` 持久化。大额消费等规则事件仍即时入忆（§6.3），不参与窗口计数。

**[打听消息]** 是记忆系统的展示窗：NPC 会用自己检索到的记忆（含八卦）回答，答非所问或以讹传讹正是实验观察点。

### 6.7 反思

后台调度每 `scheduler_tick_real`（默认 60s）巡检，`scheduler_enabled=false` 可整体关闭：

> 中期记忆不写 `memory`，长期记忆也完全隔离，因此对话本身不再直接增加反思素材；反思只由大额消费/战斗目击/已有反思/八卦触发。这是记忆分层后的预期，不要为恢复反思频率把每 10 轮对话写回 event。

- 触发：某 NPC 自上次反思后新增记忆 ≥`reflect_mem_threshold`（8），或新增记忆 >0 且距上次反思 ≥`reflect_interval_real`（360s）；另设 `scheduler_tick_real × 0.75` 冷却兼失败退避，避免 60s 巡检抖动成每两拍才反思；
- 素材：取该 NPC 最近 `reflection_take_n`（30）条记忆；少于 `reflection_min_memories`（3）条直接跳过；
- 流程：LLM 归纳 1~3 条"高阶洞察"（第三人称，每条 ≤80 字）→ 以 `kind=reflection, importance=8` 写入记忆流（importance 不采信模型）；失败时以"近来常想起：<最近一条>"兜底，不阻塞后台；
- 好感：仅当洞察文本确实提到玩家名才应用 `affinity_delta`，clamp ±`max_reflection_delta`（2）；
- 计数：写库后用快照扣减 `mem_since_reflection = MAX(0, mem_since_reflection - 快照值)`，不吞掉反思期间并发写入的新记忆；`last_reflection_at` 记现实时间；
- 反思结束顺带尝试一次关系摘要刷新（§6.8）；
- 反思走优先级 3，与对话共用全局限流，可延迟。

### 6.8 关系演化

- `relationship(npc_id, target_kind, target_id)` 为每对 (NPC, 对象) 维护一段 LLM 滚动摘要（≤`relationship_max_chars`，默认 100 字）；`target_kind=player` 时 `target_id` 为玩家名，`target_kind=npc` 时为对方 NPC 名；
- **素材边界**：对话/中期/长期都不进关系摘要，`relationship` 只统计 `memory` 的 event/gossip/reflection。分层后关系摘要更新会变稀疏，这是长期记忆完全隔离的预期结果；要恢复需单独设计（例如把 `longterm_memory` 计入素材），不要恢复每 10 轮 event；
- 触发更新：`updated_at`（现实时间）以来，与该对象相关的 event/gossip 新增 ≥`relationship_update_events`（5），或提及该对象的 reflection ≥`relationship_mention_reflections`（3）；material 取 events[:20] + reflections[:10]；
- 更新 = 旧摘要 + 新增事件列表 → LLM 压缩为新摘要；失败时用"旧摘要；最近两条事件"截断兜底；
- 摘要进对话 prompt 的"你对{玩家}的印象"，是长程关系记忆的压缩载体——防止历史对话无限膨胀；
- 摘要用 `created_at_real` 过滤素材，与游戏时间字段严格分开；同步/测试可走 `force=True`。

### 6.9 NPC-NPC 八卦与传播

- 后台每 `gossip_interval_real`（120s）扫描：同一 POI、均处于 work/gather 相位且未被对话暂停、距离 ≤64px 的 NPC 两两配对；扫描按时间轮转配对，每次最多成功传播 `gossip_max_pairs_per_scan`（2）对；
- 每对每个游戏日最多八卦 1 次，kv 键 `gossip_pair:{min_id}:{max_id}` 存游戏日；
- 每次八卦 = 共享"对方未亲历、未听说过且 importance≥`gossip_importance_min`（5）的最重要一条记忆"；候选按 importance、ts_game 降序；
- 传播链用 `memory.source_memory_id` 恒指向根记忆，B 已持有同根记忆则跳过，避免同一件事反复转述；
- 八卦文本默认模板：`{A}与我闲聊：{事件内容}`；若根事件 importance≥`gossip_rewrite_importance`（7），用 LLM 改写成一句转述（≤60 字，失败回退原文）；
- 双方各写一条 `kind=gossip` 记忆（importance 根值−1，`source_memory_id` 指向根），并各自尝试刷新对对方的关系摘要；
- 效果：午间镇口聚集时消息扩散；在东山打完狼，柳小蝶也可能"听说"——玩家行为在网络中传播。

### 6.10 日程与移动

位置 = 游戏时钟的**确定性函数**（客户端本地推算，零同步）。服务端下发 `phases`、`pacing`、`gather_anchor` 与每人两条折线，Godot 与 Python 各自实现同一套纯函数：

| 时段 | 行为 |
|---|---|
| 06:30~07:00 | 从家走向工作 POI（`home_to_work` 折线插值） |
| 07:00~11:00 | 停留工作 POI（小范围踱步） |
| 11:00~11:10 | 走向镇口聚集点（`work_to_gather`） |
| 11:10~13:50 | 镇口聚集（`GATHER_ANCHOR=(768,440)`，八卦扫描窗口） |
| 13:50~14:00 | 走回工作 POI |
| 14:00~19:00 | 停留工作 POI |
| 19:00~19:30 | 走回家（反向 `home_to_work`） |
| 19:30~次日 06:30 | 停留家中 |

- 折线绕开实心建筑；踱步以 NPC id + 游戏日 + 600s 槽位为种子，用 LCG（`1103515245/12345/1013904223` 等常量，两端逐位一致）生成 ±8/±6px 偏移，120s smoothstep 过渡；
- 相位切换处行走段端点不带踱步偏移，可能跳变 ≤10px，属预期表现；
- 对话期间服务器把 `paused_until` 设为现实时间 +5 分钟（`end=true` 提前清除），仅用于八卦扫描排除与调试观察；客户端只冻结当前对话框对应的 NPC，关闭即恢复日程；
- `paused_until`/`last_reflection_at`/`last_greet_real`/关系 `updated_at` 用现实时间；`ts_game`、日程、八卦日界用游戏时间，不得混用。
- **每日心事与安全偏离（M5-A）**：后台 `_plan_loop` 每 `scheduler_tick_real` 巡检，`plan_enabled=false` 可停用；每个 NPC 每游戏日最多一行 `npc_plan`。素材为最近 10 条记忆、关系摘要、世界阶段与固定立场；LLM 产出 `heart`（≤60 字）、`reason`（≤40 字）、偏离 `none|gather_early|gather_late|stay_work|home_early` 与 `minutes`（5~20）。校验失败重试一次后写空心事/无偏离行，当天不再重试。
- 偏离**只平移既有相位边界**：`gather_early`/`gather_late` 同时改前后两段 `work`，`stay_work` 合并两段 `work`，`home_early` 提前回家段；`shift` clamp 0~3600，不新增坐标、不做寻路。服务端 `_day_phases` 与 Godot `_deviated_phases` 常量逐位一致；`phase_at/position_for/poi_label_for/is_idle` 均接受 `deviation`（默认 `None`，不传行为不变）。
- `/api/npcs/state` 每人附带 `heart`/`deviation`，客户端随 10 秒状态同步取到，不新增轮询；对话/搭话/送礼/拜师/反思/八卦的地点与位置全部按偏离后的相位计算。

### 6.11 世界公设与断岳刀世界事件

后山、神器、张馒头与三派构成全镇共享的背景公设（代码 `game/world.py`，纯数据 + 纯函数，不依赖 DB/agent）：

- **世界阶段**存 kv：`world.stage` 只有两态 `0` 传闻期、`2` 全镇皆知；`world.artifact_acquired` 标记神器已取得；`world.announced_at_game` 记通告游戏时刻。
- **公开事实**四条（山贼占寨、断岳刀传闻、张馒头与山外、三派重开山门）按话题命中注入。知识分三层：L0 公开、L1 王大柱/柳小蝶的夸张传闻、L2 掌门与陈半松的机密（需好感门槛 50~60）；张馒头独占核心真相（断岳刀是山外旧朝信物），仅阶段 2 且问及神器/张馒头/山外时注入，并明确“只说七分”。
- `brief_for` 生成 120~260 字 prompt 片段，固定“公开事实 → 你的立场 → 你知道的细节 → 分寸”顺序；无公设话题且阶段 0 只给一句氛围，避免每句话都谈后山。NPC 不知道的细节要求岔开、装糊涂，不编造新人物/宝物/门派。
- **断岳刀**是唯一神器（`duanyue_dao`，rare，攻击 +28、暴击 +8%，`price=0` 且 `unsellable`）：不可出售、不重复掉落。黑旋风首次胜利必定掉落；拿到刀但未清山贼时阶段仍为 0。
- **推进条件**：黑旋风与 `bandit_1~3` 各自至少被击杀过一次（`mob_kills` 只留最近时间戳，键存在即可，不看是否重生）且已取得断岳刀 → 写 `world.stage=2` 与通告时刻。判定幂等；启动时补算一次，防止“胜利结算后、写阶段前”进程退出漏写。`POST /api/game/new` 调 `world_state.reset` 清三个 kv 与 `mob_kills`，新档不继承旧世界事件。
- 阶段 2 后所有 NPC 的世界简报追加“{玩家}杀了黑旋风，清了后山那伙山贼，取回了断岳刀；这件事已经传遍清溪镇”，并附各自 `stage2` 说法；拜师规则不因此改变（仍好感 ≥60、等级 ≥3）。
- 公设与全镇通告**不逐人写记忆**，只作常驻 prompt 上下文；具体互动/目击仍按 §6.3 入忆。
- **M6 术语调整**：清溪镇三派在武馆/门派拆分后统称“三馆”，馆主不再授予正式门派身份；世界公设、NPC 人设与对话示例中的“三派/掌门”在 M6 文案阶段统一改为“三馆/馆主”，南风城才用“门派/掌门”。

### 6.12 附近事件感知（M5-C 精简版）

进程内环形缓冲（`agent/events.py`，挂在 `MemoryStore.recent_events`），**不落库、重启丢失可接受**；最多 20 条，TTL 30 游戏分钟。

- 只记显著事件：战斗胜利`在{POI}击败了{敌人}`、战斗失败`在{POI}被打倒`、单次消费 ≥100 两`在{店}一掷{N}两`、送礼`向{NPC}送了{物品}`、切磋`与{NPC}切磋，{胜负}`、拜师`拜入{门派}`；POI 按敌方类型取 `MOB_POI`（狼→东山林地，山贼/黑旋风→后山崖）。
- 对话/搭话时按 NPC 当前 POI 取最近 3 条，拼进 prompt 的“近年附近发生的事”；**不写记忆流、不新增 LLM 调用**，阶段 2 的全镇通告仍只来自世界简报。
- 客户端可见反应气泡不在本阶段实现，避免新增轮询与刷屏。

### 6.13 NPC 角色卡同构（M6）

NPC 与玩家使用**同一套角色卡**，不再使用花名册里的硬编码 `combat`：

```
等级 / 经验 / 修炼点 / HP / MP / 银两 / 四维 / 自由点
当前功法 / 已学功法 / 招式熟练度 / 装备 / 背包
```

NPC 额外保留：`role`、`home_poi/work_poi`、`affinity`、`persona`、记忆。固定 NPC 的等级/功法/装备在花名册里给预设值，运行时写进角色卡；玩家与 NPC 共用 `stats()`、战斗引擎、功法上限与散功规则。

- **战力来源统一**：`battle.start_spar()` 不再读 `npc["combat"]`，改为读取 NPC 角色卡并快照；`player.stats()` / `modifiers()` 按 `character_id` 查装备与被动，不再按 `player_id`。
- **NPC 招式**：规则 AI 从 NPC 已装备的招式中选，不能只会普攻；MP、冷却、buff 与玩家同一套结算。
- **NPC 成长**：后续工具调用统一走 `learn_art` / `switch_art` / `buy` / `equip` / `allocate` / `train` 服务函数，玩家 API 与 NPC 工具调用共用，NPC 不能绕过上限与散功规则。
- **NPC 自身事件**：NPC 突破、散功、换装、买到好装备，写它自己的 `memory`，并可能被同场 NPC 目击、进入八卦。比如“铁无双散了玄土刀法的修为，改练风系”——这是这套系统最有价值的 emergent 剧情。
- **对话上下文**：NPC prompt 里加入当前功法、属性、等级、上限、是否瓶颈。卡在上限的馆主和门主可以自然地在对话里提这件事；这是把数值系统翻译成人格的一部分。
- **经济**：店铺库存与 NPC 个人背包分开；NPC 买装备进个人背包，不直接消耗店铺固定库存。V1 不做 NPC 主动交易，先把数据结构留好。
- **新局**：V1 的 `new_game` 把 NPC 角色卡重置回花名册预设；等工具调用稳定后，再决定是否让 NPC 成长跨局持久化。
- **张馒头**：当前 1 级无功法，正好留作“隐世高人”伏笔；以后揭示时给他一门高上限功法即可。

约束不变：服务器权威、SQLite 单写队列、NPC 位置零同步、LLM 不阻塞玩法；NPC 同构不新增 LLM purpose。

---

## 7. LLM 集成（Ollama）

### 7.1 配置

```env
OLLAMA_HOST=http://localhost:11434
CHAT_MODEL=qwen3:4b-instruct        # 4bit 量化 tag
EMBED_MODEL=qwen3-embedding:0.6b    # 8bit 量化 tag
LLM_TIMEOUT=20
LLM_CONCURRENCY=2
LLM_NUM_CTX=20480   # 本地 Ollama 上下文窗口；36 轮长期总结需要 ≥16k
```

**可选 OpenAI 兼容后端**：本地 Ollama 是默认后端；若同时配置了
`LLM_API_KEY` 与 `LLM_API_BASE_URL`，则对话改走该 API（`/chat/completions`，
base_url 需以 `/v1` 结尾，如 `LLM_API_BASE_URL=https://api.example.com/v1`），
`LLM_API_MODEL` 指定其模型名（缺省回退 `CHAT_MODEL`）。健康检查
`/api/health` 会标注当前生效的对话后端（`openai-api` / `ollama`）与有效模型名。

**嵌入的双格式兼容**：embedding 后端可能是 Ollama（`/api/embed` →
`{"embeddings": [[...]]}`）也可能是 OpenAI 兼容服务（`/embeddings` →
`{"data": [{"embedding": [...]}]}`）。`OllamaClient.embed` 按
「配置的 API 后端 → Ollama `/api/embed` → Ollama `/embeddings`」顺序尝试，
解析两种响应形状，并缓存命中成功的路由；某条路失败自动降级到下一条，
全部失败才抛错（由调用方的兜底链路接住）。`LLM_API_EMBED_MODEL` 可单独指定
API 侧的嵌入模型名，留空回退 `EMBED_MODEL`。健康检查的 `/api/tags` 缺失时
自动改查 `/v1/models`，因此把 host 直接指向 OpenAI 兼容服务也不会误报不可达。

```env
LLM_API_KEY=
LLM_API_MODEL=
LLM_API_EMBED_MODEL=     # API 侧嵌入模型名；留空回退 EMBED_MODEL
LLM_API_BASE_URL=
LLM_API_TRUST_ENV=false   # 外接 API 默认直连；需要 shell 代理时改 true
LLM_API_TIMEOUT=8         # 外接 API 单次超时（秒），失败后仍走重试/模板兜底
```

### 7.2 调用面与 token 预算

| 场景 | 频率 | 输入/输出 tok | 说明 |
|---|---|---|---|
| 对话回合 | 玩家驱动 | ~600 / ~120 | 同步，前端显示"…" |
| 送礼/切磋感受 | 玩家驱动 | ~600 / ~120 | 同步；复用对话 prompt/上下文，每次送礼/切磋各一次 |
| 主动搭话 | 进入半径+达标 | ~300 / ~60 | 同步、带缓存 |
| 反思 | 后台低频 | ~1200 / ~200 | 异步 |
| 关系摘要 | 后台低频 | ~800 / ~150 | 异步 |
| 每日心事 | 每 NPC 每日一次 | ~500 / ~80 | 后台低频，失败当天不再重试 |
| 八卦改写 | 后台低频 | ~400 / ~80 | 仅 importance≥7 |
| 记忆 embedding | 每条记忆 | — | `/api/embed` 批量 |

embedding：`POST /api/embed {"model": ..., "input": [...]}`，向量 L2 归一化后 upsert 进 Chroma 集合（**恒显式传 embeddings，禁用 Chroma 默认嵌入函数**）。

### 7.3 结构化输出

- 对话/反思用 `/api/chat` + `format:"json"` + prompt 内 JSON schema 说明；对话请求 `stream:true`，服务端边收边解码 `reply` 并经 SSE 转发（§6.6），JSON 校验仍在流结束后做；
- qwen3 禁思考：请求体 `"think": false`（旧版 Ollama 退化为 system 附加 `/no_think`）；
- 校验→重试 1 次→模板兜底（§6.6）；所有调用写 `llm_log` 表（用途/耗时/token/成败）便于实验分析。流式调用的 token 数取 Ollama 末行或 OpenAI `stream_options.include_usage`（不支持时自动去掉该字段重试，代价是没有 token 计数）。

### 7.4 观测（Langfuse，与 llm_log 并存）

可选旁路，`LANGFUSE_ENABLED`（默认 false，fail-closed：不可达/未配零副作用）。自托管 v2 栈：`server/deploy/langfuse/` docker-compose（Postgres+Redis，对外 3000，v1 Basic 用 public/secret key 鉴权）。集中装配在 `app/agent/trace.py`，钉定 SDK `langfuse==2.14.0`（v5 为 OTel 全重写、API 与下文形态不符）。

- **父 trace**：对话 `dialogue.talk_stream()` 开 `dialogue` 根 trace（tags=npc/player、metadata=affinity_before/place/rag_top_k），包 `retrieve`/`recent_turns`/`add_memory`/`outcome` span；拜师/送礼/切磋各开 `apprentice`/`gift`/`spar` 轻量 trace（好感前后）。流式生成在流结束时以完整文本补一条 generation。
- **全局咽喉点**：`OllamaClient.chat/embed` 命中当前 trace 上下文时，chat 发 generation（model/温度/prompt/输出/usage/attempt）、embed 发 span；M4 反思/八卦/搭话无需各自埋点即被覆盖。
- 打点全部 best-effort、后台缓冲发送，`flush/shutdown` 只在 lifespan shutdown；绝不放热路径（AGENTS #4/#6），也不动 `llm_log` 本地 SSOT。

### 7.5 并发控制

`PriorityLimiter`（堆式异步优先级队列，并发上限 `LLM_CONCURRENCY=2`）全局限流；优先级：对话/送礼/切磋/中期批总结/embedding（0）> 搭话（1）> 关系摘要（2）> 长期批总结/反思/每日心事与默认（3）> 八卦（4）。`dialogue_summary` 前台中期总结用 0；每 36 轮长期总结在后台任务中复用同一 purpose，显式传 `priority=3`。等待者按优先级与入队顺序取槽，取消安全；后台反思/心事/八卦不会挤占玩家对话。

### 7.6 本地运行日志

服务端同时输出控制台与 `server/data/xia.log`（5MB×3 滚动）：启动配置、HTTP 方法/路径/状态/耗时、LLM purpose/model/耗时/成败与异常、后台反思/八卦/心事结果。`/api/health` 返回 `log_path` 与当前对话后端/API 直连开关。客户端在 Godot 输出面板以 `[xia/api]` / `[xia/main]` 记录请求耗时、网络错误与连接状态变化；只有 `/api/game/state` 轮询失败才切换“连接中断”，应用层 4xx/超时不会误标离线。

---

## 8. 数据模型（SQLite）

```sql
-- M6 目标形态：玩家与 NPC 共用角色卡
character(id, kind, name, level, exp, cultivation, hp, mp, gold,
          str, con, agi, int_, free_points, art_id NULL, sect_id NULL,
          map_id, created_at, last_saved_at)  -- kind: player|npc
player(id, character_id UNIQUE, pos_x, pos_y, clock_offset)  -- 存档/位置锚点
npc(id, character_id UNIQUE, name, sect_id NULL, role, init_affinity,
    home_poi, work_poi, map_id)  -- 身份/日程锚点；memory 外键仍指向 npc.id
npc_state(npc_id, affinity, last_reflection_at, mem_since_reflection,
          paused_until, last_greet_real,
          interacts_since_summary, last_summary_conv_id)  -- 旧批总结计数/水位，保留兼容
npc_mood(npc_id PK, mood, reason, until_real)  -- 轻情绪，只进 prompt；until_real 为现实时间
npc_plan(npc_id, game_day, heart, reason, deviation_json, created_at_real,
         PRIMARY KEY(npc_id, game_day))  -- 每日心事+安全偏离，每 NPC 每天最多一行

art(id, name, element, cap_level, sect_id NULL, is_true, passive_json, desc)
  -- element: metal|wood|water|fire|earth|wind|thunder|ice|light|dark|demon
character_art(character_id, art_id, learned_at, source,
              PRIMARY KEY(character_id, art_id))
skill(id, art_id, name, kind,  -- 主动招式；内功被动在 art.passive_json
      unlock_level, mp_cost, multiplier, cooldown, effect_json)
character_skill(character_id, skill_id, level, equipped,
                PRIMARY KEY(character_id, skill_id))  -- equipped: 主动技是否装入 3 战斗栏

item(id, name, type,        -- potion|weapon|armor|accessory|misc|gift
     tier, price, effect_json)
character_inventory(character_id, item_id, qty, equipped_slot NULL,
                    PRIMARY KEY(character_id, item_id))  -- weapon|armor|accessory

> `npc` 表只存身份/职分/地点锚点、`character_id` 与稳定主键；**人格、说话风格等静态性格定义只
> 在 `agent/npc.py` 的 `NPC_ROSTER`（代码）中**，对话/调取按枚举使用，不再入库
> （原 `persona_json / speech_style / waypoints_json` 列死数据，已随迁移 DROP）。
> 动态运行态（好感、反思计时、搭话冷却等）一律在 `npc_state` 持久化。
>
> **M6 迁移**：旧库先把 `player` 行迁成 `character` 行，再给每个 `npc` 建 `character` 行；
> `player_skill` / `inventory` 迁到 `character_skill` / `character_inventory`，旧表在新链路
> 验证前保留只读。`map_id` 给旧数据默认 `qingxi`。玩家与 NPC 走同一套
> `stats()` / 战斗引擎 / 功法上限 / 散功规则。详见 `docs/M6-PLAN.md`。
>
> **多地图取址**：地点用 `(map_id, poi)`；`character.map_id`、`npc.map_id`、`memory.map_id`
> 必须一致，避免两张图的“客栈”串记忆。V1 NPC 不跨图。
>
> **世界阶段按图拆**：`world.stage` 目前是清溪镇专用；南风城 V1 不做世界阶段。
> 后续如需按图推进，把 kv 键改成 `world.stage:{map_id}`，不要用一个全局 stage 继承
> 另一张图的断岳刀进度。

memory(id, npc_id, ts_game,           -- 游戏时钟；只存 event/reflection/gossip
       kind,                           -- event|reflection|gossip
       content, participants_json, poi, map_id, importance,
       created_at_real,                -- 现实时间：关系摘要水位线
       source_memory_id NULL)          -- gossip 恒指向根记忆，防重复转述
relationship(npc_id, target_kind,     -- player|npc
             target_id, summary, updated_at)  -- updated_at 为现实时间

conversation(id, npc_id, ts_game, turns_json, affinity_delta_total)
             -- 短期窗口；turns_json={user,npc,place}；超过容量即总结为中期记忆（纯内存）并删最早一批
longterm_memory(id, npc_id, ts_game, content, turn_count, created_at_real)
             -- 每 36 轮原始对话后台总结；不写 memory/Chroma，不参与反思/关系/八卦/RAG
battle(id, player_id, target_kind, target_id, state_json, active, created_at)
llm_log(id, purpose, model, in_tokens, out_tokens, ms, ok, created_at)
kv(k, v)  -- 全局：时钟偏移、初始化标记、gossip_pair:{min}:{max}=游戏日、gift_count:{npc_id}:{game_day} 等
```

**中期记忆不落库**：`MemoryStore._midterms`（summary+线索+线索向量）与 `_longterm_turns`（36 轮原始轮缓冲）都是进程内结构，服务重启清零；短期窗口与 `longterm_memory` 持久化。`new_game` 同样只清中期/缓冲/附近事件，不清 `conversation`/`longterm_memory`。

**Chroma 集合 `memories`**（PersistentClient 本地目录，`hnsw:space=cosine`）：

- id = memory.id；document = content；embedding = Ollama 1024 维（L2 归一化）；metadata = {npc_id, kind, importance, ts_game, poi}；
- **Chroma 只索引 `memory` 表**（event/reflection/gossip）；中期线索向量只在 `MemoryStore` 内存，`longterm_memory` 不进 Chroma；
- 写入顺序：SQLite 落库分配 id → 同一写锁内 Chroma upsert（先 SQLite 后 Chroma）；
- 索引是**派生数据**：换 embedding 模型或索引损坏时，删集合从 SQLite 全量重嵌入重建——SQLite 不存向量正是为了留这条退路；
- Chroma 元数据不支持排序与列表值，反思（按时间取最近 30 条）、八卦/关系（按参与人过滤）等查询走 SQLite，Chroma 只做 ANN。

索引：`memory(npc_id, ts_game)`、`memory(npc_id, importance)`、`memory(source_memory_id)`（`idx_memory_source`）。WAL 模式 + 单写队列（asyncio.Lock）避免后台反思与对话写冲突。`source_memory_id` 由 `connect()` 幂等迁移补列，旧库无需重建。

---

## 9. API 设计

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/game/new` | 新档 `{name}`（覆盖旧档需确认标记） |
| GET | `/api/game/state` | 玩家全量状态+游戏时钟+`map_id`+`art`（当前功法/属性/上限/等级/瓶颈/已学招式）+`learned_arts`+`npcs` 花名册（id/slug/name/role/好感/当前 poi/锚点/`paused_until`）+`world`（`stage/artifact_acquired/announced_at_game/announcement/premise`） |
| GET | `/api/npcs/state` | 日程推算全量参数：`clock/map_id/sense_radius/greet_threshold/phases/pacing/gather_anchor` + 每人 `home_to_work/work_to_gather/pos/poi/affinity/paused_until/greet_ready/heart/deviation`（只返回当前地图 NPC） |
| POST | `/api/npc/{id}/greet` | 主动搭话：`{greet,reply,affinity}` 或 `{greet:false,reason:affinity|cooldown|paused}`；无存档 409、查无此人 404 |
| POST | `/api/npc/{id}/talk` | SSE `text/event-stream`：`{message}` → `start`/`token`/`reset`/`done` 事件，`done` 含 `{reply, affinity, end, player, fallback, place}`（LLM 生成，RAG 检索记忆；仍为单次 LLM 调用） |
| POST | `/api/npc/{id}/gift` | `{item_id}` |
| POST | `/api/npc/{id}/challenge` | 请求切磋 → 进入战斗 |
| POST | `/api/npc/{id}/apprentice` | 请求拜入正式门派（南风城；条件校验+仪式台词）；武馆学艺走 `/api/game/art/learn` |
| GET | `/api/shop/{shop_id}` | 货架（含好感折扣） |
| POST | `/api/shop/{shop_id}/buy` | `{item_id, qty}` |
| POST | `/api/shop/{shop_id}/sell` | `{item_id, qty}` 半价卖出（装备中须先卸下；`unsellable` 神器拒绝） |
| POST | `/api/game/allocate` | `{stats:{str,con,agi,int}}` 自由点分配 |
| POST | `/api/game/equip` | `{item_id}` 穿装备（同槽自动卸回背包） |
| POST | `/api/game/unequip` | `{slot}` 卸下槽位装备 |
| POST | `/api/game/skill/equip` | `{skill_id, equip}` 主动技装入/卸出战斗栏（≤3） |
| POST | `/api/game/skill/upgrade` | `{skill_id}` 修炼点升级（`max(20, 升级经验×5%)×当前级`） |
| GET | `/api/game/arts` | 功法图鉴：全量功法+当前功法+已学状态+上限+是否需要散功+可学来源 |
| POST | `/api/game/art/learn` | `{art_id, confirm_reset?}` 学/换功法；同源直接换，异源未确认返回 `409 requires_reset` |
| POST | `/api/game/travel` | `{map_id}` 切换地图与落点；V1 仅清溪镇↔南风城官道，服务端校验 |
| POST | `/api/sect/join` | `{sect_id}` 入正式门派：无门派、等级≥15、掌门好感≥60；清溪镇三馆不走此路由 |
| POST | `/api/battle/start` | `{target_type: mob, target_id}` 野外怪物战斗；NPC 切磋经 `/api/npc/{id}/challenge` |
| POST | `/api/battle/action` | `{action: attack|skill|item|flee, id?}` → 结算后全量战况 |
| GET | `/api/battle/state` | 断线重入 |
| GET | `/api/debug/npc/{id}` | **实验观察**：好感、反思计数与 `seconds_since_reflection`、关系摘要、最近 50 条记忆（含 `source_memory_id`）、当前日程 `{phase,poi,pos,paused}`、世界立场 `world{stage,stance,knowledge,stage2}`、`agenda`、`mood`、`speech_examples`、`heart`、`deviation`、`character`（等级/功法/属性/装备/招式） |
| GET | `/api/health` | 含 Ollama 连通性检查 |

战斗响应含 log 数组（逐条结算文本），客户端按序播放浮字/震动动画；每条伤害日志含 `element_counter: advantage|disadvantage|neutral` 与最终倍率，用于战斗 UI 显示“克制/被克”。

---

## 10. 客户端 UI

当前实现在 `xia-ai-town/`，旧 `client/`（Phaser）已删除。采用纸墨浅色面板、低饱和玉绿像素城镇与朱砂点缀；1440×900 逻辑画布保持比例缩放，地图镜头跟随玩家。左侧角色状态与玩法提示，右侧小地图及人物录，底部快捷操作栏。实际玩法对接 `server/app/main.py` 的 M0–M5 路由：对话、好感、拜师/切磋/送礼、日程移动与主动搭话均已在 Godot 客户端开放；M6 功法/南风城为规划（见 §13）。

- **HUD**：HP/MP 条、修为等级经验条（`Lv.10 / 15`）、当前功法/属性、银两、游戏时辰（"未时"）、当前 POI 名；到上限显示“瓶颈”；
- **面板**：角色(I)（属性分配）、背包(B)（装备/使用/送礼入口）、武学(K)（当前功法/内功/招式/已学功法图鉴/装备与升级）；
- **功法切换**：武学面板点选已学或可学功法；同源提示“修为保留，上限 15→40”，异源红字“散功重修：等级归 1，经验清零；四维/装备/图鉴保留”；新上限低于当前等级时二次确认；
- **NPC 日程**：每 10 秒随状态同步拉取 `/api/npcs/state`，用与服务器一致的 LCG/折线函数逐帧本地推算位置；偏离参数随同步到达，Godot `_deviated_phases` 与服务端 `_day_phases` 同常量翻译相位；对话中冻结当前 NPC，关闭对话框立即回到日程位置；人物录显示当前地点与当日心事，`paused_until` 未过期时标注"（正忙）"；
- **主动搭话**：走近 NPC 进入 48px 感知半径且 `greet_ready=true` 时，客户端异步请求 `/api/npc/{id}/greet`（不阻塞走动），成功后在 NPC 头顶绘制纸色气泡，最多两行、每行约 14 字；
- **对话**：立绘位+文本框+输入框+5 快捷键；NPC 头顶好感小图标（心形分档，仅示意档位不显数值）；
- **战斗**：全屏半透明遮罩面板，双方状态条/属性徽记/“克制·被克”标记/Buff 图标/四指令钮/滚动战斗日志/浮字伤害；
- **地图**：M 舆图显示清溪镇/南风城与官道；切换地图走 `/api/game/travel`，V1 NPC 不跨图；等级不足时只提示“城外凶险”；
- **商店**：列表+价格（显示折扣）；
- **操作**：WASD/方向键移动，E 交互，I 角色、B 行囊、K 武学、M 舆图，滚轮缩放，Esc 关闭面板。战斗进行中只能通过结算或逃走退出；靠近 NPC 按 E 进入对话（输入或快捷键[打招呼][打听消息][学艺/拜师][切磋][送礼]）。
- **连接**：默认 `http://127.0.0.1:8080`，设置面板可保存地址，`XIA_API_URL` 环境变量优先；请求有超时、等待状态和错误反馈。游戏状态每 10 秒同步，时钟连续本地推算。状态版本号阻止较早发出的轮询覆盖后续操作结果；断线重连后以服务器战局恢复。
- **镇志**：左侧“江湖小笺”第二段显示后山传闻摘要与“清溪镇志”按钮；点击才展开四条公开事实，阶段 2 追加全镇通告。**不自动弹窗**，首次进入只更新面板文案。
- **落点**：加载或战败时，若后端坐标落在障碍物内，客户端将其投影到最近可行走位置（包括旧客栈复活坐标）；下次交互上报该位置。

---

## 11. 美术与字体渲染规格

### 11.1 分辨率与字体

**UI 与世界全部按原生分辨率渲染**：`project.godot` 的 `display/window/stretch/mode` 为 `disabled`，不做整帧拉伸。原因是 `canvas_items` 会把 1440×900 的整帧（含中文笔画）重采样到实际窗口尺寸，非整数倍时字缘必然发灰；中文无法用点阵字体规避，因此走「UI 独立原生层 + TTF 动态栅格化」路线。

| 项 | 规格 |
|---|---|
| 拉伸模式 | `disabled`（禁止整帧重采样） |
| 世界缩放 | 整数档 1×/2×/3×，滚轮切换，默认 2×；相机位置取整到像素 |
| HUD/面板 | 锚点 + 容器布局，随窗口原生尺寸重排，字号不缩放 |
| 字体 | `SystemFont` + CJK 回退（Noto Serif CJK / Source Han Serif / SimSun…），按最终像素尺寸直接栅格化 |
| 采样 | 世界贴图 `TEXTURE_FILTER_NEAREST` |

**约束**：任何以「先渲染小画布再放大」为手段的适配都会让中文发虚；需要缩放时只允许整数倍，且只作用于世界层，绝不作用于 HUD。

### 11.2 贴图资源

地图、建筑与人物已由程序绘制改为图片资源；源图在 `xia-ai-town/asserts/`，切图脚本 `tools/build_art_assets.py` 生成 `xia-ai-town/assets/`（进版本库），像素规格与生图提示词见 `docs/ART-PROMPTS.md`。

| 类别 | 产出 | 规格 |
|---|---|---|
| 地形 | `assets/atlas/terrain.png` | 4×4 格，每格 16×16，不透明 |
| 草木山石 | `assets/atlas/props.png` | 4×4 格，每格 32×32，透明底 |
| 建筑部件 | `assets/atlas/parts.png` | 4×4 格，每格 64×64，透明底 |
| 建筑成品 | `assets/buildings/<id>.png` | 按 `BUILDINGS` 的 rect 尺寸合成 |
| 角色 | `assets/characters/<角色>.png` | 4 行朝向 × 4 帧，每格 32×32 |
| 待补 | 怪物、野外加密道具、UI 九宫格 | 提示词见 `ART-PROMPTS.md` §5–§7 |

**关键约定**：1 美术像素 = 1 世界像素。地图格 16 世界像素、人物 32 世界像素，配合整数倍缩放才能保证放大后仍锐利。世界静态层（地形 + 建筑 + 道具 + 野外植被）在启动时烘焙成单张 1600×960 纹理，动态层每帧绘制主角、NPC 与怪物并按 y 排序。

**调色**：武侠水墨风，低饱和，点缀朱砂/黛青。

---

## 12. 项目结构

```
Xia/
├─ docs/DESIGN.md
├─ xia-ai-town/                # Godot 4 + GDScript，当前客户端
│  ├─ project.godot
│  ├─ scenes/main.tscn         # 主场景
│  ├─ scripts/                 # main / api / ui_kit / town_world / town_map
│  ├─ asserts/                 # 生图源素材（手放，不入库）
│  ├─ assets/                  # 切好的图集/建筑/人物（build_art_assets.py 生成）
│  ├─ tools/                   # 切图脚本 + 隔离存档的真实后端冒烟验证
│  └─ addons/godot_ai/         # 已有 Godot MCP 插件
├─ server/
│  ├─ app/
│  │  ├─ main.py  config.py  db.py
│  │  ├─ game/                 # player, inventory, shop, battle(规则AI), clock, schedule(确定性日程),
│  │  │                        # world(公设/立场), world_state, plan(当日偏离读取), data
│  │  ├─ agent/                # npc, memory(对话窗口/中期内存/长期表), rag, dialogue(对话/送礼/切磋共用),
│  │  │                        # summary(中期/长期总结), json_utils(JSON 围栏), reflection, relationship, gossip, greet, scheduler,
│  │  │                        # agenda, mood, heart(心事/偏离), events(附近事件)
│  │  └─ llm.py                # ollama 客户端 + PriorityLimiter + llm_log
│  ├─ data/                    # xia.db + chroma/（向量库），均 gitignore
│  └─ requirements.txt         # fastapi uvicorn numpy httpx pydantic-settings chromadb
└─ README.md
```

---

## 13. 里程碑

| 里程碑 | 内容 | 验收 | 状态 |
|---|---|---|---|
| M0 脚手架 | server 跑通、Ollama 健康检查、存活校验 | server 启动 + 健康检查通过 | ✅ 已完成（原在 Phaser client 网页端验收，client 已删除、UI 迁至 Godot） |
| M1 战斗循环 | 野怪、回合制战斗、药剂、商店、存档、死亡复活 | 完整打一只狼并买药 | ✅ 已完成 |
| M2 成长系统 | 属性升级、装备、技能、拜师（原纯条件判定，M3 改为好感对话式） | 入门剑阳派用出赤阳斩 | ✅ 已完成 |
| M3 NPC智能v1 | 记忆流、RAG、自由对话、好感度、对话式拜师/切磋/送礼 | 连聊三天 NPC 记得你 | ✅ 已实现（Godot 侧 M3 路由已开放，见 §10） |
| M4 反思与传播 | 反思、关系摘要、八卦、主动搭话、确定性日程 | 隔天 NPC 主动提起你昨天的事 | ✅ 已实现（确定性冒烟覆盖反思→关系→八卦→搭话链路与暂停恢复；Godot 四阶段冒烟全绿。真实 4B 长时观察按 `docs/M4-PLAN.md` §7.2 复现） |
| M5 世界公设与感知 | 后山公设/立场/知识分层、断岳刀世界事件、对话议程/口癖/轻情绪、每日心事与日程偏离、附近事件感知 | 取刀后全镇知晓但拜师规则不变；议程不重复、偏离两端一致 | ✅ 已实现（W/B/A/C 四包分次提交；`docs/M5-PLAN.md`） |
| M6 功法境界与南风城 | 功法属性/上限、同源转换与散功、属性克制、NPC 角色卡同构、清溪镇后山深处、南风城两派与城主 | 同源换功保留等级、异源归 1；NPC 与玩家同一套角色卡；清溪镇练到 15 后可去南风城 | 🚧 Phase A 已实现（功法/角色卡/三馆学艺）；Phase B/C 待续，见 `docs/M6-PLAN.md` |

每个里程碑结束产出可玩 build + 一段实验观察记录（debug API 截图/数据）。

---

## 14. 可配参数（config.py / .env）

| 参数 | 默认 | 说明 |
|---|---|---|
| TIME_SCALE | 60 | 1 现实秒=多少游戏秒 |
| GREET_THRESHOLD | 70 | 主动搭话好感阈值 |
| GREET_COOLDOWN_REAL | 300s | 搭话冷却 |
| SCHEDULER_ENABLED | true | 后台反思/八卦调度总开关（测试与冒烟置 false） |
| SCHEDULER_TICK_REAL | 60s | 反思巡检周期；反思冷却 = 该值 × 0.75 |
| REFLECT_MEM_THRESHOLD | 8 | 触发反思的新记忆数 |
| REFLECT_INTERVAL_REAL | 360s | 距上次反思的最短时间间隔 |
| REFLECTION_TAKE_N | 30 | 每次反思取最近记忆条数 |
| REFLECTION_MIN_MEMORIES | 3 | 少于此数不反思 |
| MAX_REFLECTION_DELTA | ±2 | 反思单次好感修正上限 |
| RELATIONSHIP_UPDATE_EVENTS | 5 | 关系摘要刷新所需新增事件数 |
| RELATIONSHIP_MENTION_REFLECTIONS | 3 | 关系摘要刷新所需提及反思数 |
| RELATIONSHIP_MAX_CHARS | 100 | 关系摘要字数上限 |
| GOSSIP_INTERVAL_REAL | 120s | 八卦扫描周期 |
| GOSSIP_IMPORTANCE_MIN | 5 | 可传播记忆的最低重要度 |
| GOSSIP_REWRITE_IMPORTANCE | 7 | 触发 LLM 改写的重要度 |
| GOSSIP_MAX_PAIRS_PER_SCAN | 2 | 每次扫描最多成功传播的对数 |
| NPC_SENSE_RADIUS | 48 | 客户端主动搭话感知半径（像素） |
| PLAN_ENABLED | true | 每日心事/日程偏离总开关 |
| PLAN_DEVIATION_SHIFT_SEC | 1200 | 偏离阶段平移上限（游戏秒）= 20 游戏分钟 |
| PLAN_HEART_MAX_CHARS | 60 | 心事字数上限 |
| DIALOGUE_WINDOW_SIZE | 6 | 每轮回复平铺的最近对话轮数 |
| CONVERSATION_WINDOW_CAPACITY | 10 | conversation 超过 N 条即把最早 N 轮总结为一条中期记忆（纯内存） |
| MIDTERM_CAPACITY | 10 | 每 NPC 最多保留的中期条目数（FIFO） |
| MIDTERM_TOP_K | 3 | 每轮最多注入的中期 summary 条数 |
| MIDTERM_MIN_SCORE | 0.45 | 中期线索余弦相似度阈值 |
| LONGTERM_TURN_INTERVAL | 36 | 每累计 N 轮原始对话触发一次长期总结 |
| MAX_LEVEL | 100 | 修为等级全局硬上限 |
| ARTLESS_LEVEL_CAP | 15 | 未习功法“凡体”的等级上限 |
| ELEMENT_ADVANTAGE | 1.20 | 属性克制伤害倍率 |
| ELEMENT_DISADVANTAGE | 0.85 | 属性被克伤害倍率 |
| SKILL_UPGRADE_EXP_RATE | 0.05 | 招式升级费 = `max(20, 当前升级经验×该值)×招式等级` |
| RECENCY_HALF_LIFE | 3 游戏日 | 记忆新鲜度衰减 |
| RAG_TOP_K | 6 | 检索条数 |
| W_REL / W_REC / W_IMP | 1.0 / 0.8 / 0.5 | 检索权重 |
| CHROMA_DIR | server/data/chroma | Chroma 向量库目录（可删重建） |
| RESET_ON_START | false | true 时每次启动后端清空全部数据，完全重新开一局 |
| LLM_CONCURRENCY | 2 | PriorityLimiter 并发上限 |
| LLM_NUM_CTX | 20480 | 本地 Ollama 上下文窗口；长期总结 36 轮 prompt 需要 ≥16k |
| MAX_DIALOGUE_DELTA | ±3 | 单次 LLM 交互（对话/送礼/切磋）好感变化上限 |
| LLM_API_KEY / LLM_API_BASE_URL | 空 | 同时设置则对话走该 OpenAI 兼容 API |
| LLM_API_MODEL | 空 | API 对话模型名；缺省回退 CHAT_MODEL |
| LLM_API_EMBED_MODEL | 空 | API 侧嵌入模型名；缺省回退 EMBED_MODEL |
| LLM_API_TRUST_ENV | false | 外接 API 是否继承 shell 的 http(s)_proxy；默认直连 |
| LLM_API_TIMEOUT | 8s | 外接 API 单次超时；失败后仍走重试/模板兜底 |

---

## 15. 风险与开放问题

**风险与对策**：
- 4B 模型 JSON 输出不稳 → format:json + 重试 + 模板兜底，永不阻塞玩法（§7.3）；
- 对话延迟 2~5s（4B Q4 本地推理）→ 前端"…"指示 + 记忆检索与 prompt 组装并行；每 10 轮多一次中期总结与线索嵌入，每 36 轮多一次后台长期总结（不占前台等待）；
- 长对话上下文膨胀 → 只平铺最近 `dialogue_window_size` 轮，长程信息靠中期 summary+线索、长期记忆表与关系摘要承载；
- 中期记忆/36 轮缓冲只在进程内存 → 重启丢失；短期窗口与 `longterm_memory` 持久化，接受该取舍。长期总结后台异步，进程退出时可能丢一批；
- 线索匹配质量依赖 4B 模型 → `MIDTERM_MIN_SCORE`/`MIDTERM_TOP_K` 先给默认值，实测调；中期兜底 `clues=[]` 时该条暂不可检索；
- 好感被 LLM 漂移 → 对话/送礼/切磋统一 clamp ±3；送礼价值与切磋胜负只作 prompt 上下文；
- SQLite 写并发（对话 vs 后台反思）→ WAL + 单写队列；
- 记忆噪声（同场目击过多）→ 目击仅限战斗/大额消费两类显著事件；
- ChromaDB 三坑：默认 embedding function 会静默下载 onnx MiniLM（英文向，检索必错）→ 建集合 `embedding_function=None`、恒显式传 embeddings；默认相似度空间是 l2 → 建集合加 `metadata={"hnsw:space": "cosine"}`；离线遥测报错噪声 → `Settings(anonymized_telemetry=False)`；
- **散功挫败感**：等级归 1 可能让玩家觉得白练 → 软散功保留四维/装备/图鉴，确认弹窗写清代价；若实测仍挫败，再调低散功惩罚或给“洗髓”道具；
- **NPC 同构是大重构**：玩家/技能/背包表都要换 owner → 分 Phase 迁移，旧表验证前保留只读，`server/tests/smoke.py` 覆盖旧档迁移与新局重置；
- **多地图串记忆**：裸 POI 字符串会在两张图出现同名地点 → 所有地点键带 `map_id`，`memory.map_id`/`npc.map_id`/`character.map_id` 一致；V1 NPC 不跨图；
- **属性克制影响平衡**：1.20/0.85 是首版拍板值 → 先在清溪镇用无属性怪保底，南风城再放属性怪，实测后调；
- **NPC 自主散功/换功**：工具调用若让掌门散功变 1 级，会破坏门派战力与剧情 → V1 NPC 成长保持预设，工具调用加剧情门槛与“掌门不可轻易散功”的保护；
- **100 级数值膨胀**：现有线性 HP/攻击公式在 100 级会放大 → 开南风城前做一次数值回归，保证同图怪 8~12 刀、玩家扛 5~8 刀；
- **旧“三派”文案**：世界公设、NPC 人设、对话示例里仍称“三派/掌门” → M6 文案统一为“三馆/馆主”，南风城才用“门派/掌门”，避免玩家混淆身份。

**开放问题（默认值先行，欢迎推翻）**：
1. 数值表（§5）为首版拍脑袋值，需 M1 后实测调平；
2. 对话语言风格浓度：目前定"古风白话 ≤80 字"，可调更白话或更文言；
3. NPC 八卦频率与传播深度目前保守（每对每日 1 条），实验后调；
4. 音效/音乐 V1 完全不做，是否接受；
5. 后续版本方向：NPC 完整自主规划、隐世高人张馒头任务线、多存档；
6. 长期记忆接入对话的检索方式（向量 RAG vs 常驻最近 N 条）待下一任务；当前只生成入库；
7. 南风城城主功法属性/名称（暂定雷/光，cap55）待定；
8. 南风城门主姓名、支持 NPC 名单与具体怪物表待 M6-C 内容阶段补；
9. NPC 成长是否跨局持久化：V1 默认 `new_game` 重置回花名册预设；
10. 武馆学费/好感门槛、叛门代价、100 级内容曲线待实测调平。
