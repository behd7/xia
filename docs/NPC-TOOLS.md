# NPC 对话：普通输出 + 原生工具

## 范围

2026-11-12 将 NPC **普通对话、送礼感受、切磋感受、主动搭话、动作失败改口**从强制 JSON 输出迁移到普通文本 + 原生 function calling。客户端 SSE/HTTP 契约、数据库结构、游戏结算和后台记忆任务不变。

- `server/app/llm.py`：Ollama 与 OpenAI 兼容接口的流式/非流式原生工具传输。
- `server/app/llm_tools.py`：完整调用和分片的归一化、参数缓冲。
- `server/app/agent/dialogue_tools.py`：七个工具定义及向旧结算格式的映射。
- `server/app/agent/dialogue.py` / `greet.py`：调用新协议，继续走既有结算。
- `server/app/agent/actions.py`：四个世界动作的校验与执行逻辑没有变化，只移走旧 JSON 提示词。

## 新旧映射

| 旧模型输出 | 新模型输出 | 效果 |
|---|---|---|
| `reply` | 普通 assistant 文本或 `reply(text)` | 显示回复，写同一对话账本 |
| `affinity_delta` | `change_affinity(delta)` | 仍由 `npc.apply_delta` 更新；默认单轮 ±3，再 clamp 到 0–100 |
| `end=true` | `end_dialogue()` | 恢复暂停、清当前议程；低好感时沿用原情绪规则 |
| `acts[].tool=give_item` | `give_item(item,count)` | 从 NPC 背包转移给玩家，默认 1、上限 3 |
| `acts[].tool=take_item` | `take_item(item,count)` | 原库存及好感门槛不变 |
| `acts[].tool=teach_art` | `teach_art(art)` | 原功法、好感、同源、不压级等条件不变 |
| `acts[].tool=refuse` | `refuse(reason)` | 原拒绝动作、文字、记忆和事件不变 |

例如原来模型正文必须是：

```json
{"reply":"拿着，路上当心。","affinity_delta":2,"end":true,"acts":[{"tool":"give_item","args":{"item":"小回春散","count":1}}]}
```

现在可以输出普通话语 `拿着，路上当心。`，同时返回原生调用：

```json
[
  {"type":"function","function":{"name":"change_affinity","arguments":"{\"delta\":2}"}},
  {"type":"function","function":{"name":"end_dialogue","arguments":"{}"}},
  {"type":"function","function":{"name":"give_item","arguments":"{\"item\":\"小回春散\",\"count\":1}"}}
]
```

也可用 `reply({"text":"拿着，路上当心。"})` 显式回复。**这里只是工具传输格式示意，不是要求模型把整段对话写成 JSON**；请求不发送 `format:"json"` / `response_format:json_object`。工具参数仍遵守原生协议的 JSON 格式。

## 一致性与安全规则

1. 完整接收并校验所有调用，才进入原有结算链；工具参数分片到达时不转移物品、不更新好感。
2. 三个控制工具各至多一次，重复或错误类型触发生成重试，防止多次好感累加。控制工具不占世界动作每轮最多两个的额度。
3. 不调用关系/结束工具时，默认 `delta=0`、`end=false`。允许只行动不说话；回复和世界动作都空时仍判失败。
4. 普通文本和显式回复同时出现时以 `reply` 为准，必要时发 `reset`，最终可见文本和 `done.reply` 一致，不重复展示。
5. 工具参数/流失败仍是重试一次（temperature 0.3）→ 模板兜底，零动作、零好感变化；副作用不进入重试路径。
6. 世界动作仍先全量做合法性校验；不合法则整批不执行，只改口一次。**保留原来的独立结算语义**：世界动作被拒不撤销本轮关系变化或结束意图。
7. 世界动作仍按本轮开始时的好感判断，不允许靠本轮增加的好感提前跨过取物/传功门槛；幂等、generation 失效保护、动作即时入忆和附近事件保持原样。
8. 搭话和改口只开放 `reply`；不执行其他工具。搭话仍不改好感，阈值、暂停、冷却与返回结构不变。
9. 原生工具成功后不额外请求模型作“最终回复”。反思、关系摘要、八卦、心事、中期/长期总结继续用 JSON，不改变 LLM purpose、队列优先级、预算或后台规则。

## 验证

在仓库根目录运行，无需在线模型，使用独立临时 SQLite / Chroma，不触碰现有存档：

```bash
server/.venv/bin/python server/tests/smoke.py
server/.venv/bin/python server/tests/npc_tools.py
```

本轮结果：

- 迁移前和迁移后的全量 `smoke.py` 均 **PASS**（本轮最终 **237 项检查通过**）；迁移后的 stub 返回原生工具，而非旧 JSON 模型正文。
- `npc_tools.py` 的 **13 个测试通过**，其中数据库对照包含 **16 个场景**：回复、上下界限幅/结束、给物、取物、传功、沉默、双动作、非法整批、低好感、关系变化不能绕过门槛、数量上限、三动作拒绝、未知动作、送礼、切磋。
- 对照使用**测试专用旧 JSON 解码 oracle**和新工具解码分别进入同一既有结算入口；逐项比较回复、好感、结束、暂停、议程/情绪、玩家与 NPC 背包/功法、动作日志、动作记忆、附近事件和对话账本，不只比较解析结果。
- 验证完整动作后仍有畸形参数时：第一轮失败、第二轮成功，只执行一次；两轮都失败时不执行；generation 失效时不执行且不新增轮号。
- 传输覆盖两种后端、流式/非流式、中文/转义/emoji 分片、调用序号/名称分片、重复控制工具、缺失结束标志、输出截断、流提前关闭，以及 `stream_options` 不支持时保持工具的兼容重试。
- 当前配置的 **`deepseek-v4.1-flash`** 已做真实流式调用，返回 `reply`、`change_affinity(delta=2)`、`end_dialogue`，成功映射为正常回复、`+2` 与结束意图。该能力探针未写入游戏数据库。

**保证边界**：同一动作意图的新旧协议产生相同游戏效果；不承诺更换提示词/输出形式后，随机模型在所有自然语言输入下必然选择完全相同的台词或动作。当前远端模型原生工具已实测，本地 Qwen/Ollama 的真实生成质量尚未实测（传输协议有模拟回归）。不支持 tools 的模型需换成支持的模型，不能静默退回旧 JSON 模式；调用失败仍按原模板兜底。未把这次能力探针当作 Godot 交互体验或延迟验收。
