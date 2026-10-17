# AGENTS.md

本文件是每个新会话的行动指南。用户已决定将当前前端改为 **Godot 4 + GDScript**：代码放在 `xia-ai-town/`，以 `server/` 实际接口为准；`client/` 是已停用的历史前端，勿继续在其中开发。如需新的图片资源，须提供生图提示词与存放路径，等待用户手动生成。本版采用 Godot 原生绘制，无外部图片依赖。M0 脚手架、M1 战斗循环、M2 成长系统已完成（浏览器实测验收），实现工作请从里程碑 M3 NPC 记忆+RAG+对话开始。

## 项目是什么

《侠》：二维像素武侠 RPG + 生成式 NPC 实验（仿斯坦福小镇）。游戏本体（门派/装备/药剂/属性/技能/回合制战斗）能简则简，一切为 NPC 智能化实验服务。无主线任务。

**权威设计文档：`docs/DESIGN.md`**。实现任何功能前先读对应章节；数值表、NPC 花名册、API 路由均以该文档为准，改动需同步更新文档。

## 已锁定的技术决策（不要重新讨论或擅自更换）

| 决策点 | 选择 |
|---|---|
| 客户端 | Godot 4 + GDScript（`xia-ai-town/`）；旧 `client/` 已停用 |
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
7. **LLM 全局信号量**（默认 2）统一限流，优先级：对话 > 搭话 > 关系摘要 > 反思 > 八卦。所有调用写 `llm_log` 表供实验分析。

## 环境与配置

依赖本地 Ollama 服务（默认 `http://localhost:11434`），需提前 pull 两个模型。模型 tag 走 `.env`，**不要在代码里写死模型名**：

```env
OLLAMA_HOST=http://localhost:11434
CHAT_MODEL=qwen3:4b-instruct-2507-q4_K_M   # 本机实际 tag，server/.env 已配好
EMBED_MODEL=qwen3-embedding:0.6b-q8_0
LLM_TIMEOUT=20
LLM_CONCURRENCY=2
```

可调参数集中在 server 端 config（`server/app/config.py`，pydantic-settings 读 `.env`，§14 清单已全量落入：时间流速、好感阈值 70、反思/八卦周期、RAG 权重与 top-k、ChromaDB 目录等）。server 依赖 chromadb（向量库），清单见 DESIGN.md §12。

### 本地运行

当前客户端：Godot 导入 `xia-ai-town/project.godot`，F5 运行主场景 `scenes/main.tscn`；或根目录 `godot --path xia-ai-town`。默认连接 `http://127.0.0.1:8000`，可在设置面板保存地址或用 `XIA_API_URL` 覆盖。隔离真实接口验证：`server/.venv/bin/python xia-ai-town/tools/smoke_test.py`。后端保持现有 Python 实现，以下 Phaser 启动/浏览器验证说明仅供历史参考。

Python 侧用 **uv**（优先 `command -v uv`；原环境为 `~/.local/bin/uv`）管理虚拟环境——本机 python3 无 pip 且缺 python3-venv，`python3 -m venv` 不可用，不要尝试。依赖装在 `server/.venv`（gitignore）：

```bash
cd server && uv venv .venv && uv pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000   # server
cd client && npm install && npm run dev               # client → http://localhost:5173
```

本机 shell 可能配了 `http_proxy`（7897 端口）：server 里 httpx 默认信任环境代理，会把 Ollama 健康检查也送进代理而误报"不可达"。启动 server 前缀加 `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`；用 curl 验证本地接口也要 `--noproxy '*'`。

client 走 Vite 代理把 `/api` 转发到 8000（server 也开了 CORS，但正常开发无需直连 8000）。

## 已知坑（改动前必须知道）

