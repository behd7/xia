# 记忆架构 v2（人物账本）执行计划

> 后续协议变更：2026-11-12，NPC 对话已从 JSON 字段改成普通文本 + 原生工具，详见 [NPC-TOOLS.md](NPC-TOOLS.md)。本文 §6/§14 的 JSON 与“不用原生 tools”描述是原实施记录，不再是现行输出协议；账本、动作执行和客户端契约不变。

> 状态：**已实施（2026-11-01，P0–P4 四个 commit）**。本文档保留为执行记录；`docs/DESIGN.md` §2/§6.3/§6.4/§6.6/§6.14/§8/§13/§14/§15 与 `AGENTS.md` #11/#12 已同步为现行实现。
>
> 本文件取代 [`MEMORY-PLAN.md`](MEMORY-PLAN.md) 中关于「窗口删除」「中期纯内存」「长期完全隔离（不写向量）」三处描述；v1 的其余内容仍是历史记录。
>
> 设计来源：`../MaiBot` 的 `src/maisaka/memory/*`（中期回想、启发式长期记忆、人物画像）与 `src/A_memorix/core/retrieval/*`（双路召回、动态阈值、PPR）。§14 记录了「照搬 / 改造 / 不搬」的逐项对照与理由。

## 0. 一句话

**把记忆从「消息窗口」改成「每个人物一本账」：每个 NPC 一个绝对轮号 `turn_no`，短期/中期/长期只是同一本账的三种读取视图，不再各自维护一套缓冲与阈值；工具调用（行动）是账本上的事件行，不是对话的附注。**

三条派生不变量：

1. **只有一个时钟**：`npc_state.turn_no` 单调递增，所有阈值（窗口 20 / 中期每 10 条一档 / 长期 36）相对它算，不再出现「窗口条数」与「累计轮数」混用。
2. **只写一处、只在开新局时清一处派生摘要**：所有轮与中期条目都在 `conversation`；长期在 `longterm_memory`。Chroma 两个集合都是可从 SQLite 重建的派生索引。
3. **行动先于措辞**：有副作用的动作产生 `memory.kind='action'`，与世界状态变更同序写入，不等 36 轮后被总结成一句台词。

## 1. 已拍板决断（2026-11-01，用户逐条确认）

| # | 决断 | 落地含义 |
|---|---|---|
| 1 | **总结后不删原文** | `conversation` 的 `kind='turn'` 行永久保留；被中期总结覆盖的轮通过 `turn_no > last_midterm_turn` **移出窗口**（不再进 prompt），但长期总结与复盘仍可读。运行期只有两类 `DELETE`：generation 失效时丢弃刚插入的那一轮、`new_game` 清 `kind='midterm'` 的派生摘要行；**原文永不删除** |
| 2 | **长期只写不读** | v2 只做「生成 + 入库 + 建向量索引」，没有召回路径；读路径与它的节流参数整体推迟到 P5 |
| 3 | **中期阈值先定 0.8，实测后改 0.55** | 命中判定 `score > 阈值`（严格大于）。为使阈值可成立，召回 query **必须是长拼接**（最近 12 条消息、≤2400 字），不能是玩家单句。实测见 §12：0.8 下召回率 0，最终落地 `MIDTERM_MIN_SCORE=0.55` |
| 4 | **原地动作** | 工具集不含移动/跟随/带路；NPC 位置仍由日程确定性函数决定（架构约束 #2 不动） |
| 5 | **`reply` 允许为空** | 一个回合可以只行动或沉默；`done` 增补 `acts` 供客户端渲染动作气泡 |
| 6 | **长期间隔 36** | 与现有 `LONGTERM_TURN_INTERVAL` 和 MaiBot 一致；与中期 10 条一档不要求整除，重叠用 `turn_range` 精确表达 |

## 2. 账本模型

三个视图都从同一张表读，区别只有 `kind` 与 `turn_no` 条件：

| 视图 | 物理载体 | 读取条件 | 用途 |
|---|---|---|---|
| 短期（最近） | `conversation.kind='turn'` | `turn_no > last_midterm_turn`；窗口内最近 `dialogue_window_size=8` 轮平铺进 prompt | 窗口（逻辑容量 20，见 §2.1） |
| 中期（近期） | `conversation.kind='midterm'` | 取最近 `midterm_capacity=10` 条 | summary + 线索向量，按线索召回，≤1 条/回合 |
| 长期（旧事） | `longterm_memory` + Chroma `longterm` | v2 不读 | 每 36 轮原文总结；只写不读 |

`last_midterm_turn` 是窗口的**下界**，它推进 = 那批轮移出窗口。`last_longterm_turn` 是长期素材的上界，它推进 = 那批轮已归档。

### 2.1 时间线示例（窗口 20、中期每档 10、长期 36）

