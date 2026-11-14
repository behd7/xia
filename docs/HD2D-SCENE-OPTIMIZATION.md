# HD-2D 场景优化计划：基于新增 Godot / game skills 的审查

> 2026-11-13。先完整载入相关 skills，再对照当前代码和 AMD 实际客户端截图重写本计划。
> 在已有场景上局部修改，不重做 UI，不改后端；当前验证只用 Radeon 780M。
> 本文是本轮的新优化计划，历史设计和旧测试留在 [场景调优记录](HD2D-SCENE-TUNING.md)。

## 1. 本轮五项要求

| 要求 | 当前结果 | 代码落点 |
|---|---|---|
| NPC 对齐新地图 | 已实现并通过路线/客户端短测：七名 NPC 使用新地图路线，保留服务端时钟、日程和身份；聚集点、怪物落点及已知出生点同步调整。全 NPC / 全时段远近交互尚未验证 | `scripts/hd2d/scenic_layout.gd`、`scripts/town_world.gd`、`scripts/main.gd` |
| 固定视角 | 完成：正式游戏方位角 **0°**、俯角 **30°**（原最小档），FOV 35°；C、Home、中键拖动不改变视角，滚轮 1×/2×/3× 保留；P1 原型仍可调角度 | `scripts/hd2d/projection.gd`、`town_view.gd`、`world_presenter.gd` 和 `scripts/main.gd` |
| 建筑牌匾 | 完成：剑阳道馆、玄铁堂、烟雨小筑、药铺、铁匠铺、客栈、破庙均有实体木牌与原生中文文字 | `scenic_layout.gd::BUILDING_SIGNS`、`tools/build_hd2d_environment.py`、`tools/hd2d/world_builder.gd` |
| 描边替代遮挡透明 | 完成：建筑、城墙和树木不再因人物被挡而变透明；玩家、当前目标、普通 NPC/怪物独立显示被遮挡区域的描边 | `scripts/hd2d/occlusion.gd`、`actors.gd`、`shaders/actor_outline.gdshader`、`shaders/scenery_common.gdshaderinc` |
| 新 skill 反思与本地计划 | 本文：按证据列出不足、修改顺序、验收方法与未覆盖范围 | `docs/HD2D-SCENE-OPTIMIZATION.md` |

路径未写前缀的脚本、工具均位于 `xia-ai-town/`。

### 已有功能保留

- 玩家 fixed-Y billboard、世界 UP 脚点、八向动画、人物投影和高低地形贴地保持。
- 原生 HUD、舆图、人物名、气泡、商店、对话与战斗保持。门口姓名/气泡若压住牌匾，移到脚点下方，不隐藏人物信息。
- **近镜头背景裁切保留**：它只处理眼前极近的布景，不是人物遮挡触发的建筑透明；不要把两个机制混淆。
- 新牌匾几何由 Blender 生成并导出 GLB；Godot 离线固化摆放、材质、字体、视觉射线。运行时不重新建模。
- 牌匾不是全轴 billboard，也没有关闭深度测试。中文来自 `BUILDINGS.name`，不由 AI 绘字，不维护第二份名称表。
- `Label3D` 使用字体 cutout 写入深度，避免布景的透明排序把文字盖掉。字体选择沿用现有 UI；其他系统的字体回退尚未实测。
- 牌匾宽度、高度和檐外距离单独配置。玄铁堂牌匾提高并移到主檐前，避免前方客栈挡住门口阅读视线。
- 服务端与存档坐标体系不变。界面映射不代表服务器也理解新布局，见下面 R02。

## 2. 实际载入的 skills 与审查依据

项目与本机引擎均为 **Godot 4.7 / 4.7.2**，3D 物理为 Jolt，与这些 Godot skills 的基线一致。先按 `router` 选择与当前场景有关的技能，不加载无关的 Unity、Unreal、联网或存档技能。

以下 **8 项 Godot skills、6 项 game 技能**已完整阅读；另读 `game-docs` 及其文档/证据规则。不是只看名称、简介，或把 skill 当成附在建议后的标签。

| Skill | 使用的规则/章节 | 对应审查项 |
|---|---|---|
| `godot-3d-essentials` | Workflow：先核实运行渲染器、相机、环境；区分真实场景与编辑器预览 | R01 运行入口；R09 光影/物件层级 |
| `godot-nodes-scenes` | Core workflow：场景组合、实例配置时机；新场景/资源的路径与 UID 检查 | R11 静态资源与场景契约 |
| `godot-physics` | Core workflow / Jolt：layer/mask 分工、查询时序、真实物理测试 | R10 射线查询时序与预算 |
| `godot-resources` | Workflow / Gotchas：共享引用、修改前复制、按场景隔离 | R11 材质/网格/MultiMesh 的共享边界 |
| `godot-shaders` | Gotchas：写入 `ALPHA` 会改变渲染队列；headless 编译不能证明 GPU 效果 | R01 渲染器覆盖；R10 布景透明队列 |
| `godot-ui-control` | Workflow：anchors/Containers、主题、初始焦点与多尺寸验证 | R06 模态焦点；R07 原生信息层级 |
| `godot-signals-groups` | Workflow / Gotchas：事件方向、初始状态、重复连接与释放后的监听 | R05 HUD 更新；R06 面板生命周期 |
| `godot-gdscript-headless-testing` | Workflow：主动 `quit(1)`、日志扫描、超时；先确认失败能被测试捕获 | R12 测试可信度 |
| `camera-systems` | Core workflow：指数平滑、约束整个视锥、移动后更新、传送后重置 | R04 固定视角下的跟随与高度平滑 |
| `level-design` | Workflow / Check it：从真实控制器量指标，先可达性/引导，再装饰 | R02 坐标语义；R03 通路；R04 步速；R09 引导 |
| `game-ui-ux` | Workflow / Gotchas：事件驱动、顶层输入、焦点回收、对比度和可访问性 | R05～R08 信息与交互 |
| `input-systems` | Workflow：按意图命名 action、物理键位、按下/持续语义、绑定与上下文审查 | R08 快捷键一致性 |
| `create-game-assets` | Workflow / Quality gates：保持已认可风格，固定规格，功能轮廓与原生尺度检查 | R09 建筑辨识；R11 字体与资产记录 |
| `performance-optimization` | Workflow：先分 CPU/GPU、解除限帧测余量、同路线三轮对照、目标设备长测 | R05 条件性缓存；R10 性能验收 |

### 不能照搬的建议

- `camera-systems` 的自由环绕/SpringArm 不适用：这里固定 **yaw=0°、pitch=30°**。只优化跟随、贴地高度和视锥边界，不重开旋转。
- `level-design` 的跳跃/钥匙模板不适用：本场景没有平台跳跃；度量真实步速、道路、门前停留点和可达性。
- `create-game-assets` 的“像素图一律关闭 mipmaps”不能直接套在倾斜 3D 屋顶/道路上。沿用已认可的 nearest + mipmap，并在真实缩放/斜面上查闪烁。
- `game-ui-ux` 的 PC 1080p 18px 与 200% 可访问性建议是审查依据，不是擅自改原生字号的授权。当前 HUD 保持；放大选项单列设计，不用缩小/删掉姓名解决遮挡。
- `performance-optimization` 的解除限帧用于诊断余量；日常 60FPS 限帧回归与十五分钟真实交互验收仍保留。headless CPU 数字不代表 GPU 或物理屏幕呈现。
- 不为了“使用 skill”重写成 CharacterBody3D、全局事件总线、对象池或 NavMesh；保留二维玩法 + 三维表现及已验证的静态场景架构。

