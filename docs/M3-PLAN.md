# M3 NPC 记忆 + RAG + 对话 — 实施拆解

> 本文档是 M3 的直接执行清单：数值、规则、接口形状、文件落点、schema 依据、验证步骤全部内联，实施时**不需要回查 DESIGN.md**（但任何设计变更仍须同步回 DESIGN.md）。沿用 `docs/M2-PLAN.md` 的范式。
> 动手前必读根目录 `AGENTS.md` 的「已知坑」——尤其：ChromDB 三坑（embedding_function=None / hnsw:space=cosine / 关遥测）、qwen3 禁思考、4B JSON 不稳走兜底、SQLite 单写队列先行落库再 Chroma upsert、NO_PROXY。

- **验收标准（DESIGN §13）：连续聊天，NPC 记得你。（M3 = 记忆流 · RAG · 自由对话 · 好感度 · 对话式拜师/切磋/送礼）**
- **交付物**：7 NPC 花名册落库；记忆双库（SQLite 记忆 SSOT + Chroma 派生向量索引）；RAG 检索重排；`/api/npc/{id}/talk` 自由对话；好感度规则 + LLM 判定与 clamp；对话式拜师/切磋/送礼；`/api/debug/npc/{id}` 实验观察；Godot 对话 UI。
- **边界**：反思、关系摘要、八卦、主动搭话均属 **M4**，本里程碑**不做**（下表"触发/展示窗"留接口即可）。
- 本里程碑**无 schema 变更**：`npc / npc_state / memory / relationship / conversation / llm_log` 六张表在 `db.py` §8 已全量建好，M3 只做**数据种子 + 逻辑接线**，无需删库迁移。

---

## 0. 现状复用清单（M0/M1/M2 已就位，不要重做）

| 已有 | 位置 | M3 怎么用 |
|---|---|---|
| `npc/npc_state/memory/relationship/conversation/llm_log` 表 | `db.py` §8 | 全量 schema 就绪，直接写入；M3 零迁移 |
| `Database` 单写队列（asyncio.Lock + WAL） | `db.py` | 记忆写入沿用 `db.execute`，**先 SQLite 落库分配 id → 同锁内 Chroma upsert** |
| `GameClock.now()` 游戏秒、`snapshot()` | `game/clock.py` | 记忆 `ts_game`、`poi` 取当前时辰公式、recency 衰减全用游戏时间 |
| 全量静态数据 seed 模式（`INSERT OR IGNORE` + 内存 key → 行字典） | `data.py`/`skills.py` | 花名册 seed 照抄同一模式，常量集中到新 `agent/npc.py` |
| Ollama 客户端骨架 `tags()` + httpx 基址 | `app/llm.py` | 只补 chat/embed/信号量/llm_log，骨架已就位 |
| 面板 UI 骨架（`_new_modal/_action/mutate/busy` 门控、toast、`world.nearby()`） | `scripts/main.gd` | 对话框复用此骨架；`nearby()` 已返回 `kind:"npc"`，只差一个分支路由 |
| 人物录静态占位（7 名、地点、`"对话与记忆 · 待后端开放"`） | `main.gd open_people()` | 改为读 `/api/game/state` 里的 npcs 存活好感数据 |
| 战斗进入/结算（mob）、`active_snapshot`/断线重入 | `game/battle.py` | 扩展 `target_kind="npc"` 走切磋；切磋规则单独分支（HP<30% 即止） |
| 商店购买、物品背包、`inventory.consume/add` | `shop.py`/`inventory.py` | 大额消费≥100两→店主记忆；送礼用同一个 consume |

---

## 1. 需求拆解（六块）

### 1.1 LLM 客户端扩展（`app/llm.py`）

现状只有 `tags()` 健康检查。M3 补齐 M0 注释里预留的能力：

