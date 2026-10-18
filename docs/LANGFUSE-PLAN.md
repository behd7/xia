# Langfuse 观测体系接入 — 实施拆解

> 目的：给《侠》的 LLM/NPC 链路引入 langfuse 全链路观测（trace / span / generation），
> 复现「为什么这个 NPC 这么回」：检索出的记忆 + 组装出的 prompt + 生成原始输出 + 重试/兜底轨迹。
> 交付物是一份可无脑照做的改造合同。改动以 **自托管 self-hosted**（v1 Basic 鉴权）为目标，
> 观测范围 = **对话「检索→组装→生成」整条链路 + OllamaClient.chat/embed 全局咽喉点**。

- **验收**：开一条 `/api/npc/{id}/talk`，langfuse 里出现一条 name=`dialogue` 的 trace，含嵌套
  `retrieve` span（top6 记忆内容+score）、`recent_turns` span、`generate` generation（model/temperature/
  用法/延迟/attempt）、以及关联 npc/player 的 tags 与好感前后/兜底开关元数据。enabled=false 时零副作用。
- **范围边界**：只做对话为主 + 全局 LLM 打点。拜师/送礼/切磋留轻量 trace（不含规则细节）。反射/八卦/搭话（M4）
  靠 OllamaClient 咽喉点自动获得 generation 级观测，trace 外壳待 M4 各任务再包。

---

## 0. 现状复用清单（已在代码里，不要重做）

| 现状 | 位置 | 说明 |
|---|---|---|
| LLM 全局咽喉点 | `llm.py` `OllamaClient.chat/embed` | 所有 LLM 往返唯一入口（M3 起对话/反思都走它）；`finally` 已落 `llm_log`（best-effort try/except） |
| 对话链路 | `agent/dialogue.py` `talk()` :72 | `retrieve` + `recent_turns` 并行 → `_build_messages` → `chat`(0.7) 校验→重试(0.3)→兜底 → `add_memory` |
| 检索重排 | `agent/rag.py` `retrieve()` :32 | 返回带 `score/memory_id/ts_game/importance/poi/content` 的 top6，正好是 trace span 的 payload |
| 配置 | `config.py` Settings | pydantic-settings 读 `server/.env`，`llm_concurrency/max_dialogue_delta/rag_*` 已落 |
| 帧内注入模式 | `main.py` lifespan | `battle_mod.mem_store = mem_store` 同类，trace 客户端照此注入 |
| 依赖注入链 | `main.py` 全局 `ollama`(24)、`db`(23) | trace 客户端在 lifespan 建、shutdown 关 |

## 1. 关键架构决策（先想清楚为什么）

1. **fail-closed 默认关闭**：`langfuse_enabled: bool = False`（.env 真值）。langfuse 不可达/没配时，
   对话照常、零崩溃、零额外延迟。所有 langfuse 调用套 `try/except`，与 `llm_log` 同级 best-effort
   （AGENTS.md #4/#6 精神：观测不能阻塞玩法）。
2. **AsyncLangfuse、不阻塞事件循环**：用 async 客户端；`chat/embed` 内打点不发 `await flush`，靠 SDK
   后台缓冲发送；`flush` 只在 lifespan shutdown 与可选周期任务里 `try/except` 掉，避免给 `/talk` 加延迟。
3. **单点打点（OllamaClient）覆盖所有 LLM 调用**：M4 的反思/八卦/搭话都调同一个 `chat/embed`，
   在这儿打 generation 一次到位，不要求各任务各自埋点。
4. **对话层只加一把「父 trace」**：`dialogue.talk` 开一个 trace 当根（输入=玩家话，tags=npc/player），
   检索/最近对话/写记忆用 span 包，`chat` 由第 3 点的 generation 自动挂进当前 trace 上下文。
   保证「父-子」关系（trace 根 → span → generation）。
5. **langfuse 是加法，不动 llm_log**：llm_log 继续作本地 SSOT（离线可查、确定性），langfuse 是可视化旁路。
   两者并存、互不影响。无 DB schema 迁移。
6. **自动鉴权用 v1 Basic**：自托管即 `host + public_key(用户名) + secret_key(密码)`；host 默认 `http://127.0.0.1:3000`。
   实现时以安装到的 SDK 版本精确签名为准（见 §6 注意事项）。

## 2. 需求拆解（四块）

### 2.1 依赖与配置
- `requirements.txt` 加 `langfuse`（会拉 httpx，已存在）。
- `config.py` Settings 新增（默认 fail-closed）：
  ```python
  # Langfuse observability (additive to llm_log; off by default)
  langfuse_enabled: bool = False
  langfuse_host: str = "http://127.0.0.1:3000"
  langfuse_public_key: str = ""
  langfuse_secret_key: str = ""
  langfuse_environment: str = "dev"
  ```