| 轮次 | 窗口内容 | 中期条目 | 长期 | 动作 |
|---|---|---|---|---|
| 1–20 | 1–20 | 0 | 0 | — |
| 21 | 11–21 | 1（轮 1–10） | 0 | 窗口 21 > 20 → 总结最久 10 条，游标 → 10 |
| 31 | 21–31 | 2（1–10, 11–20） | 0 | 又一次溢出 |
| 36 | 21–36 | 2 | 1（轮 1–36） | 后台长期总结，游标 → 36 |
| 41 | 31–41 | 3（21–30） | 1 | — |
| 72 | 41–72 | 4 | 2（37–72） | — |
| 201+ | 191–201 | FIFO 丢最旧，始终 10 条 | 5 | 中期最多覆盖 100 轮 |

**中期条目数与时长的关系**：10 条 × 10 轮 = 最多覆盖最近 100 轮；再早的事只有长期记忆（v2 不读），这是「长期只写不读」的直接后果，属于预期状态。

## 3. 时钟与游标

`npc_state` 新增三列（都是整数，语义都是 `turn_no`）：

| 列 | 含义 | 何时推进 |
|---|---|---|
| `turn_no` | 该 NPC 累计对话轮（含送礼/切磋/行动回合；**工具子调用不计**） | 每轮 `add_conversation_turn` 内 +1 |
| `last_midterm_turn` | 已被中期覆盖到的轮号（窗口下界） | 中期总结**成功写入后** `+= midterm_turns_per_entry`（10） |
| `last_longterm_turn` | 已归档到长期记忆的轮号 | 长期总结**成功入库后** `+= longterm_turn_interval`（36） |

游标只在写库成功后推进：中期总结失败 → 游标不动 → 同一批轮留在窗口里，下一轮自然重试（不需要回滚删除，这是决断 1 的直接收益）。长期任务靠 `generation` 屏障判失效，失效不推进。

`new_game`：`DELETE FROM conversation WHERE kind='midterm'`、`UPDATE npc_state SET last_midterm_turn = turn_no`（清空窗口）、清召回冷却表（`MemoryStore._recalled_at`）；**`turn_no` / `last_longterm_turn` / `conversation` 的 `kind='turn'` 行 / `longterm_memory` 全部保留**——长期记忆跨局保留是既有设计，`turn_no` 因此必须跨局单调，否则新局区间会与旧局长期区间重叠。

## 4. 写入流程

```
dialogue.talk_stream（一轮）
  ├─ turn_id = uuid4()                     # 幂等键前缀，写进 turns_json
  ├─ 生成回复（含 acts）→ 校验 → 执行动作（见 §6）
  ├─ add_conversation_turn(...)
  │    ├─ 同 NPC 锁内：
  │    ├─ turn_no = npc_state.turn_no + 1
  │    ├─ INSERT conversation(kind='turn', turn_no, ts_game, turns_json, affinity_delta_total)
  │    │     turns_json = {user, speech, acts, obs, place, turn_id}
  │    ├─ 若 turn_no - last_midterm_turn > conversation_window_capacity(20)：
  │    │     ├─ 取 [last_midterm_turn+1, +10] 的轮
  │    │     ├─ 中期总结（dialogue_summary，priority 0）→ summary + 3~6 线索
  │    │     ├─ 线索批量 embed
  │    │     ├─ INSERT conversation(kind='midterm', turn_no=区间末, turns_json={summary, clues, clue_vectors, turn_range})
  │    │     └─ 成功才 last_midterm_turn += 10
  │    └─ 若 turn_no - last_longterm_turn >= 36：投递 LongTermJob（区间 [last+1, last+36]）
  └─ done
```

长期 worker：从 **SQLite 读原文**（不再用 `_longterm_turns` 内存缓冲）→ `summary.summarize_longterm`（priority 3）→ `INSERT longterm_memory`（含 `turn_range_start/end`、`participants_json`、`poi`、`importance`）→ 同锁内 embed + upsert Chroma `longterm` → 成功后 `last_longterm_turn += 36`。

内存缓冲因此可以整块删掉：重启不再丢 36 轮素材，且「总结失败重试」变成免费的（游标未推进就会重来）。

## 5. 读取流程

### 5.1 一次嵌入调用，两个查询向量

```python
qvec, midterm_qvec = await mem_store.llm.embed([message, midterm_query])
```

- `qvec`：玩家本轮原话，给 Chroma RAG（现有三因子重排不动）。
- `midterm_qvec`：`midterm_query` = 窗口内最近 `midterm_query_message_limit=12` 条消息（含本轮）的文本拼接，截断 `midterm_query_max_chars=2400`，格式 `玩家：…\n你：…`。

这样仍然是**每轮一次嵌入往返**（批量），不新增前台往返；同时把中期 query 抬到「长文本」，使决断 3 的 0.8 有意义。原 AGENTS 里「查询向量每轮只算一次、RAG 与中期共用」改为「每轮一次嵌入调用，产出两个向量，RAG 与中期各用一个」——实施时同步该句。

### 5.2 中期召回

