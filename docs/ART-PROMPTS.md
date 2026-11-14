# 美术规格与生成提示词（ART-PROMPTS）

地图逻辑以 [DESIGN §4](DESIGN.md) 为准（100×60 网格、逻辑 tile 16px、世界 1600×960）；本文只定义素材生产，不改坐标、碰撞或日程。

**HD-2D 首批源图已生成并接入独立 P1 候选样板，尚待目视验收**，实施顺序与验收以 [HD2D-PLAN.md](HD2D-PLAN.md) 为准。下方旧 2D 提示词仅用于维护现有基线，**不是新世界素材的生成任务**；不沿用旧 32px/四向/固定左上光规范限制重制。

## HD-2D 新素材生产规范（P1 候选已接入）

| 项目 | 目标 |
|---|---|
| 风格 | 自然像素武侠、江南山麓小镇，细颗粒像素；青灰瓦、灰白墙、深木、旧石/青苔，低饱和灰绿与少量暖色点缀 |
| 重制范围 | 地面、房屋、树木、家具、道具、玩家、NPC、怪物及基础动画全部重做；旧世界图只占位/对照，现有 UI 不重制 |
| 建筑素材 | 优先平铺瓦/墙/木/石与正视门窗/装饰，体积由 3D 几何提供；不直接生成整栋透视贴纸或先量产概念图 |
| 光照 | 中性、可接受实时灯影与昼夜变化；不烘焙强烈固定方向的环境投影、夜景光晕或地面阴影 |
| 人物 | 人形约 4～5 头身，保留年龄、职业和身份特征；野兽保持自身比例。玩家/NPC/怪物均八向待机与行走 |
| 显示比例 | 1440×900、世界 2×时，人形可见主体先按约 64～80 屏幕像素高验证；不是源图画布尺寸，也不改变碰撞 |
| 镜头适配 | P1 默认透视 FOV35°／俯角38°／方位30°；人物固定Y竖直贴片，源图为略见肩顶的正立视图，脚点须实机校准 |
| 像素/图集 | 跨组件像素颗粒一致；方向、动画、帧数/时序、脚点、有效边界在完整样板中锁定，不强塞旧 4×4 图集 |
| 可读性 | 人物清晰、动画稳定、透明边缘无残色；纹理平铺无明显接缝。不靠高清插画缩小、全屏锐化或景深遮缺陷 |
| 动态准备 | 树/草、灯笼/布幌适于局部微动，脚底固定；水面细波由表现层处理，不制作整张带强反射的水景插画 |

### 新源图与派生路径

```text
xia-ai-town/asserts/hd2d/            # 源图，不入库；本轮授权 pi-image-gen
├─ materials/                      # 瓦、墙、木、石、地面等
├─ components/                     # 门窗、店招、建筑装饰
├─ props/                          # 草木、家具、灯等
├─ characters/                     # 玩家/NPC 八向素材
└─ mobs/                           # 野狼/山贼/头目八向素材

xia-ai-town/assets/hd2d/            # 加工后的纹理与元数据，由 --hd2d 生成
```

`tools/build_art_assets.py --hd2d` 已扩展新流程；`--check` 验证源图与派生结果，`--preview-dir /tmp/xia-hd2d-art-review` 输出角色/复铺审阅图。默认不加 `--hd2d` 时保留旧流程，不手改派生 PNG。源图未纳入版本库，重建者须另行保留；运行已有派生资源不需要源图。

### R09 建筑功能轮廓局部修订（2026-11-14）

- 沿用已认可的江南山麓像素材质、`1/16` 模型比例与固定 yaw=0°／pitch=30°／FOV35°。原生 HUD、中文 Label3D、二维占地和 NPC 日程不改。
- 输入为本项目原创程序化 Blender 几何，**没有新增位图、图像生成调用或第三方素材**。脚本仍为 `xia-ai-town/tools/build_hd2d_environment.py`；源码是可重建依据，不手改导出 GLB。
- 药铺：檐前四束草药扩大可读轮廓，右侧药柜上增加实体凹口药臼与斜杵；陈设避开中央牌匾／门口。
- 铁匠铺：侧炉床、炭块、开口炉罩与尖角铁砧；炭色复用 `window_paper` 并使用已有 shader 的 `glow`，不加实时灯、粒子或新纹理。
- 破庙：阶梯形缺瓦、封闭厚度的断口、露出的斜椽、断柱／倒梁和少量杂草。不是在完整屋顶上画裂纹；几何探针验证缺口处无 roof 三角形、另一侧瓦面仍存在。
- 可编辑源：`xia-ai-town/asserts/hd2d/buildings/{clinic,smith,temple}.blend`（忽略、不入库）；运行输出：`xia-ai-town/assets/hd2d/buildings/{clinic,smith,temple}.glb`、`environment.json` 与离线固化的 `town_scenery.tscn`。
- `scenic_layout.gd` 只将中轴道路旁一张桌和两个桶移到西侧；`world_builder.gd` 降低远山纹理反差。`hd2d_art_check.gd` 对照改前布局验证全部 6,000 个玩法格不变，并检查桌／桶完整包络不盖住主路。
- 技术验收／原生“显示文字、隐藏文字”截图见 [优化进度 R09](HD2D-SCENE-OPTIMIZATION.md)。用户的功能辨识评审尚待确认；助手目视和几何通过不能代替用户认可。

### P1 客栈 Blender 模型（单栋候选，不量产）

- 建模输入为原创低模几何，不调用图生 3D、不下载第三方建筑，不新增或手改 PNG。暂复用已有瓦／灰墙／木／石／门窗像素材质。
- 可重建脚本：`xia-ai-town/tools/build_hd2d_house.py`；独立工作场景 `XIA_P1_Inn`，不清空用户其他 Blender 场景。
- 可编辑源模型：`xia-ai-town/asserts/hd2d/buildings/inn.blend`（忽略、不入库）；运行资产：`xia-ai-town/assets/hd2d/buildings/inn.glb` 与构件／材质／校验信息 `inn.json`。当前以脚本为可重建来源：手工修模后须同步脚本与导出校验；直接重建会覆盖脚本自己拥有的场景构件，不会保留这些构件的手工改形。
- 几何目标：朴素双坡屋顶的轻曲线和小幅檐口起势、瓦面厚度／檐底／椽尾、青灰脊瓦、实厚墙段、门窗内退与框架、独立木匾位置；不做宫殿式飞檐、逐片满铺几何瓦、可进入室内或新碰撞。
- 模型原点为原客栈占地中心地面，局部逻辑占地 `[-96,-64,192,128]`，导出后 `1/16` 缩放对应 Godot X／Y高／Z地图纵轴；二维逻辑占地仍是唯一玩法判定。
- 灯笼加工须保留朱红前景：仅此素材要求红／蓝两通道均高于绿色才判为洋红背景，避免旧平均值判定抠空红纸。通过 `build_art_assets.py --hd2d` 重建；不手绘补派生 PNG，其余素材保持原色键策略。
- 静态建筑统一为一个 `structure` Mesh／`inn/structure` 局部虚化组：墙、门窗/匾、柱梁、台基、两坡/檐底/椽尾和屋脊共9个材质Surface，同材质仅一面组（门窗重制新增独立纸窗材质）。前后墙及梁带连续生成，取消仅服务旧分段渐隐的边界；当前3196三角形（此前房梁修复版3624）。sidecar schema 2显式映射材质名和整体分组，Godot仅让人物前方遮罩内像素虚化。灯笼需要固定Y朝向和摆动，保留独立节点及 `inn/lantern/0`、`inn/lantern/1` 分组。
- 模型合并当轮按用户要求未运行测试；之后房梁修复通过单栋重建校验和双GPU定向采样，当前像素素材候选亦有定向验证，范围见HD2D-PLAN §9.2。完整P1验收仍需用户认可与后续回归。

### 样板与生成分工