- `server/.env` 追加（gitignore，不入库）：
  ```
  LANGFUSE_ENABLED=true
  LANGFUSE_HOST=http://127.0.0.1:3000
  LANGFUSE_PUBLIC_KEY=...
  LANGFUSE_SECRET_KEY=...
  ```

### 2.2 观测结合层 `app/agent/trace.py`（新）
集中管理 AsyncLangfuse 生命周期与 `fail-closed`，避免各 caller 各自 import langfuse：
- 模块级 `_lf: AsyncLangfuse | None`；`trace_enabled() -> bool` 读 settings。
- `setup()`：若 enabled，`AsyncLangfuse(public_key=, secret_key=, host=, environment=)` 存全局；
  不可用/异常则置 None（fail-closed）。在 main lifespan 调用。
- `close()`：`try: await _lf.flush(); await _lf.shutdown() except: pass`；lifespan shutdown 调用。
- 两个 helper（内部 try/except，返回 None 或空 handle，绝不抛）：
  - `async def trace_turn(purpose, input_, *, tags=None, metadata=None)` → trace 根（None 时返回 None）。
  - `async def span(trace, name, *, input=None, output=None, metadata=None)`（None 时 no-op）。
- 导出 `langfuse`（None 安全 getter）供 llm.py / dialogue.py 使用。

### 2.3 OllamaClient 全局打点（`llm.py` chat/embed）
- `chat()`：若 `trace_enabled()` 且当前在 trace 上下文里→发一个 **generation**：
  ```python
  obs = trace.generation(name=purpose, model=settings.chat_model,
      model_parameters={"temperature": temperature, "think": False},
      input=messages, output=text, metadata={**metadata or {}, "json_format": json_format},
      usage={"input": in_tok, "output": out_tok})
  ```
  可选 `metadata: dict | None = None` 形参（对话层用它带 attempt 序号）；不破坏现有调用。
- `embed()`：同判断，发 span（input=texts, output=dim+norm 提示）。
- 全部套 `try/except: pass`；打点不影响返回值。

### 2.4 对话「检索→组装→生成」整条 trace（`dialogue.talk`）
在 `talk()` 首部开根 trace，节点按步骤 span：
1. `trace = await trace_turn("dialogue", input=message, tags=["npc:{name}","npc_id:{id}","player:{p.name}"] , metadata={"affinity_before": current_affinity, "place": place, "rag_top_k": settings.rag_top_k})`。
2. `retrieve` 结果包 `await span(trace, "retrieve", input=message, output=[{memory_id, content, score, importance, poi, kind} for m in memories], metadata={"n_results": 20, "rerank": "w_rel/w_rec/w_imp"})`。
3. `recent_turns` 包 `await span(trace, "recent_turns", output=turns)`。
4. `chat` 自动 generation（见 2.3），对话层在重试循环里给 `chat(..., metadata={"attempt": i, "temperature": temp})`，
   并在每次 parse 成败时把 attempt 序号挂到当次 generation 的 metadata。
5. 结果：`await span(trace, "add_memory", input=result["event"], output=mid, metadata={"importance": result["importance"], "kind":"event"})`。
6. 结 trace：`await span(trace, "outcome", metadata={"affinity_after": new_affinity, "fallback_used": result is fallback, "attempts": n, "end": result["end"]})`。

### 2.5 拜师/送礼轻量 trace（`agent/npc.py`，可选但推荐）
- `apprentice()` / `gift()` 各开 name=`apprentice`/`gift` 的 trace，input=请求，output=reply，
  tags=npc/player，metadata={affinity_before, affinity_after}。战斗(切磋)与商店暂不打总 trace，
  其对话/写入若产生 LLM 调用仍会被 2.3 咽喉点覆盖。

### 2.6 接线（`main.py` lifespan）
- import `from .agent import trace as trace_mod`；`await trace_mod.setup()`（yield 前）；
- shutdown 段：`await trace_mod.close()`（在 `ollama.close()` / `db.close()` 前）。

---

## 近期的关卡（避免踩坑）
- WSL/自托管在 `127.0.0.1:3000` → 受 `NO_PROXY=localhost,127.0.0.1` 保护，不会误进 7897 代理（AGENTS 已知坑）。
- langfuse SDK 不同版本 `generation()` 的参数名（`model_parameters` vs `model` + `metadata`、`usage` 字典键）有差异；
  实现时以包内 `inspect.signature` 为准，别硬背。
