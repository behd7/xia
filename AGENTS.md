# AGENTS.md

本文件是每个新会话的行动指南。项目尚未开始编码（当前仅有设计文档），实现工作请从里程碑 M0 开始。

## 项目是什么

《侠》：二维像素武侠 RPG + 生成式 NPC 实验（仿斯坦福小镇）。游戏本体（门派/装备/药剂/属性/技能/回合制战斗）能简则简，一切为 NPC 智能化实验服务。无主线任务。

**权威设计文档：`docs/DESIGN.md`**。实现任何功能前先读对应章节；数值表、NPC 花名册、API 路由均以该文档为准，改动需同步更新文档。

## 已锁定的技术决策（不要重新讨论或擅自更换）

| 决策点 | 选择 |
|---|---|
| 客户端 | Phaser 3 + TypeScript + Vite（浏览器运行） |
| 后端 | Python FastAPI 单进程，同时承载游戏逻辑与 NPC 智能体 |
| 存储 | SQLite 单文件（WAL 模式），游戏状态+记忆+向量同库；**V1 不引入外部向量库**，embedding 存 float32 BLOB，numpy 暴力余弦检索 |
| 对话模型 | 本地 Ollama `qwen3:4b-instruct`（4bit 量化） |
| 向量模型 | 本地 Ollama `qwen3-embedding:0.6b`（8bit 量化，1024 维，入库前 L2 归一化） |
| 美术 | 第一版全部占位图（色块+汉字标签脚本生成），后期替换 |
| 玩家入门 | 无初始门派，靠与掌门 NPC 好感度达标后对话拜师；一生只入一派 |
| 战斗 | 1v1 回合制，敌方 AI 为规则型（**不调 LLM**） |

## 实现时必须遵守的架构约束

这些是设计阶段定下的原则，改动前先想清楚为什么存在：

1. **服务器权威**：属性、背包、战斗、好感度、记忆只在服务器结算；客户端只做渲染与输入。玩家移动本地推演，交互时上报位置。
2. **NPC 位置零同步**：NPC 位置 = 日程表的确定性函数（按游戏时钟推算）。客户端与服务器各自用同一份数据推算，不做位置轮询。伪随机踱步以 NPC id + 游戏日为种子，保证两端可复现。
3. **战斗为同步请求-响应**：`POST /api/battle/action` 一次返回双方结算结果。不用 WebSocket。
4. **LLM 调用永不阻塞玩法**：所有 LLM 结构化输出必须走 校验 → 重试一次(temperature 0.3) → 模板兜底 的链路；JSON 解析失败时用模板回复，游戏可继续。
5. **好感度防漂移**：LLM 单回合好感变化 clamp ±3；送礼/切磋等规则类增益以规则为准。
6. **SQLite 单写队列**：对话写入与后台反思/八卦任务并发写库，用 asyncio.Lock 串行化 + WAL，不要改成多连接并行写。
7. **LLM 全局信号量**（默认 2）统一限流，优先级：对话 > 搭话 > 关系摘要 > 反思 > 八卦。所有调用写 `llm_log` 表供实验分析。

## 环境与配置

依赖本地 Ollama 服务（默认 `http://localhost:11434`），需提前 pull 两个模型。模型 tag 走 `.env`，**不要在代码里写死模型名**：

```env
OLLAMA_HOST=http://localhost:11434
CHAT_MODEL=qwen3:4b-instruct        # 实际 tag 可能形如 qwen3:4b-instruct-q4_K_M
EMBED_MODEL=qwen3-embedding:0.6b
LLM_TIMEOUT=20
LLM_CONCURRENCY=2
```

可调参数集中在 server 端 config（时间流速、好感阈值 70、反思/八卦周期、RAG 权重与 top-k 等），清单见 DESIGN.md §14。

## 已知坑（改动前必须知道）

- **qwen3 思考模式**：对话/结构化输出必须禁用思考——请求体加 `"think": false`；旧版 Ollama 不支持该字段时退化为 system 附加 `/no_think`。
- **4B 模型 JSON 不稳**：靠 `/api/chat` + `format: "json"` 提高成功率，但兜底链路（见上）是必需品，不是可选项。
- **本地推理延迟 2~5s**：对话请求是同步的，前端必须显示等待指示；记忆检索与 prompt 组装并行做。
- **游戏时钟**：1 现实秒 = 1 游戏分（可配），一个游戏日 ≈ 24 现实分钟。记忆时间戳、NPC 日程、recency 衰减（半衰期 3 游戏日）全部基于游戏时间，不要混用现实时间。
- **记忆控制噪声**：同场目击入忆仅限战斗与大额消费（≥100 两）两类显著事件，不要扩大范围。

## 计划目录结构

```
Xia/
├─ docs/DESIGN.md          # 设计 SSOT
├─ client/                 # Vite + Phaser 3 + TS（scenes / ui / systems / api）
├─ server/
│  ├─ app/
│  │  ├─ main.py config.py db.py
│  │  ├─ game/             # player, inventory, shop, battle(规则AI), save
│  │  ├─ agent/            # memory(RAG), npc, dialogue, reflection, gossip, scheduler
│  │  └─ llm.py            # ollama 客户端 + 信号量 + llm_log
│  └─ data/xia.db          # gitignore
└─ README.md
```

## 实施顺序

按 DESIGN.md §13 里程碑推进：M0 脚手架（跑通+Ollama 健康检查）→ M1 战斗循环 → M2 成长/拜师 → M3 NPC 记忆+RAG+对话 → M4 反思/八卦/主动搭话。每个里程碑交付可玩 build；调试观察主要靠 `GET /api/debug/npc/{id}`（好感、记忆流、反思、关系摘要）。

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
