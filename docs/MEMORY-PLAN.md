# 记忆分层重构执行计划：短期 / 中期 / 长期

> 状态：待实施。实施完成后把 §6.3/§6.4/§6.6/§7.5/§8/§14 的最终口径折回 `docs/DESIGN.md`，并同步 `AGENTS.md`。
>
> 本计划取代现有“满窗（10 轮）即总结成一条 `kind=event` 落库 + Chroma”的单一链路。旧链路代码在 `server/app/agent/memory.py::add_conversation_turn` 与 `summary.py::summarize_dialogue_window`。

## 0. 目标与已拍板决策

把对话记忆拆成三层：

| 层 | 载体 | 生命周期 | 是否入库 | 产生时机 | 用途 |
|---|---|---|---|---|---|
| 短期 | SQLite `conversation` 表（每 NPC 独立窗口，容量 10） | 持久化，重启保留 | 已有表 | 每轮对话 / 送礼 / 切磋 | 最近 `DIALOGUE_WINDOW_SIZE=6` 轮原文直接平铺进 prompt |
| 中期 | `MemoryStore` 进程内 `dict[npc_id, list[MidTerm]]`，每 NPC 最多 10 条 | 进程内，重启清零 | **否**（不写 `memory` / Chroma） | 窗口超过 10 时，取最早 10 轮 → 1 条 summary + 3~6 条线索 | 用当前消息向量匹配线索，命中后把完整 summary 放进 prompt 独立板块 |
| 长期 | 新表 `longterm_memory` | 持久化，重启保留 | **是**（只进 SQLite，不进 Chroma） | 每 NPC 累计 36 轮原始对话，后台异步总结成 1 条 | 本期只生成 + 入库；不参与对话 / RAG / 反思 / 关系摘要 / 八卦 |

已确认的口径（用户逐项拍板）：

1. **长期总结素材 = 最近 36 轮原始对话**，不是二次汇总中期记忆；36 轮按 NPC 独立累计，含送礼 / 切磋轮。
2. **长期记忆暂不进入对话**，本期只做“生成 + 入库”。
3. **中期记忆按线索做向量检索**：当前对话消息 embedding → 与线索向量余弦匹配 → 命中后载入完整中期 summary，作为 prompt 中一个**独立参考板块**，不塞进“你们最近的往来”。
4. **中期与 36 轮计数纯内存**，服务重启清零。
5. **中期最多保留 10 条**，超出按 FIFO 丢最旧；这样长期总结“覆盖”后也不会无限增长。
6. **长期记忆完全隔离**：独立 `longterm_memory` 表，不写 `memory`、不写 Chroma，不被对话/RAG/反思/关系摘要/八卦读取。
7. **第 36 轮的长期总结后台异步**：回复立即 `done`，总结任务在后台跑；前台不额外等待。

## 1. 不可变约束

- 服务器权威、LLM 永不阻塞玩法、所有结构化输出走「校验 → 重试一次(temperature 0.3) → 模板兜底」。
- **不新增前台 LLM purpose**：中期总结与长期总结都复用 `dialogue_summary`。中期在 `done` 前同步执行（priority 0）；长期在后台执行，显式传 `priority=3`，不挤占前台对话。
- 短期窗口仍按 NPC 独立；送礼/切磋继续复用 `dialogue.talk(..., purpose="gift"/"spar", pause=False)` 并进入同一个窗口。
- 好感 clamp ±3、反思 ±2 等既有数值规则不动。
- 长程信息不再靠“每 10 轮一条 event”承载；`memory` 表继续只存大额消费/战斗目击/反思/八卦等既有事件。
- 客户端零改动：SSE 事件序列与 `done` payload 形状保持不变。
- 冒烟测试必须 `SCHEDULER_ENABLED=false`。

## 2. 数据流

### 2.1 单轮写入

```
dialogue.talk_stream
  └─ 回复完成、好感写回 npc_state
     └─ mem_store.add_conversation_turn(...)
        ├─ 1. INSERT conversation(npc_id, ts_game, turns_json, affinity_delta_total)
        ├─ 2. _longterm_turns[npc_id].append({user, npc, place, ts_game})
        ├─ 3. 若 conversation 条数 > 10（CONVERSATION_WINDOW_CAPACITY）:
        │     ├─ 取最早 10 条
        │     ├─ LLM 中期总结（dialogue_summary, priority 0）
        │     │    → {"summary": "≤120字", "clues": ["3~6条短线索"]}
        │     ├─ clues 批量 embed（llm.embed）
        │     ├─ 存入 _midterms[npc_id]；超过 10 条丢最旧
        │     └─ DELETE 已总结的 10 条 conversation
        ├─ 4. 若 len(_longterm_turns[npc_id]) >= 36:
        │     ├─ 取最早 36 轮，从内存缓冲删除
        │     └─ asyncio.create_task(后台长期总结)
        └─ 5. 返回 TurnOutcome(midterm_created, longterm_scheduled)
```

