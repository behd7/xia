# AGENTS.md

《侠》是 Godot 像素武侠 RPG。本指南只保留当前前端开发所需约定。

## 工作范围

- **当前只开发前端**：默认修改 `xia-ai-town/` 与直接相关文档；不修改 `server/` 业务、数据库、LLM/记忆系统或配置。后端视为现成接口服务，必要时只读查阅接口；遇到接口缺失/不一致先报告，不擅自改后端或在前端伪造结算。
- 技术栈固定 **Godot 4 + GDScript**；旧 `client/` 已删除，不恢复或引用旧实现。
- 设计查 [`docs/DESIGN.md`](docs/DESIGN.md) 对应章节，前端重点为地图 §4、UI §10、美术 §11；API 以 `server/` 实际实现为准。只读本任务相关内容，不默认展开后端历史计划。
- HD-2D 按 [`docs/HD2D-PLAN.md`](docs/HD2D-PLAN.md) 推进；P1 样板已获用户认可，按锁定规格推进 P2 正式接入及 P3–P5；默认入口暂留 2D，HD-2D 完整客户端走独立入口，P5 完整验收后再切默认，不顺带做多地图。

## 代码与资源入口

以下路径均相对 `xia-ai-town/`：

| 路径 | 职责 |
|---|---|
| `project.godot` / `scenes/main.tscn` | 工程配置与主场景 |
| `scripts/main.gd` | HUD、面板、输入、交互、战斗、状态同步 |
| `scripts/api.gd` | 普通 HTTP 与 SSE 流式对话 |
| `scripts/town_world.gd` / `scripts/town_layout.gd` / `scripts/town_canvas.gd` | 二维状态/日程、共用布局、独立旧 2D 绘图；HD-2D 不创建旧画布 |
| `scripts/town_map.gd` / `scripts/ui_kit.gd` | 小地图/舆图、纸墨 UI 组件 |
| `asserts/` → `tools/build_art_assets.py` → `assets/` | 源图、切图脚本、生成贴图 |
| `tools/smoke_test.py` / `tools/smoke_test.gd` | 隔离真实后端的客户端冒烟 |
| `scenes/hd2d/game.tscn` / `scripts/hd2d/world_presenter.gd` | P2 完整客户端 HD-2D 入口、原生子视口及世界标签；仍接真实 API |
| `tools/build_hd2d_environment.py` / `tools/build_hd2d_scenes.gd` | P3六栋新建筑／全套立体家具的Blender重建与37个GLB清单；`tools/hd2d/`仅离线固化原布局，运行不加载构建器 |
| `scenes/hd2d/town_view.tscn` / `town_scenery.tscn` / `actor.tscn` / `lighting.tscn` | 可编辑固定3D层级／布景／人物组件／灯光；动态状态绑定仍在脚本，静态资源与重建覆盖边界见HD2D-PLAN §6.2 |
| `scenes/hd2d/prototype.tscn` / `tools/hd2d_smoke.py` | 已认可P1离线样板；`--background --environment-probe`测P3环境，`--background --game`测完整客户端 |

## 必须保留的前端契约

1. **服务器权威**：属性、背包、战斗、好感等以响应为准；客户端只做输入和表现。玩家移动本地推演，交互上报原二维 `x/y`。保留状态版本保护与断线后的战局恢复。
2. **坐标不变**：世界 `1600×960`、逻辑瓦片 16px；旧人物帧 32px 只是当前资源规格，不限制 HD-2D 新素材。建筑是实心区域，门口交互按原占地外扩 32px；保留脚部碰撞、可走落点修正和怪物可达性，不能用视觉模型尺寸替代玩法判定。
3. **NPC 零位置轮询**：按服务端下发的日程、偏离与时钟参数本地推算；不改变两端一致的算法/常量，不增加坐标同步。对话中冻结当前 NPC，关闭后恢复。请求使用服务端数字 NPC id，不拿 slug 或列表下标代替。
4. **时间与连接**：游戏时间由服务端参数本地续推；气泡到期用 `Time.get_ticks_msec()`，不与 epoch 时间互比。只有 `/api/game/state` 轮询失败才标记离线；单次对话/送礼/切磋超时只提示该操作失败。
5. **对话必须流式**：`POST /api/npc/{id}/talk` 用 `api.gd::stream_request`，处理 `start → token* → done` 及重试前的 `reset`；不能改用会缓冲整包的 `HTTPRequest`。保留空回复、动作气泡和现有 stale 处理，不增加 LLM 调用。送礼/切磋为普通 POST，必须保留 busy、等待指示及错误反馈。
6. **原生分辨率**：`stretch/mode=disabled`；世界缩放保留 1×/2×/3×，HUD/中文不随世界缩放，禁止整帧拉伸。人物优先 nearest，斜面过滤/mipmap 实测调优；清晰与运动稳定优先，不承诺斜面像素等宽方正。
7. **资源可重建**：不手改 `assets/` PNG；改源图或切图脚本。需要新图片时，先在 [`docs/ART-PROMPTS.md`](docs/ART-PROMPTS.md) 写提示词与存放路径；默认由用户生成，本轮已明确授权使用 pi-image-gen 制作 P1 样板。P1 样板现已认可，后续按锁定规格与阶段清单制作、逐项检查素材。不复制参考游戏素材。