## 3. 新审查结果与逐项修改计划

**状态约定：**“代码事实/画面事实”表示已经核对；“风险”表示尚未复现；“性能假设”必须先测量。下面都是后续计划，不冒充本轮已经修改。

### 第一优先：补运行与玩法边界

**R01 · P0 · 默认入口与 GPU 证据不一致（代码事实，默认 GPU 效果待测）**
- 依据：`project.godot` 默认 `gl_compatibility`；`hd2d_game_smoke.py` 强制 `forward_plus` 并校验该 backend。现有 33/14 项 GPU 通过不是默认入口通过；景深的渲染器差异已经存在。
- 计划：先让正式客户端回归可显式选择渲染器，在同一 AMD 上对照默认入口与 Forward+；列出实际功能差异，再决定是否需要改入口。未经验证不偷偷切换渲染器。
- 落点：`project.godot`、`tools/hd2d_game_smoke.py`、`tools/hd2d_smoke.py`、`scenes/hd2d/lighting.tscn`。
- 验收：两条路径分别记录实际 renderer、文字、硬遮挡、描边、贴地、缩放与夜景；不能只通过 shader 语法检查。未补测前保留“默认 Compatibility GPU 未验证”。

**R02 · P0 · 新地图位置与服务端距离语义（代码事实，全矩阵风险待测）**
- 依据：前端 `NPC_ROUTES` 重排了坐标，服务端 `schedule.position_for()`/感知距离仍按旧布局；现有购买/对话/战斗短测只覆盖部分角色和时间。
- 计划：只读核对有坐标的请求，明确 `pos`、`server_pos` 和玩家上传位置的语义。先测七名 NPC 的工作、通勤、聚集阶段，并在每阶段验证近处可交互/远处不可交互。接口不足时报告，由用户决定是否扩大后端范围。
- 落点：`scripts/town_world.gd`、`scripts/main.gd`、`tools/hd2d_game_smoke.py`；`server/` 只读。
- 验收：[计划] 至少 **7×3×2 = 42** 个距离/时段案例，连同对话停留/恢复；不出现“画面靠近但服务器误拒绝”，也不出现远处误触发。路线可走不等于服务器交互正确。

### 第二优先：固定镜头下先改善行走，再装饰

**R03 · P1 · 客栈遮住北侧通道，描边不能代替清晰布局（画面事实）**
- 依据：牌匾画廊的玄铁堂门口仍被前方客栈屋顶大面积挡住。抬高牌匾解决了识字，没有解决整段行人视线。当前路线检查验证预设线段，没有验证所有玩家出发点到各入口的全局连通。
- 计划：固定镜头记录客栈→玄铁堂/道馆/药铺的遮挡区间；优先局部错开路与停留点，必要时调整近侧构件，不透明化建筑。给出生点→七栋入口→聚集点→南侧怪物区补连通检查。
- 落点：`scripts/hd2d/scenic_layout.gd`、`tools/hd2d/world_builder.gd`、`tools/hd2d_route_check.gd`；同步 NPC 路线、二维脚部碰撞与舆图。
- 验收：主交互落点和方向能直接看见；逐条保存改前/后同机位截图与连续行走片段。每 4px 线段、休息摆动范围和聚集间距检查仍通过；新增连通检查没有孤立入口。描边只作临时遮挡提示。

**R04 · P1 · 步速与镜头响应受帧率影响（代码事实，感受差异待量测）**
- 依据：`main.gd::_process()` 的步长为 `150 * min(delta, 0.04)`；由代码推导，20FPS 持续行走只有约 120 逻辑 px/s，不是 150。`town_view.gd` 的水平/高度跟随用 `lerp(..., min(delta*8,1))`，不是指数平滑。
- 计划：先跑真实控制器的步速、贴地与跟随对照；再选固定步进或安全分步，不丢弃低帧率时间。镜头改用 `1-exp(-rate*delta)`，移动后更新；出生/复活/传送重置平滑状态。保留当前镜头与 1×/2×/3× 视锥约束。
- 落点：`scripts/main.gd`、`scripts/hd2d/town_view.gd`、`camera_limits.gd` 与控制器测试。
- 验收：[计划] 无障碍路径在 **20/30/60/144FPS** 各跑 10 秒，位移差不超过 1%；固定目标平滑测试在同一时间点误差小于 1 逻辑 px。再实走坡道、墙角和复活；无穿墙、抖动或镜头拖越全图。

### 第三优先：信息和输入一致性

**R05 · P1 · 静态 HUD/地图与动态世界更新混在每帧循环（代码事实，收益待测）**
- 依据：`main.gd::_process()` 每帧设置时间、地区、交互提示，并 `minimap.queue_redraw()`；`town_map.gd::_draw_scenic()` 同时重画静态道路/建筑和动态点位。牌匾节点已有缓存，移动人物投影仍需要随帧更新。
- 计划：数值/文字只在变化时赋值；地图尺寸/布局变化时重建静态层，玩家/怪物点位分开更新。使用现有数据入口或局部 typed signal，不另造全局事件总线；连接时先同步初始值，开关面板不重复监听。
- 落点：`scripts/main.gd`、`scripts/town_map.gd`、`scripts/world_overlay.gd`。
- 验收：地图仍即时跟随，NPC 停步后动画/气泡/昼夜照常更新；空闲状态不重复绘制静态地图；开关面板十次只响应一次。记录 CPU 帧时和更新次数，没收益就不保留额外缓存复杂度。

**R06 · P1 · 模态阻止移动已测，但焦点封闭/回收未测（代码事实与覆盖风险）**
- 依据：`_new_modal()` 没有统一的首控件 focus/trap/恢复；目前 `grab_focus()` 主要用于输入框。OS 键鼠回归证明模态阻止移动，不证明键盘焦点不能跑到背后或手柄能导航。
- 计划：记录打开前焦点；模态首个可操作控件获焦点；底层递归焦点/鼠标输入暂停；关闭后恢复。沿用现有面板，不改业务按钮，也不凭布尔状态继续叠加新弹层。
- 落点：`scripts/main.gd::_new_modal/_remove_modal`、`scripts/ui_kit.gd`、交互回归。
- 验收：只用 Tab/方向键/Enter/Esc 能完成地图、商店、角色/背包等流程；底层按钮不能被激活；关闭后返回合理焦点。真实手柄单列“未验证”，不把鼠标通过当手柄通过。

**R07 · P1 · 密集信息和可访问性仍有缺口（代码事实与画面风险）**
- 依据：`world_overlay.gd` 已避让牌匾，但逐人放置，没有角色之间的占位规则；姓名 14px、气泡 13px，HUD 部分信息 11～14px。气泡超过 26 字截断，完整内容是否可查询尚需核对。当前截图不证明所有背景下文字达到 4.5:1。
- 计划：聚集/边缘/缩窗场景补“玩家→当前目标→其他角色”的排布优先级，保留信息，不以隐藏姓名解决。颜色之外保留形状/名称区别。维持现有原生字号，另提可选文字放大/完整气泡查询方案，批准后再实施。
- 落点：`scripts/world_overlay.gd`、`scripts/hd2d/scenic_hud.gd`、`scripts/ui_kit.gd`。
- 验收：[计划] 七名 NPC 聚集、窗口边缘、**1100×720 / 1440×900 / 2560×1080** 无裁切或压住牌匾，目标与普通角色可区分。对比度测量标明背景；可访问性方案在放大 200% 时不丢业务内容。不要宣称当前已满足字号/对比度标准。

