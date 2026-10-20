# 侠 · 清溪志

二维像素武侠 RPG 与生成式 NPC 实验。当前客户端使用 **Godot 4**，工程位于 [`xia-ai-town/`](xia-ai-town/)，通过 HTTP 连接 [`server/`](server/) 的 FastAPI 后端。旧 `client/`（Phaser 前端）已删除，历史见 git。

设计文档：[`docs/DESIGN.md`](docs/DESIGN.md)。M0–M5 已全部接入：开档、地图探索、成长、装备、商店、拜师、回合制战斗，NPC 对话（记忆检索 RAG）、送礼、切磋与拜师，反思、关系摘要、NPC 间八卦、主动搭话和按游戏时钟确定性推算的 NPC 日程，以及世界公设/断岳刀、对话议程/口癖/轻情绪、每日心事/安全偏离与附近事件感知。客户端每十秒随 `/api/npcs/state` 拉取日程参数，本地推算 NPC 位置，不轮询坐标；好感到达阈值后靠近 NPC 会自动触发搭话气泡。

## 本地运行

需要 Godot 4.4+（已在 4.7.2 验证）、Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

启动后端：

```bash
cd server
uv venv .venv
uv pip install -r requirements.txt
cp .env.example .env  # 首次运行；已有配置时跳过
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  .venv/bin/uvicorn app.main:app --reload --port 8000
```

如果 Godot 跑在 Windows、后端跑在 WSL2，`localhost` 转发偶尔会失效：后端改用 `--host 0.0.0.0` 启动，并在 Godot 设置里填 `http://<WSL-IP>:8000`（WSL 内 `hostname -I` 第一项），必要时在 Windows 防火墙放行该端口。外接 LLM API 默认直连（`LLM_API_TRUST_ENV=false`），不会受 shell 里未启动的 `HTTP(S)_PROXY` 影响；需要代理时再显式开启。若 WSL 内出现偶发连接失败，可检查是否有进程（如 VS Code Server）占用大量到 `127.0.0.1:7897` 的连接并耗尽临时端口，重载 VS Code 窗口即可释放。

在 Godot 项目管理器中导入 `xia-ai-town/project.godot`，按 **F6** 运行 `scenes/main.tscn`，或按 **F5** 运行主场景。也可以在仓库根目录执行：

```bash
godot --path xia-ai-town --editor
# 或直接启动游戏
godot --path xia-ai-town
```

默认后端地址为 `http://127.0.0.1:8000`。界面右上角的「设置」可以更改并保存地址；环境变量 `XIA_API_URL` 可覆盖已保存的配置：

```bash
XIA_API_URL=http://127.0.0.1:8000 godot --path xia-ai-town
```

基础探索与战斗无需 Ollama。NPC 对话、主动搭话、反思、关系摘要与八卦改写需要本地 Ollama 模型；模型 tag 通过 `server/.env` 配置（模板见 `server/.env.example`），不得写死在代码中。后台调度可用 `SCHEDULER_ENABLED=false` 关闭（测试与冒烟默认关闭）。`GET /api/health` 可检查数据库、Ollama 及模型状态。

LLM 调用默认落 `llm_log` 表便于本地排查；如需跨链路可视化（检索的记忆 + 组装的 prompt + 生成输出 + 重试/兜底轨迹），可开启自托管 Langfuse 观测——默认关、fail-closed，在 `server/.env` 设 `LANGFUSE_ENABLED=true` 即可，部署栈见 `server/deploy/langfuse/`，详见 `docs/DESIGN.md §7.4`。

运行日志同时写控制台与 `server/data/xia.log`（滚动，5MB×3），包含启动配置、HTTP 请求状态/耗时、LLM purpose/model/耗时/异常和后台调度结果；`GET /api/health` 会返回 `log_path`。Godot 输出面板以 `[xia/api]` / `[xia/main]` 前缀记录请求耗时、网络错误与连接状态变化。

## 本地停止