### 2.2 单轮读取

```
dialogue.talk_stream
  ├─ embed(message) 一次（复用给两处）
  ├─ Chroma RAG（现有 memory 表向量）
  ├─ match_midterms(npc_id, query_vec) → 命中的中期 summary
  └─ _build_messages(...)
       prompt 结构（新增板块插在“你记得的一些事”之后、“你们最近的往来”之前）：
         你是{NPC}。...
         你对{玩家}的印象：{关系摘要}
         你记得的一些事：
         {RAG top6}
         你想起一些旧事（只作参考，不要逐字复述）：
         - {中期 summary 1}
         - {中期 summary 2}
         你们最近的往来：
         {最近 6 轮原文}
         ...
```

中期板块只在对话链路使用；送礼/切磋复用同一对话 prompt，因此也自动获得该板块。主动搭话 `greet.py` 不改。

### 2.3 时间线示例（窗口 10，长期 36，中期上限 10）

| 轮次 | conversation 窗口 | 中期内存 | 长期原始轮缓冲 | 动作 |
|---|---|---|---|---|
| 1–10 | 10 | 0 | 10 | — |
| 11 | 1（第 11 轮） | 1（第 1–10 轮） | 11 | 中期总结 |
| 21 | 1（第 21 轮） | 2 | 21 | 中期总结 |
| 31 | 1（第 31 轮） | 3 | 31 | 中期总结 |
| 36 | 6（31–36） | 3 | 36 → 0 | 后台长期总结 + 入库 |
| 41 | 1（第 41 轮） | 4 | 5 | 中期总结 |

说明：

- 第 36 轮时窗口里还有 31–36 共 6 轮；长期总结用内存缓冲里的 36 轮原文，不依赖窗口。
- 长期总结只从缓冲移除前 36 轮，不动 `conversation` 窗口；窗口继续按自己的 10 轮节奏滚动。
- 中期条目被长期总结“覆盖”后不主动删除；它们只受“每 NPC 最多 10 条”的 FIFO 约束。

## 3. 变更地图

| 文件 | 变更 |
|---|---|
| `server/app/config.py` | 新增 `MIDTERM_CAPACITY`、`MIDTERM_TOP_K`、`MIDTERM_MIN_SCORE`、`LONGTERM_TURN_INTERVAL` |
| `server/app/db.py` | `SCHEMA` 新增 `longterm_memory` 表与索引；`reset()` 自动覆盖新表 |
| `server/app/agent/memory.py` | 中期/长期运行时状态、窗口溢出改造、线索匹配、后台任务、drain/reset、debug 辅助 |
| `server/app/agent/summary.py` | `summarize_midterm`（summary+clues）、`summarize_longterm`（36 轮原文）、两套兜底 |
| `server/app/agent/rag.py` | 抽出 `embed_query`，`retrieve` 支持传入已算好的 `qvec` |
| `server/app/agent/dialogue.py` | 一次 embed 复用、中期匹配、prompt 新板块、`TurnOutcome` 适配、trace |
| `server/app/llm.py` | 更新 `dialogue_summary` 的优先级注释（前台 0 / 后台长期显式 3）；不新增 purpose |
| `server/app/main.py` | debug 暴露中期/长期；new game 与 shutdown 清理运行时后台任务 |
| `server/tests/smoke.py` | stub 区分中期/长期；窗口、中期注入、长期入库、隔离断言 |
| `server/.env.example` | 新配置注释 |
| `docs/DESIGN.md` | §6.3 / §6.4 / §6.6 / §7.5 / §8 / §14 同步 |
| `AGENTS.md` | 架构约束 #11、已知坑、配置说明、目录说明同步 |
| `docs/MEMORY-PLAN.md` | 本计划 |

## 4. Phase 1：配置与 Schema

### 4.1 `server/app/config.py`

在现有 `conversation_window_capacity` 附近新增：

```python
# Memory tiers (docs/DESIGN.md §6.6)
midterm_capacity: int = 10          # 每 NPC 最多保留的中期条目数（FIFO）
midterm_top_k: int = 3              # 每轮最多注入的中期 summary 条数
midterm_min_score: float = 0.45     # 线索余弦相似度阈值
longterm_turn_interval: int = 36    # 每累计 N 轮原始对话触发一次长期总结
```

`conversation_window_capacity` 保持默认 10。摘要字数/线索条数上限写在 `summary.py` 模块常量里，不进 config：