- **qwen3 思考模式**：对话/结构化输出必须禁用思考——请求体加 `"think": false`；旧版 Ollama 不支持该字段时退化为 system 附加 `/no_think`。
- **4B 模型 JSON 不稳**：靠 `/api/chat` + `format: "json"` 提高成功率，但兜底链路（见上）是必需品，不是可选项。
- **本地推理延迟 2~5s**：对话请求是同步的，前端必须显示等待指示；记忆检索与 prompt 组装并行做。
- **游戏时钟**：1 现实秒 = 1 游戏分（可配），一个游戏日 ≈ 24 现实分钟。记忆时间戳、NPC 日程、recency 衰减（半衰期 3 游戏日）全部基于游戏时间，不要混用现实时间。时钟 anchor 存 kv（`clock_anchor_real/game`），**服务器停机期间游戏时间照常流逝**（偏移语义，重启续走）；客户端 `systems/clock.ts` 用同一 anchor 本地推算 HUD，不做轮询。
- **ChromaDB 三坑**：① 建集合用 `embedding_function=None` 且 add/query 恒显式传 embeddings——否则 Chroma 会静默下载 onnx MiniLM（英文向），检索全错；② 集合默认相似度空间是 l2，须以 `metadata={"hnsw:space": "cosine"}` 建集合（向量入库前仍 L2 归一化）；③ `Settings(anonymized_telemetry=False)` 关遥测，免离线时 PostHog 报错刷日志。
- **记忆双库分工**：记忆正文/元数据在 SQLite（反思按时间序、八卦按参与人过滤、调试查询都要用，Chroma 元数据不支持排序与列表值），Chroma 只做 ANN 向量检索；换 embedding 模型时删 Chroma 集合、从 SQLite 全量重嵌入重建。
- **记忆控制噪声**：同场目击入忆仅限战斗与大额消费（≥100 两）两类显著事件，不要扩大范围。
- **占位资产双端同步**：`client/public/assets/` 由 `client/tools/gen_placeholder_assets.mjs` 生成（`npm run assets`，dev/build 预脚本自动跑），产物 gitignore、不入库。新增/调整 tile 时必须同步改脚本里的 `TILES` 数组和 `FieldScene` 里的 `T` 常量——tile id 从 1 开始按数组顺序对应，两端不一致地图就错位。
- **无头浏览器验证（WSL）**：WSL 内没有中文字体（`fc-list :lang=zh` 为空），headless Chromium 里汉字全是方框；用 playwright-core + `~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome`（免下载浏览器），并用 `FONTCONFIG_FILE` 指向含 `<dir>/mnt/c/Windows/Fonts</dir>` 的临时 fontconfig 才能渲染汉字。Windows 宿主浏览器无此问题。首次无头冷加载可能超 15s（TS 按需编译 + 软渲染），等待超时放宽到 45s。两条补充：① 指向整个 Windows 字体目录时 fontcache 写入不可靠，把 `NotoSerifSC-VF.ttf` 复制到测试目录、fontconfig 只指该目录更稳；② CDP 瞬时 keydown+keyup 不触发 Phaser 的 JustDown，测试脚本按键要按住 ≥150ms 再松开（真实按键远超一帧，应用本身无此问题）；③ M2 起发现 **0ms down-up 的快速 mouse.click 同样竞态丢失**（命中测试正常、handler 不执行）——面板按钮点击改用 `move → down → wait(120) → up` 离散序列，或直接 `page.evaluate` 调 `openShop/openPanel`/`emit("pointerup")`，键盘同理可换合成 KeyboardEvent。
- **Phaser 场景实例复用（M1 踩坑）**：`scene.start` 重启场景会销毁全部游戏对象，但**场景类的实例字段跨重启存活**——`busy` 标志、Text 数组（logTexts）、对象池若不在 `create()`/`init()` 里重置，下一场战斗会对已销毁对象调 `setText`，直接崩掉渲染循环（报错为 `Cannot read properties of null (reading 'drawImage')`，实际是 Frame.data 为 null，别被字面意思带偏）。战斗内浮字等高频 UI 用对象池复用，不在动画 onComplete 里 destroy 热路径对象（软渲染 mid-frame destroy 也会崩）。
- **地图交互与出生点约定**：建筑是实心瓦片、玩家站不进去，商店等 E 交互判定用建筑外扩 2 格的门口区（FieldScene 的 SHOP_AREAS）；怪物出生点放野外土路行（ROAD），避免与确定性随机树/崖重叠导致不可达。
- **派生属性单一入口（M2 起）**：装备与内功被动通过 `modifiers(db,p)` 聚合进 `derived(p, mods)`，任何要拿攻/防/速/闪避/暴击/上下限的代码一律走 `player.stats(db,p)`，**不要直接调 `derived(p)`**（会漏装备与被动）。入门门派加成是直改 player 行四维的一次性写入，不进 mods。
- **战斗数据分工**：战斗进行态存 `battle.state_json` + `active` 列（敌方快照、回合、药剂用量、`buffs`、`skill_cd`），玩家 HP/MP 以 player 行为单一事实源——断线重入靠这个约定，M3 加新战斗状态时继续挂 state_json。怪物存活存 kv `mob_kills`（击杀时间戳，60s 重生）。胜利结算会把玩家坐标推离怪物出生点 48px，防止重生后刷新页面被站桩开战；`_finalize` 的复活/推离规则改动时想想这条。
- **杀 dev server 用端口，别用 pkill -f**：`pkill -f "uvicorn ..."` 会匹配到自己这条 bash 命令行里同样含该文本的部分而自杀。用 `fuser -k 8000/tcp` / TaskStop，或 `pkill -f "app[.]main"`（字符类技巧）。

