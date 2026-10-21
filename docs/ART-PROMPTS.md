# 瓦片地图美术生成提示词（ART-PROMPTS）

本文档锁定《侠》瓦片美术的**视觉规格**与**生图提示词**，用于把 `town_world.gd` 当前的原生程序绘制替换为图片资源。
地图权威定义见 `DESIGN.md §4`（100×60 网格、tile 16px、世界 1600×960）；本文只描述"画成什么样"，不改坐标与碰撞。

**产出落点**（`xia-ai-town/assets/`，当前工程无任何图片依赖，本目录为新增）：

```
xia-ai-town/assets/tiles/
├─ raw/            # 生图原始图集（使用模型实际输出尺寸，不直接进游戏）
├─ terrain/        # 地形瓦片 16×16（草地/镇土/石铺/沙地/水面/崖岩/墙砖…）
├─ props/          # 草木山石小物 32×32（树/灌木/岩/井/灯笼/家具…）
├─ buildings/      # 建筑部件 32×32（瓦面/屋脊/檐角/白墙/门窗/台基石阶…）
└─ characters/     # 人物 32×32（4 方向 × 4 人物站立帧）
```

## 全局视觉规格

| 项 | 规格 |
|---|---|
| 风格 | 16-bit 像素画，武侠水墨取向：低饱和、留白、点缀朱砂与黛青 |
| 主色 | 玉绿 `#778f72` `#9aa58a`、青灰 `#717e72` `#4e6968`、米白 `#d0c6a6`、墨 `#263e38`、朱砂 `#aa6651`、黛青 `#647e83` |
| 禁忌 | 高饱和霓虹、卡通描边、渐变光晕、3D 渲染、写实照片感、日式浮世绘纹样、文字（除匾额外一律不出现字） |
| 网格 | 严格正方形网格排布，无间距、无边框、无描边分隔，格子之间不得有缝隙或阴影溢出 |
| 背景 | 洋红 `#ff00ff` 纯色垫底（仅用于抠图；最终瓦片须改为真透明或铺满整格） |
| 光照 | 左上光源，向下投 1px 冷暗影；不使用环境光遮蔽大色块 |
| 一致性 | 同一张图集内所有格子共享同一调色板与像素密度（每格 1 像素 = 1 游戏像素的整数倍） |

## 输出尺寸适配

原始图集的分辨率按模型或平台支持的选项选择，优先 **1:1 正方形**；`2048×2048` 是可选示例，不是硬性要求。提示词保留“4 列 × 4 行、16 个等大格子”的构图约束，最终游戏瓦片尺寸在切图阶段确定。

- 平台提供具体尺寸时，选择其支持的正方形尺寸，例如 `1024×1024`；只有明确支持时才选 `2048×2048`。
- 平台提供 `1K / 2K` 或宽高比选项时，按平台含义设置清晰度，并优先选 `1:1`。
- API 没有 `size` 参数时，省略该参数，使用模型默认输出；在提示词中要求正方形图集。写入提示词的尺寸不能代替 API 对尺寸的支持。
- `slice_tileset.py` 会读取图片实际宽高后按 `--grid 4` 分格，无需把源图先放大到 2048。

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

## 提示词 5：野兽与山贼（mobs）——**待生成**

战斗单位目前仍是程序绘制的色块，接到贴图后替换 `town_world.gd` 的 `_draw_mob`。
与人物同一套路：4 行朝向（下/左/右/上）× 4 帧行走，绿幕底，单张 1:1。

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

## 提示词 6：野外山石与树林加密（props，可选）——**待生成**

`草木山石.png` 只有 1 棵树、2 块石头，野外成片铺开时会看出重复。若要加密，请生成第二张 4×4 道具图集，落点 `xia-ai-town/asserts/草木山石2.png`，
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

## 提示词 7：UI 边框与纸纹（可选）——**待生成**

界面现在是纯色 StyleBox；若要换成纸纹与朱砂边框，落点 `xia-ai-town/asserts/ui-panel.png`
（九宫格：四角各占 1/4 边长，中段可拉伸），切 3×3 后以 `StyleBoxTexture` 替代 `ui_kit.gd` 的 `box()`。

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

## 切图与落盘流程

源图放在 `xia-ai-town/asserts/`（不入库的原始素材），一条命令切好全部图集与合成图：

```bash
uv run --with pillow python xia-ai-town/tools/build_art_assets.py           # 写出 assets/
uv run --with pillow python xia-ai-town/tools/build_art_assets.py --check    # 只校验源图可切
```

产出（`assets/`，进版本库）：

| 输出 | 内容 | 用法 |
|---|---|---|
| `assets/atlas/terrain.png` | 4×4 格，每格 16×16 | 地形，键位见 `town_world.gd` 的 `T_*` 常量 |
| `assets/atlas/props.png` | 4×4 格，每格 32×32 | 草木山石与家具，`P_*` 常量 |
| `assets/atlas/parts.png` | 4×4 格，每格 64×64 | 建筑部件（屋瓦、墙、门、窗、台基、灯笼…），`B_*` 常量 |
| `assets/buildings/<id>.png` | 已合成的整栋房子 | 直接贴到 `BUILDINGS` 的 rect 位置 |
| `assets/characters/<角色>.png` | 4 行朝向 × 4 帧，每格 32×32 | 主角与七位 NPC |

**约定**：

- **1 美术像素 = 1 世界像素**。地图格 16 世界像素，人物 32 世界像素；整数倍（1×/2×/3×）缩放才不会糊。
- 洋红 `#ff00ff`（道具/建筑部件）与绿幕 `#0b8f0b`（人物）按色度抠图，脚本会连边缘溢色一起减掉。
- 建筑部件**保持原始长宽比**：屋顶本来就宽而扁，塞进正方形会把瓦垄拉成竖条。
- 素材有明显接缝或残留粉边时，改源图后重跑脚本，不要在手改 `assets/`（会被下次生成覆盖）。