- **全局限流**：`asyncio.Semaphore(settings.llm_concurrency)`（默认 2）。M3 只有对话用 chat，优先级暂不适用，但锁保留给 M4。
- `async def chat(purpose, messages, *, json_format=False, temperature=0.7) -> ChatResult`：
  - POST `/api/chat` body `{model, messages, stream:false, format:("json" if json_format), think:false, options:{temperature}}`；
  - **禁思考**：请求体带 `"think": false`；同时给 system 附加 `\n/no_think`（旧版 Ollama 退化路径，AGENTS.md 已知坑）。两个兜底都上，不用判断版本；
  - 返回 `ChatResult { text, in_tokens, out_tokens }`（从响应的 `prompt_eval_count`/`eval_count` 读）；
  - **llm_log**：每条调用写 `llm_log(purpose, model, in_tokens, out_tokens, ms, ok)` —— 封装成一个 `_log(...)` 内部方法，ok 用 try/except 包裹保证失败也记录。
- `async def embed(texts: list[str]) -> list[list[float]]`：
  - POST `/api/embed` `{model, input: texts}` → 返回 `data[i]["embedding"]`；
  - **L2 归一化** `v / (sum(x*x)**0.5)` 后返回（DESIGN §8）。
- `async def close()`：补设信号量无需释放，保留既有 httpx 关闭。
- token 兜底：`eval_count` 可能缺失，用 `len(text)` 粗估，不因取不到而崩。

> 注意：`/api/chat` 的 `format:"json"` 只保证输出更接近 JSON，**不保证合法**——兜底链路（§1.7）是必需且不可省略的。

### 1.2 花名册 seed（新 `agent/npc.py`）

常量表 `NPC_ROSTER`，key = 键盘 slug，字段对齐 `npc` + `npc_state` 表 + 客户端 `main.gd open_people` 的地点文字：

| slug | 姓名 | sect_id | role | 性格 | 风格 | 口头禅 | 初始好感 | 据点 home/work |
|---|---|---|---|---|---|---|---|---|
| gu_changfeng | 顾长风 | jianyang | master | 傲·严苛·重悟性 | 冷硬简短 | "且试锋芒。" | 25 | 剑阳道馆／道馆后屋 |
| tie_wushuang | 铁无双 | xuantie | master | 豪爽·嗜酒·重品性 | 大嗓门，爱称"小子" | "好小子！" | 25 | 玄铁堂／客栈 |
| su_wanqing | 苏婉清 | yanyu | master | 高深·戏谑·重机敏 | 半文半白，带调侃 | "趣味。" | 30 | 烟雨小筑／小筑内室 |
| chen_bansong | 陈半松 | null | clinic | 慈祥·啰嗦·爱忆当年 | 缓慢絮叨 | "想当年啊……" | 30 | 药铺／铺后家 |
| wang_dazhu | 王大柱 | null | smith | 爱财·爱打听·消息贩子 | 热络市侩 | "消息灵通着呢。" | 25 | 铁匠铺／铺后家 |
| liu_xiaodie | 柳小蝶 | null | inn | 活泼好奇·爱看热闹 | 清脆快语 | "快说说！" | 40 | 客栈／客栈 |
| zhang_mantou | 张馒头 | null | temple | 癫三倒四·偶尔惊人 | 疯话押韵 | "馒头歌，歌馒头。" | 50 | 镇口水井／破庙 |

每项再带：
- `persona`: 供 prompt 的单段文言白话人设串；
- `combat`: `{level, hp, atk, def, spd}` —— 掌门约 Lv5~6、店家 Lv3、柳小蝶 Lv2、张馒头 Lv4（切磋对手，数值初设后实测调平）。

`async def seed_and_load(db)`：先 `INSERT OR IGNORE INTO npc(name, sect_id, role, persona_json, speech_style, init_affinity, home_poi, work_poi, waypoints_json)`，再对每行 **inserr npc_state**（npc_id=刚插入 id，affinity=init_affinity，其余默认）。内存建 `_npc_by_slug`、`_npc_row_by_id`。