```python
MIDTERM_SUMMARY_MAX = 120
MIDTERM_CLUE_MAX = 12
MIDTERM_CLUES_MAX = 6
LONGTERM_SUMMARY_MAX = 200
TURN_TEXT_MAX = 200       # 长期 prompt 中单条发言截断
```

### 4.2 `server/app/db.py`

在 `SCHEMA` 末尾、`llm_log` 之后新增：

```sql
CREATE TABLE IF NOT EXISTS longterm_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id INTEGER NOT NULL REFERENCES npc(id),
    ts_game REAL NOT NULL,
    content TEXT NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 36,
    created_at_real REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_longterm_npc_ts ON longterm_memory(npc_id, ts_game);
```

要点：

- 新表用 `CREATE TABLE IF NOT EXISTS`，旧库启动即补建，无需 ALTER。
- `Database.reset()` 按 `sqlite_master` 枚举全部表，新表自动被清空。
- **不**给 `memory` 增加 `kind=long_term`，也不把长期记忆写进 Chroma。隔离靠表结构本身，不靠散落各处的过滤条件。

### 4.3 `server/.env.example`

```env
# 记忆分层：短期窗口 10 轮；溢出后生成中期记忆（纯内存，最多 10 条）；
# 每 36 轮原始对话在后台总结成一条长期记忆写入 longterm_memory。
# CONVERSATION_WINDOW_CAPACITY=10
# MIDTERM_CAPACITY=10
# MIDTERM_TOP_K=3
# MIDTERM_MIN_SCORE=0.45
# LONGTERM_TURN_INTERVAL=36
```

## 5. Phase 2：中期记忆（纯内存）

### 5.1 `server/app/agent/memory.py` 新增运行时结构

```python
from dataclasses import dataclass, field

@dataclass
class MidTerm:
    summary: str
    clues: list[str]
    vectors: list[list[float]]      # 与 clues 一一对应，llm.embed 已 L2 归一化
    ts_game: float
    turn_count: int

@dataclass
class TurnOutcome:
    midterm_created: bool = False
    longterm_scheduled: bool = False
```

`MemoryStore.__init__` 增加：

```python
self._midterms: dict[int, list[MidTerm]] = {}
self._longterm_turns: dict[int, list[dict]] = {}
self._bg_tasks: set[asyncio.Task] = set()
```

`_window_locks` 继续作为每 NPC 的串行锁，覆盖窗口、中期列表、长期缓冲三者的写入。

### 5.2 改写 `add_conversation_turn`

签名增加 `npc_name`（中期/长期 prompt 需要 NPC 名），返回值由 `int | None` 改为 `TurnOutcome`：

```python
async def add_conversation_turn(
    self, db, npc_id, *, user, npc, npc_name, player_name, place,
    affinity_delta: int = 0,
) -> TurnOutcome:
```

步骤：

1. `INSERT conversation(...)`，`turns_json` 存 `{"user","npc","place"}`；`recent_turns` 仍只读 `user/npc`，旧行缺 `place` 也能解析。带上 `place` 是为了中期总结能按每轮实际地点拼 prompt。
2. 追加原始轮到 `_longterm_turns[npc_id]`：
   ```python
   {"user": user, "npc": npc, "place": place, "ts_game": self.clock.now()}
   ```
3. 窗口溢出判断：
   - `COUNT(*) > conversation_window_capacity`
   - 取最早 `capacity` 条，解析 `user/npc`
   - 调 `summary_mod.summarize_midterm(self, npc_id, npc_name, player_name, place, turns)`
   - `await self._store_midterm(npc_id, result, ts_game=...)`
   - `DELETE FROM conversation WHERE id IN (...)`
   - **不再调用 `add_memory`**
4. 长期触发判断：
   - `if len(self._longterm_turns[npc_id]) >= settings.longterm_turn_interval:`
   - `batch = buf[:interval]; del buf[:interval]`
   - `self._schedule_longterm(db, npc_id, batch, ts_game=self.clock.now(),
                              npc_name=npc_name, player_name=player_name)`
5. `return TurnOutcome(midterm_created=..., longterm_scheduled=...)`

`_store_midterm`：

```python
async def _store_midterm(self, npc_id, result, *, ts_game):
    clues = result["clues"]
    vectors: list[list[float]] = []
    if clues:
        try:
            vectors = await self.llm.embed(clues)
        except Exception:
            logger.warning("midterm clue embed failed npc=%d clues=%d", npc_id, len(clues))
    item = MidTerm(summary=result["summary"], clues=clues, vectors=vectors,
                   ts_game=ts_game, turn_count=result["turn_count"])
    bucket = self._midterms.setdefault(npc_id, [])
    bucket.append(item)
    cap = max(1, settings.midterm_capacity)
    if len(bucket) > cap:
        del bucket[:len(bucket) - cap]
```