**R08 · P1 · 移动使用 action，快捷键仍直接读 KEY（代码事实）**
- 依据：`_bind_inputs()` 使用物理键 + `move_*`，移动读取 `Input.get_vector()`，这些保留；I/B/K/M/E/Esc 的面板/交互仍在 `KEY_*` 分支，提示写死按键。
- 计划：把业务快捷键变成按意图命名的 actions，统一读取、提示和模态上下文；保留当前默认键位。手柄/重绑定作为独立增量，不悄悄增加完整设置系统。
- 落点：`scripts/main.gd`、`project.godot`（或既有绑定入口）、`scripts/hd2d/scenic_hud.gd`。
- 验收：当前 WASD/箭头、E、面板键、Esc、滚轮仍工作；长按一次性动作不重复触发；聊天/输入框/模态不透传。审查绑定冲突，手柄与持久化只有实测后才记通过。

**R09 · P1 · 建筑识别过度依赖文字，主线与故事层次还不够（画面判断）**
- 依据：七栋大量共用青瓦/木框/完整门面；牌匾可读，但破庙仍显得完整整齐。草簇、瓦片、城墙反差接近；“牌匾遮住后还能辨认功能”没有评审证据。
- 计划：保持江南山麓像素风与已有模型复用，先用入口留白、道路连续性/少量地标强调客栈→药铺/道馆主线。再做少量功能剪影与可控破损：药铺入口陈设、锻造区轮廓、破庙缺损/杂草；远景降低对比、用疏密分层。不是等量再加树灯，也不量产新素材。
- 落点：`scripts/hd2d/scenic_layout.gd`、`tools/build_hd2d_environment.py`、已有材质源/构建器；新位图仍先记录提示词/路径。
- 验收：同原生尺度的“牌匾显示/暂遮文字”对照；用户能判断主路、入口与建筑功能，破庙不只是名称不同。新几何不遮挡入口、不改可走范围、不削弱角色剪影；每次装饰后重跑路线与牌匾回归。

### 最后：有证据地优化与固化

**R10 · P2 · 射线/透明队列/长帧先测瓶颈，不猜收益（代码事实、性能假设、时序风险）**
- 依据：`occlusion.gd` 以 **30Hz** 测玩家/目标，每次另轮询 **4** 名角色；有多采样点和最多 **128** 层射线穿透。调用来自表现层 update，而非明确的 physics callback，按 `godot-physics` 需审查线程/space 时序，当前未复现错误。`scenery_common.gdshaderinc` 仍写 `ALPHA`，取消人物渐隐不等于布景已进入 opaque 队列。
- 计划：先记录同路线 CPU/提交/GPU 帧时、真实射线次数/时长、draw calls、RSS 和 shader/pipeline 编译；解除限帧做余量诊断，再回到限60。只对确认的最大成本动手：射线在安全时序查询/缓存结果；或按 opaque/cutout/近景软裁切区分材质。不能一刀切掉近景裁切、人物描边、阴影或降低世界分辨率。
- 落点：`scripts/hd2d/occlusion.gd`、`town_view.gd`、`shaders/scenery_common.gdshaderinc`、`tools/hd2d_game_probe.gd` 与性能运行器。
- 验收：同 AMD/画质/窗口/路线改前后三轮，记录 avg/P95/P99/最大帧、截图读回长帧及独立无截图样本；射线线程检查不报错，遮挡提示/恢复不迟钝。随后 **900 秒真实连续行走/菜单/对话/战斗**，记录 RSS 前后与帧时趋势，不能用反复加载关闭代替。

**R11 · P2 · 保持资源共享正确，把字体/产物契约补齐（正确实现需保护，跨机风险待测）**
- 依据：`scenery.gd::_instance_material()` 已同世界共享、跨世界复制；upright 网格已复制；`grass_patch.gd` 已复制 MultiMesh 并恢复 authored buffer，这些不是“待重写问题”。UI/牌匾字体使用 `SystemFont` 和系统候选列表，跨机字形/缺字未验证。
- 计划：补两个视口的资源独立性测试，禁止批量深拷贝网格/纹理来掩盖状态串扰。保持 Blender→GLB→离线场景链；核对 owner、UID、引用、清单、再构建一致性。字体是否随包提供另行决定，先查授权和已有字体资源，不扩大位图量产。
- 落点：`scripts/hd2d/scenery.gd`、`grass_patch.gd`、`tools/hd2d/*_builder.gd`、`scripts/ui_kit.gd`、环境 manifest。
- 验收：改视口 A 灯光/材质参数不影响 B；同世界资源不无谓复制。真实 GPU 草簇分布/buffer/AABB 正确；干净 import 后无丢失引用。Windows/Linux 字体检查分别记录，不能用一台机器的有字截图替代。

**R12 · P2 · 测试必须能失败，短测与完整验收分开（覆盖风险）**
- 依据：Godot 的 SCRIPT ERROR/裸 assert 不自动使进程非零；已有路线 runner 自计数并退出，但失败分支还应做可控反例测试。字面 9 点视线不证明字体逐字清晰，仍需真实截图；两分钟通过不证明十五分钟或 Mango。
- 计划：新测试统一计数 + `quit(1)`、日志扫描和外部 timeout；用隔离反例证明会失败，恢复后再跑通过。默认入口、几何、GPU、实际输入、字体与性能各自记录，不把测试名当覆盖范围。
- 落点：`tools/hd2d_route_check.gd`、`tools/hd2d_contract.gd`、客户端探针/运行器和本文证据表。
- 验收：故意坏路线/缺资源/错误视角时相应测试非零，恢复后通过；不因 assert/await 永久挂住。MCP 恢复后补检查，未连接不能记作通过。

**直接推荐的执行顺序：R01 → R02 → R03/R04 → R05～R08 → R09 → R10～R12。**
先保住实际入口与玩法正确性，再做固定镜头下的行走/信息；最后按测量结果优化与固化。R10 的基线采样可以前置，但不能绕过测量直接大改。只保留有证据的局部修改，不重新开放可旋转镜头，不恢复遮挡建筑透明。

## 4. 当前版本的实测

环境：Godot 4.7.2 / Blender 5.2.3；Radeon 780M、Forward+ / Vulkan、轻量档，1440×900 原生世界视口，关闭 VSync、限 60 FPS。独立 gamescope/私有 X11 窗口内使用真实 OS 键鼠，不操作前台焦点。使用临时后端/存档，模型输出由测试 SSE fixture 代替，真实商店/战斗结算仍走后端。

| 检查 | 本轮结果与证据 |
|---|---|
| 新 skills 的项目引用检查 | `check_godot_project.py` 检查 **667** 个文件，**0 errors / 0 warnings**；`/tmp/xia-skill-project-check.json`。仅证明资源/场景引用检查，不证明画面或性能 |
| NPC/怪物/出生点可达性 | `HD2D_ROUTE_PASS: 7 NPC routes, 7 mob seats, 2 spawns`；`/tmp/xia-route-check.log` |
| 静态场景离线构建 | `HD2D_STATIC_BUILD_PASS`；`/tmp/xia-sign-static.log` |
| 环境几何可重建 | Blender `--check` 通过，核对 GLB 几何、材质、UV 与贴图哈希；`/tmp/xia-sign-blender-check.log` |
| 既有结构契约 | `hd2d_contract.gd` 通过，包括 3,600 投影/朝向用例、23,868 相机边界用例、旧 2D canvas 与全地图；`/tmp/xia-contract-check.log`。它不替代 GPU 描边/牌匾检查 |
| 场景、牌匾、边界实际交互 | **33/33**，120 秒；`/tmp/xia-sign-review-amd-final/interaction.json` |
| NPC 可见客户端实际交互 | **14/14**，120 秒；固定镜头、缩放、气泡到期、模态阻止移动、购买、对话冻结/SSE 和战斗；屋后描边触发/关闭另有状态断言和截图；`/tmp/xia-fixed-camera-outline-amd-final/interaction.json` |