- **Godot 客户端**：直接关闭游戏窗口即可退出。
- **后端 uvicorn**：前台跑时按 `Ctrl+C`。若在后台/无头跑，按端口停止——**不要 `pkill -f "uvicorn"`**（会连你自己的命令行一起匹配而自杀），改用：

  ```bash
  fuser -k 8000/tcp          # 默认端口，推荐
  # 或 pkill -f "app[.]main"  # 字符类技巧，避免误杀
  ```

- **Langfuse 观测栈**（若你 `docker compose` 启过 `server/deploy/langfuse/`）：

  ```bash
  docker compose -f server/deploy/langfuse/docker-compose.yml down
  ```

## 操作与玩法

| 操作 | 按键 / 入口 |
|---|---|
| 移动 | WASD / 方向键 |
| 附近交互 | E；铺面、门派或人物附近会显示提示 |
| 角色与属性分配 | I / 底部「角色」 |
| 行囊与装备 | B / 底部「行囊」 |
| 武学、招式装备与修习 | K / 底部「武学」 |
| 大地图 | M / 右侧「展开舆图」 |
| 缩放地图 | 鼠标滚轮 |
| 关闭面板 | Esc；战斗中须结算或选择逃走 |

首次进入输入名号开档，已有存档自动恢复。药铺出售药剂；铁匠铺出售兵器与衣甲，两店均支持半价卖出未装备的物品。走近野怪自动进入 1v1 回合战斗，可普攻、使用已装备的招式、服药或逃走。胜利获得经验、银两与修炼点；战败后在客栈门口恢复探索。等级达到三重且与掌门交情热络（好感≥60）后，在三派驻地按 E 可拜师，一生只入一派。

NPC 按游戏时钟作息：清晨出门、白天在铺面/门派、午间到镇口聚集、傍晚回家，玩家可在人物录查看其当前地点。与 NPC 的好感达到亲近（≥70）后，靠近时会主动搭话，头顶气泡显示台词；对话期间该 NPC 停在原地，关闭对话后回到日程。游戏时钟在本地连续推算，每十秒同步服务器状态；交互时上报玩家位置。属性、物品与战斗完全由后端结算，未完成的战斗可在重新启动后继续。若战斗响应丢失，恢复连接后会同步服务器战局。

## 美术与工程

纸墨色面板、玉绿地图、朱砂点缀。建筑、草木、人物和怪物均由 Godot 原生绘制，无需下载或手动生成图片；中文使用系统宋体 / Noto CJK 字体并启用系统回退。

```text
xia-ai-town/
  project.godot       Godot 配置与主场景入口
  scenes/main.tscn    游戏主场景
  scripts/main.gd    HUD、面板、移动与玩法流程
  scripts/api.gd     HTTP 请求、超时与地址配置
  scripts/ui_kit.gd  统一主题与 UI 组件
  scripts/town_world.gd  确定性地图、碰撞、人物与怪物绘制
  scripts/town_map.gd    小地图与舆图
  tools/             隔离存档的真实接口冒烟验证
  addons/godot_ai/    工程已有的 Godot MCP 插件
server/              FastAPI、SQLite、游戏规则与 NPC 智能体
docs/                设计文档
```

Godot 的 `.godot/` 缓存、Python 虚拟环境、`.env` 和服务器存档均不入库。

## 验证

导入与脚本检查：

```bash
godot --headless --path xia-ai-town --editor --import --quit
godot --headless --path xia-ai-town --check-only --script scripts/main.gd
```

服务端关键路径冒烟使用临时 SQLite/Chroma 并 stub LLM（无需 Ollama），覆盖建档、战斗、对话、反思、关系、八卦与搭话：

```bash
server/.venv/bin/python server/tests/smoke.py
```

完整接口冒烟验证会启动临时端口的真实后端，并使用临时数据库和独立的 Godot 用户目录，不影响现有存档：

```bash
server/.venv/bin/python xia-ai-town/tools/smoke_test.py
```
