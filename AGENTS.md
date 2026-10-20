# AGENTS.md

本文件是每个新会话的行动指南。用户已决定将当前前端改为 **Godot 4 + GDScript**：代码放在 `xia-ai-town/`，以 `server/` 实际接口为准；旧 `client/`（Phaser 历史前端）已删除，历史见 git，勿再引用。如需新的图片资源，须提供生图提示词与存放路径，等待用户手动生成。本版采用 Godot 原生绘制，无外部图片依赖。M0–M4 已全部落地：脚手架、战斗、成长、NPC 记忆/RAG/对话、反思/关系/八卦/主动搭话与确定性日程。

## 项目是什么

《侠》：二维像素武侠 RPG + 生成式 NPC 实验（仿斯坦福小镇）。游戏本体（门派/装备/药剂/属性/技能/回合制战斗）能简则简，一切为 NPC 智能化实验服务。无主线任务。

**权威设计文档：`docs/DESIGN.md`**。实现任何功能前先读对应章节；数值表、NPC 花名册、API 路由均以该文档为准，改动需同步更新文档。

## 已锁定的技术决策（不要重新讨论或擅自更换）

| 决策点 | 选择 |
|---|---|
| 客户端 | Godot 4 + GDScript（`xia-ai-town/`）；旧 `client/` 已删除 |
| 后端 | Python FastAPI 单进程，同时承载游戏逻辑与 NPC 智能体 |
| 存储 | SQLite 单文件（WAL 模式）存游戏状态+记忆记录（**记忆的 SSOT**）；向量检索用 **ChromaDB**（PersistentClient 本地目录，cosine 空间单集合 memories），Chroma 是可从 SQLite 全量重嵌入重建的派生索引 |
| 对话模型 | 本地 Ollama `qwen3:4b-instruct`（4bit 量化） |
| 向量模型 | 本地 Ollama `qwen3-embedding:0.6b`（8bit 量化，1024 维，入库前 L2 归一化） |
| 美术 | Godot 原生程序绘制像素地图与纸墨 UI；如需新图片，按用户要求先提供提示词和路径并等待 |
| 玩家入门 | 无初始门派，靠与掌门 NPC 好感度达标后对话拜师；一生只入一派 |
| 战斗 | 1v1 回合制，敌方 AI 为规则型（**不调 LLM**） |

## 实现时必须遵守的架构约束

这些是设计阶段定下的原则，改动前先想清楚为什么存在：

1. **服务器权威**：属性、背包、战斗、好感度、记忆只在服务器结算；客户端只做渲染与输入。玩家移动本地推演，交互时上报位置。
2. **NPC 位置零同步**：NPC 位置 = 日程表的确定性函数（按游戏时钟推算）。客户端与服务器各自用同一份数据推算，不做位置轮询。伪随机踱步以 NPC id + 游戏日为种子，保证两端可复现。
3. **战斗为同步请求-响应**：`POST /api/battle/action` 一次返回双方结算结果。不用 WebSocket。
4. **LLM 调用永不阻塞玩法**：所有 LLM 结构化输出必须走 校验 → 重试一次(temperature 0.3) → 模板兜底 的链路；JSON 解析失败时用模板回复，游戏可继续。
5. **好感度防漂移**：LLM 单回合好感变化 clamp ±3；送礼/切磋等规则类增益以规则为准。
6. **SQLite 单写队列**：对话写入与后台反思/八卦任务并发写库，用 asyncio.Lock 串行化 + WAL，不要改成多连接并行写。记忆写入 = SQLite 落库 → 同一锁内 Chroma upsert（先 SQLite 后 Chroma）。
7. **LLM 全局优先级限流**（`PriorityLimiter`，并发默认 2）：`dialogue`/`dialogue_summary`/`embed`=0 > `greet`=1 > `relationship`=2 > `reflection`=3（默认）> `gossip`=4。`llm.chat` 按 purpose 自动取级，新增 purpose 要在 `PRIORITY` 里登记。所有调用写 `llm_log` 表供实验分析。

## 环境与配置

依赖本地 Ollama 服务（默认 `http://localhost:11434`），需提前 pull 两个模型。模型 tag 走 `.env`，**不要在代码里写死模型名**：

```env
OLLAMA_HOST=http://localhost:11434
CHAT_MODEL=qwen3:4b-instruct-2507-q4_K_M   # 本机实际 tag，server/.env 已配好
EMBED_MODEL=qwen3-embedding:0.6b-q8_0
LLM_TIMEOUT=20
LLM_CONCURRENCY=2
RESET_ON_START=false   # true=每次启动后端清空数据库（SQLite+Chroma 重建），完全重新开局
```

可调参数集中在 server 端 config（`server/app/config.py`，pydantic-settings 读 `.env`，§14 清单已全量落入：时间流速、好感阈值 70、反思/八卦周期、RAG 权重与 top-k、ChromaDB 目录等）。server 依赖 chromadb（向量库），清单见 DESIGN.md §12。