| 完整短测指标（不删除截图/缩窗长帧） | 场景/牌匾，120s | NPC 可见，120s |
|---|---:|---:|
| 实际绘制帧率 | 58.50 FPS | 59.16 FPS |
| 帧间隔 P50 / P95 / P99 | 16.663 / 16.962 / 17.609 ms | 16.664 / 17.935 / 31.753 ms |
| >50ms 帧 | 32 | 14 |
| 真实输入事件 | 773 | 623 |
| 实際行走逻辑像素 | 8,665 | 10,312 |

这是实际运行客户端的绘制间隔，**不是物理显示器呈现帧时**。完整短测包括截图读回、缩窗、加载/交互，不删除不利数据。当前版本未做 15 分钟长测，不能用历史版本十五分钟数据替代，也不能宣称稳定 60 FPS 或 P5 通过。

### 已目视检查

- [药铺牌匾与姓名避让](/tmp/xia-sign-review-amd-final/manual-000022689.png)
- [1×镇区与破庙牌匾](/tmp/xia-sign-review-amd-v4/manual-000025037.png)
- [夜间客栈文字](/tmp/xia-sign-review-amd-v4/manual-000028153.png)
- [七栋牌匾画廊（避让改动前）](/tmp/xia-sign-review-amd-v4/sign-gallery.png)
- [最终玩家/NPC 描边，建筑不透明](/tmp/xia-fixed-camera-outline-amd-final/manual-000011164.png)

最初发现的两项失败均已修复：透明排序盖字、玄铁堂牌匾被客栈挡住；早期失败目录 `xia-sign-review-amd-v2/v3` 保留作定位证据，不算通过。临时日志/截图不入库。

MCP 本轮调用因 `CERTIFICATE_VERIFY_FAILED` 无法建立连接；本轮几何和场景结果来自离线 CLI 与真实 GPU，不记作 MCP 检查通过。Compatibility headless 启动会提示景深只支持 Forward+/Mobile；这是已知渲染器差异，不是本轮 Forward+ GPU 错误。

## 5. 本轮执行进度（2026-11-13，按 R01→R12）

| 项目 | 执行状态 | 本轮证据/边界 |
|---|---|---|
| R01 | 渲染器选择与默认入口覆盖已补齐 | `--renderer project/gl_compatibility/forward_plus`；正式客户端默认不覆盖项目渲染器，P1 保持 Forward+。8 项 runner 策略测试通过；780M/light/1440×900 两条路径各120s、33/33；`/tmp/xia-opt-r01-project`、`/tmp/xia-opt-r01-forward`。实际 backend 分别为 gl_compatibility/forward_plus。文字、硬遮挡/描边、贴地、1/2/3×与夜景通过；目视对照 `/tmp/xia-opt-r01-compare.png`。Compatibility 光照较亮、无景深支持；轻量档双方均未请求景深，不冒充 standard 景深对照。默认入口未改。 |
| R02 | 前端近远矩阵通过；服务端鉴权范围已核对 | `nearby_npc/npc_in_range` 按实际前端位置选择最近 NPC，修复门前建筑抢占 NPC 交互；`open_dialogue` 添加近处门禁。商店停留点改在 NPC 圆外，保留交易。`hd2d_npc_check.gd` 85项（42近远、42对话冻结/恢复、商店入口）通过。默认 Compatibility/780M 实际 OS 输入矩阵180s：7×3×2=42案例＋私有窗口，共43/43；21次模型 fixture SSE 走真实 talk 接口，七人每阶段截图，`/tmp/xia-opt-r02-matrix-v4`。时段仅在隔离测试后端/客户端同步冻结，生产服务器未改。 |
| R03 | 客栈错位、直达通道与连通检查完成 | 客栈西移176逻辑px，北侧主入口保持原位；新增玄铁堂南侧与道馆/药铺支路，同步掌柜之女路线与客栈复活点。图/碰撞/静态布景同源重建。全局8px图、4px边检查连通17个目的地（2出生、7入口、聚集点、7怪物），17,300个可达格；原七人路线/摆动/落点仍通过。默认入口120s、34/34实际OS检查，包括从(680,624)连续行至北入口并确认不需描边。改前/后同机位玄铁堂图与连续行走端点已目视：`/tmp/xia-opt-r03-comparison.png`；连续轨迹 `xia-opt-r03-scene/samples.jsonl`。 |
| R04 | 安全分步、指数平滑与重置完成 | `TownWorld.advance_actor` 保留二维脚盒/轴滑动，单步≤4px，消费全部delta。20/30/60/144FPS各10s真实控制器核心分别1500/1500/1500/1499.9891px；水平/高度同时间指数平滑<1px，初始化/显式出生复活与大位移重置，2s长帧不穿客栈与北通道测试：19/19；`/tmp/xia-opt-hd2d_controller_check.log`。真实AMD短测包含坡道、边界、菜单与移动，未宣称四种物理显示帧率均完成手测。 |
| R05 | HUD 条件赋值、静态地图/动态点位分层完成 | 保留原生 canvas commands，不增加缓存纹理或全局信号。尺寸/主题/详细模式变化才重画静态层；点位检测包含原地修改的 alive 字段，展开地图也同步。两轮相同 AMD/default/light 客户端各120s、14/14；取4～110s窗口：HUD赋值18,979→308，静态地图6,331→3，动态点位6,331→3,822。`/tmp/xia-opt-r05-before-client`、`xia-opt-r05-after-client`、`xia-opt-r05-comparison.json`；原生地图目视对照 `xia-opt-r05-map-compare.png`。现有 cpu_ms/gpu_ms 是世界SubViewport的提交/GPU时间，不包含原生HUD，所以不据此声称UI CPU/FPS提升。收益是确认消除了重复静态绘制/文字更新。 |
| R06 | 单模态焦点封闭/回收完成 | `modal_focus.gd` 保存打开前焦点与各底层Control递归策略；暂停底层GUI输入，不改按钮业务disabled；初始焦点优先正文首控件，保留已有LineEdit/caret/IME；Tab/ShiftTab闭环，方向邻居只在模态内，关闭恢复。请求完成/对话footer换页后刷新，连接不叠加。真实键盘药铺交易、十轮舆图Tab/方向/Enter返回及底层快捷键阻断通过。真实手柄未验证。 |
| R07 | 多角色占位、原生字号与形状区分完成 | 玩家◆→当前目标◇→其他角色顺序放置姓名/气泡，同时避让其他标签、牌匾、HUD和窗口边界；原生14/13px保持，姓名加2px深色边，不靠隐藏解决。三尺寸1100×720/1440×900/2560×1080各实际日程聚集＋隔离边缘压测，六组GPU截图/矩形检查通过；没有标签/牌匾重叠或排布失败。原生裁切图已目视 `/tmp/xia-opt-r07-native-matrix-final.png`。排布采样均值403µs/P95 604µs/P99 721µs；这是局部函数采样，不是整帧性能。未宣称全部HUD满足4.5:1或200%放大标准。 |
| R08 | 业务快捷键actions与提示统一完成 | `input_actions.gd` 注册open_character/open_inventory/open_skills/open_map/interact/close_panel及滚轮动作，保留WASD/箭头/I/B/K/M/E/Esc；注册幂等并保留已有绑定，提示由实际物理键标签生成。验证自定义Y代替M能打开舆图且提示Y、绑定无业务冲突、echo不触发、输入框ibkme作为文字；真实私有Rime Esc取消preedit而不退出对话、提交“你好”通过。手柄/重绑定设置与持久化未实施。 |
| R09 | 局部几何与入口留白完成；用户已认可保留当前版 | 三个主路边桌／桶西移，主路完整包络留白；远山降低反差。药铺草药束与实体凹口药臼／斜杵；铁匠铺尖角铁砧、侧炉床／炭块；破庙阶梯形缺瓦、露椽、断柱／倒梁／杂草。均由原 Blender 脚本生成，不加位图、不改玩法碰撞。`hd2d_art_check.gd` 10/10（含改前／后6,000格一致、家具包络、导出屋顶真缺口／另一侧瓦面），Blender `--check` 和药臼外向法线／破庙缺口探针通过；路线17目的地、既有结构契约通过。默认Compatibility/780M/light、120s真实OS输入41/41；`/tmp/xia-opt-r09-final`。同机位原生2×“显示文字／隐藏文字”前后对比已目视检查，用户已看图选择“保留当前版继续推进”；MCP 恢复后的补验见下文，不以脚本通过代替用户认可。 |
| R10 | 完成：三轮对照、15分钟及截图／输入法回归通过 | 不改默认Compatibility或画质。发现中文控件初次入树时使用引擎默认拉丁字体，重复进入FcFontMatch；`gui/theme/custom_font`与原UI复用同一SystemFont，保留原字体族顺序、字号和系统fallback。运行时射线移到物理阶段，描边仍逐渲染帧渐变；诊断射线单列。见下文R10测量记录。 |
| R11 | 完成：双视口资源／重建契约；字体风险已记录 | headless 1,175项；同一780M的Compatibility／Forward+各1,379项，A改灯光、材质、释放后B全部像素不变。275个静态网格继续共享、运行材质每世界23份、5,436株草的buffer／bounds／写入隔离通过。连续重建428文件字节一致；字体97字形实测及授权记录见下文，Windows和地区字形未验收。 |
| R12 | 完成：失败拦截、当前描边契约和最终回归 | 13个正常入口、8种故障反例、4项恢复检查、37项Python通过。AMD两渲染器各P1 85／环境92／人物35／资源1,379项通过；默认入口150秒Rime／三尺寸36项、120秒NPC矩阵43项通过。经用户授权仅将Compatibility人物曝光设为0.80，Forward+九张图逐字节不变；仍不等于Mango／Windows／P5，见下文。 |