- `AsyncLangfuse` 是 async；`flush/shutdown` 必须 await 且在 lifespan，别在热路径 `await flush()`。
- langfuse 发送失败只写一条 `llm_log`（或直接忽略），绝不能把异常冒到 `/talk`（AGENTS #4）。

---

## 4. 数据模型 → langfuse 映射

| 我们的对象 | langfuse 类型 | 关键属性 |
|---|---|---|
| 一次对话回合 | Trace | name=`dialogue`, input, tags(玩家/职位/NPC), 结 trace 时写 outcome metadata |
| 检索 top6 / 最近对话 | Span (子) | name=`retrieve`/`recent_turns`, input/output=记忆列表, metadata=重排权重 |
| 一次 chat 调用 | Generation (孙) | model, input=messages(即完整 prompt), output, usage, metadata={temperature, attempt, json_format} |
| embed 调用 | Span | input=texts, output=归一化向量维数 |
| 拜师/送礼 | Trace | name=`apprentice`/`gift`, input, output, metadata=好感前后 |

---

## 5. 文件落点总表

| 文件 | 改动 |
|---|---|
| `agent/trace.py`（新） | AsyncLangfuse 生命周期 + `setup/close` + `trace_turn/span` 助手（全 best-effort） |
| `llm.py` | `chat/embed` 加 metadata 可选参 + generation/span 打点 |
| `agent/dialogue.py` | `talk()` 开根 trace，包 retrieve/recent_turns/add_memory/outcome span，重试 attempt 元数据 |
| `agent/npc.py` | `apprentice/gift` 轻量 trace |
| `config.py` | `langfuse_*` 字段（默认 off/fail-closed） |
| `main.py` | lifespan 调 `trace_mod.setup()/close()` |
| `requirements.txt` | + `langfuse` |

## 6. 验证计划

**前置**：`cd server && uv pip install -r requirements.txt`；跑回归需真后端 + Ollama（NO_PROXY 前缀启动，AGENTS）。

**A. enabled=false 回归（必过，默认路径）**
1. 不设 langfuse 相关 .env（或显式 `LANGFUSE_ENABLED=false`）启动；
2. `POST /api/npc/{id}/talk {"message":"..."}` → 200，`llm_log` 有 dialogue/embed 行；
3. 无任何 langfuse import/初始化报错；行为与改造前逐字节不变。

**B. 自托管联调（enabled=true）**
1. 起 langfuse（自托管 docker compose，host 127.0.0.1:3000，配 Postgres/Redis，定 Basic 账号）；例：
   `docker compose up -d`（附官方 quickstart compose，实现时只做占位说明，不拷大段）。
2. `server/.env` 设 `LANGFUSE_ENABLED=true + HOST + PUBLIC_KEY + SECRET_KEY`；
3. 启动，`POST /talk`；langfuse UI/API 查询该项目应见一条 `dialogue` trace：
   - tags 含 npc/player；嵌套 `retrieve`(top6 记忆+score) → `recent_turns` → `generate`(model/temperature/usage)；
   - 重试场景（故意差 message 提高 parse 失败率）：生成含 attempt=0/1 两节点或 metadata 标 attempt；
   - outcome metadata 含 affinity_before/after、fallback_used、attempts。

**C. 故障注入（fail-closed）**
1. 置 `LANGFUSE_ENABLED=true` 但 HOST 指向死端口（如 `http://127.0.0.1:9`）；
2. `/talk` 仍 200、`llm_log` 正常；请求延迟不显著增大；无 traceback 刷后端日志。

---

## 7. 明确不做（防 scope 蔓延）
- 不迁移/删除 `llm_log`（保留为本地 SSOT）。
- 不做 UI/网页嵌入；观测只看 langfuse 控制台。
- 不把检索嵌入向量也打点（噪声）；只打 query 与记忆写入向量。
- 反射/八卦/主动搭话（M4）的 trace 外壳留到各任务实现时再包（generation 已由咽喉点覆盖）。
- 不做 trace 采样/配额管理（本地实验量小，暂不需要）。

## 8. 原子提交建议（照 AGENTS：一个任务一 commit，正文三段式）
1. `feat: langfuse 依赖与配置接入 + AsyncLangfuse 生命周期（trace.py/config/requirements/main）`
2. `feat: OllamaClient chat/embed 全局打点到 langfuse generation`
3. `feat: 对话「检索→组装→生成」链路 langfuse trace（retrieve/组装/重试/兜底/好感元数据）`
4. `feat: 拜师/送礼轻量 langfuse trace`
5. `docs: DESIGN.md §7.3 观测章节同步 langfuse`