**ID 约定**：API 统一用 `npc.id`（int 主键），客户端经 `/api/game/state` 的 npcs 数组拿到 `{id, slug, name, role, affinity}` 后用数字 id 调交互接口——**不要**在 URL 里传中文名或 slug，避免两端不一致。

### 1.3 记忆写入（`agent/memory.py` 同步核心 + `rag.py` 检索）

**Chroma 单例**（建在 main lifespan 里，随生命周期 close）：
```python
import chromadb
from chromadb.config import Settings
client = chromadb.PersistentClient(path=settings.chroma_dir_abs,
    settings=Settings(anonymized_telemetry=False))
coll = client.get_or_create_collection(
    "memories", embedding_function=None, metadata={"hnsw:space": "cosine"})
```
（AGENTS.md 三坑全命中：恒显式传 embeddings、cosine 空间、关遥测；`server/data/chroma` gitignore 已排除。）

**写入链路**（`async def add_memory(db, coll, npc_id, clock, *, content, kind, participants, poi, importance)`）：
1. `ts_game = clock.now()`；
2. `id = await db.execute(INSERT INTO memory(npc_id, ts_game, kind, content, participants_json, poi, importance, created_at_real))`（created_at_real=time.time()；participants_json 存玩家名或其他 NPC 名列表）；
3. `kind`：M3 只写 `event`（reflection/gossip 留给 M4，但同样会落到本函数后备分支——**所有 memory 都入库向量**，SQLite 与 Chroma 一致，无特例）；
4. `vec = (await llm.embed([content]))[0]`（L2 归一化）→ `coll.add(ids=[str(id)], embeddings=[vec], documents=[content], metadatas=[{npc_id:str(npc_id), kind, importance, ts_game, poi}])`。
5. 同步累加 `npc_state.mem_since_reflection += 1`（M4 反思触发用，M3 先维护列值，不消费）。

> **写入锁**：Chroma 的 add 必须在与 SQLite id 生成同一临界区内，避免 id/embedding 错位。实现用 `db.execute` 结束后立刻 embed + coll.add（embed 走服务器，不进 DB 锁；但同一 NPC 的两条并发对话可能交叉）——**简单可靠做法**：在 `add_memory` 内加一个模块级 `asyncio.Lock`，包住「落库 + embed + chroma add」全程，保证按序。串行代价可接受（对话低并发）。

**事件入忆生成**（`async def remember(db, ..., npc_id, context)`）：区分交互驱动与规则驱动——
- **对话/礼物/切磋/拜师**：由各端点拿到 LLM 或规则输出后，调 `add_memory` 写入 event；
- **大额消费 ≥100 两**：在 `shop.buy` 成功后，若 `cost>=100`，给该店主 NPC 写一条模板 event（`[X时·店名] 秦侠客一掷X两买下X，手头真宽裕。` importance=3）；
- 热情控制：**记忆控制噪声**（AGENTS.md）——M3 只监控制这与购买两类显著事件，不扩大范围（野外打狼不写镇内 NPC 记忆）。

### 1.4 RAG 检索重排（`agent/rag.py`）

```python
async def retrieve(db, coll, npc_id, query, clock, llm, settings) -> list[dict]:
    qvec = (await llm.embed([query]))[0]
    res = coll.query(query_embeddings=[qvec], n_results=20,
                     where={"npc_id": str(npc_id)})   # 只取向量相似度 top20
    # 应用侧三因子综合分，重排取 top6（RAG_TOP_K）
    def score(m):   # m: {comment? no—document, distance, metadata}
        sim = 1 - m["distance"]                          # cosine distance → similarity
        age_days = (clock.now() - float(m["metadata"]["ts_game"])) / 86400
        rec = math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE)
        imp = float(m["metadata"]["importance"]) / 10
        return W_REL*sim + W_REC*rec + W_IMP*imp
    return sorted(chroma_rows, key=score, reverse=True)[:RAG_TOP_K]
```
- chroma 返回体：`coll.query` → `ids/embeddings/documents/metadatas/distances` 平行数组，用索引对齐取 `(document, distance, metadata)`；
- 半衰期 3 游戏日、权重 w_rel=1.0/w_rec=0.8/w_imp=0.5 走 settings（§14 已全量落）。
- **只算向量近似，recency/importance 重排必须在应用侧**（DESIGN §6.4）。Chroma 不做排序。