1. 首批只做**一名完整八向角色 + 一栋房屋的必要材质/组件 + 道路/树/灯**，不先生成全镇资源。
2. 实施获准后，助手先在本文给每项用途、尺寸/视角、提示词、准确源图路径、派生目标及检查要求；默认由用户生成；本轮已授权助手使用 **pi-image-gen**，首批任务与提示词见下节。
3. 分辨率/帧数/图集布局由助手在目标内设计和实测；不要求用户逐个决定技术参数。图像模型的网格、八向身份一致性、动画步态与平铺质量都必须验证，不能只凭源图尺寸通过。
4. 切图/去底/对齐后在 Godot 验证比例、八向动画、白天/夜晚、人物前方局部柔边虚化和两 GPU；用户认可后锁定规格并量产。
5. 生成质量不满足标准时先修图或改流程，不把不一致图集批量铺开；人物左右不对称武器/服饰不随意镜像。素材更精细不等于角色在屏幕上更大。

## P1 首批生成任务（本轮授权 pi-image-gen）

本轮用户已授权助手直接生成图片。以下只制作独立样板，不代表 P1 已通过或可以量产。源图仍不入库；生产规则保持「先记录提示词 → 生图 → 检查 → 加工 → 实机验证」。

| 源图（`asserts/hd2d/` 下） | 布局与派生目标 | 检查 |
|---|---|---|
| `characters/hero-v2/{s,sw,w,nw,n,ne,e,se}.png` | 当前源图每向1536×1024、5列×2行，2帧待机＋8帧行走；合并10×8图集、64×80单帧；旧 `characters/hero.png` 仅留对照 | 同一米白衣青灰腰带、黑发髻、背剑青年；无镜像代替方向；逐帧有效边界、脚点、时序；2×主体约72px |
| `materials/surfaces-pixel-v2.png` | 1536×1024，3列×2行；瓦/灰白墙/深木、石基/石路/草地；每面派生64×64；旧`surfaces.png`留作对照 | 正视可平铺，中性光，大块像素色簇；脚本处理接缝并输出复铺预览 |
| `components/facade.png` | 1024×1024，2列×2行；木双门/格窗、空白木匾/布幌 | 正面正交，无透视、无文字、无固定投影 |
| `props/tree-pixel-v2.png` | 1024×1536，单棵江南松树；派生128×192；旧`tree.png`留作对照 | 明确树干脚点，非等轴地面贴纸；宽叶簇、硬alpha，无地面影 |
| `props/lantern.png` | 1024×1024，单盏朱砂纸灯；派生48×64 | 顶部悬挂点、底部流苏；无预烘焙光晕，灯光由3D控制 |

共同规则：细颗粒、有限调色板像素画，硬边像素簇，无平滑高清插画、文字、水印或参考游戏素材；中性漫射光。抠图源底为纯洋红 `#ff00ff`，不是透明棋盘。加工只用 nearest 与显式调色板量化，不通过 LANCZOS 平滑。输出导入使用 lossless；人物/布景不生成 mipmap，斜面使用 mipmap 与单独过滤设置。

### P1-树体积与墙门窗重制（当前候选）

上一版只换纹理，仍沿用圆柱树干／三张整树交叉卡片，墙与门窗也保留细碎模型细节，用户已明确要求重做。当前只重制同一棵样板树及客栈局部，不量产。

设计参考（只学习方法，不复制图像／游戏资产）：