线索 embedding 失败不阻塞对话：中期条目仍保留（可在 debug 看到），只是不参与匹配。

### 5.3 线索匹配

```python
def match_midterms(self, npc_id: int, query_vec: list[float]) -> list[MidTerm]:
    scored: list[tuple[float, MidTerm]] = []
    for item in self._midterms.get(npc_id, []):
        if not item.vectors:
            continue
        score = max(sum(a * b for a, b in zip(query_vec, v)) for v in item.vectors)
        if score >= settings.midterm_min_score:
            scored.append((score, item))
    scored.sort(key=lambda t: -t[0])
    return [item for _, item in scored[: settings.midterm_top_k]]
```

- 向量由 `llm.embed` 统一 L2 归一化，点积即余弦相似度。
- 每个中期条目取“最高分线索”作为该条目的分数。
- 命中一个线索就载入该条目的**完整 summary**（不是只把线索塞进 prompt）。
- 没有任何条目过阈值时返回空列表，prompt 不出现该板块。

### 5.4 运行时清理与观测辅助

```python
def reset_runtime(self) -> None:
    self._midterms.clear()
    self._longterm_turns.clear()

def debug_midterms(self, npc_id: int) -> list[dict]:
    return [
        {"summary": m.summary, "clues": m.clues, "ts_game": m.ts_game,
         "turn_count": m.turn_count}
        for m in self._midterms.get(npc_id, [])
    ]

def pending_longterm_turns(self, npc_id: int) -> int:
    return len(self._longterm_turns.get(npc_id, []))
```

`reset_runtime` 不负责取消后台任务；调用方先 `await drain()` 再重置。

## 6. Phase 3：长期记忆（独立表 + 后台任务）

### 6.1 后台任务调度

```python
def _schedule_longterm(self, db, npc_id, turns, *, ts_game, npc_name, player_name):
    task = asyncio.create_task(
        self._run_longterm(db, npc_id, turns, ts_game=ts_game,
                           npc_name=npc_name, player_name=player_name),
        context=contextvars.Context(),   # 不继承请求侧 Langfuse trace
        name=f"longterm-summary-{npc_id}",
    )
    self._bg_tasks.add(task)
    task.add_done_callback(self._bg_tasks.discard)
```

- `context=contextvars.Context()`：避免后台 LLM 调用挂到一个已经 `done` 的请求 trace 上（Python 3.12 支持）。
- 任务集合保留强引用，防 GC 回收；完成自动移除。
- 同一 NPC 同一时间最多只会有一个 36 轮批次在跑：触发时已把 36 轮移出缓冲，下一批要再攒 36 轮。

### 6.2 后台任务本体

```python
async def _run_longterm(self, db, npc_id, turns, *, ts_game, npc_name, player_name):
    try:
        result = await summary_mod.summarize_longterm(
            self, npc_id, npc_name, player_name, turns)
        await db.execute(
            "INSERT INTO longterm_memory(npc_id, ts_game, content, turn_count, created_at_real)"
            " VALUES(?,?,?,?,?)",
            (npc_id, ts_game, result["summary"], len(turns), time.time()),
        )
        logger.info("longterm memory stored npc=%d turns=%d chars=%d",
                    npc_id, len(turns), len(result["summary"]))
    except asyncio.CancelledError:
        logger.info("longterm summary cancelled npc=%d turns=%d", npc_id, len(turns))
        raise
    except Exception:
        logger.exception("longterm summary failed npc=%d turns=%d", npc_id, len(turns))
```

- LLM 内部已有「重试一次 → 模板兜底」，`summarize_longterm` 不会因模型输出失败而抛异常；这里的 `except` 主要兜 DB/意外错误。
- 后台失败不重试、不回滚缓冲：36 轮已从内存移除，进程重启本来也会丢，接受该取舍。

### 6.3 drain / wait

```python
async def wait_background(self) -> None:
    tasks = list(self._bg_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def drain(self) -> None:
    tasks = list(self._bg_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    self._bg_tasks.clear()
```

接入点：

- `main.py` lifespan shutdown：`await mem_store.drain()`，再 `mem_store.close()`、关闭 DB。
- `/api/game/new`：`await mem_store.drain()` + `mem_store.reset_runtime()`，防止旧角色的中期/长期缓冲泄漏到新档（DB 里的旧 `conversation`/`memory` 不在本任务范围）。
- 测试用 `await mem_store.wait_background()` 等异步长期总结完成后再断言。

