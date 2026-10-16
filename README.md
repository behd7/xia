# 侠 — 二维像素武侠 + 生成式 NPC 实验游戏

设计文档（SSOT）：[`docs/DESIGN.md`](docs/DESIGN.md)。当前进度：**M0 脚手架**（client/server 跑通、Ollama 健康检查、占位地图+走动）。

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

## 目录速览

```
client/   Vite + Phaser 3 + TS（scenes / systems / ui / api，占位资产由 tools 脚本生成）
server/   FastAPI 单进程（app: config / db / llm / game.clock；data/ 为 SQLite + ChromaDB，gitignore）
docs/     DESIGN.md 设计 SSOT
```