- [SLYNYRD Pixelblog 44: Top Down Trees](https://www.slynyrd.com/blog/2023/5/22/pixelblog-44-top-down-trees)：先树枝骨架，再安排叶簇；叶簇保持相近颗粒尺度，用少量色阶组织大体积，避免叠加后产生散点噪声。针叶树需区分向前／向后／侧向枝。
- [SLYNYRD Pixelblog 2: Texture](https://www.slynyrd.com/blog/2018/2/15/pixelblog-2-texture)：简化、重复、平衡、疏密对比、避免孤立像素。
- [Pedro Medeiros: Cluster sketching and painting](https://medium.com/pixel-grimoire/how-to-start-making-pixel-art-2-bcd705cb04d7)：从大色簇开始，控制剪影阶梯，最后加少量细节；不是给写实纹理直接降分辨率。

实施：树改为有根盘和弯折分叉的锥缩不规则树干，枝端布置闭合、多叶瓣的立体松叶团，取消整树卡片与相机朝向；树皮单独使用像素树皮纹理，不复用建筑木板。松针团以少量平面色簇纹理和受控法线表现立体明暗，保留真实日夜／投影／风动与局部柔边虚化。墙保留实厚与原占地，墙皮低噪声；门窗以整数逻辑像素宽度安排少量明确框格和大面板，保留凹入、窗台与实体框厚度。

源图与构建落点：

- `xia-ai-town/asserts/hd2d/materials/facade-pixel-v3.png`：1536×1024，3列×2行；顺序为灰白墙、深色结构木、暖褐门窗框、门板木、冷灰纸窗、树皮。墙派生128×128，其余64×64，显式有限色板、无抖色；树皮按原木材方向UV。
- `xia-ai-town/asserts/hd2d/materials/pine-clusters-v3.png`：请求1024×1024，实际生成1254×1254；一张无背景无透明的可平铺松针叶簇表面，派生64×64，6绿色色阶。不是整棵树图，不复制树形，不含透明孔洞或固定投影。
- 图像先在本文记录提示词，再生成并检查；运行PNG只由`build_hd2d_assets.py`重建。树源模型在`asserts/hd2d/props/pine.blend`，运行`assets/hd2d/props/pine.glb`与`pine.json`，可重建脚本`tools/build_hd2d_tree.py`，仅拥有独立`XIA_P1_Pine`场景。当前每树2Mesh／2Surface／6336三角形，树高约159逻辑像素；14个闭合枝冠合并一个Mesh，根盘／弯曲树干／分枝合并一个Mesh，导出前检查全部边都有两个相邻面。Godot共享材质／几何，两棵旋转摆放；少量色阶漫反射随真实主光及阴影变化，关闭高光，不使用常态自发光。新树树干与树冠遮罩内每层残留不透明度为1%，避免多层实体叠加过暗；其他布景仍为4%，遮罩外／人物后方／阴影通道保持正常。

墙／门窗／树皮材质源图提示词：

```text
Use case: ORIGINAL production pixel-art albedo materials for a Chinese Jiangnan wuxia village, deliberately designed for a native pixel RPG environment.
Layout: edge-to-edge atlas exactly THREE equal square columns and TWO rows on a 1536x1024 canvas, each cell 512x512, no margins, gutters, labels, border or text. Orthographic flat surfaces ONLY, seamlessly repeating cell edges.
Top left: calm warm ivory lime plaster, at least 85 percent restful unmarked ivory surface, a few small connected pale beige chipped plaster clusters and short stepped cracks; NOT clouds, noisy crackle, checkerboard blocks, stones or watercolor. Top middle: aged deep umber structural timber with 4-5 warm flat brown shades and a FEW long broken vertical grain strips; NO horizontal planks, photographic wood, fine striation or glossy light. Top right: muted honey brown door and window joinery timber with 5-6 clear flat colors, broad vertical grain clusters and a few small knots, with large quiet areas.
Bottom left: weathered medium warm brown door plank material, 5-6 flat shades and deliberately sparse stepped grain marks, vertical grain, no tiny mottling or built-in joints. Bottom middle: pale cool grey blue rice-paper window surface, 3-4 close flat shades with almost no fiber detail, no frames, mullions, reflections or modern glass. Bottom right: rough organic pine bark, desaturated walnut brown with broken broad vertical bark plates and a few short angular dark crevices, 5 colors, NOT sawn wood planks or smooth cylindrical highlights.
Style: hand-placed 16-bit pixel-art CLUSTERS, limited palettes, no dithering, noise, orphan bright dots, blur, antialiasing, photographic textures, smooth gradients, painterly strokes or lighting baked into the materials. Every 512px cell is the exact nearest-neighbor 8x enlargement of a carefully composed 64x64 native tile, square grid aligned. Plaster may have finer stair steps but the overall surface must stay calm. Subtle warm and cool hue shifts within flat ramps. Different materials use clear readable graphic masses, NOT a post-filtered photograph. Neutral ambient light; no perspective or cast shadow. These materials will be used on real 3D walls and recessed solid doors/windows, and on a bent modeled tree trunk, with real dynamic lighting.
```

松针叶簇表面提示词：

```text
Use case: one ORIGINAL seamless pixel-art albedo texture for the surface of CLOSED 3D pine bough clusters in a Jiangnan wuxia RPG. This is a surface material, NOT a tree, NOT a cutout sprite, NOT a branch card.
Composition: edge-to-edge square canvas, a uniform continuous field of interlocked small pine-needle BUNDLES, each bundle a compact angular hooked rhombus or wedge, all bundles of a similar moderate size, mostly 3 to 7 native pixels across. Organize texture as a FEW large connected leaf-color clusters with quiet green areas, a few angular needle tufts on cluster borders. Most bundles are implied by short broken 2-pixel dark marks rather than outlined individually. No gaps to background, no isolated dots, no random speckles.
Style: expert deliberate 16-bit pixel art with 5-7 desaturated pine-jade green colors, darker blue-green, muted sage mid greens, limited yellow-green highlights. Crisp staircase shapes and clean shared-color pixel CLUSTERS, hand-placed with strong large-to-small hierarchy. Native 64x64 tile enlarged exactly 16x by nearest-neighbor to a 1024x1024 canvas, square pixels aligned to grid. Opposite edges tile seamlessly. Neutral lighting, all-over balanced surface, no top-to-bottom gradient, no baked cast shadows, no bevel highlights, no glossy sheen.
Constraints: NO transparent alpha holes, magenta, tree silhouette, branches, bark, whole pine, round lollipop clumps, painted blobs, geometric triangle pattern, checkerboard, photographs, smooth gradients, antialiasing, blur, dithering, text, labels, watermark, game logos or borrowed game assets. Final material must suggest overlapping short needle sprays when mapped onto shaped three-dimensional pine canopies while keeping light, medium and dark masses readable at small size.
```

### P1-环境像素风强化候选（上一版，待替换树及立面）

- 目的：把房屋、地面与松树从细碎写实噪点改为可读的像素色簇；保留现有立体几何、昼夜、风动、局部虚化与原生HUD。仅制作本P1候选，不量产。
- 源图：`xia-ai-town/asserts/hd2d/materials/surfaces-pixel-v2.png`（1536×1024，3×2等分，单格等效64×64像素）与 `xia-ai-town/asserts/hd2d/props/tree-pixel-v2.png`（1024×1536，等效128×192像素）。旧源图保留对照；模型输出若带文件名后缀，检查后复制到固定路径。
- 加工目标：材质按格切成64×64，nearest、32色量化、无抖色，保留可复铺检查；木材旋转90°让主木纹沿立柱方向。松树仍派生128×192与脚点信息，硬alpha，叶冠使用宽度至少2～4纹素的叶簇，减少零散单像素针叶。内存叶冠分离保留偏黄灰绿高光，绿色相对红色的阈值从1.06调到1.02，仍排除暖棕树干。角色、灯笼和门窗组件沿用。
- 目视要求：屋瓦轮廓与窗棂仍清楚，灰墙不过度脏裂；三张固定交叉树冠仍保有树形与厚度；默认2×、转镜头和远近缩放核对颗粒与闪烁。斜面保留mipmap，不对整帧做低分辨率拉伸。

环境材质源图提示词：

```text
Use case: production pixel-art textures for an existing 3D Jiangnan wuxia RPG sample, NOT a scene or concept poster.
Backdrop and layout: an edge-to-edge rectangular atlas, exactly THREE equal columns and TWO equal rows, no gutters, borders, labels, text or props. Every cell is a square seamless repeating orthographic material surface. Top row left to right: curved blue-grey Chinese clay roof tiles, warm ivory lime plaster, aged brown timber. Bottom row left to right: grey stone foundation blocks, irregular old grey flagstone path, muted moss-green short grass.
Style: deliberate hand-placed 16-bit era pixel ART, with chunky connected pixel CLUSTERS, staircase contours and a small restrained palette of 8-16 colors per cell. Each 512x512 cell should look like an EXACT nearest-neighbor 8x enlargement of a native 64x64 pixel tile; square 8x8 macro pixels aligned to the canvas grid. Roof uses 3-4 broad value steps with clearly separated curved tile rows; plaster mostly large calm ivory areas and a few subdued weathered patches; timber has a few long broad grain bands; stones use a few flat interior colors and narrow dark joints; grass uses quiet broad green clusters with a few readable short grass tufts. Materials have large restful areas and do not look like photos reduced in resolution.
Lighting: flat neutral diffuse albedo, no cast shadows, no baked ambient occlusion gradients, no highlights of a strong fixed light. Muted natural blue grey, ivory, umber, grey and sage greens appropriate to a Chinese mountain village. Opposite edges of EVERY cell must tile continuously.
Constraints: NO fine stipple, dither, noise, random bright single pixels, photographic texture, smooth gradients, antialiasing, blur, vector smooth curves, isometric scene, perspective, buildings, text, watermark or decorative frame. Do not create voxel blocks. Intended for retaining real 3D roof curvature, windows, doors, real shadows and dynamic day/night while the material itself reads distinctly as pixel art.
```

松树源图提示词：

```text
Use case: one production pixel-art pine tree sprite to derive three fixed crossed foliage cards around an existing low-poly trunk in a 3D wuxia RPG.
Backdrop: perfectly uniform pure solid magenta #ff00ff, no ground, no cast shadow, no gradient or checkerboard.
Subject: ONE full upright mature Jiangnan mountain pine, centered, natural thin irregular trunk with a visible rooted foot, five to six layered broad asymmetrical needle-leaf canopies and readable open gaps between branches. Entire tree including crown tip and roots fits with generous magenta margins. Upright front elevation with very slightly visible upper boughs, no isometric ground or landscape.
Style: crisp deliberate 16-bit pixel ART. The entire image is equivalent to a nearest-neighbor 8x enlargement of a native 128x192 sprite. Chunky connected dark jade and desaturated sage foliage clusters, canopy silhouette in staircase pixels, individual leaf clumps 2-8 NATIVE pixels wide. A small coherent palette, 6-8 greens and 4 browns. Light and shadow are drawn as 3 broad FLAT color masses, not hundreds of isolated needles. A few warm moss highlights. Broad leafy masses should read clearly even when shrunk. Warm brown trunk and side branches; foliage exclusively recognizably green, so its mask can be separated from the trunk by color. Neutral diffuse light without a strong directional light.
Constraints: NO fine stippled or dotted leaves, no thin isolated one-pixel needles, no photographic detail, dithering, antialiasing, painterly strokes, smooth gradients, glow, blur, outline around each leaf, text, labels, borders, watermark, additional objects, cast ground shadows or ground tile. Natural pine proportions, not a cone-shaped Christmas tree, cartoon lollipop, voxel tree or chibi prop. Intended to keep the current tree height, real 3D trunk, stable world-space geometry, swaying crossed foliage cards, shadows and local occlusion.
```

### P1-初版角色提示词（已由下节逐向重制替换）

```text
A production pixel-art spritesheet on a perfectly uniform solid magenta #ff00ff background. Exactly 6 columns and 8 rows of evenly spaced cells, no grid lines or labels. ONE identical young Chinese wandering swordsman in every cell, four-and-a-half heads tall, black hair in a compact topknot, ivory cotton outer robe over slate trousers, muted blue-grey sash, dark shoes, a sheathed narrow sword attached diagonally to his back. Small readable face, natural adult proportions, not chibi. Fine hand-placed crisp pixel clusters, restrained 24-color palette, no smooth shading or antialiasing, no photographic detail. Orthographic upright RPG sprite views with very slightly visible shoulders, NOT overhead views or isometric ground tiles. Rows from top to bottom face: south toward viewer, southwest diagonally front-left, west left profile, northwest diagonally back-left, north full back, northeast diagonally back-right, east right profile, southeast diagonally front-right. Each row keeps its exact facing. Columns 1 and 2 are distinct subtle idle/breath poses; columns 3 through 6 are four genuine alternating walk-cycle poses, alternating forward feet and opposite arms, not six copies. All 48 figures have the SAME outfit, anatomy, hair and sword attachment, same scale, consistent ground baseline within each cell, generous magenta margins. Flat neutral diffuse illumination. No cast ground shadows, scenery, text, numbers, borders, extra objects or watermark. Intended for eight-direction idle/walk animation on fixed-Y billboards in a 3D wuxia town, not a concept-art poster.
```

### P1-角色步态重制（当前任务）

- 用途：替换旧图集中错误的侧向、近似站姿的走路帧，仍只制作同一名样板角色。
- 新源图：`xia-ai-town/asserts/hd2d/characters/hero-v2/{s,sw,w,nw,n,ne,e,se}.png`，每个方向单独生成，复用同一身份参考；旧 `characters/hero.png` 留作对照。生成返回名若有后缀，验收后复制到上述固定路径。
- 每张实际1536×1024，5列×2行，按图像实际宽高均分；按行优先：2帧待机、8帧走路循环。派生 `assets/hd2d/characters/hero.png` 为10列×8行（640×640），单帧64×80；方向顺序S/SW/W/NW/N/NE/E/SE，主体约70～73纹素。同源图行的五帧共用缩放比例，按躯干中心与支撑脚底对齐，保留腿部屈伸、宽度和身体起伏，不逐帧拉高。
- 审阅：逐方向检查面部可见侧、鼻尖/脚尖朝向、背剑固定侧、左右脚交替、反向摆臂、接触→压低→经过→抬高的闭环，输出逐帧图与循环预览。手绘/生成缺陷不能用静态格子数量代替验收。
- 参考裁图：`asserts/hd2d/characters/hero-v2/identity.png` 从旧图正面、前斜和背面取图，仅约束米白衣、青灰腰带、黑发髻、深色裤靴、背剑；姿势全部重画，不复用旧错误方向，也不镜像不对称装束。

共同提示词初稿如下；首轮尺寸/姿势不满足要求时按下方骨架参考修订：

```text
Use case: production sprite animation for a fine-pixel wuxia RPG.
Backdrop: perfectly uniform solid magenta #ff00ff, no ground, no cast shadows.
Subject: ONE identical young Chinese wandering swordsman throughout. Image 1 is identity/outfit reference only, NOT a pose/layout reference. Ivory knee-length split robe, slate trousers, muted blue-grey sash and cuffs, black cloth boots, compact black topknot, one narrow sheathed sword diagonally across the back with its hilt always above his anatomical RIGHT shoulder. Natural four-and-a-half-head adult proportions.
Layout: exactly 5 columns and 2 rows of 320x320 cells on a 1600x640 canvas; no grid, letters or labels. Entire figure centered at x=160 within each cell, ground at y=290. Consistent anatomy and scale, full figure about 245px high. Generous empty margins, no clipping. Upright orthographic sprite, just slightly visible shoulders, never an overhead tile.
Animation order is row-major: first row = relaxed idle A, subtle idle B, left heel contact/right toe push-off, weight/down pose on left, right leg passing under hips. Second row = right knee advancing/up pose, right heel contact/left toe push-off, weight/down pose on right, left leg passing under hips, left knee advancing/up pose. These eight WALK poses form ONE continuous alternating walk cycle. Left and right always mean the character's anatomical sides. Feet are distinct; planted foot lies flat, trailing heel lifts, swinging knee bends and clears the ground. Hips shift weight slightly, torso stays upright, shoulders/arms counter-swing naturally against the legs. Modest stride, relaxed hands, no running, lunging, marching, crossing legs or skating. Robe has two separated front panels so knees and both boots stay readable. Contact and passing poses must be clearly different. Idle frames have relaxed arms and both feet resting.
Facing: {one exact direction supplied per call}; face, chest, hips and toes keep this same direction in ALL ten cells. DO NOT turn mid-cycle or mirror the whole character. Preserve the sword's anatomical attachment and identical clothing throughout.
Style: fine crisp pixel-art clusters and restrained neutral colors, clear silhouette, no smooth painting, no antialiasing, no gradients, no highlights baked from a directional light. The output will be downsampled with nearest to about 72 texels of body height.
Constraints: no extra people, extra limbs, two swords, duplicated overlapping feet, motion trails, dust, scenery, text, symbols or watermark. Full unbroken eight-pose gait with small consistent vertical bob; no six near-identical standing figures.
```

首张W候选抬膝偏高，未接入。修正采用 `tools/build_hd2d_pose_guides.py` 生成统一低抬脚、左右交替的三维骨架投影视图 `hero-v2/guide-{direction}.png`（1600×1024、5×2；仅制作参考、不当运行人物）。第二轮按实际模型输出1536×1024分格；生成时Image 1为服饰身份、Image 2为姿势布局，补充："Match Image 2 joint positions, low swing-foot clearance, modest stride, consistent head/hip anchors and relaxed counter-swing exactly; replace the mannequin with the fully dressed character from Image 1. Keep the same body height and planted ground baseline within each cell. No high knees or marching. Render the requested facing throughout, not colored bones. Image 2 defines the two idle plus eight walking poses in row-major order." 其余风格、背剑固定侧、无文字/阴影约束不变。生成尺寸仅供请求，以实际图像宽高均分，切图检查边界。

方向补充：S正面朝观察者；SW前3/4朝画面左下；W严格左侧面鼻尖脚尖朝左；NW后3/4朝画面左上；N纯背面朝远处；NE后3/4朝右上；E严格右侧面朝右；SE前3/4朝右下。各方向独立生成，不左右镜像。

当前选择：W使用 `w-guided.png` 复制为 `w.png`；S使用 `s-refined.png` 复制为 `s.png`；其他方向使用同名生成结果。S修订提示强调两接触相位由相反脚领步、前两格双脚放松，去掉画面右肩后多余剑柄；模型仍残留该剑柄，构建脚本仅清除耳后外侧小楔形，保留真剑/头发/耳朵。未手改运行PNG。`manifest.json` schema 2保存列数、两帧待机、八帧行走、0.065s参考帧时、78逻辑像素步态周期及每帧源缩放/锚点/hash；运行根据实际位移推进，相机转向不推进步态。`--preview-dir` 输出 `hero-review.png` 与八向循环 `hero-walk.gif`。80帧均有硬alpha、脚底对齐，源图身份/服饰细节与自然度仍需P1目视认可；不据自动化断言宣称美术通过。

### P1-材质提示词

```text
A production pixel-art material atlas, exactly 3 columns by 2 rows, six equal square cells filling the entire canvas with no margins, borders or labels. Each cell is an independent seamless repeating orthographic surface. Top row left to right: overlapping blue-grey Chinese clay roof tiles with rows horizontal and tile channels vertical; weathered warm grey-white lime plaster with subtle tiny cracks but no beams; dark aged brown timber planks. Bottom row: grey stone foundation blocks with fine moss joints; worn irregular grey flagstone road with sparse muted moss in joints; muted sage-green short meadow grass. Fine crisp 2-4 pixel clusters, limited desaturated natural palette, no antialiasing or smooth gradients. Neutral flat diffuse albedo, NO directional lighting, ambient occlusion, cast shadows, perspective or objects. Roof and road are not full buildings or scenery. Every individual square must tile seamlessly on both axes. All surfaces use compatible pixel density for a Jiangnan foothills wuxia game. No text, logos, people or watermarks.
```

### P1-门面提示词

```text
A production pixel-art Chinese architectural component atlas, exactly 2 columns by 2 rows, four independent centered objects on uniform solid magenta #ff00ff. Top left: closed double wooden entrance door, aged dark brown vertical planks with two small brass ring handles and rectangular wooden frame. Top right: rectangular traditional wood lattice window with muted dark recesses. Bottom left: plain horizontal hanging wooden shop sign with absolutely NO writing. Bottom right: plain hanging ivory cloth pennant on a short horizontal dark wood rod, NO writing. Strict front elevation, orthographic, no perspective or isometric view. Fine crisp pixel clusters, muted warm dark wood and ivory, neutral illumination, no cast shadows or baked glow. Generous magenta margins, objects fully visible and separated. No text, characters, borders or watermark. Components will be mounted on real 3D walls, not pasted as a complete house.
```

### P1-树提示词

```text
On a perfectly uniform solid magenta #ff00ff background, a single complete mature Chinese pine tree with a subtly crooked dark brown trunk, airy branching silhouette and layered irregular muted jade/sage needle foliage. Front elevation upright scenery sprite, orthographic, not a bird's-eye tile, no ground plane. Fine crisp pixel-art clusters, restrained 24-color natural palette, detailed but clean silhouette, no smooth painterly brushwork or antialiasing. Tree fills 80 percent of canvas height with all branches and trunk base visible, generous empty border, centered trunk foot at bottom. Neutral diffuse light with modest foliage shade, no dramatic sun highlights or cast ground shadow. No rocks, extra trees, text, labels, grid, watermark or glow. Intended as a fixed-Y billboard scenery asset with anchored trunk and subtle upper-foliage shader sway in a 3D Jiangnan foothills wuxia town.
```

树木立体化候选复用上述已有源图：`scripts/hd2d/tree.gd` 在内存分离绿色针叶供三张固定交叉贴片使用，树干/树枝用低面数几何和现有木纹。没有新增图片或修改派生 PNG；当前针叶分离和交叉接缝待后续 GPU 目视检查。

### P1-灯提示词

```text
On a perfectly uniform solid magenta #ff00ff background, one traditional Chinese hanging paper lantern, muted cinnabar-red ribbed round paper body, dark wood top and bottom cap, small hanging cord above and short tassel below. Strict upright front elevation, orthographic, no perspective. Fine crisp pixel-art clusters with a limited palette, neutral diffuse illumination, no antialiasing, smooth gradients, glow or cast shadow. Lantern occupies 75 percent of height, centered, all cord and tassel visible, generous magenta margin. No text, writing, glyphs, scenery, frame, watermark or additional objects. This is an unlit albedo sprite; warm night illumination and tiny pendulum motion are provided by Godot.
```

## P3 全镇环境模型（沿用已认可像素材质）

首批几何复用上文已认可的瓦、灰墙、结构木／框木／门板／纸窗、石、树皮、松针和灯笼；终审补充下面两张P3专用自然岩面／夯土源图，避免山崖使用砌石纹、练武场使用铺路石纹。几何由 `tools/build_hd2d_environment.py` 调用 P1 外壳裁切器，在各自独立的 `XIA_P3_<id>` Blender 场景中制作。Godot 负责绑定共享像素材质、摆放、光照、微动与局部虚化，不拿新几何替代二维碰撞；派生PNG仍只由加工脚本重建。

- **P3自然岩面**：源图 `asserts/hd2d/materials/rock-pixel-p3.png`，派生 `assets/hd2d/materials/rock.png`，64×64／6色／nearest+mipmap，用于山崖、野外岩石与苔石。

  提示词：`One seamless square natural weathered rock albedo swatch for the approved fine-pixel Jiangnan foothill wuxia world. Flat orthographic texture only, edge-to-edge tile, calm broad irregular gray-green mineral masses with sparse stepped diagonal fractures and short broken strata. Authentic hand-placed pixel clusters on a 64 by 64 logical grid enlarged with nearest-neighbor, six muted stone tones, low contrast, very few isolated pixels. Neutral diffuse light, no baked directional highlights or shadows, no perspective, no masonry, no bricks, no regular courses, no cobblestones, no wood, no grass, no border, no labels or text. Must repeat continuously on all four sides. Original game-ready diffuse material, not a concept rendering.`

- **P3夯土地面**：源图 `asserts/hd2d/materials/earth-pixel-p3.png`，派生 `assets/hd2d/materials/earth.png`，64×64／6色／nearest+mipmap，用于原练武场沙土地格。

  提示词：`One seamless square compacted earthen training-yard albedo swatch for the approved fine-pixel Jiangnan foothill wuxia world. Flat orthographic texture only, edge-to-edge tile, quietly worn warm gray-ochre packed soil with broad soft-shaped pixel clusters, a few tiny embedded aggregates and faint irregular compressed-soil cracks. Authentic hand-placed pixel art on a 64 by 64 logical grid enlarged with nearest-neighbor, six low-saturation earth tones, very low contrast, no noisy speckle. Neutral diffuse light, no baked directional highlights or shadows, no perspective, no separate paving stones, no bricks, no wood planks, no grass, no footprints, no border, no labels or text. Must repeat continuously on all four sides. Original game-ready diffuse material, not a concept rendering.`

| 模型 | 源库（忽略，不入库） | 运行 GLB 与用途 |
|---|---|---|
| 剑阳道馆／玄铁堂／烟雨小筑 | `asserts/hd2d/buildings/{jianyang,xuantie,yanyu}.blend` | `assets/hd2d/buildings/<id>.glb`；原160×96占地，主堂、实体门窗、坡顶／厚檐／屋脊，剑架、铁器、布幔区分身份 |
| 药铺／铁匠铺 | `asserts/hd2d/buildings/{clinic,smith}.blend` | `assets/hd2d/buildings/<id>.glb`；原128×96占地，药柜／烟囱／铁器门饰，不占门前交互区 |
| 破庙 | `asserts/hd2d/buildings/temple.blend` | `assets/hd2d/buildings/temple.glb`；原160×112占地，封闭墙体、破损窗棂／钉板、缺角檐口与苔石；不增加室内 |
| 家具与野外布景 | `asserts/hd2d/props/<key>.blend` | `assets/hd2d/props/<key>.glb`；29种新模型，覆盖树木／灌丛／岩石、井／围栏／桌凳／兵器架／草靶、草捆／桶／药草／席、木料／车轮／水洼／石堆等 |
| 镇门与灯柱 | `asserts/hd2d/props/{gate,lantern_post}.blend` | `assets/hd2d/props/{gate,lantern_post}.glb`；门柱在原64px道路两侧，梁顶高于人物；原寺外灯笼改有实体支架 |
| 清单与坐标契约 | 无额外源图 | `assets/hd2d/environment.json`；37个GLB的几何hash、材质／组件名、原点、原占地和纹理hash；松树两种索引及灯笼复用P1，32种旧道具身份都有对应项 |

建筑静态壳体按材质合批，并沿用相交盒体裁切／去共面封口；每个模型有唯一网格名，避免 Blender 跨场景自动重命名破坏 Godot 构件索引。所有几何用1/16比例、占地中心／地面原点。门窗保持实体凹凸，树冠和灌丛是闭合体积，不恢复整树交叉卡片；水面使用原材质加克制细波，水边界不动。

```bash
blender --background --factory-startup --python-exit-code 1 \
  --python xia-ai-town/tools/build_hd2d_environment.py
blender --background --factory-startup --python-exit-code 1 \
  --python xia-ai-town/tools/build_hd2d_environment.py -- --check
```

本批已覆盖P3环境和人物：7位NPC与3类怪物的全新八向图集已生成、处理并接入运行manifest。资源／实机验证与证据边界见 [HD2D-PLAN §9.4](HD2D-PLAN.md#94-p3-全镇环境与人物批次)。

## P3 人物与怪物八向图集生成任务

P3 角色源图落在忽略目录 `xia-ai-town/asserts/hd2d/actors-p3/`，每张提示源图目标为 10列×8行：行顺序 `S/SW/W/NW/N/NE/E/SE`，列0～1为待机，列2～9为行走。派生目标为 `assets/hd2d/actors/<key>.png`，10×8、单帧64×96、硬alpha、nearest；Godot使用独立的每角色manifest，人物脚点和实际视觉高度分开记录。生成器偶尔输出11个接触姿势，处理脚本按透明像素x-run恢复10个运行姿势并记录源hash；生成提示词不能用镜像替代斜向，不能把身份特征交给随机背景；所有格子均不得有文字、网格线、阴影或其他角色。

共同图集提示词前缀：

```text
Use case: game-ready fine-pixel wuxia character spritesheet for a Godot HD-2D world.
Asset type: one single consistent character in an exact 10-column by 8-row sheet, 80 independent cells, no visible cell borders. Rows top to bottom are S, SW, W, NW, N, NE, E, SE. Columns 0 and 1 are two quiet idle poses; columns 2 through 9 are a restrained eight-pose walking cycle. Every cell contains the same character, same clothing, same asymmetrical equipment and same scale. Draw all eight facings separately; never mirror a facing. Feet share one planted baseline inside every row and the torso/head anchor stays stable through the walk.
Scene/backdrop: solid chroma magenta #ff00ff in every unused pixel, no scenery.
Style/medium: crisp hand-placed pixel art, 32 muted colors or fewer, nearest-neighbor hard pixels, about 4 to 5 heads tall for a human, readable silhouette at native 64x80 frame size, neutral diffuse design light.
Composition/framing: centered full-body figure in every cell, enough magenta margin to cut cleanly, no touching cell borders, no perspective camera, no ground shadow, no glow, no anti-aliasing, no gradients, no outlines made of smooth vector strokes.
Animation: two genuinely different idle poses and eight low-stride walk poses with alternating support feet, modest knee lift, no marching, no changing costume, no changing body height, no teleporting limbs.
Constraints: no text, labels, numbers, watermark, logo, grid lines, background props, extra characters, speech bubbles, UI or floating particles. The final image is an original game asset and must be suitable for nearest-filter alpha-cutout rendering.
```

身份／输出路径（将对应说明追加在共同前缀末尾）：

| key | 身份设计追加句 | 源图 |
|---|---|---|
| `gu_changfeng` | `A proud austere male Jianyang martial hall master, dark blue-gray long robe, high tied black hair, narrow stern face, simple straight sword fixed diagonally across his back, cinnabar cord detail.` | `asserts/hd2d/actors-p3/npc-gu-changfeng.png` |
| `tie_wushuang` | `A broad cheerful male Xuantie hall master, sturdy shoulders, dark iron-brown sleeveless outer vest over a muted ochre robe, short beard, tied topknot, heavy straight saber across his back, small wine gourd at the belt.` | `asserts/hd2d/actors-p3/npc-tie-wushuang.png` |
| `su_wanqing` | `A poised clever female Yanyu pavilion master, cool jade and blue-gray layered robe, long black hair with one pale hairpin on the character's left, narrow sleeves and a small folded fan at the belt, graceful upright posture.` | `asserts/hd2d/actors-p3/npc-su-wanqing.png` |
| `chen_bansong` | `A kindly elderly male apothecary, soft jade-green robe and brown apron, silver hair in a modest cap, round spectacles, medicine satchel and a few herb leaves at the belt, slightly stooped but planted feet.` | `asserts/hd2d/actors-p3/npc-chen-bansong.png` |
| `wang_dazhu` | `A friendly stocky middle-aged male blacksmith, warm brown work robe, dark leather apron, rolled sleeves, tied hair, broad forearms, hammer and short tool roll fixed at the back, one red cord.` | `asserts/hd2d/actors-p3/npc-wang-dazhu.png` |
| `liu_xiaodie` | `A lively young female inn keeper's daughter, dusty rose and cream robe, twin braids with small blue ties, bright curious face, short waist sash and a folded cloth at the belt, quick light stance.` | `asserts/hd2d/actors-p3/npc-liu-xiaodie.png` |
| `zhang_mantou` | `A ragged eccentric elderly male beggar, patched gray-green robe, crooked straw hat, wild gray hair and beard, uneven shoulder drape, wooden bowl at one hand and a red thread charm, asymmetrical silhouette.` | `asserts/hd2d/actors-p3/npc-zhang-mantou.png` |
| `wolf` | `A lean gray mountain wolf shown at natural four-legged proportions, darker saddle stripe, scarred left ear, pale muzzle, low tail, visible four-foot support changes through walking, no human anatomy.` | `asserts/hd2d/actors-p3/mob-wolf.png` |
| `bandit` | `A wiry mountain bandit in ragged dark-brown cotton, cloth headwrap, dark scarf over the neck, short sabre fixed across the back, cautious crouched human gait, muted red cord.` | `asserts/hd2d/actors-p3/mob-bandit.png` |
| `boss` | `A hulking bandit chief named Black Whirlwind in a black padded jacket, iron shoulder guard, broad chopping blade fixed across the back, heavy boots and a red waist sash, 1.3 times the human bandit height.` | `asserts/hd2d/actors-p3/mob-boss.png` |

The P3 processing command will key the magenta, align each cropped frame to its actual support-foot bounds, quantize the shared palette, write binary-alpha import policies and record source hashes. Artistic identity, direction consistency, gait and in-game occlusion need real GPU review before this batch is treated as P3 complete.

## 现有 2D 视觉规格（仅用于基线维护）

当前图片已接入游戏：源图在 `xia-ai-town/asserts/`，现有产物在 `assets/atlas/`、`assets/buildings/`、`assets/characters/`、`assets/mobs/` 和 `assets/ui/`；实际切图入口见下方“切图与落盘流程”。以下风格、网格、背景和固定光照只适用于这些旧资源。

| 项 | 规格 |
|---|---|
| 风格 | 16-bit 像素画，武侠水墨取向：低饱和、留白、点缀朱砂与黛青 |
| 主色 | 玉绿 `#778f72` `#9aa58a`、青灰 `#717e72` `#4e6968`、米白 `#d0c6a6`、墨 `#263e38`、朱砂 `#aa6651`、黛青 `#647e83` |
| 禁忌 | 高饱和霓虹、卡通描边、渐变光晕、3D 渲染、写实照片感、日式浮世绘纹样、文字（除匾额外一律不出现字） |
| 网格 | 严格正方形网格排布，无间距、无边框、无描边分隔，格子之间不得有缝隙或阴影溢出 |
| 背景 | 洋红 `#ff00ff` 纯色垫底（仅用于抠图；最终瓦片须改为真透明或铺满整格） |
| 光照 | 左上光源，向下投 1px 冷暗影；不使用环境光遮蔽大色块 |
| 一致性 | 同一张图集内所有格子共享同一调色板与像素密度（每格 1 像素 = 1 游戏像素的整数倍） |

## 现有 2D 输出尺寸适配

原始图集的分辨率按模型或平台支持的选项选择，优先 **1:1 正方形**；`2048×2048` 是可选示例，不是硬性要求。提示词保留“4 列 × 4 行、16 个等大格子”的构图约束，最终游戏瓦片尺寸在切图阶段确定。

- 平台提供具体尺寸时，选择其支持的正方形尺寸，例如 `1024×1024`；只有明确支持时才选 `2048×2048`。
- 平台提供 `1K / 2K` 或宽高比选项时，按平台含义设置清晰度，并优先选 `1:1`。
- API 没有 `size` 参数时，省略该参数，使用模型默认输出；在提示词中要求正方形图集。写入提示词的尺寸不能代替 API 对尺寸的支持。
- 当前生成入口是 `xia-ai-town/tools/build_art_assets.py`，按源图实际尺寸和预设布局加工；以下 4×4 示例仅适用旧提示词，HD-2D 新布局待样板确定。

| 原图尺寸（支持时选用） | 4×4 分格后的源单格 | 最终地形 / 道具尺寸 |
|---|---|---|
| 1024×1024 | 256×256 | 16×16 / 32×32 |
| 1536×1536 | 384×384 | 16×16 / 32×32 |
| 2048×2048 | 512×512 | 16×16 / 32×32 |

源图边长能被 4 整除时便于等分，但仍需目视检查模型是否真的遵守网格。若输出为非正方形，当前脚本会居中裁剪每格以匹配目标比例，可能切掉物件；应先检查构图，必要时重新生成或人工调整裁切。

> 生图模型不能保证严格网格、像素密度和无缝拼接。降到 16×16 后需检查细节是否可辨，并用同类瓦片重复铺排检查接缝；增大源图分辨率或随机轮换瓦片都不能保证消除接缝。有明显问题的素材需修图或重做，再接入游戏。

---

## 提示词 1：地形瓦片图集（terrain）

**输出设置**：选择模型支持的 1:1 尺寸，切 4×4 网格，每格最近邻降采样到 16×16。

```text
Use case: pixel-art game tileset, top-down 2D map.
Asset type: terrain tilesheet that will be cut into individual 16x16 px tiles and tiled in a Godot game.
Primary request: a 4x4 grid of sixteen seamless square terrain tiles, each tile filling its whole cell edge to edge, no gaps, no borders, no drop shadows between cells.
Scene/backdrop: top-down (bird's eye) view of a low-saturation wuxia town and its wild outskirts.
Style/medium: 16-bit pixel art, crisp hard pixel edges, limited palette of about 24 colors, ink-wash wuxia mood, muted jade green, celadon grey, rice-paper beige, ink black, tiny cinnabar-red accents. No anti-aliasing, no gradients, no glow, no 3D rendering, no photorealism, no cartoon outlines.
Composition/framing: exactly sixteen equal square cells in a strict 4-column by 4-row grid, cells touching each other with zero margin, every cell completely filled with texture so the grid is only implied.
Cell contents, left to right, top to bottom:
row 1: packed-dirt town ground (beige-grey rammed earth with faint pebbles), grey stone paving slabs (irregular flagstones with thin dark joints), lush grass (short jade blades, sparse), darker overgrown grass with tiny wildflowers.
row 2: pale river sand, still water surface (muted jade-teal with two soft ripple lines), grey cliff rock face (angular facets, moss in cracks), dirt trail (worn earth with small stones).
row 3: grey brick town wall (regular brick courses, mortar lines), dark grey clay roof tiles seen from above (overlapping rows), whitewashed wooden wall panel (off-white plaster with a wood post at one edge), stone foundation ledge (grey blocks with a step).
row 4: packed training-yard ground (flat clay with a faint painted circle), wooden plank floor, sparse dead-leaf litter over earth, gravel and rubble ground.
Color palette: jade green #778f72, #9aa58a; celadon #717e72, #4e6968; rice paper #d0c6a6, #b7af8b; ink #263e38, #3b4e49; cinnabar accent #aa6651.
Materials/textures: hand-placed pixel clusters, 2-4 pixel detail marks, dithered transitions instead of soft gradients.
Background: flat solid magenta #ff00ff behind the tilesheet only, never inside the tiles themselves.
Constraints: no text, no writing, no labels, no numbers, no watermarks, no characters, no buildings, no objects at cell borders that would break tiling; each tile must read clearly at 16x16 pixels; keep all tiles the same brightness range so they blend when mixed.
```

## 提示词 2：草木山石小物（props）

**输出设置**：选择模型支持的 1:1 尺寸，切 4×4 网格，每格降采样到 32×32。

```text
Use case: pixel-art game props sheet, top-down 2D map.
Asset type: small scenery and furniture props that will be cut out, background removed, and placed on a 16 px tile map.
Primary request: a 4x4 grid of sixteen separate props, one prop centered in each cell, each on a flat magenta #ff00ff background with generous empty space around it so it can be cut out cleanly.
Scene/backdrop: a quiet wuxia town in the jade-green, ink-wash style.
Style/medium: 16-bit pixel art, crisp pixels, about 24-color muted palette, ink-wash wuxia mood. No anti-aliasing, no gradients, no glow, no soft shadows, no outlines, no 3D.
Composition/framing: strict 4 columns by 4 rows, equal square cells, one object per cell, object occupies roughly 70 percent of its cell, seen from a three-quarter top-down angle consistent with a 16 px tile map, bottom of each object sitting near the bottom of its cell.
Cell contents: pine tree with layered dark jade foliage, bare gnarled tree, round shrub with two tiny cinnabar flowers, bramble bush, angular grey boulder, tall grey cliff rock outcrop, stone well with wooden windlass and bucket, wooden fence segment with two posts.
Second half: wooden bench and table set, weapon rack holding three swords, stone lantern, grass bale, wooden water barrel, bamboo drying rack with herbs, wooden training dummy post, woven straw mat.
Color palette: jade #476b54, #778f72; celadon #717e72, #8fa582; wood #635e46, #8d7553; stone #7c8273, #a4a58a; cinnabar #aa6651.
Materials/textures: chunky 2-3 pixel highlights on the upper-left faces, 1 pixel cold shadow at the base, dithered shading only.
Background: uniform flat magenta #ff00ff, absolutely solid, no vignette, no floor plane, no cast shadow on the background.
Constraints: no text, no labels, no numbers, no people, no creatures, no buildings, no border lines, nothing touching the cell edges.
```

## 提示词 3：建筑部件（buildings）

**输出设置**：选择模型支持的 1:1 尺寸，切 4×4 网格，每格降采样到 32×32。

```text
Use case: pixel-art game architecture kit sheet, top-down 2D map.
Asset type: modular building parts for a wuxia town, to be cut out and assembled into tile-based houses.
Primary request: a 4x4 grid of sixteen separate architectural pieces, one piece centered in each cell on a flat magenta #ff00ff background, drawn as straight-on pieces that stack into a top-down house (roof above, wall below).
Scene/backdrop: Chinese wuxia town architecture, low saturation ink-wash palette.
Style/medium: 16-bit pixel art, crisp hard pixels, about 24 colors, no anti-aliasing, no gradients, no glow, no 3D, no outlines.
Composition/framing: strict 4 columns by 4 rows, equal square cells, pieces are axis-aligned rectangles that fill their cell horizontally so adjacent pieces tile seamlessly when stacked.
Cell contents: grey clay roof ridge with two upturned corner tips, mid grey roof tile slope row, dark eave edge with exposed rafter ends, single upturned roof corner.
Second row: off-white plaster wall panel, weathered brick wall panel, wooden door with two leaves and a brass ring, wooden lattice window with three vertical bars.
Third row: grey stone foundation block row, two stone entry steps, dark wooden door post, hanging red paper lantern with tassel.
Fourth row: wooden gate arch top, hanging cloth banner with no writing, cracked and broken temple wall fragment, small stone censer with a wisp of grey smoke.
Color palette: roof #62635b, #4e6968; plaster #d0c6a6, #b3af91; wood #675044, #8d7553; stone #7d897b, #a9ae96; lantern #aa6651, #cf9364; ink #263e38.
Materials/textures: 1 pixel ink joints between planks and bricks, chunky 2-3 pixel lit pixels on top edges, no cast shadows on the background.
Constraints: no text, no letters, no signage copy, no people, no whole buildings, no border lines, nothing touching the cell edges.
```

## 提示词 4：人物图集（characters）

**输出设置**：选择模型支持的 1:1 尺寸，切 4×4 网格，每格降采样到 32×32。

```text
Use case: pixel-art game character spritesheet, top-down 2D RPG map.
Asset type: four wuxia characters, each shown in four facing directions, to be cut into 32x32 sprite frames.
Primary request: a strict 4-column by 4-row grid of sixteen small standing figures, one figure per cell, each centered and standing on the bottom edge of its cell, flat magenta #ff00ff background.
Scene/backdrop: a low-saturation wuxia town, characters at rest.
Style/medium: 16-bit pixel art, crisp hard pixels, about 20 colors, muted ink-wash palette, small heads and slightly elongated robes, no anti-aliasing, no gradients, no glow, no outlines, no 3D.
Composition/framing: columns are four different characters, rows are facing directions top to bottom: facing down toward viewer, facing left, facing right, facing up with back turned. Each figure about 24 pixels tall inside a 32 pixel cell, feet aligned to the same baseline, identical scale across all cells.
Character designs: a young wandering swordsman in a beige cotton robe with a sword on the back; an austere sect master in a dark grey-blue robe with long sleeves; a plain-clothed old apothecary in a jade green robe; a lively inn keeper's daughter in a dusty rose robe with twin braids.
Color palette: robe beige #d0c2a1, grey-blue #647e83, jade #91a68b, rose #b68b8b; hair and ink #354039; skin #d4b690; cinnabar accent #aa6651.
Constraints: no text, no labels, no shadows on the background, no props, no weapons held in hands, no faces rendered larger than 4x4 pixels, nothing touching the cell edges.
```

---

## 提示词 5：野兽与山贼（mobs）——**已实现**

已生成并切图输出到 `assets/mobs/<type>.png`，替换 `town_world.gd` 的 `_draw_mob` 动态帧绘制。
4 行朝向（下/左/右/上）× 4 帧行走，绿幕底抠图，单张 1:1。

**落点**：`xia-ai-town/asserts/mob-<type>-sprite.png`，`type` 取 `wolf` / `bandit` / `boss`；切图后输出到 `assets/mobs/<type>.png`（32×32×16 帧）。

```text
Use case: pixel-art game creature spritesheet, top-down 2D RPG map.
Asset type: one wild animal (or one bandit) shown in four facing directions, to be cut into 32x32 sprite frames.
Primary request: a strict 4-column by 4-row grid of sixteen sprite frames of the SAME single creature, centered in each cell, feet/underside aligned to the same baseline, flat chroma-green #0b8f0b background.
Scene/backdrop: jade-green ink-wash wuxia wilderness.
Style/medium: 16-bit pixel art, crisp hard pixels, about 20 colors, muted palette with a single dusty accent, no anti-aliasing, no gradients, no glow, no outlines, no 3D.
Composition/framing: columns are four walk frames; rows are facing directions top to bottom: facing down toward viewer, facing left, facing right, facing up with back turned. The creature is about 26 pixels wide inside a 32 pixel cell, identical scale across all cells.
Creature design: a lean grey mountain wolf with a scarred left ear and a darker saddle stripe down its back, seen from a high three-quarter angle natural to a top-down map.
Color palette: fur #8a8b78, shadow fur #666f5f, pale muzzle #b6ad91, ink #263e38, single cinnabar accent #aa6651 on the ear scar.
Constraints: no text, no labels, no shadows on the background, no scenery, no human, nothing touching the cell edges.
```

`bandit` 版本把 Creature design 换成：
```text
a wiry mountain bandit in ragged dark-brown cotton, a cloth headwrap and a short sabre slung on the back.
```

`boss`（黑旋风）版本：
```text
a hulking bandit chief in a black padded jacket with an iron shoulder guard, a broad chopping blade resting across his back, seen from the same angle and at 1.3x the height of the ordinary bandits.
```

## 提示词 6：野外山石与树林加密（props，可选）——**已实现**

`草木山石.png` 只有 1 棵树、2 块石头，野外成片铺开时会看出重复。已生成第二张 4×4 道具图集，落点 `xia-ai-town/asserts/草木山石2.png`，
切图后并入 `assets/atlas/props.png`（第二张追加到第 16–31 格）。

```text
Use case: pixel-art game props sheet, top-down 2D map.
Asset type: sixteen additional wilderness props for a 16 px tile map, each centered on a flat magenta #ff00ff background with generous empty space around it.
Style/medium: 16-bit pixel art, crisp pixels, about 24-color muted palette, ink-wash wuxia mood. No anti-aliasing, no gradients, no glow, no soft shadows, no outlines, no 3D.
Composition/framing: strict 4 columns by 4 rows, equal square cells, one object per cell, object occupies roughly 70 percent of its cell, three-quarter top-down angle, bottom of each object near the bottom of its cell.
Cell contents: a second pine tree in a different silhouette, a tall cypress, a bare winter sapling, a clump of dry tall grass, a mossy boulder, a flat slab rock, a rock with lichen patches, a fallen log, a tree stump with rings, a bamboo cluster, a thorn bush, a flowering shrub, a mud puddle outline, a small cairn of stacked stones, a broken wooden cart wheel, a bundle of firewood.
Color palette: jade #476b54, #778f72; celadon #717e72, #8fa582; wood #635e46, #8d7553; stone #7c8273, #a4a58a; cinnabar #aa6651.
Constraints: no text, no labels, no numbers, no people, no creatures, no buildings, no border lines, nothing touching the cell edges.
```

## 提示词 7：UI 边框与纸纹（可选）——**已实现**

落点 `xia-ai-town/asserts/ui-panel.png`，切图后输出为 `assets/ui/panel.png`（128×128，四角各占 32 像素九宫格拉伸），以 `StyleBoxTexture`（`UI.panel_box()`）赋予面板纸墨质感。

```text
Use case: pixel-art game UI nine-slice panel, for a wuxia RPG interface.
Asset type: one panel frame that will be cut into a 3x3 nine-slice and stretched in a Godot UI.
Primary request: a rectangular panel frame with softly torn rice-paper edges, a thin cinnabar-red border line inset from the edge, and a very faint fibre texture in the flat interior. Corner ornaments must stay inside the four corner cells so the middle rows and columns can stretch without distortion.
Style/medium: 16-bit pixel art, crisp pixels, low saturation, ink-wash wuxia mood, about 12 colors.
Composition/framing: one panel filling the whole image with a 1:1 aspect, generous even margin, no drop shadow outside the frame.
Color palette: rice paper #f3f0e7, paper fibre #e4e0d1, cinnabar #aa6651, ink #293b37, muted line #c9c8b6.
Constraints: no text, no letters, no icons, no creatures, no scenery, no gradients, no glow.
```

---

## 切图与落盘流程（当前 2D，非 HD-2D 生产命令）

源图放在 `xia-ai-town/asserts/`（不入库的原始素材），一条命令切好全部图集与合成图：

```bash
uv run --with pillow python xia-ai-town/tools/build_art_assets.py           # 写出 assets/
uv run --with pillow python xia-ai-town/tools/build_art_assets.py --check    # 只校验源图可切
```

产出（`assets/`，进版本库）：

| 输出 | 内容 | 用法 |
|---|---|---|
| `assets/atlas/terrain.png` | 4×4 格，每格 16×16 | 地形，键位见 `town_world.gd` 的 `T_*` 常量 |
| `assets/atlas/props.png` | 4×8 格（含草木山石2），每格 32×32 | 草木山石、野外加密与家具，`P_*` 常量 |
| `assets/atlas/parts.png` | 4×4 格，每格 64×64 | 建筑部件（屋瓦、墙、门、窗、台基、灯笼…），`B_*` 常量 |
| `assets/buildings/<id>.png` | 已合成的整栋房子 | 直接贴到 `BUILDINGS` 的 rect 位置 |
| `assets/characters/<角色>.png` | 4 行朝向 × 4 帧，每格 32×32 | 主角与七位 NPC |
| `assets/mobs/<type>.png` | 4 行朝向 × 4 帧，每格 32×32 | 野狼（wolf）、山贼（bandit）、黑旋风（boss） |
| `assets/ui/panel.png` | 128×128 九宫格贴图（边距 32px） | 纸纹与朱砂回纹边框面板，`UI.panel_box()` |

**约定**：

- **当前 2D：1 美术像素 = 1 世界像素**，地图格 16 世界像素、人物帧 32px，世界按整数档缩放。HD-2D 保留逻辑格与坐标，新图像素密度/视觉高度单独定义，不照抄此旧比例。
- 洋红 `#ff00ff`（道具/建筑部件）与绿幕 `#0b8f0b`（人物）按色度抠图，脚本会连边缘溢色一起减掉。
- 建筑部件**保持原始长宽比**：屋顶本来就宽而扁，塞进正方形会把瓦垄拉成竖条。
- 素材有明显接缝或残留粉边时，改源图后重跑脚本，不要在手改 `assets/`（会被下次生成覆盖）。