```python
entries = await db.fetch_all(
    "SELECT id, turn_no, turns_json FROM conversation"
    " WHERE npc_id=? AND kind='midterm' ORDER BY turn_no DESC LIMIT ?",
    (npc_id, settings.midterm_capacity))
# 解析 clues + clue_vectors（base64 float32），跳过冷却中的条目
score = max(dot(midterm_qvec, cue_vec) for cue_vec in entry.vectors)
```

规则：

- 每条中期取**最高分线索**作为该条分数（不要平均——一条强线索就够）。
- `score > MIDTERM_MIN_SCORE`（实测落地 0.55）才算命中；只取分数最高的一条（`MIDTERM_TOP_K=1`）。
- **冷却去重**：`MemoryStore._recalled_at[npc_id][midterm_id] = turn_no`，`turn_no - 上次 < MIDTERM_RECALL_COOLDOWN_TURNS(5)` 就跳过该条。这是 MaiBot「已注入身份去重」的等价物；**不设「历史累计最多 N 条」配额**（那会把功能在第 N 次命中后永久关掉）。
- 注入：prompt 现有板块「你想起一些旧事（只作参考，不要逐字复述）」，一条 summary，≤120 字。
- 未命中：整块不出现，不留空行。

### 5.3 预算

v2 只有中期一个可选板块（≤120 字），不需要独立预算模块。P4 加入人物画像（≤2 人 / ≤400 字）时再引入 `agent/prompt_budget.py`：总闸 `memory_context_max_chars=1500`，优先级 中期 > 画像，超出从低优先级砍。

## 6. 行动层（P3）

### 6.1 契约

沿用单次 `format:"json"` 调用，只扩字段，SSE 事件序列不变：

```json
{"reply": "……", "acts": [{"tool": "give_item", "args": {"item": "金疮药", "count": 1}}],
 "affinity_delta": 0, "end": false}
```

- `acts` 缺省/空数组 = 只说话（老行为）。
- `reply` 允许 `""` = 只行动或沉默（决断 5）；`done.reply=""` 且 `done.acts` 非空时客户端显示动作气泡。
- 流式解码仍只针对 `reply` 字段；`acts` 在流结束后解析。
- `_parse` 放宽两处：`reply` 键缺失视为 `""`（不再算解析失败）；`acts` 不是数组视为无 acts，而单条 act 结构非法算**校验失败**（走 §6.3 的修正回合，不重试整轮）。
- 送礼/切磋复用 `dialogue.talk`，因此它们的回合同样允许 `acts`（例如收了礼物回赠一件），不要为它们单独关掉动作解析。

### 6.2 工具白名单（v1，四个，全部原地）

| 工具 | 参数 | 前置校验 | 副作用 | 入忆 |
|---|---|---|---|---|
| `give_item` | `item`(名), `count`(1~3) | NPC 持有该物品且数量够 | 双方 `character_inventory` 增减 | importance 4 |
| `take_item` | `item`, `count` | 玩家持有且好感 ≥ 50 | 反向增减 | importance 5 |
| `teach_art` | `art`(功法名) | NPC 正在练这门、好感 ≥ 70、玩家没学过、同源且不压级 | 走 `arts.switch_art` 写 `character_skill` | importance 6 |
| `refuse` | `reason`(≤20 字) | 无 | 仅生成措辞，不改状态 | importance 3 |

> P3 实施修订（2026-11-01）：原计划的 `teach_skill` 改为 `teach_art`。本项目的招式由功法等级自动授予，单独"教一招"等于空操作；真正可教的是功法。属性相冲（需散功）或上限压级时工具**直接拒绝**，因为工具无法替玩家确认散功——DESIGN §15 已把"NPC 自主散功"列为风险。

- 一切效果走现有服务端函数（`inventory` / `skills`），**不给 NPC 开侧门**；好感仍只由 `affinity_delta` 与反思改，工具不能直接写 `npc_state.affinity`。
- `wait` / `stop` 不单独做工具：沉默 = `reply=""` 且 `acts=[]`；收住话头 = 现有 `end=true`。
- 不做移动类工具（决断 4）。

### 6.3 执行链与两种安全阀

```
parse → 校验全部 acts（白名单 / 参数 schema / 前置条件）
  ├─ 任一失败：不执行任何 act（原子性），把原因作为 observation 回填
  │             → 再生成一次（temperature 0.3，本次输出中的 acts 一律丢弃，只取 reply）
  │             → 仍失败：本回合无动作，模板 reply 兜底
  └─ 全部通过：逐条执行 → 落 tool_call_log → 生成 observation
                → reply 非空则直接 done（不额外调用，保住 2~5s 延迟预算）
```

- **副作用与重试隔离**：动作一旦执行成功，禁止重试整轮。现有「解析失败 → 重试整轮」必须发生在执行之前；执行之后的修正只允许重生成文本。
- **幂等**：`tool_call_log(turn_id, call_index)` 唯一约束；回放发生在**校验之前**——副作用已经发生、世界状态变了，再校验必然失败（东西已经给出去）。`turn_id` 每轮一个 uuid，同时写进 `turns_json` 便于对账。
- 工具内部**禁止再调 LLM**（会破延迟预算）；需要模型判断的东西留在生成里。