**最近对话平铺**：`async def recent_turns(db, npc_id, n=3)` 从 `conversation` 表取最近 n 行拉平 turns_json（M3 对话一行存一轮），拼进 prompt。

### 1.5 好感度系统（`agent/affinity.py` + `skills.py` rank 接线）

- 范围 0~100；来源 `npc_state.affinity`。
- **规则增益（不调 LLM，优先）**：
  - 切磋：`+3 ~ +5`（表现好取高，M3 固定按结果给 表现/惜败 档位）；
  - 送礼：按档 `+5/+8/+12`；**同游戏日第二次起减半**（维护 `gifts_on_day`：用 kv 或 memory 查询当日 gift event 计数）。
  - greet（问候入会 DDR）。
- **对话增益**：LLM 判定 `affinity_delta ∈ [-2, +3]`，应用 `clamp(0, 100, old + delta)`——**夹住 ≤ ±3 由模型输出约束 + 服务端再 clamp**（DESIGN §6.5 防漂移）。
- **分段效果**：
  - 0~29 冷淡：对话可被 LLM 判 `end=true` 提前结束；
  - 30~59 客气：正常对话；
  - 60~69 热络：**掌门拜师条件达成（需 ≥60 且 lv≥3）**；王大柱 95 折；
  - 70~84 亲近：王大柱 9 折（给 90 折仅显示，主动搭话是 M4）；
  - 85~100 挚交：（掌门偶赠药剂 → M4）。
- **王大柱折扣**：`shop.info("smith")` 读玩家与该店主的 npc 好感给价格系数（95→90）。M3 只做王大柱对应 `wang_dazhu`。

**rank 与位阶解锁（M2 遗留，M3 补齐）**：
- `skills.py` 把 `MAX_RANK=1` 改为 `def rank_of(affinity): 60→1, 75→2, 85→3`；
- `tier_unlocked(db, p, tier)` 改为：读玩家拜入门派 master（顾/铁/苏）的 `npc_state.affinity` → `rank_of(affinity) >= tier`；
- 玩家面板 `sect.rank` 现算（不再是恒"入门弟子"）；`player.api_dict` 里 `skills_view[i]["locked"]` 改按此判定。

### 1.6 对话（`agent/dialogue.py` → `POST /api/npc/{id}/talk`）

```
请求  {message}
流程（async，先把记忆检索与 prompt 组装并行做——DESIGN §7.2）：
 1. p = player; n = npc; ns = npc_state
 2. 并行 fetch：retrieve(db,coll,nid,message) 、 recent_turns(nid, 3) 、 relationship(nid,'player',player.id)（M4 空）
 3. 组装 prompt（见下）→ llm.chat(purpose='dialogue', json_format=True)
 4. 校验 JSON {reply,event,affinity_delta,importance,end}
      → 解析失败：重试 1 次 (temperature 0.3)
      → 再失败：模板兜底 reply=f"{n.姓名}沉吟片刻，不置可否。", event=message, delta=0, importance=3, end=False（永不阻塞）
 5. 应用：affinity clamp；add_memory(...kind='event', importance, participants=[p.name], poi=当前位置)；npc_state.mem_since_reflection++
 6. 可选：relationship 维护（M4）；ns.paused_until 置对话冻结（M3 客户端无日程，仅登记）
 7. 写 conversation(npc_id, ts_game, turns_json=[{user, npc}], affinity_delta_total=delta)
 8. 返回 {reply, affinity, end}
```

