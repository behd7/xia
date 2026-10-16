# 侠 — 二维像素武侠 + 生成式 NPC 实验游戏

设计文档（SSOT）：[`docs/DESIGN.md`](docs/DESIGN.md)。当前进度：**M1 战斗循环**（野怪/回合制战斗/药剂/商店/存档/死亡复活，浏览器实测通过完整打狼+买药+战败复活流程）。

## 环境要求

- Python 3.12+（推荐用 [uv](https://docs.astral.sh/uv/) 管理虚拟环境）
- Node 20+
- 本地 [Ollama](https://ollama.com/)，提前拉取两个模型：

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull qwen3-embedding:0.6b-q8_0
```

模型 tag 走 `server/.env`（模板见 `server/.env.example`），代码不写死。

## 运行

启动 server（默认 8000 端口）：

```bash
cd server
uv venv .venv && uv pip install -r requirements.txt
cp .env.example .env   # 首次：按本机 ollama list 修改模型 tag
.venv/bin/uvicorn app.main:app --reload --port 8000
```

启动 client（默认 5173 端口，`/api` 自动代理到 8000）：

```bash
cd client
npm install
npm run dev
```

浏览器打开 <http://localhost:5173>：占位地图（清溪镇 / 镇墙镇门 / 东山林地 / 后山崖），WASD 或方向键移动，相机跟随；HUD 显示游戏时辰、当前地点、服务器与 Ollama 状态。

## M0 验收自查

- `GET http://localhost:8000/api/health` → 服务器、SQLite、Ollama 连通性与两个模型是否就绪；
- `GET http://localhost:8000/api/game/state` → 游戏时钟（1 现实秒 = 1 游戏分，重启续走）；
- 浏览器里小人满地图跑。

## M1 玩法速览

- 首次进入弹出取名开档（`POST /api/game/new`，单存档）；
- WASD 走到东山林地/后山崖，撞上野怪（野狼×3 / 山贼×3 / 头目黑旋风）进入回合制战斗：攻击 / 药剂（每场上限 3 瓶）/ 逃走，技能 M2 解锁；
- 胜利得经验/银两/掉落，死亡回客栈复活（气血内力减半、损失一成银两）；
- 药铺（陈半松）门口按 E 买药剂，铁匠铺（王大柱）售装备（装备佩戴 M2）。

## 目录速览

```
client/   Vite + Phaser 3 + TS（scenes / systems / ui / api，占位资产由 tools 脚本生成）
server/   FastAPI 单进程（app: config / db / llm / game.clock；data/ 为 SQLite + ChromaDB，gitignore）
docs/     DESIGN.md 设计 SSOT
```