R02 接口语义：`npcs[].pos`/保存的 `server_pos` 是旧日程位置，只作诊断，不用于新的前端近远判定。玩家上传 `pos` 仍是1600×960前端坐标，用于商店/战斗保存与重载；已知旧出生点才由 `local_point` 重映射。talk/greet/gift/apprentice 无玩家坐标，后端无距离门禁，所以新地图不会被旧坐标误拒绝；**前端远处不触发 ≠ 服务端拒绝伪造远程请求**。需要服务端距离鉴权时必须另行授权接口/布局协议改动，不伪造通过。

R01 完整短测（包含截图/resize 长帧）：Compatibility 57.77FPS、P95/P99 16.985/17.675ms、max150.769ms；Forward+ 58.56FPS、P95/P99 16.963/17.421ms、max100.881ms；双方各32帧>50ms。不是稳定60FPS或物理屏幕/P5验收。

R01时Godot MCP曾恢复，`session_manage list`确认4.7.2会话；R05～R08再次连接失败，原因为UnknownIssuer。Blender MCP仍未恢复。本阶段API核对来自本机ClassDB与CLI/真实GPU，不能记作本阶段MCP检查通过。

R05～R08最终联合回归：默认Compatibility/780M/light、150s、36/36真实OS检查，包含私有Rime与三尺寸密集气泡；证据 `/tmp/xia-opt-r05-r08-client-final/interaction.json`。`hd2d_ui_check.gd` 1,624项（焦点、十次生命周期、三尺寸聚集/边缘排布、初始/原地变更点位、绑定冲突与自定义键）及既有NPC 85项均通过。早期一次probe静态类型编译错误及一次隔离矩阵未恢复NPC路线的测试缺陷已经修复；`v2`宽屏聚集截图不用作最终证据，最终矩阵另断言全部七人位于聚集点90px内。

R07待批准的可访问性增量：推荐独立“文字大小100%～200%”选项，仅改原生信息/面板并重验排布；长气泡推荐点击/键盘选中当前角色后打开只读全文，直接复用完整bubble字符串，不改模型回复或日程。当前字号和26字截断保持，不未经批准新增阅读弹层；这不是已完成200%验收。

### R09 评审与技术证据（2026-11-14）

下面都是相同固定镜头、相同游戏位置、原生世界2×截图的**未缩放裁切**，上下两排分别显示／隐藏牌匾文字，左右为改前／改后。隐藏文字只发生在隔离测试的 Label3D 节点，不移走实体木牌，也不改变生产入口。

- [药铺：草药束、药柜与实体药臼](/tmp/xia-opt-r09-build/clinic-comparison-final.png)
- [铁匠铺：尖角铁砧与侧炉炭块](/tmp/xia-opt-r09-build/smith-comparison-final.png)
- [破庙：真缺瓦、露椽与断梁](/tmp/xia-opt-r09-build/temple-comparison-final.png)
- 完整改前／改后原图及取样状态：`/tmp/xia-opt-r09-before`、`/tmp/xia-opt-r09-final`；最新互动报告 `interaction.json` **41/41**，实际 backend `gl_compatibility`，120.018s、行走8,252逻辑px、745输入事件。所有七栋新增文字开关同机位断言，三种缩放、北通道连续行走、边界与夜景仍通过。
- 技术日志：`/tmp/xia-opt-r09-build/{art-final,art_geometry,route,contract,reproducibility,import-final}.log`。新增药臼的内壁／底面使用独立朝外绕序，生成器已加入正体积检查；破庙缺口边缘保留厚度与封闭断面，不把阴影裂纹当作开口。
- 全样本帧时（含40次截图读回／缩窗）：平均17.295ms、P95 17.011ms、P99 17.826ms、max149.596ms、43帧>50ms，绘制57.325FPS。**没有把这次短测当作性能提升／稳定60FPS／十五分钟通过**。
- 助手第一轮目视发现炉床被铁砧挡住、破庙缺口过于规整，已继续局部修正后重采最终对比。用户随后看图明确选择 **“保留当前版继续推进”**，R09 观感评审已认可。
- 本轮再次调用 Godot MCP 和 Blender MCP，双方连接均失败：`invalid peer certificate: UnknownIssuer`。可重建几何与场景来自 Blender/Godot CLI、本机AMD真实GPU／OS输入；**MCP 未验收**，不静默禁用证书校验或将离线通过冒充 MCP 成功。

### MCP 恢复与 R09 补验（2026-11-14）

用户明确选择等待 MCP 恢复，不接受 CLI 替代。继续时重新连接：Godot `session_manage(list)` / `editor_state` 返回本项目 Godot 4.7.2、ready/stopped；Blender 实际返回 5.2.3 LTS。未修改 TLS／代理／插件配置。