**prompt 结构**（DESIGN §6.6 原文，组装成 messages：system + user）：
```
你在扮演武侠游戏NPC：{persona 姓名·身份·性格·风格·口头禅}
你与玩家({玩家名}，{门派或无})好感度 {n}/100，关系印象：{relationship or None}
你对玩家的相关记忆(按重要度倒序)：{top6 记忆，各带时间/地点/important}
最近对话：{最近3轮}
当前：{干支时辰}·{地点}。玩家说："{message}"
要求：以角色口吻回复(≤80字,古风白话)；同时输出事件摘要(第三人称,你的视角)、
好感变化(-2~+3整数)、记忆重要度(1~10整数)、是否想结束对话。
输出JSON: {"reply":"","event":"","affinity_delta":0,"importance":5,"end":false}
```
- 干支时辰：`["子丑寅卯辰巳午未申酉戌亥"][(hour+1)//2 % 12] + "时"`（与客户端 `main.gd` 同一公式）。
- `ast.literal_eval` 或 `json.loads` 解析；若返回带 ```` ```json ```` 围栏，先剥离再解析。

### 1.7 对话式拜师 / 切磋 / 送礼（各归 `agent/npc.py`）

**`POST /api/npc/{id}/apprentice`**：
- 前置校验：该 NPC 是掌门（role=master）且与玩家欲拜门派一致；玩家无门派；`affinity≥60` 且 `lv≥3`；一生只入一派（已有 sect → 409）。
- 通过→ 走 `skills.join_sect` 入派 + 写玩家记忆 + 该掌门好感 +3（拜师礼成）；返回 `{reply 仪式台词, joined:true, sect:{name}, player}`。
- 未达标→ `409`，附原因（好战度不足 / 等级不足），客户端显示该 NPC 婉拒台词。

**`POST /api/npc/{id}/challenge`**：
- 门派掌门需 `affinity≥40`（DESIGN §5.6）才应战；非掌门一律可切磋（店家也认）。
- → 调 `battle.start_spar(db, p, npc)`（§1.8）开启切磋战斗，返回同 `/api/battle/start` 结构。

**`POST /api/npc/{id}/gift {item_id}`**：
- `inventory.consume(db, p.id, item_id)` 扣一件；
- 按 item.price 分档：`price < 50 → +5；50 ≤ price < 150 → +8；price ≥ 150 → +12`（仿 §6.5「5/10/15 两档」）；
- 同日第 2 次送礼起该档减半（查当日该 NPC 的 gift event 条数）；
- 写 NPC event 记忆 + 给玩家 toast 反馈（好感变化）。若玩家与礼物类型匹配（如给铁无双送酒类/给掌门送剑）→ M4 可加，M3 不判。
- 空背包 / 宝物不足 → 400。

### 1.8 切磋战斗（`game/battle.py` 扩展）

复用现有 `target_id`，新增 `spar` 语义字段：
- `start` 加分支：`target_type == "npc"` → 校验 `battle` 表 `target_kind='npc'`、`target_id=npc.id`；敌方快照用 `npc.combat`；`state["npc_id"]=npc.id`、`state["spar"]=True`。
- **切磋规则（spar=True 时）**：任一方 HP<30% 即结束回合并由 `_finalize` 判定：
  - 双方 `exp ×0.3`（切磋无银两、无掉落）；写入该 NPC 记忆（importance=6，`[X时·练武场] 与{玩家}切磋，他{硬接我三刀/被我两招制住}。`）；
  - 好感按表现：胜/占优 +5、惜败 +3（规则值）；
  - 无死亡：不触发复活/掉钱/推离。
- `apply_action` 在 spar 分支：HP<30% 检查放回合末；`_finalize` 战利品逻辑按 spar 跳过掉落/银两/修炼货币，改发切磋经验与失生活记忆。
- `start_battle` 客户端验：result win/lose/draw（切磋以"分胜负"记，非死亡）。
- 断线重入：spar 状态同样走 `active_snapshot`（state_json 已含 spar/敌方快照），不新增表结构。

---

## 2. 数据层结论与常量（内联，勿再查 DESIGN）

- **无 schema 迁移**。三处运行期数据：
  - `npc_state.affinity` 玩家对该 NPC 好感（int 0~100）；
  - `memory / chroma collections` 记忆双库；
  - `conversation` 最近对话（供 recent_turns）。
- 常量进 `agent/npc.py` / `agent/affinity.py`：
  - `GIFT_TIERS = [(50, 5), (150, 8), (inf, 12)]`（price 档 → delta）；
  - `APPRENTICE_AFFINITY = 60`、`CHALLENGE_AFFINITY = 40`；
  - `REFLECT_HOTSPOT...` 无需（M4）。
  - 掌门映射：`MASTER_BY_SECT = {"jianyang":"gu_changfeng", "xuantie":"tie_wushuang", "yanyu":"su_wanqing"}`。
- **配置**：全部走 `settings.*`；`server/.env` 模型 tag 已就位（`qwen3:4b-instruct-2507-q4_K_M` / `qwen3-embedding:0.6b-q8_0`）。**代码里不写死模型名。**

---

## 3. API 新增（同步回 DESIGN.md §9）

统一：错误用 `HTTPException(400/409, 中文)`。`talk/gift/apprentice` 成功响应附最新 `player` 与消息相关字段；`challenge` 复用 battle 响应结构。

| Method & Path | Body | 说明 |
|---|---|---|
| GET `/api/game/state`（扩展） | — | 响应加 `npcs: [{id, slug, name, role, affinity, home_poi, work_poi}]`，供人物录/对话热栏 |
| POST `/api/npc/{id}/talk` | `{message}` | → `{reply, affinity, end, player}` |
| POST `/api/npc/{id}/gift` | `{item_id}` | → `{reply, affinity, player}` |
| POST `/api/npc/{id}/challenge` | `pos?` | → battle 结构（切磋） |
| POST `/api/npc/{id}/apprentice` | — | → `{reply, joined, sect?, player}`；未达标 409 |
| GET `/api/debug/npc/{id}` | — | 实验观察：亲和 8、最近 50 条记忆、关系摘要(空)、flags |
| GET `/api/npcs/state`（占位） | — | M3 返回 npcs 列表 + pause 标记（日程/主动搭话 M4） |

`/api/health` 顺带把 `npc` 种子是否就绪列入（`db.fetch_one("SELECT COUNT(*) FROM npc")`）。

---

## 4. 界面（Godot 客户端）落点

`xia-ai-town/scripts/main.gd`（沿用现有单文件面板模式，不新拆文件以匹配项目惯例）：

1. **`interact()` 加分支**：`nearby()` 返回 `kind:"npc"` 时 → `open_dialogue(npc_id, npc_name)`（当前 `_` 全落 `open_people`，需要先接住 NPC）。
2. **`open_dialogue(id, name)`**：新模态 `_new_modal("dialog", 立绘位/名 + 身份行, "dialog")`：
   - 对话气泡区（`UI.paragraph` 滚动，历句累加）、**等待指示**（LLM 2~5s：发请求前置 `modal_status.text = "NPC 沉吟许久……"`，沿用 `busy` 门控）；
   - 输入框 `LineEdit` + 发送；
   - 快捷键行（`_action`）：**[打招呼]**（发 `"（打招呼）"`）**[打听消息]**（发 `"（打听）"`）**[门派拜师]**（调 `/apprentice`，成功 `notify` + `open_skills`）**[切磋]**（`/challenge` → `open_battle`）**[送礼]**（弹背包选一件调 `/gift`）；
   - 顶部身份行显示好感（心形档位图标仅示意不显数值——沿用现有纸墨风格手绘）。
3. **`send_talk(message)`**：走 `api.request` 而非通用 `mutate`（响应无 `player` 时才单独渲染 reply；talk 响应其实带 player，可直接复用 `mutate` 语义再展示 reply）。关键：**请求期间按钮禁用 + 等待文本**。
4. **`open_people()` 改读数据**：`/api/game/state` 的 `npcs`（医院后 `refresh()` 已冗余，但人物录首开时本地拉一次并存 `people_npcs` 供热栏）。修补占位文案"待后端开放"为现实感述。
5. **`open_inventory()` 复用**：送礼背包在对话内再开一层（嵌套模态），沿用 `_remove_modal`/`modal_kind` 栈约定（Es 关闭当前层）。
6. **超时**：`api.gd request` 默认 12s 对 talk（可到 20s）偏紧——给 `request` 加可选 `timeout` 参数，talk 传 `20.0`。
7. **目标休息瑕疵**：`town_world.gd` `nearby()` 已按 NPC 距离返回 `kind:"npc"`，无需改碰撞；NPC 位置 M3 保持静态（日程/主动搭话 = M4）。

---

## 5. 文件落点总表

**Server（新 → 老）**：

| 文件 | 改动 |
|---|---|
| `app/llm.py` | 信号量、`chat`（含 think:false + /no_think + json format + llm_log）、`embed`（L2 归一）。定型 `ChatResult` |
| `app/agent/__init__.py` | 新包 |
| `app/agent/npc.py`（新） | NPC_ROSTER、seed_and_load、gift / apprentice / challenge 端点逻辑（读 DB + 调 memory） |
| `app/agent/memory.py`（新） | Chroma 单例（get_or_create history）、`add_memory`（写锁包 落库→embed→chroma）、`remember` 事件生成、杂物 |
| `app/agent/rag.py`（新） | `retrieve`（chroma query top20 + 应用侧三因子重排 top6）、`recent_turns` |
| `app/agent/affinity.py`（新） | `clamp/apply_delta`、礼物分档、同日计数、分段效果、`rank_of` |
| `app/game/battle.py` | `start` 加 npc/spar 分支；`apply_action` 切磋规则（HP<30% 即止）；`_finalize` 切磋经验+记忆+好感；`active_snapshot` 兼容 |
| `app/game/skills.py` | `MAX_RANK` → `rank_of`；`tier_unlocked` 改读拜师掌门好感 |
| `app/game/player.py` | `api_dict`：sect.rank 现算；`skills_view.locked` 用新解锁判定 |
| `app/game/shop.py` | `info("smith")` 好感折扣；`buy` 成功后大额消费≥100→店主记忆 |
| `app/main.py` | lifespan：seed npc + 建 Chroma 单例 + close；接线 §3 路由；game/state 加 npcs；health 查 npc 种子 |

**Godot 客户端（`xia-ai-town/scripts/`）**：`main.gd`（对话 UI + 分支 + 送礼 + 切磋 + 拜师 + 人物录接真实数据）、`api.gd`（timeout 参数）。

---

## 6. 验证计划

**前置**：启动前 `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`；`server/.venv/bin/uvicorn app.main:app --reload --port 8000`；Ollama 已 pull 两个模型（`ollama pull qwen3:4b-instruct-2507-q4_K_M qwen3-embedding:0.6b-q8_0`）。

**A. curl 冒烟（真后端 + Ollama）**：
1. `POST /api/game/new {"name":"记忆侠"}`；
2. `GET /api/game/state` → 响应含 `npcs` 7 条，各带 id/slug/affinity；
3. `POST /api/npc/{id}/talk {"message":"你叫什么名字？"}`（id=顾长风的数字 id）→ 200，`reply` 非空、`affinity` 变化、`end` 存在；`sqlite3` 查 `memory` 有 1 行、`conversation` 1 行；
4. `sqlite3` 查 `npc_state.affinity` 已按 delta 更新且被 clamp；
5. 查 Chroma：`python -c` 打开集合 `memories` 有 ≥1 条 metadata.npc_id 匹配；
6. `POST /api/debug/npc/{id}` → 最近 50 条含刚才 event；
7. `POST /api/npc/{id}/gift {"item_id":<铁剑铁剑80两>}` → 背包 -1、affinity +8（80 属 50≤price<150 档）；
8. 拜师前强制 `UPDATE npc_state SET affinity=70 WHERE npc_id=<剑阳掌门>`；`POST /api/npc/{id}/apprentice` → joined:true、sect=剑阳派、player_skill 有烈阳剑诀+赤阳斩；
9. `POST /api/npc/{id}/challenge`（该掌门 affinity≥40）→ battle 结构；连续 attack 到 HP<30% → 切磋结束，`memory` 有切磋 event(importance=6)、affinity +规则值、无掉落/银两；
10. `POST /api/shop/smith/buy 铁剑` 花 ≥100 两 → 查 `wang_dazhu` 记忆出现大额消费 event；
11. **兜底**：临时停 Ollama（或把 model 改错）→ talk 仍返回模板 reply、游戏不崩、`llm_log` 记 ok=0。

**B. Godot 无头 E2E（复用 `smoke_test.py`，模式里可关 Ollama 把 talk 走模板兜底保证确定）**：
- 新档 → 走到顾长风口 → 按 E → 对话框出现 → 输入"你好"回车 → 出现回复文本 + 等待指示 → 点[问候]→ 人物录显示好感 → 送礼（选一件）→ 切磋（若好感已达）→ 全程零 SCRIPT ERROR、截图核对（纸墨对话面板 + 好感档位图标）。
- **已知坑**：WSL 无中文字体 → `FONTCONFIG_FILE` 指含 NotoSerifSC 的临时目录（AGENTS.md）；按键按压 ≥150ms；Modal 出现时机在 `refresh` 后。
- **跑法**：`server/.venv/bin/python xia-ai-town/tools/smoke_test.py`。

**回归**：商店买卖、装备、技能升级、正常打狼战斗（非切磋）不回归；`/api/battle/start` 的 mob 路径不受 spar 分支影响。

---

## 7. 明确不做（防 scope 蔓延）

- **反思 / 关系摘要 / 八卦 / 主动搭话** —— 全部 M4。M3 只落表不实现；`relationship` 表留空、`mem_since_reflection` 只累加不消费。
- NPC 真实日程移动与位置轮询 —— M4；M3 保持 `town_world.gd` 静态标记。
- 好感 -3 以下惩罚细则、王大柱折扣的 UI 介入（仅 shop.info 生效）。
- 礼物类型定制（给铁无双送酒才 + 更多）。
- 存档多开、音效等 V1 明确不做项。

---

## 8. Git 提交建议（照 AGENTS.md：一个任务一 commit）

建议按如下原子任务推进，每完成一块即提交，正文三段式：
1. `feat: LLM 客户端补齐 chat/embed/信号量/llm_log`
2. `feat: NPC 花名册种子 + 记忆双库写入链路 + RAG 检索重排`
3. `feat: 好感度系统 + 对话接口 /api/npc/{id}/talk`
4. `feat: 对话式拜师/切磋/送礼 + 切磋战斗 spar 分支`
5. `feat: Godot 对话 UI 接入（对话框/送礼/切磋/拜师/人物录）`
6. `docs: DESIGN.md 同步 M3 新路由与切磋规则`

DESIGN.md 需同步点：§9 新增 4 个 NPC 路由 + game/state 的 npcs 字段；§4/§6 若是择将 rank 现算与切磋 HP<30% 判定已在 §5.6 有；若实现中桥设计与 DESIGN 冲突，回改 DESIGN 并同步本文档。