### 本地运行

当前客户端：Godot 导入 `xia-ai-town/project.godot`，F5 运行主场景 `scenes/main.tscn`；或根目录 `godot --path xia-ai-town`。默认连接 `http://127.0.0.1:8000`，可在设置面板保存地址或用 `XIA_API_URL` 覆盖。隔离真实接口验证：`server/.venv/bin/python xia-ai-town/tools/smoke_test.py`。无 Ollama 的后端回归：`server/.venv/bin/python server/tests/smoke.py`——stub LLM + 临时库，覆盖 M4 后台链路，重构后必须全绿。后端保持现有 Python 实现。

Python 侧用 **uv**（优先 `command -v uv`；原环境为 `~/.local/bin/uv`）管理虚拟环境——本机 python3 无 pip 且缺 python3-venv，`python3 -m venv` 不可用，不要尝试。依赖装在 `server/.venv`（gitignore）：

```bash
cd server && uv venv .venv && uv pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000   # server
```

本机 shell 可能配了 `http_proxy`（7897 端口）：server 里 httpx 默认信任环境代理，会把 Ollama 健康检查也送进代理而误报"不可达"。启动 server 前缀加 `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`；用 curl 验证本地接口也要 `--noproxy '*'`。

## 已知坑（改动前必须知道）

- **qwen3 思考模式**：对话/结构化输出必须禁用思考——请求体加 `"think": false`；旧版 Ollama 不支持该字段时退化为 system 附加 `/no_think`。
- **4B 模型 JSON 不稳**：靠 `/api/chat` + `format: "json"` 提高成功率，但兜底链路（见上）是必需品，不是可选项。
- **本地推理延迟 2~5s**：对话请求是同步的，前端必须显示等待指示；记忆检索与 prompt 组装并行做。
- **游戏时钟**：1 现实秒 = 1 游戏分（可配），一个游戏日 ≈ 24 现实分钟。记忆 `ts_game`、NPC 日程、recency 衰减（半衰期 3 游戏日）全部基于游戏时间；但 M4 的 `paused_until`、`last_greet_real`、关系摘要水位 `relationship.updated_at` 和 `memory.created_at_real` 用现实时间（`time.time()`），两套时间不要混用。时钟 anchor 存 kv（`clock_anchor_real/game`），**服务器停机期间游戏时间照常流逝**（偏移语义，重启续走）；客户端（Godot）用同一 anchor 本地推算 HUD，不做轮询。气泡到期用本地 `Time.get_ticks_msec()`，不要与服务器 epoch 字段互比。
- **ChromaDB 三坑**：① 建集合用 `embedding_function=None` 且 add/query 恒显式传 embeddings——否则 Chroma 会静默下载 onnx MiniLM（英文向），检索全错；② 集合默认相似度空间是 l2，须以 `metadata={"hnsw:space": "cosine"}` 建集合（向量入库前仍 L2 归一化）；③ `Settings(anonymized_telemetry=False)` 关遥测，免离线时 PostHog 报错刷日志。
- **记忆双库分工**：记忆正文/元数据在 SQLite（反思按时间序、八卦按参与人过滤、调试查询都要用，Chroma 元数据不支持排序与列表值），Chroma 只做 ANN 向量检索；换 embedding 模型时删 Chroma 集合、从 SQLite 全量重嵌入重建。八卦的 `source_memory_id` 恒指向根记忆做祖源去重（配 `idx_memory_source`），不要改成指向直接来源，否则同一件事会沿传播链被反复转述。
- **记忆控制噪声**：同场目击入忆仅限战斗与大额消费（≥100 两）两类显著事件，不要扩大范围。
- **地图交互与出生点约定**：建筑是实心瓦片、玩家站不进去，商店等 E 交互判定用建筑外扩 2 格的门口区；怪物出生点放野外土路行（ROAD），避免与确定性随机树/崖重叠导致不可达。
- **派生属性单一入口（M2 起）**：装备与内功被动通过 `modifiers(db,p)` 聚合进 `derived(p, mods)`，任何要拿攻/防/速/闪避/暴击/上下限的代码一律走 `player.stats(db,p)`，**不要直接调 `derived(p)`**（会漏装备与被动）。入门门派加成是直改 player 行四维的一次性写入，不进 mods。
- **战斗数据分工**：战斗进行态存 `battle.state_json` + `active` 列（敌方快照、回合、药剂用量、`buffs`、`skill_cd`），玩家 HP/MP 以 player 行为单一事实源——断线重入靠这个约定，M3 加新战斗状态时继续挂 state_json。怪物存活存 kv `mob_kills`（击杀时间戳，60s 重生）。胜利结算会把玩家坐标推离怪物出生点 48px，防止重生后刷新页面被站桩开战；`_finalize` 的复活/推离规则改动时想想这条。
- **杀 dev server 用端口，别用 pkill -f**：`pkill -f "uvicorn ..."` 会匹配到自己这条 bash 命令行里同样含该文本的部分而自杀。用 `fuser -k 8000/tcp` / TaskStop，或 `pkill -f "app[.]main"`（字符类技巧）。
- **测试/冒烟必须 `SCHEDULER_ENABLED=false`**：否则 lifespan 启动的后台反思/八卦会调用真实 Ollama，拖慢甚至污染断言；`server/tests/smoke.py` 在 import 前设置，Godot 冒烟在子进程 env 里设置。
- **NPC 位置零同步**：服务端 `game/schedule.py` 与客户端 `town_world.gd` 各实现同一套相位/折线/LCG 踱步；改任一侧常量或算法必须同步另一侧，否则两端位置漂移。客户端不轮询 NPC 坐标，只随 10 秒状态同步取参数。