### 6.4 动作如何进记忆

- 计数：一个回合 = 一个 `turn_no`，无论内部多少个 act；acts/obs **不占窗口**。
- `turns_json` 保留结构：`{"user","speech","acts":[{tool,args,ok,result}],"obs":[...],"place","turn_id"}`。
- 总结素材：中期/长期 prompt 渲染成两行，动作合并进「你」那一行：
  ```
  秦侠客（铁匠铺）：我这几日缺一味金疮药。
  你：拿去罢，莫要再熬夜打铁。（你给了他一包金疮药）
  ```
  `acts` → 中文短句的映射只写在 `agent/actions.py::acts_text()`，prompt 渲染与客户端气泡共用。
- 有副作用的 act 立刻落一条 `memory(kind='action')` 并推 `recent_events`（目击者扩散），与世界状态变更同序（先 SQLite 后 Chroma，同一个 `_lock`）。这会让反思/八卦重新有素材，是设计意图，不要因此关掉它们。
- `refuse` 虽无状态副作用，但「拒绝」本身改变 NPC 的立场认知，写入 `memory(kind='action', importance=3)`；其余无副作用的 act（纯措辞）不写 `memory`。

### 6.5 客户端（P3 同批）

`done` 增加 `acts: [{"tool": "...", "text": "（他递过来一包金疮药）"}]`；`xia-ai-town/scripts/main.gd` 在 `reply` 为空时把 `acts` 渲染成气泡/系统行。这条是决断 5 的必然代价，不能省。

## 7. Schema 与迁移

### 7.1 `db.py` 的 `SCHEMA`（新库）

```sql
CREATE TABLE IF NOT EXISTS tool_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    call_index INTEGER NOT NULL,
    npc_id INTEGER NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at_real REAL NOT NULL,
    UNIQUE(turn_id, call_index)
);
```

`conversation` 与 `npc_state` 的新列**不进 `SCHEMA`**（旧库已有该表，`CREATE TABLE IF NOT EXISTS` 不会补列），只在迁移段 `ALTER`：

```sql
ALTER TABLE conversation ADD COLUMN kind TEXT NOT NULL DEFAULT 'turn';
ALTER TABLE conversation ADD COLUMN turn_no INTEGER NOT NULL DEFAULT 0;
ALTER TABLE npc_state ADD COLUMN turn_no INTEGER NOT NULL DEFAULT 0;
ALTER TABLE npc_state ADD COLUMN last_midterm_turn INTEGER NOT NULL DEFAULT 0;
ALTER TABLE npc_state ADD COLUMN last_longterm_turn INTEGER NOT NULL DEFAULT 0;
ALTER TABLE longterm_memory ADD COLUMN turn_range_start INTEGER NOT NULL DEFAULT 0;
ALTER TABLE longterm_memory ADD COLUMN turn_range_end INTEGER NOT NULL DEFAULT 0;
ALTER TABLE longterm_memory ADD COLUMN participants_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE longterm_memory ADD COLUMN poi TEXT NOT NULL DEFAULT '';
ALTER TABLE longterm_memory ADD COLUMN importance INTEGER NOT NULL DEFAULT 5;
```

**迁移顺序**（AGENTS「增量迁移顺序」坑）：`executescript(SCHEMA)` → 补列 `ALTER` → 建引用新列的索引 → 回填。索引不能在 `SCHEMA` 里，否则旧库启动即 `no such column`：

```sql
CREATE INDEX IF NOT EXISTS idx_conversation_npc_kind_turn
    ON conversation(npc_id, kind, turn_no);
```

回填（新旧库同一条路径，新库无行则空转）：

1. `UPDATE conversation SET kind='turn' WHERE kind IS NULL OR kind=''`（默认值已覆盖，可省）。
2. 旧行 `turn_no` 按 `id` 顺序赋 1..N（`ORDER BY id`，用 `ROW_NUMBER()` 或 Python 循环）。
3. `npc_state.turn_no = MAX(conversation.turn_no)`；`last_midterm_turn` / `last_longterm_turn` 置 `turn_no`（历史轮视为「已归档、不进窗口」，避免老档一上线就把 100 轮塞进 prompt）。
4. 旧 `longterm_memory` 行的 `turn_range_*` 置 0（未知），`importance` 用 5。

### 7.2 Chroma 集合