- Blender MCP 在现有应用内执行纯几何检查：药臼有向体积0.275071>0，破庙缺口无命中／另一侧瓦面有命中；药铺、铁匠铺、破庙GLB签名与manifest一致。调用前后当前Scene、3个对象、1个mesh均不变，未覆盖用户场景。
- Godot MCP `resource_manage(load)` 成功读取上述三个GLB为PackedScene，炭火材质为ShaderMaterial、glow=0.8、贴图／shader引用正确；原始返回保存在 `/tmp/xia-opt-r09-build/mcp-*.txt`。这是资源加载补验，不能替代此前实际GPU画面与CLI全格／射线断言。
- Blender addon协议15、服务端预期16，返回兼容回退警告；本轮实际get_scene_info/execute_code成功。未自动升级、重启应用或开启遥测。

### R10 测量与局部修正（2026-11-14）

测量入口新增 `--game --profile --max-fps 0 --no-captures`：私有gamescope/Xwayland、AMD 780M、项目默认Compatibility、轻量档、原生1440×900。探针核对实际Engine.max_fps=0；合成器名义240Hz，但实际绘制回调约445Hz，非物理显示FPS。固定午时、相同OS输入路线，首段仍包含缩放／缩窗、行囊、药铺购买、SSE对话与战斗，后段在同一客户端反复步行／菜单／对话；F4/F5仅定位夹具起点，没有反复加载／关闭游戏。正常回归仍默认60FPS且必须截图，性能模式才可明确禁止GPU读回。

- `profile_frames.csv`：逐帧间隔、世界SubViewport CPU提交／GPU时间、draw calls；只排除开始3秒，完整记录所有长帧。`samples.jsonl`：Main／演员／镜头／灯光／描边计时、运行与诊断射线、内存和编译计数；`profile_events.jsonl`单列控件构建／焦点。`TIME_PROCESS`仍是每秒最大值，不能当逐帧CPU时间；Compatibility全零编译计数不作为“无编译”证明。
- 同路线120秒基线3次（每次24项交互检查通过）：

| 基线 | 平均帧间隔ms | P95 / P99 ms | 最大ms | >50ms | 世界GPU均值ms |
|---|---:|---:|---:|---:|---:|
| 1 | 2.223 | 3.377 / 4.288 | 82.968 | 7 | 1.177 |
| 2 | 2.224 | 3.375 / 4.280 | 68.359 | 6 | 1.169 |
| 3 | 2.237 | 3.310 / 4.166 | 71.452 | 5 | 1.170 |

- 原始证据 `/tmp/xia-opt-r10-before-{1,2,3}`，汇总 `/tmp/xia-opt-r10/before-analysis.json`。运行查询每批约0.20ms、约30Hz；世界GPU约1.17ms，均非此次40ms级重复停顿的主要成本。因此没有盲目拆分透明shader、关闭近景柔裁切、减少NPC或降低渲染像素。
- 70秒真实交互细分记录 `/tmp/xia-opt-r10-ui-diagnostic`：重复打开对话平均35.062ms，其中外壳15.378ms、footer15.616ms，焦点配置约0.112ms。先试“控件整批挂树”仅小幅下降，已撤回，不混入最终改动。
- 进一步取样 `/tmp/xia-opt-r10/ui-gdb.log` 捕获主线程位于`FcFontMatch`；headless仅用于定位调用阶段，未代替真实性能验收。修改为 `assets/ui/body_font.tres`，由项目启动默认字体和 `UI.make_theme()`共用；字体族仍为Noto Serif CJK SC→Source Han Serif SC→SimSun→Microsoft YaHei，`allow_system_fallback=true`。没有硬编码Linux字体文件，也没有关闭罕用字回退。
- 射线拆为`occlusion.query()`／`advance()`：town_view在`_physics_process`查询，渲染帧仅消费缓存并保持原渐变／迟滞。独立真实墙体射线测试在单线程与独立物理线程各13项通过，覆盖恢复迟滞、禁用／重新启用、失效演员和诊断计数；`/tmp/xia-opt-r10/final-query{,-threaded}.log`。正式项目没有开启独立物理线程。Main计时少了迁出的查询成本不等于消除了这部分CPU工作。
- 正常截图模式的牌匾诊断也改在物理阶段查询；定时状态只取缓存，截图在下一物理阶段补新结果，并断言查询阶段。性能无截图模式不跑牌匾诊断。截图耗时计数包括GPU读回与PNG保存，不把这段总耗时全算成GPU工作。

| 改后，同路线120秒 | 平均帧间隔ms | P95 / P99 ms | 最大ms | >50ms | 世界GPU均值ms |
|---|---:|---:|---:|---:|---:|
| 1 | 2.116 | 3.033 / 3.906 | 14.546 | 0 | 1.127 |
| 2 | 2.594 | 3.551 / 17.863 | 35.230 | 0 | 1.380 |
| 3 | 2.143 | 3.078 / 3.954 | 16.234 | 0 | 1.147 |

三次均24/24；路径 `/tmp/xia-opt-r10-after-{1,2,3}`，汇总 `after-analysis.json`。**第二次异常数据保留，不以第一／第三次代替**：高间隔分散在步行／菜单／对话，多段世界GPU耗时同时升高，但Main局部计时并未升高；现有数据不足以区分调度／驱动／同机其他负载，不能明确归因，也不宣称三轮总体FPS提高。可靠收益是重复中文面板构建停顿消除，三次均无>50ms帧。

**900秒同一真实客户端长测**：`/tmp/xia-opt-r10-long`，`long-analysis.json`，95/95检查通过，11,359个真实OS输入事件、累计实际移动72,393逻辑px（探针不计≥32px的定位跳变）。初段包含真实购买／战斗，随后持续步行、开关面板及SSE对话，不反复加载／关闭客户端。

- 全样本（仅排除开始3秒）：均值16.666ms、P95/P99 **17.403/17.811ms**、最大**33.211ms**、**0帧>50ms**；实际绘制59.921FPS。每120秒窗口平均16.666ms、P99在17.731～17.889ms之间，未见随时长恶化。
- RSS按进程时间30～60秒与最后30秒比较中位数：**655.414→655.426MiB，+0.012MiB**；峰值655.426MiB。保留全部0.2秒采样及30秒桶，不拿进程exec前0.9MiB作起点；仅证明本次15分钟没有可见持续增长，不证明永远无泄漏。
- 25秒后的79次对话面板构建：均值**6.389ms**、P95 **8.439ms**、最大 **12.578ms**；焦点配置均值0.116ms。世界GPU均值2.729ms；限60后的功耗／调度状态与无限帧不同，不跨档直接比GPU均值。运行射线outside_physics=0，诊断射线=0、截图=0。
- Python 25项、字体11项、物理阶段13×2项、UI 1,624项、NPC85项、控制器19项通过。一次临时独立物理项目缺少tools链接导致正确非零退出，已补链接并复测通过；不把该次失败算通过。
- `/tmp/xia-opt-r10-captured`：150秒默认入口截图／真实Rime／三窗口矩阵 **36/36**；已目视中文对话输入“你好”、药铺按钮/字号与小窗口七人边缘气泡。独立保留完整帧时：均值16.900ms、P95/P99 19.943/32.533ms、最大311.709ms、17帧>50ms；16次截图读回＋PNG保存共2.173秒，并包含缩窗。不得把无截图长测的0帧>50ms套到该样本。牌匾诊断44,253条与运行射线125,856条分账，运行outside_physics=0，诊断调用有物理阶段断言且日志无错误。原生裁切：`/tmp/xia-opt-r10/native-ui-matrix.png`、`native-shop.png`；未压缩文字字号。
- **R10完成；以上不是Mango物理呈现、Windows、稳定60FPS或P5完整验收。**