## 7. Phase 4：总结模块 `summary.py`

### 7.1 中期总结 `summarize_midterm`

签名：

```python
async def summarize_midterm(store, npc_id, npc_name, player_name, place,
                            turns: list[dict]) -> dict:
    """返回 {"summary": str, "clues": list[str], "turn_count": int}，永不抛异常。"""
```

Prompt（system）：

```
你是{npc_name}。下面是玩家{player_name}与你最近的一段对话。
{player_name}（{place}）：{user}
你：{npc}
...
请以你的视角完成两件事：
1. 把这段交流归纳成 1 条简短摘要（一句话，古风白话，不超过120字），
   只概括对你有意义、日后值得记起的事，不要虚构对话中没提到的内容；
2. 提炼 3~6 条“线索”：日后听到/聊到时能让你想起这段往事的短词或短语，
   可以是人名、地点、物品、事由，每条不超过12字。
仅输出JSON：{"summary":"...","clues":["...","..."]}
```

解析与兜底：

- 每条 `user`/`npc` 截断 200 字（`TURN_TEXT_MAX`），防止超长输入撑爆 prompt。
- 去掉 ```json 围栏；`summary` 去空白后截断 120 字；`clues` 过滤空串、去重、每条截断 12 字、最多 6 条。
- `summary` 为空 → 视为失败。
- 两次尝试（0.7 → 0.3）都失败：
  - `summary = f"与{player_name}在{place}闲谈：{turns[-1]['user']}"[:120]`；`turns` 为空时用 `"与玩家闲谈了几句。"`
  - `clues = []`（该条中期不参与检索，但仍在 debug 可见）
- 日志：`midterm_summary npc=%d window=%d clues=%d`。
- 复用 `llm.chat("dialogue_summary", ...)`，不传 `priority`（`dialogue_summary` 默认 0）。

### 7.2 长期总结 `summarize_longterm`

签名：

```python
async def summarize_longterm(store, npc_id, npc_name, player_name,
                             turns: list[dict]) -> dict:
    """36 轮原始对话 -> {"summary": str}；永不抛异常。"""
```

Prompt（system）：

```
你是{npc_name}。下面是你与玩家{player_name}从早前到最近的一段往来，
共{turn_count}轮。
{player_name}（{place}）：{user}
你：{npc}
...
请以你的视角，把这段往来归纳成一条长期记忆：
第三人称，古风白话，不超过200字；只保留对你有意义、日后值得记起的人、事、
约定与态度变化，不要虚构；不要逐轮复述。
仅输出JSON：{"summary":"..."}
```

- 每条 `user`/`npc` 截断 200 字，防止超长输入撑爆 prompt。
- 若某轮有 `place`，行首带地点；没有则省略。
- 两次尝试（0.7 → 0.3），解析失败走兜底：
  - `summary = f"与{player_name}的一段往来：{first_user[:60]}……{last_user[:60]}"[:200]`
  - 若连 turns 都为空，`summary = "与玩家的一段往来。"`
- 调用时必须显式传优先级：

```python
cr = await store.llm.chat(
    "dialogue_summary", msgs, json_format=True, temperature=temperature,
    priority=3,   # 后台长期总结，不与前台对话抢槽
    metadata={"npc_id": npc_id, "stage": "longterm", "turns": len(turns)},
)
```

中期调用对应 `metadata={"npc_id": npc_id, "stage": "midterm", "window": len(turns)}`。

### 7.3 `llm.py` 注释同步

`PRIORITY` 表不变，仍只有 `dialogue_summary: 0`。只改注释：

```python
# dialogue_summary 前台中期总结发生在一次对话请求内，优先级 0；
# 36 轮长期总结在后台任务里复用同一 purpose，调用时显式传 priority=3。
```

不新增 `longterm_summary` purpose（遵守 AGENTS「不要为总结新增 purpose」）。

## 8. Phase 5：对话链路接入

### 8.1 `rag.py`

```python
async def embed_query(store, query: str) -> list[float]:
    return (await store.llm.embed([query]))[0]

async def retrieve(db, store, npc_id, query, *, settings, qvec=None) -> list[dict]:
    qvec = qvec if qvec is not None else await embed_query(store, query)
    ...
```

`greet.py` 不传 `qvec`，行为完全不变。

### 8.2 `dialogue.py`

把现有 `asyncio.gather` 改为“先算一次查询向量，再并行/顺序取 RAG 与中期”：

```python
qvec, turns, impression = await asyncio.gather(
    rag_mod.embed_query(mem_store, message),
    rag_mod.recent_turns(db, npc_id, n=settings.dialogue_window_size),
    relationship_mod.summary_for(db, npc_id, "player", p["name"]),
)
memories = await rag_mod.retrieve(
    db, mem_store, npc_id, message, settings=settings, qvec=qvec)