| 集合 | 索引来源 | 说明 |
|---|---|---|
| `memories` | `memory` 表 | 语义不变（event/reflection/gossip/**action**） |
| `longterm` | `longterm_memory` 表 | v2 新增；id=行 id，document=content，metadata={npc_id, ts_game, turn_range_end, poi}；`hnsw:space=cosine` + `embedding_function=None` + 显式 embeddings |

`MemoryStore.reconcile()` 改成**按集合分别校验**：`memories` 对 `COUNT(memory)`、`longterm` 对 `COUNT(longterm_memory)`，任一不符只重建那一个集合（不要因为一张表数量不符去重建另一个）。

### 7.3 `turns_json` 兼容

新格式用 `speech`（旧行用 `npc`）。所有读取点统一走一个 helper：

```python
speech = data.get("speech") or data.get("npc", "")
acts = data.get("acts") or []
```

涉及：`rag.recent_turns`、`summary._turn_lines`、`dialogue._build_messages` 的近轮拼接、`memory` 的中期/长期素材。

## 8. 配置

### 8.1 本批新增/调整（`config.py`）

```python
dialogue_window_size: int = 8            # 6 -> 8（只影响 prompt 平铺轮数）
conversation_window_capacity: int = 20   # 10 -> 20（只影响窗口水位与中期粒度）
midterm_turns_per_entry: int = 10        # 新：每次中期总结的轮数
midterm_capacity: int = 10               # 不变
midterm_top_k: int = 1                   # 3 -> 1
midterm_min_score: float = 0.55          # 实测标定（见 §12），0.8 时召回率为 0
midterm_recall_cooldown_turns: int = 5   # 新：同一条中期的最短重投间隔
midterm_query_message_limit: int = 12    # 新：召回 query 拼接的消息条数
midterm_query_max_chars: int = 2400      # 新：召回 query 字数上限
longterm_turn_interval: int = 36         # 不变
memory_context_max_chars: int = 1500     # 新：P4 预算闸（P4 才生效）
profile_max_persons: int = 2             # 新：P4
profile_max_chars: int = 400             # 新：P4
```

### 8.2 长期召回参数（P5 已实施）

| 参数 | 落地值 | 与 §8.2 草案的差异 |
|---|---|---|
| `LONGTERM_RECALL_ENABLED` | **true** | 草案是 false；读路径就是 P5 的目的，确认默认开 |
| `LONGTERM_RECALL_LIMIT` | 3 | 不变 |
| `LONGTERM_RECALL_MAX_CHARS` | 900 | 不变 |
| `LONGTERM_RECALL_CACHE_TTL` | 300s | 不变 |
| `LONGTERM_RECALL_MIN_INTERVAL` | 180s | 不变（**AND** 语义：与下一条同时满足） |
| `LONGTERM_RECALL_MIN_NEW_TURNS` | 30 | 原草案是 60 条消息；Xia 一轮 = 玩家+NPC 两条消息，改按轮计更直白（30 轮 ≈ 60 条） |
| `LONGTERM_CANDIDATE_K` | 20 | 不变 |
| `LONGTERM_MIN_SCORE` | **0.45** | 新增固定阈值；实测 0.55 时召回率只剩 0.33（见 §12） |
| `LONGTERM_DYNAMIC_THRESHOLD` | false | 动态阈值夹 `[0.42, 0.65]`；实测在这套分布上会退化成「全都要」，默认关 |
| `LONGTERM_HYBRID_ENABLED` | false | 稀疏 BM25 路 + RRF；实测只加噪声不加命中 |
| `LONGTERM_GRAPH_ENABLED` | false | 实体图多跳 + PPR；实测与纯向量打平 |
| `LONGTERM_FALLBACK_ENABLED` | false | 空召回兜底取最近几条；阈值已标定，兜底只会加噪声 |

### 8.3 `server/.env.example`

P0 实施时同步注释段（`DIALOGUE_WINDOW_SIZE` / `CONVERSATION_WINDOW_CAPACITY` / `MIDTERM_*` / `LONGTERM_TURN_INTERVAL`）。

## 9. 变更地图

| 文件 | 变更 |
|---|---|
| `server/app/config.py` | §8.1 参数 |
| `server/app/db.py` | `tool_call_log` 建表、四张表补列、索引、回填 |
| `server/app/agent/memory.py` | 账本游标读写、窗口/中期查询、中期条目入库与召回、冷却表、长期 worker 改从 DB 读、`longterm` 集合与分集合 `reconcile`、删掉 `_longterm_turns`；`DELETE` 只剩两类（generation 失效丢弃刚插入的那一轮、`new_game` 清 `kind='midterm'`） |
| `server/app/agent/rag.py` | `recent_turns` 改成「只取窗口内（`turn_no > last_midterm_turn`）」+ 新格式归一化；`embed_query` 改批量双向量 |
| `server/app/agent/summary.py` | `_turn_lines` 支持 `speech/acts`；中期输入 10 轮、长期输入 36 轮不变 |
| `server/app/agent/dialogue.py` | 一次 embed 两个 query、中期板块、acts 解析与执行编排、`done` 增 `acts`、`turn_id` |
| `server/app/agent/actions.py`（新） | 白名单、参数/前置校验、执行、`acts_text()` 中文映射 |
| `server/app/main.py` | debug 增字段、`new_game` 游标处理、SSE 透传 `acts` |
| `server/tests/smoke.py` | stub 与断言（§11） |
| `xia-ai-town/scripts/main.gd`、`api.gd` | P3：`reply` 为空时渲染动作气泡 |
| `docs/DESIGN.md` | §6.3/§6.4/§6.6/§8/§14/§15 同步（v2 落地后逐条回改，届时删掉 §6.3 的「v2 目标」块） |
| `AGENTS.md` | #11 与「已知坑」记忆条目同步；`docs/MEMORY-V2-PLAN.md` 链接 |

## 10. 阶段拆分（每个阶段独立可玩、独立 commit）

- **P0 账本地基（已实施）**：`conversation.kind/turn_no`、`npc_state` 三游标、`longterm_memory` 补列、索引与回填、窗口查询改子查询、删掉运行期的窗口删除（只保留 generation 失效丢弃与本轮新局清理）、debug 暴露游标。
  - 验收：现有冒烟全绿（窗口断言改为「不做删除，只移出窗口」）；老库能启动、游标回填正确（已用旧库夹具手验）。
- **P1 中期回想（已实施）**：中期条目入库（`kind='midterm'`）、双向量嵌入、`midterm_query` 长拼接、阈值严格大于（实测落地 0.55，见 §12）、`top_k=1`、冷却去重、prompt 板块。
  - 验收：§11 的 A/B 两组断言。
- **P2 长期归档（已实施）**：worker 改从 DB 读、`longterm_memory` 写 `turn_range`、Chroma `longterm` 集合与分集合 `reconcile`、游标推进。
  - 验收：§11 的 C/D 组；重启后 `longterm` 与中期条目都仍在、长期最近那条已在 Chroma 里。
- **P3 行动层（已实施）**：`actions.py`、契约扩 `acts`、4 个工具、`tool_call_log` 幂等、原子校验与修正回合、动作入忆与 `recent_events`、总结渲染、客户端动作气泡。
  - 验收：§11 的 E 组。
- **P4 画像与预算（已实施）**：确定性画像（`tool_call_log` 聚合）、`prompt_budget.py` 总闸。
  - 验收：画像随给物/拒绝次数变化，且不调 LLM。
- **P5 长期召回（已实施）**：`agent/retrieval.py`（BM25 char-2gram / 加权 RRF / 动态阈值 / 闭集实体链接 / 共现图 PPR / 去重）+ `agent/longterm.py`（三路候选 → 余弦阈值门 → RRF 排序 → 去重 → 预算截断 + 三道节流闸）+ 对话 prompt 的「你记得更早年间的事」板块 + `SERVER/tools/calibrate_longterm.py`。
  - 验收：§11 的 F 组；实测结论是**只开向量路**（BM25/图/动态阈值默认关），数据见 §12。

## 11. 冒烟断言（`server/tests/smoke.py`）

环境固定：`CONVERSATION_WINDOW_CAPACITY=20`、`MIDTERM_CAPACITY=1`、`MIDTERM_TOP_K=1`、`MIDTERM_MIN_SCORE=0.55`、`LONGTERM_TURN_INTERVAL=36`、`MIDTERM_RECALL_COOLDOWN_TURNS=5`。

**A. 窗口 21 → 中期条目入库、原文不删**
- 对 NPC 7 连说 21 轮。
- `SELECT COUNT(*) FROM conversation WHERE npc_id=7 AND kind='turn'` == 21（**没有删除**）。
- `SELECT COUNT(*) ... kind='midterm'` == 1；`turns_json.turn_range == [1,10]`。
- 窗口 = `turn_no > last_midterm_turn` 的行 == 11。
- `CALLED.count("dialogue_summary")` +1。
- `memory` 表没有该 summary；Chroma `memories` 计数不变。

**B. 召回命中与冷却**
- 第 22 轮发一句与线索高度重合的话（stub embed 按关键词分桶，保证 >0.8）。
- 取最后一条 `dialogue` prompt：含「你想起一些旧事」，含该 summary。
- 紧接着第 23 轮再发同一句：prompt **不含**该板块（冷却生效）。
- 第 27 轮（距上次命中 5 轮，`>= cooldown`）再发：重新命中。

**C. 36 轮 → 长期入库**
- 补到 36 轮，`await mem_store.wait_background()`。
- `longterm_memory` 1 行，`turn_range_start=1, turn_range_end=36`，`content` 来自 stub，`importance` 有值。
- Chroma `longterm` 集合 count == 1；`memories` 计数不变。
- `memory` 表无该内容。
- `last_longterm_turn == 36`。

**D. 重启语义**
- 重开 app（同库）：中期条目仍在（入库）、`longterm_memory` 仍在、`last_midterm_turn`/`last_longterm_turn` 保持。
- 新局 `/api/game/new`：`kind='midterm'` 行清空、窗口空、`last_midterm_turn == turn_no`；`kind='turn'` 行与 `longterm_memory` 保留。

**E. 行动层**
- stub 返回 `acts=[{"tool":"give_item",...}]`：玩家背包 +1、NPC -1、`memory` 新增 1 条 `kind='action'`、Chroma `memories` +1、`tool_call_log` 1 行。
- 同一 `turn_id/call_index` 再执行：副作用不重复（幂等）。
- 前置不满足（NPC 没这个物品）：无副作用、`tool_call_log` 记 ok=0、本回合 `done.acts` 为空、回复走修正模板。
- `reply=""` + `acts` 非空：`done.reply == ""`、`done.acts` 非空、`conversation.turns_json.speech == ""`。

**F. 长期召回（P5）**
- 手工两行 `longterm_memory` + 对应向量：命中主题的 query 返回 1 条且内容正确；无关主题返回空。
- 同一 NPC 连续两次召回：第二次命中缓存（返回同一对象，不重算）。
- TTL 过期但新增轮数不足：返回空（节流生效）；补足轮数后恢复。
- 对话 prompt 出现「你记得更早年间的事」板块；`/api/debug/npc/{id}` 的 `longterm_recall.cached` 非空。

**G. 纯函数断言**
- `midterm` 分数：两条手工条目，查询向量分别命中，验证「取最高分线索」与 `> 0.55` 严格判定。
- `retrieval`：BM25 只命中含查询词的文档；`rrf` 两路同分时名次靠前者胜；`dynamic_threshold` 单调且被夹在区间内；`dedupe` 吃掉包含关系与近重复。
- `acts_text()`：每个工具 → 固定中文短句，未知工具回退空串。
- `_turn_lines`：旧格式（`npc` 键）与新格式（`speech`+`acts`）渲染结果都非空且含动作描述。

## 12. 边界与已知取舍

- **中期阈值必须实测，不能照抄**。`server/tools/calibrate_midterm.py` 用**真实对话后端 + 真实嵌入**打 6 组中期归档、每组 3 条相关 + 3 条无关探针，输出余弦分布与阈值扫描（recall / FPR / Youden J）。2026-11-01 的实测（`deepseek-flash` 生成线索 + `qwen3-embedding:0.6b` 嵌入，n=18/18）：

  | τ | 召回率 | 误报率 | J |
  |---|---|---|---|
  | 0.40 | 1.00 | 0.50 | +0.50 |
  | 0.45 | 0.83 | 0.17 | +0.67 |
  | 0.50 | 0.83 | 0.11 | +0.72 |
  | **0.55** | **0.72** | **0.06** | **+0.67** |
  | 0.60 | 0.61 | 0.06 | +0.56 |
  | 0.65 | 0.44 | 0.00 | +0.44 |
  | 0.70 | 0.17 | 0.00 | +0.17 |
  | 0.80 | **0.00** | 0.00 | 0.00 |

  分布：长拼接 query 下相关分中位数 0.66（min 0.40 / max 0.77），无关分中位数 0.42（min 0.24 / max 0.64）。**0.8 时召不回任何一条**——这不是"阈值偏高"，是功能完全关闭。落地取 0.55（召回 72% / 误报 6%）；偏好召回可降到 0.50（83% / 11%），偏好精度可用 0.65（44% / 0%）。
  另一个被数据推翻的预判：**单句 query 并不比长拼接好**（最佳 J 只有 +0.56，且高分区靠的是近乎逐字复述），长拼接的分布更紧、更稳，所以 query 构造保持不变。
  改 embedding 模型 / 线索 prompt / query 构造后必须重跑该脚本再定值。
- **长期召回阈值另测**（`server/tools/calibrate_longterm.py`，同一套真实后端，语料 = 6 条长期记忆、探针相关/无关各 18 条）。逐条二元判定：

  | τ | 召回率 | 误报率 | J |
  |---|---|---|---|
  | 0.35 | 0.94 | 0.56 | +0.39 |
  | 0.40 | 0.94 | 0.17 | +0.78 |
  | **0.45** | **0.78** | **0.00** | **+0.78** |
  | 0.50 | 0.56 | 0.00 | +0.56 |
  | 0.55 | 0.33 | 0.00 | +0.33 |
  | 0.65 | 0.06 | 0.00 | +0.06 |

  分布：相关中位数 0.508（0.31~0.66）、无关中位数 0.366（0.22~0.44）。**比中期整体低一截**——长期条目是整段小说式摘要，query 只有 60~90 字，长度落差把余弦压下来；所以两个阈值各测各的，落地 0.45（误报 0、召回 0.78），0.40 可以多拿 3 个百分点召回但要付 17% 误报。

- **三路混合实测无增益**（同一脚本，top-3，τ=0.45，正确条目命中 / 漏召 / 多带噪声）：

  | 配置 | 命中 | 漏召 | 噪声 |
  |---|---|---|---|
  | 仅向量 τ=0.40 | 15/18 | 3 | 26 |
  | 仅向量 τ=0.45 | 14/18 | 4 | 16 |
  | 仅向量 τ=0.50 | 12/18 | 6 | 5 |
  | +BM25(RRF) τ=0.45 | 14/18 | 4 | 19 |
  | +BM25+实体图/PPR τ=0.45 | 14/18 | 4 | 19 |
  | +实体图/PPR τ=0.45 | 14/18 | 4 | 16 |
  | 动态阈值（夹 [0.35, 0.70]） | 17/18 | 1 | 36 |

  结论：BM25 命中不变、噪声 +3；实体图/PPR 与纯向量**完全打平**；动态阈值在这套分布上算出来 ≈0.35，等于把阈值撤了（噪声 36）。所以三路代码保留、开关默认全关——语料只有几十条时，稀疏路与图路没有可捞的空间。语料涨到几百条以上再重测。

- **中期只覆盖最近 100 轮**（10 条 × 10 轮），更早的内容只有长期记忆。
- **长期只写不读**：`longterm` 集合没有查询方，索引写错不会立刻暴露 → debug 端点必须同时暴露 `longterm_memory` 行数与 `longterm` 集合 count，靠人工对账；`reconcile` 分集合校验是第二道保险。
- **原文永久保留**：`conversation` 会随轮数增长（一轮约 300 字节，1000 轮 ≈ 300KB/NPC）。若将来需要清理，只能删「`turn_no <= last_longterm_turn` 且 `kind='turn'`」的行，不要动未归档区间。
- **两次向量仍然是一次调用**：批量 `embed([message, midterm_query])`；若某个后端不支持批量，退化为两次调用，延迟 +30~80ms，可接受但要记录。
- **行动层不引入移动**：`follow`/带路等若将来要做，必须复用「日程相位平移」而不是新增位置同步，否则违反架构约束 #2。
- **工具的效果边界**：所有工具走既有服务端函数，工具不写好感、不改日程、不跨地图、不调 LLM。
- **旧库首启**：`last_midterm_turn = last_longterm_turn = turn_no`，历史轮不进窗口；想从头验证窗口行为请用 `RESET_ON_START=true` 或新库。
- **迁移回填不可逆**：`turn_no` 一旦按 id 顺序回填，就不要再改排序规则（否则长期 `turn_range` 与中期窗口会对不上）。

## 13. 开放项（不阻塞 P0–P4）

1. 中期板块与将来长期板块的文案是否合并成一个「你隐约记得的事」多子块——等 P5 有第二块再定。
2. 人物画像的字段与模板（P4 再细化：给物/收物/教招/拒绝的次数、首次与最近时间）。
3. 客户端动作气泡的呈现（逐条气泡 vs 一条合并系统行）。

## 14. 与 MaiBot 的逐项对照（照搬 / 改造 / 不搬）

| MaiBot 做法 | Xia v2 处理 | 理由 |
|---|---|---|
| 窗口水位 2×、裁到 1×，中期总结「被裁掉的那批」 | **改造**：窗口水位 20、中期每次 10 轮、游标推进；不删原文 | Xia 有持久 SQLite，删除只带来回滚复杂度；长期归档还要原文 |
| 中期摘要作为内部消息存历史、cue 向量序列化在 payload | **照搬思路**：中期条目就是 `conversation` 的一行，`clues+向量` 进 `turns_json` | 零新表、零重启重建 |
| 召回 query = 最近 12 条消息拼接（≤2400 字），阈值 0.8，只取 best 1，已注入身份去重 | **照搬** | 0.8 只在长 query 下成立，这是配套关系 |
| 「保留 10 条回想」被误读为「累计最多回想 10 次」 | **改造**：10 是 FIFO 容量；去重靠 5 轮冷却 | 配额会让功能在 N 次后永久关闭 |
| 窗口计数按消息类型区分（参考消息/工具结果不占窗） | **照搬** | 行动回合不撑爆窗口 |
| 工具调用与结果进历史 | **照搬**，但**总结素材必须含动作** | MaiBot 只总结 `role=user`；Xia 的 NPC 自己说的话与做的事是主体 |
| `reply` 是工具、另有 `wait`/`stop` | **改造**：`reply` 保留为 JSON 字段（SSE 不动）；沉默 = 空 reply；结束 = `end` | 4B + `format:"json"` 比原生 function calling 稳 |
| 原生 function calling + ToolSpec/ToolProvider 三层 | **改造**：只借 `ToolSpec{name,description,parameters_schema}` 的形状，执行走自研 `actions.py` | 保留将来切原生 tools 的余地 |
| Faiss SQ8 + append-only 向量池 | **不搬** | 全镇几百条 vs 十万级；Chroma 足够，numpy 点积也够 |
| 动态阈值（p75/mean−1.5σ/gap 夹 [0.29,0.95]、保底 4） | **延后到 P5**，且保底改 `min(2, 候选数)` | 候选池只有 20 条时保底 4 是注噪 |
| 三路融合 0.65/0.20/0.15、关系路 0.45/0.15/0.40、PPR α=0.85 | **不搬（P5 再议）** | 需要实体图谱与 BM25 索引；库小的时候收益为负 |
| 启发式长期记忆 180s/60 条/300s 缓存（AND + 缓存优先） | **延后到 P5**，参数已定稿在 §8.2 | 没有召回路径就没有节流对象 |
| 人物画像 4 人/900 字、从结构化 facts 拼 | **改造**：≤2 人/≤400 字，从 `kind='action'` 聚合 | Xia 是单玩家 1v1，4 人是群聊参数 |