## 6. 可直接运行的复核

在仓库根目录运行；每个 Godot/Blender 命令都带超时，防止未退出的临时探针挂住。正式正确性验收优先使用统一运行器；下面单项Godot直跑仅供诊断，不能只看退出码。

```bash
# R12统一入口：隔离副本、脚本/shader解析、全部正确性、真实故障反例与恢复
# 每次必须使用新/空目录；不指定 --output 则自动创建新的 /tmp 目录
 timeout 300 server/.venv/bin/python xia-ai-town/tools/hd2d_verify.py --negative

# Python失败策略、隔离与性能报告自检
 server/.venv/bin/python -m unittest discover -s xia-ai-town/tools -p 'test_hd2d*.py'

# 技能自带的场景/资源引用检查（不需要 GPU）
 timeout 30 python3 /home/kong/.pi/agent/skills/godot-nodes-scenes/scripts/check_godot_project.py \
   xia-ai-town --json

# 可达性与既有结构契约
 timeout 30 godot --headless --path xia-ai-town --script res://tools/hd2d_route_check.gd
 timeout 60 godot --headless --path xia-ai-town --script res://tools/hd2d_contract.gd

# 原生HUD/焦点/密集排布/绑定正确性；还需扫描日志中的SCRIPT ERROR/ERROR
 timeout 60 godot --headless --path xia-ai-town --script res://tools/hd2d_ui_check.gd

# R09 入口陈设／屋顶实际缺口（只证明技术守护，不是用户功能辨识认可）
 timeout 30 godot --headless --path xia-ai-town --script res://tools/hd2d_art_check.gd
# 本轮改前基线还在时，加 -- --baseline-layout=/tmp/xia-opt-r09-build/scenic_layout_before.gd

# 重建牌匾/环境几何；校验时加 -- --check
 timeout 180 blender --background --factory-startup --python-exit-code 1 \
   --python xia-ai-town/tools/build_hd2d_environment.py
 timeout 90 godot --headless --path xia-ai-town --editor --import
 timeout 90 godot --headless --path xia-ai-town --script res://tools/build_hd2d_scenes.gd

# 七栋牌匾与场景回归（隐藏 NPC，避免混淆场景证据）
 timeout 270 server/.venv/bin/python xia-ai-town/tools/hd2d_smoke.py \
   --background --game --scene-review --gpu-device amd --quality light \
   --duration 120 --output /tmp/xia-sign-recheck

# NPC 可见、对话、战斗等真实客户端回归
 timeout 270 server/.venv/bin/python xia-ai-town/tools/hd2d_smoke.py \
   --background --game --gpu-device amd --quality light \
   --duration 120 --output /tmp/xia-client-recheck
```

下一轮长测使用同一入口 `--duration 900`，把外部 `timeout` 同时提高到至少 1050 秒，并单独保留证据目录。合成器隔离测试仍不等于物理屏幕/Mango 验收。

未执行 `git add` 或提交；是否暂存由用户决定。

### R11 资源、重建与字体边界（2026-11-14）

- 新增 `tools/hd2d_resource_probe.gd`，复用真实town_view场景同时建立两个独立World3D／1440×900 SubViewport。`--background --resource-probe --renderer project --gpu-device amd --quality light` 走原私有显示运行器；也可选`forward_plus`。全程不连接后端。
- `/tmp/xia-opt-r11-final-{project,forward}/probe.json`：双方各**1,379/1,379**；改变A的昼夜、CameraAttributes、灯笼glow、屋顶tint，A真实图像改变，B原生图片**全部像素字节一致**，释放A后B仍一致。275个静态mesh跨世界共享；可变ShaderMaterial每世界23份，同世界重复使用、shader/纹理共享。upright quad／对应射线shape只局部复制。资源所有权检查保留GLB子场景owner。
- 草簇共**5,436株**；每个chunk检查12浮点/实例、authored bounds包含全部blade、真实GPU恢复buffer/AABB；只改A实例transform，B不变，blade mesh仍共享。隐藏A草簇后，3px采样网格中约一万点改变，排除“buffer有长度但草全挤原点”的假通过。headless **1,175项**单列，明确跳过GPU buffer／图像证明。已目视原生场景／昼夜／草簇：`/tmp/xia-opt-r11/resource-views-native.png`，完整图在各GPU目录。
- **未重写已有正确资源逻辑。** 草叶shader、StandardMaterial地形、静态网格和贴图属于只读共享资源；允许逐世界修改的是`scenery.materials`缓存、Environment／CameraAttributes和MultiMesh实例数据。不能直接修改共享mesh内部材质再要求自动隔离。RenderingServer的directional shadow atlas尺寸仍是全局设置，不承诺不同质量视口的独立shadow atlas。
- 干净隔离import无错误；项目引用检查**687文件、0 errors／0 warnings**。Blender原构建器`--check`通过39个资产的几何／材质／UV／贴图哈希；Godot连续离线重建**428文件全部字节一致**。首次仅发现`static/ui_theme.tres`还嵌入旧SystemFont，已用构建产物更新为R10的共享`body_font.tres`，不手改生成物；其余427文件无需刷新。证据 `/tmp/xia-opt-r11/{import.log,project-check-final.json,blender-rebuild.log,rebuild-comparison.json}`。
- MCP已实际调用：Godot `resource_manage load`核对现有SystemFont；Blender在用户当前Scene不变的情况下只读核对39个GLB头、mesh数、无嵌入贴图及builder哈希，全部通过。`godot-mcp-font.txt`、`blender-mcp.json`保存原始结果。不在用户编辑器中运行会清空／切换场景的Blender重建。

**字体：有字 ≠ 字形/跨机一致。**

1. `hd2d_font_check.gd`新增真实TextLine shaping检查：本机Linux 12项、选定97个不重复UI／牌匾／姓名／符号字符无缺字；`/tmp/xia-opt-r11/font-linux.json`。`Font.has_char()`不计自动系统回退，最初误报爱心缺字；现保留primary_missing诊断，但用实际glyph RID/index验收，不删除爱心或禁用fallback。
2. 本机fontconfig选择`/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc`（26,411,360字节），但Godot实际shaping报告`Noto Serif CJK JP` face 0，爱心用`Adwaita Mono` face 0。原候选顺序和既有显示不变；**不把指定SC族名当成地区字形已锁定**，也未验证全部模型回复Unicode。
3. 已读本机`noto-fonts-cjk`与`adwaita-fonts`的LICENSE，均为**SIL OFL 1.1**；版本／文件／授权哈希在`font-license-local.json`。若后续随包，需保留版权和OFL文本、遵守保留名称条件，不能把字体单独售卖；没有把SimSun／Microsoft YaHei的本机可用性视为分发授权。
4. **后续推荐：获批后固定随包Noto Serif SC字体并补符号字体**，避免TTC face和系统fallback随机器变化，再测中文地区字形、包体／子集与Linux／Windows。此次没有拷入字体文件，没有重命名／修改字体，没有冒充Windows通过。平台复测入口：`XIA_HD2D_FONT_REPORT=<报告路径> timeout 30 godot --headless --path xia-ai-town --script res://tools/hd2d_font_check.gd`（Windows使用对应环境变量与外部超时方式）。


### R12 失败拦截、探针迁移与人物曝光（2026-11-14 完成）

**1. 先证明运行器会拒绝失败。**