## 当前 Godot 客户端

`xia-ai-town/scripts/main.gd` 负责 HUD、面板、输入、交互、战斗及状态同步；`api.gd` 是 HTTP 客户端；`ui_kit.gd` 是主题组件；`town_world.gd` 原生绘制与碰撞、坐标保持原地图约定，并在本地按日程推算 NPC 位置、绘制搭话气泡；`town_map.gd` 提供小地图与舆图。NPC 对话/送礼/切磋/拜师、日程移动与主动搭话均已接入后端；对话中的 NPC 在客户端冻结。地图与人物无需外部图片。Godot `.godot/` 缓存不入库。

## 目录结构（实现位置参考）

```
Xia/
├─ xia-ai-town/           # Godot 4 客户端（当前前端，原生绘制）
├─ docs/DESIGN.md          # 设计 SSOT
├─ docs/M2-PLAN.md         # M2 执行拆解（已实施，留作范式）
├─ server/
│  ├─ app/
│  │  ├─ main.py config.py db.py llm.py  # main 路由+lifespan装配；config 产 .env 配置；db 单写队列+WAL；llm 双后端封装+优先级限流
│  │  ├─ game/             # data skills player inventory mobs battle shop clock schedule（确定性日程，纯函数）
│  │  └─ agent/            # npc memory（双库+reconcile）rag dialogue summary（批总结）reflection（反思入忆）relationship（滚动摘要）gossip（八卦）greet（前台搭话）scheduler（后台巡检）
│  ├─ data/               # xia.db + chroma/（向量库），均 gitignore
│  └─ requirements.txt
└─ README.md
```

`agent/scheduler.py` 是后台唯一入口：按 `scheduler_tick_real` 巡检反思、按 `gossip_interval_real` 扫描八卦；两条循环都先 sleep 再工作，单个 NPC 失败不会拖死任务。反思在素材足够时写 `kind=reflection` 记忆并顺带更新关系摘要，八卦只写双方 `kind=gossip` 记忆。主动搭话不走后台，是 Godot 进入 48px 感应圈后调用的 HTTP 接口。

## 实施顺序

按 DESIGN.md §13 里程碑推进：M0 脚手架、M1 战斗循环、M2 成长系统、M3 NPC 记忆+RAG+对话、M4 反思/关系摘要/八卦/主动搭话与确定性日程均已完成。每个里程碑交付可玩 build；调试观察主要靠 `GET /api/debug/npc/{id}`（好感、记忆流、反思、关系摘要、当前日程）。

**里程碑的执行前必读**：M2 有专属拆解文档 [`docs/M2-PLAN.md`](docs/M2-PLAN.md)，M4 有 [`docs/M4-PLAN.md`](docs/M4-PLAN.md)——数值、API 形状、文件落点、schema 迁移、验证步骤全部内联，关键决策已在文档里拍板，**实施对应里程碑时以计划文档为准、不要重新翻 DESIGN 与旧代码做考古**；仅当实现中发现设计需要变更时才回改 DESIGN.md 并同步该计划文档。后续里程碑建议沿用 `docs/M?-PLAN.md` 模式。

## Git 提交规范：任务完成自动提交

**每个任务完成时自动 commit**（无需再询问用户），一个任务一个 commit，不新增任何任务记录/变更日志文件——`git log` 就是任务时间线，`git show <hash>` 看细节。提交信息固定三段式：

```
feat: 支持智能体并发日程生成

做了什么：xxx
怎么做的：xxx
为什么这样做：xxx（取舍、踩过的坑）
```

- 首行 = conventional commit 前缀（feat / fix / refactor / docs / chore / test）+ 中文一句话标题，一行说清本次任务。
- 正文三行不可省略：做了什么（交付结果）、怎么做的（关键实现路径与涉及文件）、为什么这样做（取舍、踩过的坑）。
- 提交前用 `git status` 复核暂存内容，`.env` 与 `server/data/*.db` 等不入库。

## 语言与风格

设计文档与游戏文案为中文（武侠古风白话，NPC 对话 ≤80 字）。代码标识符与注释遵循常规英文/无注释约定，UI 文案中文。