## HD-2D 开发边界

- 采用 **3D 几何 + 全新细像素素材**，自然武侠/江南山麓风格；旧世界图只占位，UI 保留。人形约 4～5 头身，玩家/NPC/怪物八向待机与行走；先一名完整角色 + 房屋/道路/树/灯，用户认可后量产。技术参数由助手实测调优，不擅自削功能或放宽目标。
- P1 采用透视相机，默认垂直FOV35°／俯角38°／方位30°；支持30°–40°FOV、30°–65°俯角与360°左右旋转，正交仅保留对照入口；样板已认可，P2 沿用同一规格接入。1×/2×/3×按玩家脚点深度校准，远处自然缩小；人物/灯笼用 `BILLBOARD_FIXED_Y`，高度按 `1/cos(pitch)` 做竖直平面补偿，脚点沿世界 UP 对齐，遮挡射线随透视收束。禁止全轴 billboard 或沿 `camera.up` 抬中心；新人物源图尺寸与视觉高度分开定义。P1/P2 移动相对当前镜头，转镜头保留停步朝向；树干/体积叶簇保持固定世界几何。
- 本轮用户将遮挡改为**局部柔边虚化**：仅玩家/当前目标前方建筑或布景覆盖人物全身包络的部分逐渐透明，同一构件其余位置保持正常。保留双目标独立遮罩、全部前方层、双面射线、平滑进出与恢复迟滞；不让普通 NPC 触发、不关角色深度测试。树按本轮要求重做为有根盘/弯折/锥缩的树干与闭合松针叶簇，取消整树交叉卡片；像素树皮独立于建筑木材，保留昼夜/风动/实际投影和局部虚化。P1 可在原1600×960内清除无可见几何的旧障碍来扩充活动空地，正式玩法碰撞/日程不变。样板认可后推进正式接入；输入链路变化须专项验证真实 IME，P5 长测另列。
- 计划用 Forward+，两档共用立体场景；Linux 首验，实际 1440×900、2×下，4050 标准档/780M 轻量档均目标稳定 60 FPS。子视口原生尺寸、HUD 独立；工程默认仍为 Compatibility；P2 `game.tscn` 用 Forward+ 启动，P5 后再改默认。资源、GPU 探针和通过门槛见 HD2D-PLAN。

## 运行与验证

```bash
# 根目录启动；也可在 Godot 导入工程后 F5
godot --path xia-ai-town

# 完整 HD-2D 客户端；P3环境已接入，NPC/怪物的新八向图集仍待制作
godot --path xia-ai-town --rendering-method forward_plus --rendering-driver vulkan \
  --display-driver x11 --disable-vsync --max-fps 60 res://scenes/hd2d/game.tscn

# 客户端隔离冒烟；Godot 不在 PATH 时设置 GODOT_BIN
SCHEDULER_ENABLED=false server/.venv/bin/python xia-ai-town/tools/smoke_test.py
```

- 默认 API：`http://127.0.0.1:8080`，可在设置面板保存或用 `XIA_API_URL` 覆盖。8000 是 Godot AI 插件端口，不是游戏后端；Windows 客户端连接 WSL 后端时使用可达的 WSL 地址。
- 纯前端工作不默认安装/调优 Ollama、改 `.env` 或跑全套后端回归。冒烟使用临时库，`SCHEDULER_ENABLED=false`，禁止清空真实存档。Python 环境使用现有 `server/.venv`，需要补依赖时用 uv，不用 `python3 -m venv`。
- 画面改动必须真实 GPU 验证：行走、缩放、遮挡、实际鼠标点击、中文输入、对话和战斗；headless 通过不等于画面正确。性能看持续实际交互的帧时间/内存，不用加载后退出替代。报告实际窗口尺寸、GPU、画质和未验证项。
- Mango Wayland 会平铺窗口：需要固定尺寸时，只对自己启动的窗口用 `mmsg` 切浮动，不改全局布局。
- 排错先看 Godot `[xia/api]` / `[xia/main]` 与 `server/data/xia.log`；本地 curl 用 `--noproxy '*'`，避免代理误报。

## 协作与提交

- 不使用子代理。优先局部修改、保留现有功能；需求不明确且无法推进时再询问。
- 中文沟通与 UI 文案；代码标识符遵循现有风格。行为/架构变化同步相关设计文档，不另建任务日志或变更记录文件。
- **不自动 `git add`，由用户决定暂存**。仅在本任务已由用户暂存且验证通过后按约定提交，不夹带其他改动；提交前检查 `git status`。`.env`、存档、`.godot/`、源图和临时截图不入库；不擅改 `addons/` 插件。
- 提交格式保留：conventional commit 前缀 + 中文标题，正文说明做了什么、怎么做、为什么这样做。

```text
feat: 中文任务标题

做了什么：交付结果
怎么做的：关键实现
为什么这样做：取舍与原因
```