## 当前 Godot 客户端

`xia-ai-town/scripts/main.gd` 负责 HUD、面板、输入、交互、战斗及状态同步；`api.gd` 是 HTTP 客户端；`ui_kit.gd` 是主题组件；`town_world.gd` 原生绘制与碰撞、坐标保持原地图约定；`town_map.gd` 提供小地图与舆图。NPC 暂为静态标记，尚无可调用的 NPC 后端接口。地图与人物无需外部图片。Godot `.godot/` 缓存不入库。

## 目录结构（历史 Phaser M2，实现位置参考）

```
Xia/
├─ docs/DESIGN.md          # 设计 SSOT
├─ docs/M2-PLAN.md         # M2 执行拆解（已实施，留作范式）
├─ client/                 # Vite + Phaser 3 + TS
│  ├─ src/
│  │  ├─ main.ts           # 入口；挂 window.__xia / __xiaStore 调试句柄（浏览器控制台/冒烟脚本用）
│  │  ├─ scenes/           # BootScene / FieldScene（地图+走动+怪物+I/B/K/E 交互+POI）/ BattleScene（回合战斗：技能/药剂子菜单、Buff 行、浮字池）
│  │  ├─ ui/               # Hud、Bars、Shop（买+卖）、PanelChrome（面板骨架，统一 setScrollFactor(0)）、CharacterPanel/InventoryPanel/SkillsPanel/JoinSectPanel、NewGame；Dialogue 待 M3+
│  │  ├─ systems/          # clock.ts（本地时钟推算）/ store.ts（玩家+怪物状态单例，10s 轮询）；日程推算等待 M3+
│  │  └─ api/              # server HTTP 封装
│  ├─ tools/gen_placeholder_assets.mjs   # 占位 PNG 生成脚本（见「已知坑」）
│  └─ public/assets/      # 生成产物，gitignore
├─ server/
│  ├─ app/
│  │  ├─ main.py config.py db.py llm.py  # llm.py 目前只有 Ollama tags 健康检查，对话/信号量/llm_log 待 M3
│  │  ├─ game/             # M1/M2 已建：data（物品/怪物/商店表+种子）skills（技能/门派目录+拜师）player（mods 聚合+allocate）inventory（含装备/卖店）mobs battle（含技能结算）shop clock
│  │  └─ agent/            # 待 M3+：memory(RAG·Chroma), npc, dialogue, reflection, gossip, scheduler
│  ├─ data/               # xia.db + chroma/（向量库），均 gitignore
│  └─ requirements.txt
└─ README.md
```

## 实施顺序

按 DESIGN.md §13 里程碑推进：M0 脚手架、M1 战斗循环、M2 成长系统已完成（属性分配/装备/技能/拜师，无头浏览器全流程实测零错误）→ M3 NPC 记忆+RAG+对话 → M4 反思/八卦/主动搭话。每个里程碑交付可玩 build；调试观察主要靠 `GET /api/debug/npc/{id}`（好感、记忆流、反思、关系摘要）。

**里程碑的执行前必读**：M2 有专属拆解文档 [`docs/M2-PLAN.md`](docs/M2-PLAN.md)——数值、API 形状、文件落点、schema 迁移、验证步骤全部内联，关键决策（effect_json 约定、skill_cd/buffs 存 state_json、拜师 M2 用等级替位好感）已在文档里拍板，**实施 M2 时以它为准、不要重新翻 DESIGN 与 M1 代码做考古**；仅当实现中发现设计需要变更时才回改 DESIGN.md 并同步该计划文档。后续里程碑建议沿用 `docs/M?-PLAN.md` 模式。

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