midterms = mem_store.match_midterms(npc_id, qvec)
midterm_summaries = [m.summary for m in midterms]
```

`_build_messages` 增加参数 `midterm_summaries: list[str] | None = None`，组装：

```python
midterm_lines = "\n".join(f"- {s}" for s in midterm_summaries or [])
midterm_block = (
    f"你想起一些旧事（只作参考，不要逐字复述）：\n{midterm_lines}\n"
    if midterm_lines else ""
)
```

插入位置：`你记得的一些事` 之后、`你们最近的往来` 之前。

Trace：

- 新增 `midterm` span，输出 `[{summary, clues, turn_count}]`（不输出向量）。
- `outcome` span 增加 `"midterm": outcome.midterm_created`、`"longterm_scheduled": outcome.longterm_scheduled`。

`add_conversation_turn` 调用处改为：

```python
outcome = await mem_store.add_conversation_turn(
    db, npc_id,
    user=message, npc=result["reply"], npc_name=n["name"],
    player_name=p["name"], place=place, affinity_delta=delta,
)
summarized = outcome.midterm_created
```

SSE `done` payload 不变；长期总结异步执行，不增加本轮等待。

### 8.3 前台预算

- 每轮：查询 `embed` 一次（原本 RAG 就要一次，现在复用给中期匹配，总数不变）。
- 每 10 轮：中期 `dialogue_summary` 一次 + 线索 `embed` 一次（增加一次本地嵌入往返，可接受）。
- 每 36 轮：长期 `dialogue_summary` 一次，后台 priority 3，不占前台等待。
- 中期板块最多 3 × 120 字 ≈ 360 字，prompt 增量可控。

## 9. Phase 6：调试接口与生命周期

### 9.1 `GET /api/debug/npc/{id}`

在返回体新增：

```python
"midterm": mem_store.debug_midterms(npc_id) if mem_store else [],
"longterm": await db.fetch_all(
    "SELECT id, ts_game, content, turn_count, created_at_real"
    " FROM longterm_memory WHERE npc_id = ? ORDER BY ts_game DESC LIMIT 20",
    (npc_id,),
),
"longterm_pending_turns": mem_store.pending_longterm_turns(npc_id) if mem_store else 0,
```

`memories` 仍只列 `memory` 表；长期记忆单独展示，避免被误认为已进入普通记忆流。

### 9.2 `/api/game/new`

在创建玩家、`world_state.reset` 之后：

```python
if mem_store is not None:
    await mem_store.drain()
    mem_store.reset_runtime()
```

### 9.3 lifespan

```python
await scheduler_mod.stop(scheduler_tasks)
await mem_store.drain()        # 新增：先取消并等待后台长期总结
await trace_mod.close()
mem_store.close()
...
```

## 10. Phase 7：冒烟测试

### 10.1 Stub 调整（`server/tests/smoke.py`）

`fake_chat` 的 `dialogue_summary` 分支按 prompt 文案区分两阶段：

```python
elif purpose == "dialogue_summary":
    system = messages[0]["content"]
    if "长期记忆" in system:
        text = json.dumps({"summary": "三十六轮往来已归档。"}, ensure_ascii=False)
    else:
        text = json.dumps(
            {"summary": "十轮闲谈已归档。", "clues": ["后山", "剑法"]},
            ensure_ascii=False,
        )