- `tools/hd2d_validation.py`统一要求**正常退出＋日志无错误＋最终完成标记/完整断言报告**；`--logic`不能只凭中途的`HD2D_CONTRACT_PASS`放行。GPU JSON需明确`passed=true`、非空断言且每项通过，不接受仅有成功字段的空报告。
- 新增`hd2d_script_check.gd`，逐一加载41份生产脚本／HD-2D探针和6份shader，避免只跑headless时漏掉GPU专用脚本的解析错误。shader解析不是GPU编译／成像证明，仍须跑下表真实GPU入口。
- `/tmp/xia-opt-r12-verify-complete/report.json`：**13个正常入口、8种真实故障反例、4项恢复检查全部通过**。故障仅注入临时项目：坏路线、缺资源、错误镜头、GPU探针错误类型、shader错误类型、assert但引擎退出0、只打印中途PASS、脚本不退出。shader错误和assert两例引擎仍退出0，包装器正确返回1；不退出返回124，包装器同样拒绝。恢复脚本／shader、路线、镜头、字体后重新通过；611份现场源码／资源哈希未变。每个Godot进程均有外部超时。
- 正常项包括UI 1,624、NPC 85、控制器19、镜头32、字体12、物理查询13×2、资源1,175，以及完整结构／玩法范围／路线／陈设契约。headless资源检查不冒充GPU buffer验证。
- 后台入口原已有空目录保护；本轮用`claim_output()`统一到手动GPU／logic／verify入口，并通过独占`.hd2d-run`防止两个运行争用同一空目录。不删除旧报告再假装新一轮。三个真实CLI入口均拒绝复用成功报告，旧报告字节不变：`/tmp/xia-opt-r12-final/reused-output-rejected.json`。不传`--output`自动创建独立目录。Python **37项通过**，含失败日志、完整报告、故障恢复和目录并发反例。

**2. 过期探针迁移，不改变已认可的描边效果。**

- 旧GPU探针还调用已删除的建筑透明API，人物探针还用`.new()`构造已固化为场景的TownView；现实例化真实预制体，保留硬遮挡、八向、多角度、真实房屋／树木和物理阶段射线，改测人物描边，未恢复建筑透明。
- 描边对照要求：人物出现轮廓、包络外零差、隐藏人物后的整幅布景逐字节一致。Compatibility一度在远处木栏交接处出现1个等深度排序像素；让零／有效强度两组保留相同描边pass拓扑后通过，**未放宽像素阈值或修改木栏素材**。
- 光照逐例用“隐藏人物”的背景验证至少95%的浅色衣料／皮肤采样确实属于人物，避免把挡住人物的屋顶当衣料。日间／固定游戏机位的高光门禁是三通道均达到0.99的样本比例<10%，不拿跨渲染器平均sRGB亮度猜曝光；另记录并核对全部九种照明／镜头的高光采样。

**3. 用户授权后的唯一生产效果改动：Compatibility人物曝光0.80。**

- `scripts/hd2d/shaders/actor.gdshader`增加`compatibility_exposure=0.80`，仅在`OUTPUT_IS_SRGB`的受光人物`ALBEDO`分支使用；Forward+乘数恒为1，诊断unshaded的EMISSION原样保留。未改默认渲染器、环境、贴图、几何、法线、alpha、人物投影或描边shader。
- 保留未补偿的真实失败基线`/tmp/xia-opt-r12-actor-project-v5`：32/34。该基线与改后`/tmp/xia-opt-r12-exposure-080`使用相同世界／镜头／姿态。每组516个浅色样本；数值是本组样本，不代表全场景每个角色像素。

| 同机位对照 | 饱和率改前→改后 | 浅色样本平均sRGB亮度改前→改后 |
|---|---:|---:|
| 白天，固定游戏机位 | 37.40% → **0%** | 0.978 → 0.860 |
| 背光 | 3.88% → **0%** | 0.874 → 0.707 |
| 真实房屋阴影 | 0% → **0%** | 0.471 → 0.365 |
| 夜晚灯下 | 0% → **0%** | 0.652 → 0.501 |

- 其余正面日光、正向主光、低／高俯角、黄昏也全部0%；改前最亮案例56.98%。夜间无灯0.464、有灯0.501，仍会受灯照亮；真实屋影开／关阴影约0.365／0.637，未用自发光或取消阴影“修亮”。数值不等于主观可读性验收。
- 已逐组目视：白天脸部／衣褶的纯白连块减少；背光、屋影和夜景虽暗一些，脸部、衣料分区和轮廓仍能辨认，未黑成一团。**最终观感仍由用户看图确认。** `/tmp/xia-opt-r12/actor-exposure-before-after.png`左改前右改后，自上而下白天固定游戏机位／背光／屋影／夜景，原生2×画面裁切后最近邻放大3倍，未额外调色或锐化。
- Forward+九张整幅GPU截图与补偿前**逐字节一致**：`/tmp/xia-opt-r12/forward-pixel-comparison.json`；不拿“同shader分支”静态推断代替实际比较。

**4. 最终回归（同一AMD 780M、light，未测试NVIDIA）。**

| 入口 | 默认Compatibility | Forward+ |
|---|---:|---:|
| P1几何／多机位／描边 | 85/85 | 85/85 |
| 全地图／边界／环境描边 | 92/92 | 92/92 |
| 人物步态／投影／光照／高光 | 35/35 | 35/35 |
| 双视口资源／草簇GPU隔离 | 1,379/1,379 | 1,379/1,379 |

- 原始索引`/tmp/xia-opt-r12-final/acceptance.json`；人物Forward+报告在`/tmp/xia-opt-r12-actor-forward-final`，其余最终GPU报告在该索引目录的对应子目录。
- **150秒真实客户端36/36**：`/tmp/xia-opt-r12-final/client`，私有X11/Rime、输入法Esc／中文提交、移动、1/2/3×、键盘购买与服务端结算、SSE、战斗、模态焦点、1100×720／1440×900／2560×1080聚集及边缘气泡。**120秒NPC矩阵43/43**：`client-npc`，七人工作／通勤／聚集的近处对话与远处不触发。均使用一次连续运行的真实客户端，不是反复启动／关闭；backend仅为临时测试实例、模型SSE使用fixture，未改`server/`代码。
- 已目视客户端HUD、中文“你好”、药铺购买、屋后轮廓、聚集／边缘气泡：`client-contact-sheet.png`；原生图位于`client/manual-*.png`。缩略联系表仅便于导航，不用来声称原生字号的额外验收。
- Godot MCP `filesystem scan`后读回材质，确认新uniform为0.80，仍引用原hero贴图；编辑器日志接口返回0条。Blender MCP只读检查41份GLB头与hero贴图哈希、当前Scene未改变。证据`/tmp/xia-opt-r12/{godot-mcp-material.txt,godot-mcp-editor-log.txt,blender-mcp.json}`；MCP只读检查不代替上述GPU结果。
- **R12技术项完成，不等于稳定60FPS、Mango物理呈现、Windows或P5完整验收。** R10的900秒无截图连续交互数据仍单列；不得将其0长帧结论套到本次带截图／缩窗测试。R09建筑辨识和本次曝光主观观感仍交给用户；R11地区字形／跨机字体风险未扩大范围处理。

复跑某项GPU检查示例（要使用新的结果目录，或省略`--output`）：

```bash
 timeout 300 server/.venv/bin/python xia-ai-town/tools/hd2d_smoke.py \
   --background --actor-probe --renderer project --gpu-device amd --quality light
 # 可分别替换为 --probe / --environment-probe / --resource-probe；
 # --renderer forward_plus 仅为对照，不改变项目默认入口。
```