```

为了在不跑 110 轮的前提下验证 FIFO，测试环境在 import app 前设置：

```python
os.environ["MIDTERM_CAPACITY"] = "2"
```

`fake_embed` 保持“所有文本同一单位向量”，因此所有线索都会命中（相似度 1.0），足以验证“命中 → 注入完整 summary”的链路；阈值/排序逻辑用单独的纯函数断言覆盖（见 10.4）。

### 10.2 替换现有窗口测试段

现有 `== conversation window (10 turns -> one event) ==` 段整体替换为：

**A. 第 11 轮后：中期生成，不入库**

- 对 NPC 7 连续说 11 轮。
- `conversation` 剩余 1 条（第 11 轮）。
- `len(mem_store.debug_midterms(7)) == 1`。
- `debug_midterms[0]` 的 `summary == "十轮闲谈已归档。"`，`clues == ["后山", "剑法"]`。
- `CALLED.count("dialogue_summary") == summary_calls + 1`。
- `memory` 表没有 `"十轮闲谈已归档。"` 行。
- Chroma 计数不变（`chroma_before`）。
- 原始第 1–10 轮不在 `memory`。
- 本次 `dialogue_summary` prompt 含“闲谈第1轮”“闲谈第10轮”，不含“闲谈第11轮”。

**B. 第 12 轮：线索命中，独立板块注入**

- 发送含“后山”的消息。
- 取最后一条 `dialogue` prompt 的 system 文本：
  - 含 `"你想起一些旧事（只作参考，不要逐字复述）"`；
  - 含 `"十轮闲谈已归档。"`；
  - 该 summary 不直接出现在 `"你们最近的往来"` 段（验证“独立板块”而不是塞窗口）。

**C. 继续到第 36 轮：长期后台总结入库**

- 补到 36 轮，`await mem_store.wait_background()`。
- `SELECT COUNT(*) FROM longterm_memory WHERE npc_id = 7` == 1。
- 行内容：`content == "三十六轮往来已归档。"`，`turn_count == 36`。
- `CALLED.count("dialogue_summary") == summary_calls + 4`（第 11/21/31 轮三次中期 + 第 36 轮一次长期）。
- `memory` 表没有 `"三十六轮往来已归档。"`。
- Chroma 计数仍为 `chroma_before`（中期/长期都不进 Chroma）。
- `conversation` 剩 6 条（31–36 轮）。
- `len(debug_midterms(7)) == 2`（`MIDTERM_CAPACITY=2` 的 FIFO 生效）。
- 长期 prompt 含“闲谈第1轮”“闲谈第36轮”。
- `longterm_pending_turns(7) == 0`。
- `GET /api/debug/npc/7` 返回 `midterm` 2 条、`longterm` 1 条。

### 10.3 隔离断言

- 长期行只存在于 `longterm_memory`，`memory` 表中同内容计数为 0。
- Chroma 计数不变（中期线索向量只在内存，长期不写 Chroma）。
- 现有 reflection / relationship / gossip 测试段不改动，仍全绿（它们只读 `memory`，结构上不会碰到 `longterm_memory`）。

### 10.4 纯函数级断言（可选但建议）

直接调用 `MemoryStore.match_midterms`，手工塞两条中期：

- 条目 A 的线索向量 `[1,0,...]`，条目 B `[0,1,...]`。
- 查询 `[1,0,...]` → 只返回 A。
- 查询 `[0.9,0.1,...]` 归一化后若低于阈值 → 返回空（验证 `MIDTERM_MIN_SCORE`）。
- 条目数超过 `MIDTERM_CAPACITY` → 最旧条目被丢弃。

这类断言不依赖 stub LLM，能覆盖阈值/排序/FIFO 三个纯逻辑点。

## 11. Phase 8：文档同步

### 11.1 `docs/DESIGN.md`

- **§6.3 记忆流 / 事件入忆规则**：改成三层描述；删除“窗口满 → event 落库”的表述；明确中期不落库、长期进 `longterm_memory`。
- **§6.4 RAG 检索**：补充中期线索检索；明确 Chroma 只检索 `memory`，长期记忆暂不参与。
- **§6.6 对话流程**：把“事件入忆（固定窗口批总结，不逐轮）”整段替换为“三层记忆与批总结”，包含：
  - 短期窗口容量 10、平铺最近 6 轮；
  - 中期 summary+线索、内存上限 10、线索向量匹配、独立 prompt 板块；
  - 长期每 36 轮原始对话、后台异步、`longterm_memory`、完全隔离；
  - 失败兜底与重启语义。
- **§7.5 并发控制**：注明 `dialogue_summary` 前台为 0，长期后台显式 3。
- **§8 数据模型**：新增 `longterm_memory` 表；更新 `conversation` 注释；注明中期是运行时内存、不在 SQLite。
- **§14 可配参数**：新增 4 个参数；更新 `CONVERSATION_WINDOW_CAPACITY` 说明。
- **§15 风险**：补充“中期重启丢失”“长期尚未接入对话”的已接受取舍。

### 11.2 `AGENTS.md`

- 架构约束 #11 整条重写为三层记忆链路。
- “已知坑”中旧的「窗口批总结旧链路已移除」条目改写为：
  - 满窗只生成中期内存条目，**不得**恢复 `kind=event` 每 10 轮入库；
  - 长期只写 `longterm_memory`，**不得**写 `memory`/Chroma；
  - 中期与 36 轮缓冲重启清零；
  - 中期/长期都复用 `dialogue_summary`，长期调用显式 `priority=3`。
- 配置段落补 `MIDTERM_CAPACITY` / `MIDTERM_TOP_K` / `MIDTERM_MIN_SCORE` / `LONGTERM_TURN_INTERVAL`。
- 目录说明更新 `memory.py` / `summary.py` 的职责。
- 在“里程碑的执行前必读”附近加入本计划文档链接。

### 11.3 `server/.env.example`

按 §4.3 更新。

## 12. 验证步骤

1. 后端冒烟：

   ```bash
   cd /home/behd/projects/Xia/server
   .venv/bin/python tests/smoke.py
   ```

   必须全绿，重点看 §10.2/10.3/10.4 的断言。

2. Godot 冒烟（无客户端改动，验证 SSE/交互无回归）：

   ```bash
   cd /home/behd/projects/Xia
   server/.venv/bin/python xia-ai-town/tools/smoke_test.py
   ```

   如 `godot` 不在 PATH，设 `GODOT_BIN=/home/behd/.local/bin/godot`。

3. 真实 Ollama 手测：

   - 启动后端（记得 `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`）。
   - 对同一 NPC 连说 11 轮；`GET /api/debug/npc/{id}` 应出现 1 条 `midterm`，`memory`/Chroma 无新增。
   - 第 12 轮说一句包含中期线索的话；日志/ Langfuse 中确认 prompt 出现“你想起一些旧事”板块。
   - 继续到 36 轮；几秒后 `GET /api/debug/npc/{id}` 的 `longterm` 出现 1 条，`longterm_pending_turns == 0`；`server/data/xia.log` 有 `longterm memory stored`。
   - 重启后端；`midterm` 为空、`longterm` 仍在。
   - 调 `MIDTERM_MIN_SCORE` / `MIDTERM_TOP_K` 观察误召回与漏召回，确定最终默认值。

4. 本地 curl 一律加 `--noproxy '*'`；测试/冒烟一律 `SCHEDULER_ENABLED=false`。

## 13. 提交建议

沿用 `docs/M5-PLAN.md` 的分阶段提交方式，便于回滚与 review：

1. `feat: 短期窗口溢出改为中期内存摘要`
   - config / schema / `memory.py` 中期结构 / `summary.py` 中期总结 / 窗口改造 / `llm.py` 注释。
2. `feat: 每36轮后台生成长期记忆并独立入库`
   - 长期缓冲、后台任务、`longterm_memory`、drain/reset、debug 接口。
3. `feat: 对话按线索注入中期记忆参考块`
   - `rag.embed_query`、`dialogue` prompt 板块、trace。
4. `test/docs: 覆盖记忆分层链路并同步文档`
   - smoke 断言、DESIGN/AGENTS/.env.example 同步。

每个 commit 都要跑 `server/.venv/bin/python server/tests/smoke.py`；第 4 个 commit 额外跑 Godot 冒烟。

## 14. 边界与已知取舍

- **中期重启清零**：短期窗口（SQLite）与长期记忆（SQLite）保留；中期与 36 轮缓冲丢失，重启后从 0 累计。这是“中期不入库”的直接代价。
- **长期总结异步**：进程在总结完成前退出会丢这一批长期记忆；36 轮原文本来就在内存，重启同样会丢，接受。
- **中期上限 10**：最旧的约 100 轮会从参考池淘汰；长期记忆尚未接入对话，因此更早的旧事在对话中不可见——这是本期“长期暂不接入”的预期状态。
- **长期完全隔离**：不入 `memory`、不入 Chroma、不进反思/关系/八卦；后续要接入对话时，必须新开任务设计检索方式（从 `longterm_memory` 读 or 重新嵌入），不要直接把 `kind=long_term` 塞进 `memory`。
- **线索质量依赖 4B 模型**：线索可能过泛或过窄；`MIDTERM_MIN_SCORE` 与 `MIDTERM_TOP_K` 先给默认值，实测后再调。中期总结失败时 `clues=[]`，该条不参与匹配但仍在 debug 可见。
- **36 轮与 10 轮窗口不对齐**：长期总结包含窗口内最后几轮，中期批次与长期批次会有少量重叠（如 31–36 轮既在长期总结里，又在第 41 轮的中期总结里）。这是两层用途不同导致的正常重叠，不需要强行对齐。
- **送礼/切磋计入 36 轮**：它们和普通对话共用窗口，因此也计入长期缓冲；长期 summary 会自然包含送礼/切磋。
- **旧库已有数据不迁移**：旧版窗口总结产生的 `memory.kind=event` 行原样保留（不做删除/改 kind，避免误伤大额消费/战斗目击等其它 event）。想要完全新口径，用 `RESET_ON_START=true` 重开一局。

## 15. 开放项（不阻塞实施）

- 是否在 Godot 调试面板展示中期/长期（本期只做服务端 debug 接口）。
- 长期记忆接入对话的检索方式（向量/RAG vs 常驻最近 N 条）留到下一任务。
- 真实模型下的 `MIDTERM_MIN_SCORE` 默认值可能需要实测调整。
