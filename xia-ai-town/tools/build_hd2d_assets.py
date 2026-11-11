"""P1 production helpers; invoked only by build_art_assets.py --hd2d.

No legacy outputs are touched. Raw sources remain ignored. All reductions use
nearest + an explicit shared palette, never interpolated alpha or LANCZOS.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DIRECTIONS = ['s', 'sw', 'w', 'nw', 'n', 'ne', 'e', 'se']
FRAME = (64, 80)
COLUMNS = 10
WALK_DURATION = 0.065
P3_ACTORS = {
    'gu_changfeng': ('actors-p3/npc-gu-changfeng.png', 72, 'human'),
    'tie_wushuang': ('actors-p3/npc-tie-wushuang.png', 72, 'human'),
    'su_wanqing': ('actors-p3/npc-su-wanqing.png', 72, 'human'),
    'chen_bansong': ('actors-p3/npc-chen-bansong.png', 72, 'human'),
    'wang_dazhu': ('actors-p3/npc-wang-dazhu.png', 72, 'human'),
    'liu_xiaodie': ('actors-p3/npc-liu-xiaodie.png', 72, 'human'),
    'zhang_mantou': ('actors-p3/npc-zhang-mantou.png', 72, 'human'),
    'wolf': ('actors-p3/mob-wolf.png', 44, 'beast'),
    'bandit': ('actors-p3/mob-bandit.png', 72, 'human'),
    'boss': ('actors-p3/mob-boss.png', 88, 'human'),
}

def key(image: Image.Image, *, preserve_red: bool = False) -> Image.Image:
    a = np.array(image.convert('RGBA')).copy()
    rgb = a[:, :, :3].astype(float)
    magenta = (rgb[:, :, 0] + rgb[:, :, 2]) / 2 - rgb[:, :, 1]
    if preserve_red:
        # A cinnabar foreground needs both R AND B above green to count as
        # magenta. Averaging R/B removed the lamp body (e.g. 143,20,22).
        # Keep the existing fringe policy for other candidate sprites.
        magenta = np.minimum(rgb[:, :, 0], rgb[:, :, 2]) - rgb[:, :, 1]
    # Hard alpha, including chroma fringes; never leave a magenta matte.
    a[:, :, 3] = np.where(magenta > 45, 0, a[:, :, 3])
    a[a[:, :, 3] == 0, :3] = 0
    return Image.fromarray(a)

def quantize(image: Image.Image, colors: int = 32) -> Image.Image:
    alpha = image.getchannel('A')
    rgb = image.convert('RGB').quantize(colors=colors, dither=Image.Dither.NONE).convert('RGBA')
    rgb.putalpha(alpha)
    return rgb

def grid(image: Image.Image, cols: int, rows: int):
    for row in range(rows):
        for col in range(cols):
            yield image.crop((round(col*image.width/cols), round(row*image.height/rows),
                              round((col+1)*image.width/cols), round((row+1)*image.height/rows)))

def sprite(image: Image.Image, canvas: tuple[int, int], height: int, *, preserve_red: bool = False):
    image = key(image, preserve_red=preserve_red)
    box = image.getbbox()
    if not box:
        raise ValueError('Empty sprite')
    trimmed = image.crop(box)
    ratio = min(height/trimmed.height, (canvas[0]-8)/trimmed.width)
    trimmed = trimmed.resize((round(trimmed.width*ratio), round(trimmed.height*ratio)), Image.Resampling.NEAREST)
    out = Image.new('RGBA', canvas)
    x, y = (canvas[0]-trimmed.width)//2, canvas[1]-4-trimmed.height
    out.paste(trimmed, (x, y))
    return quantize(out), {'source_bounds': list(box), 'bounds': [x, y, trimmed.width, trimmed.height],
                          'foot': [canvas[0]//2, canvas[1]-4]}

def p3_actor_atlas(src: Path, frame_height: int, body_height: int) -> tuple[Image.Image, list[dict]]:
    """Convert a generated directional sheet to the runtime 64x96 atlas.

    Image models occasionally render an extra contact pose or let a detached
    prop cross the requested cell seam. We recover the intended poses from
    the opaque x-runs in each row, use a stable row of subject centers as the
    column guide, and merge every nearby pixel into that subject before crop.
    This keeps equipment attached and prevents neighboring poses becoming
    vertical slivers in the runtime sheet.
    """
    source = Image.open(src).convert('RGBA')
    keyed_source = key(source)
    alpha = np.asarray(keyed_source)[:, :, 3] >= 128
    source_height, source_width = alpha.shape
    row_runs: list[list[tuple[int, int]]] = []
    for row in range(len(DIRECTIONS)):
        y0, y1 = round(row * source_height / len(DIRECTIONS)), round((row + 1) * source_height / len(DIRECTIONS))
        projection = alpha[y0:y1].sum(axis=0) > 2
        runs: list[tuple[int, int]] = []
        active = False
        for x, present in enumerate(projection):
            if present and not active:
                start, active = x, True
            if active and (not present or x == source_width - 1):
                runs.append((start, x if not present else x + 1))
                active = False
        row_runs.append(runs)
    counts = [len(runs) for runs in row_runs]
    base_columns = 11 if counts.count(11) >= counts.count(10) else 10
    guide_runs = next((runs for runs in row_runs if len(runs) == base_columns), None)
    if guide_runs is None:
        raise ValueError(f'{src}: cannot find a clean {base_columns}-pose guide row; counts={counts}')
    centers = np.array([(left + right) * 0.5 for left, right in guide_runs], dtype=float)
    boundaries = np.empty(base_columns + 1, dtype=float)
    boundaries[1:-1] = (centers[:-1] + centers[1:]) * 0.5
    boundaries[0] = max(0.0, centers[0] - (centers[1] - centers[0]) * 0.5)
    boundaries[-1] = min(float(source_width), centers[-1] + (centers[-1] - centers[-2]) * 0.5)
    selected = [0, 1] + list(range(3, 11)) if base_columns == 11 else list(range(10))
    sheet = Image.new('RGBA', (FRAME[0] * COLUMNS, frame_height * len(DIRECTIONS)))
    frames = []
    for row in range(len(DIRECTIONS)):
        y0, y1 = round(row * source_height / len(DIRECTIONS)), round((row + 1) * source_height / len(DIRECTIONS))
        for column, source_column in enumerate(selected):
            left = max(0, round(boundaries[source_column]))
            right = min(source_width, round(boundaries[source_column + 1]))
            cell = keyed_source.crop((left, y0, right, y1))
            box = cell.getbbox()
            if box is None:
                raise ValueError(f'{src}: pose row {row} source column {source_column} is empty')
            frame, info = sprite(cell, (FRAME[0], frame_height), body_height)
            sheet.paste(frame, (column * FRAME[0], row * frame_height))
            bounds = frame.getbbox()
            frames.append({'source_bounds': [left + box[0], y0 + box[1], box[2] - box[0], box[3] - box[1]],
                'bounds': [bounds[0], bounds[1], bounds[2]-bounds[0], bounds[3]-bounds[1]],
                'foot': info['foot'], 'rect': [column*FRAME[0], row*frame_height, FRAME[0], frame_height],
                'direction': DIRECTIONS[row], 'animation': 'idle' if column < 2 else 'walk',
                'duration': 0.5 if column < 2 else WALK_DURATION})
    return quantize(sheet, 32), frames


def p3_actor_preview(sheets: dict[str, Image.Image], destination: Path) -> None:
    """Create a nearest-neighbor review strip for all generated identities."""
    destination.mkdir(parents=True, exist_ok=True)
    tile_w, tile_h = 320, 384
    review = Image.new('RGB', (tile_w * 2, tile_h * ((len(sheets)+1)//2)), '#263b38')
    draw = ImageDraw.Draw(review)
    for index, (name, sheet) in enumerate(sheets.items()):
        x, y = index % 2 * tile_w, index // 2 * tile_h
        image = sheet.resize((tile_w, tile_h), Image.Resampling.NEAREST).convert('RGB')
        # The keying stage leaves transparent cells; composite against the
        # review background so magenta remnants remain visible to inspection.
        review.paste(image, (x, y), sheet.resize((tile_w, tile_h), Image.Resampling.NEAREST))
        draw.text((x+8, y+8), name, fill='#eee5ce', stroke_width=2, stroke_fill='#263b38')
    review.save(destination/'p3-actors-review.png')


def hero_atlas(src: Path) -> tuple[Image.Image, list[dict]]:
    """One scale per source row; align planted soles without stretching frames.

    Each independent direction is a 5x2 sheet. The two source rows sometimes
    differ in drawing scale; normalize each cohort together, retaining the
    gait's contact/passing height and width changes inside that cohort.
    """
    sheet = Image.new('RGBA', (FRAME[0]*COLUMNS, FRAME[1]*8))
    frames = []
    for row, direction in enumerate(DIRECTIONS):
        cells = [key(cell) for cell in grid(Image.open(src/f'characters/hero-v2/{direction}.png'), 5, 2)]
        if direction == 's':
            # The model left a second small hilt behind the screen-right ear.
            # Remove only that exterior wedge; keep hair, ear and the real sword.
            for cell in cells:
                a = np.asarray(cell)
                box = cell.getbbox()
                _, xx = np.nonzero(a[box[1]:box[1]+45, :, 3])
                center = float(np.median(xx))
                top = box[1]
                ImageDraw.Draw(cell).polygon([(center+43, top+60), (center+65, top+60),
                    (center+65, top+114), (center+34, top+114), (center+38, top+98),
                    (center+41, top+82)], fill=(0, 0, 0, 0))
        boxes = [cell.getbbox() for cell in cells]
        for col, (cell, box) in enumerate(zip(cells, boxes)):
            if box is None or min(box[0], box[1], cell.width-box[2], cell.height-box[3]) < 3:
                raise ValueError(f'hero {direction}/{col}: empty or crosses source cell boundary')
            cohort = boxes[col//5*5:col//5*5+5]
            ratio = 72 / float(np.median([b[3]-b[1] for b in cohort]))
            # At least one sole is planted in every walking/idle pose. Correct
            # source baseline drift without resizing its body or lifting boots.
            ground = float(box[3])
            # Align to the torso, not the moving hands/sword/extended boot bounds.
            a = np.asarray(cell)
            torso = a[round(box[1]+(box[3]-box[1])*.36):round(box[1]+(box[3]-box[1])*.52)]
            rgb = torso[:,:,:3].astype(int)
            light = (torso[:,:,3] > 0) & (rgb[:,:,0] > 100) & (rgb[:,:,0] >= rgb[:,:,2]-8)
            _, xs = np.nonzero(light)
            center = float(np.median(xs)) if len(xs) else (box[0]+box[2])/2
            trimmed = cell.crop(box)
            trimmed = trimmed.resize((round(trimmed.width*ratio), round(trimmed.height*ratio)), Image.Resampling.NEAREST)
            x = round(FRAME[0]/2+(box[0]-center)*ratio)
            y = FRAME[1]-4-trimmed.height
            if x < 1 or y < 1 or x+trimmed.width >= FRAME[0] or y+trimmed.height > FRAME[1]-1:
                raise ValueError(f'hero {direction}/{col}: aligned sprite exceeds frame: {x,y,trimmed.size}')
            frame = Image.new('RGBA', FRAME)
            frame.paste(trimmed, (x,y))
            ax, ay = col*FRAME[0], row*FRAME[1]
            sheet.paste(frame, (ax,ay))
            b = frame.getbbox()
            frames.append({'source_bounds': list(box), 'source_scale': ratio,
                'source_anchor': [center,ground], 'bounds': [b[0],b[1],b[2]-b[0],b[3]-b[1]],
                'foot': [FRAME[0]//2,FRAME[1]-4], 'rect': [ax,ay,*FRAME], 'direction': direction,
                'animation': 'idle' if col < 2 else 'walk', 'duration': 0.5 if col < 2 else WALK_DURATION})
    return quantize(sheet, 32), frames


def hero_review(sheet: Image.Image, destination: Path) -> None:
    """A labeled contact sheet and a real looping preview of all eight directions."""
    atlas = Image.new('RGBA', sheet.size, '#263b38')
    atlas.alpha_composite(sheet)
    atlas.resize((sheet.width*2, sheet.height*2), Image.Resampling.NEAREST).save(destination/'hero-review.png')
    previews = []
    for col in range(2,COLUMNS):
        preview = Image.new('RGB', (640,480), '#263b38')
        draw = ImageDraw.Draw(preview)
        for row, direction in enumerate(DIRECTIONS):
            cell = sheet.crop((col*FRAME[0], row*FRAME[1], (col+1)*FRAME[0], (row+1)*FRAME[1]))
            cell = cell.resize((128,160), Image.Resampling.NEAREST)
            x, y = row%4*160+16, row//4*240+38
            preview.paste(cell,(x,y),cell)
            draw.text((x+52,y+170),direction.upper(),fill='#eee5ce',font=ImageFont.load_default(size=17))
        previews.append(preview)
    # GIF uses centiseconds: alternate 60/70ms to retain the 520ms cycle.
    previews[0].save(destination/'hero-walk.gif',save_all=True,append_images=previews[1:],
                     duration=[60,70]*4,loop=0,disposal=2)


def periodic(image: Image.Image) -> Image.Image:
    """Repair a narrow edge band, preserving interior pixel clusters.

    Matching opposite edge texels avoids a discontinuity; a repeated preview
    still needs artistic review (mathematical periodicity is not visual quality).
    """
    a = np.array(image.convert('RGB')).astype(float)
    for axis in (0, 1):
        b = np.swapaxes(a, 0, axis)
        edge = (b[0] + b[-1]) / 2
        for i in range(4):
            t = (4-i)/4
            b[i] = b[i]*(1-t)+edge*t
            b[-1-i] = b[-1-i]*(1-t)+edge*t
    return quantize(Image.fromarray(a.astype('uint8')).convert('RGBA'))

def write_import_policy(path: Path, relative: str) -> None:
    mip = relative.startswith('materials/')
    path.with_name(path.name+'.import').write_text(
        '[remap]\nimporter="texture"\ntype="CompressedTexture2D"\n\n[deps]\n'
        f'source_file="res://assets/hd2d/{relative}"\n\n[params]\n'
        'compress/mode=0\ncompress/high_quality=false\ncompress/lossy_quality=1.0\n'
        f'mipmaps/generate={str(mip).lower()}\n'
        'process/fix_alpha_border=true\nprocess/premult_alpha=false\ndetect_3d/compress_to=0\n')

def build(root: Path, check: bool = False, preview_dir: Path | None = None) -> int:
    src, out = root/'asserts/hd2d', root/'assets/hd2d'
    outputs: dict[str, Image.Image] = {}
    sources = [f'characters/hero-v2/{d}.png' for d in DIRECTIONS] + ['materials/surfaces-pixel-v2.png', 'materials/facade-pixel-v3.png', 'materials/pine-clusters-v3.png', 'materials/rock-pixel-p3.png', 'materials/earth-pixel-p3.png', 'components/facade.png', 'props/tree-pixel-v2.png', 'props/lantern.png'] + [path for path, _, _ in P3_ACTORS.values()]
    hashes = {p: hashlib.sha256((src/p).read_bytes()).hexdigest() for p in sources}
    outputs['characters/hero.png'], frames = hero_atlas(src)
    actor_meta = {}
    actor_sheets = {}
    for key_name, (source_name, body_height, kind) in P3_ACTORS.items():
        image, actor_frames = p3_actor_atlas(src/source_name, 96, body_height)
        output_name = f'actors/{key_name}.png'
        outputs[output_name] = image
        actor_sheets[key_name] = image
        actor_meta[key_name] = {
            'sheet': output_name, 'frame_size': [FRAME[0], 96], 'columns': COLUMNS,
            'directions': DIRECTIONS, 'idle_columns': [0, 1], 'walk_columns': list(range(2, COLUMNS)),
            'walk_duration': WALK_DURATION, 'walk_cycle_distance': 78.0 if kind == 'human' else 56.0,
            'pixel_ratio': 0.5, 'visual_kind': kind, 'body_height': body_height,
            'source': source_name, 'source_sha256': hashes[source_name], 'frames': actor_frames,
        }
    # Deliberate native 64px clusters: each texel spans one logical pixel on
    # the house's metric 64px UV repeat. Keep mipmaps for sloping/distant faces.
    for name, cell in zip(['roof', 'plaster', 'wood', 'stone', 'road', 'grass'], grid(Image.open(src/'materials/surfaces-pixel-v2.png'), 3, 2)):
        if name == 'wood':
            cell = cell.transpose(Image.Transpose.ROTATE_90)
        outputs[f'materials/{name}.png'] = periodic(cell.resize((64, 64), Image.Resampling.NEAREST))
    # These replace only the facade/bark surfaces. Keep roof, ground, actors
    # and lamps byte-identical; native palette steps are part of the drawing.
    facade = list(grid(Image.open(src/'materials/facade-pixel-v3.png'), 3, 2))
    for name, cell, size, colors in zip(
            ['plaster','timber','joinery','door_wood','window_paper','bark'], facade,
            [128,64,64,64,64,64], [5,5,6,6,3,5]):
        outputs[f'materials/{name}.png'] = quantize(periodic(
            cell.resize((size,size), Image.Resampling.NEAREST)), colors)
    outputs['materials/pine_needles.png'] = quantize(periodic(Image.open(
        src/'materials/pine-clusters-v3.png').resize((64,64), Image.Resampling.NEAREST)), 6)
    for name, colors in [('rock', 6), ('earth', 6)]:
        outputs[f'materials/{name}.png'] = quantize(periodic(
            Image.open(src/f'materials/{name}-pixel-p3.png').resize((64,64), Image.Resampling.NEAREST)), colors)
    for name, cell in zip(['door', 'window', 'sign', 'banner'], grid(Image.open(src/'components/facade.png'), 2, 2)):
        outputs[f'components/{name}.png'], _ = sprite(cell, (96, 96), 88)
    prop_meta = {}
    for name, canvas, height in [('tree', (128, 192), 180), ('lantern', (48, 64), 56)]:
        source = f'props/{name}-pixel-v2.png' if name == 'tree' else f'props/{name}.png'
        outputs[f'props/{name}.png'], prop_meta[name] = sprite(
            Image.open(src/source), canvas, height, preserve_red=name == 'lantern')
    metadata = {'schema': 2, 'source_sha256': hashes, 'frame_size': list(FRAME), 'directions': DIRECTIONS,
                'columns': COLUMNS, 'idle_columns': [0,1], 'walk_columns': list(range(2,COLUMNS)),
                'walk_duration': WALK_DURATION, 'walk_cycle_distance': 78.0,
                'logical_pixels_per_texel': 0.5, 'visual_body_pixels': 36,
                'environment_style': {'material_size': [64,64], 'logical_pixels_per_texel': 1.0,
                    'palette_colors': 32, 'tree_model': 'props/pine.glb',
                    'facade_source': 'materials/facade-pixel-v3.png', 'facade_palette_colors': [5,5,6,6,3,5],
                    'pine_surface_source': 'materials/pine-clusters-v3.png', 'pine_palette_colors': 6,
                    'tree_geometry': 'closed bough volumes and bent rooted trunk; no tree cards'},
                'frames': frames, 'props': prop_meta,
                'actors': actor_meta,
                'review': 'P1 candidate; direction identity, gait and artistic tiling require visual approval'}
    for name, image in outputs.items():
        print(f'assets/hd2d/{name}: {image.width}x{image.height}')
        pixels = np.asarray(image)
        if not set(np.unique(pixels[:, :, 3])).issubset({0, 255}):
            raise ValueError(f'{name}: non-binary source alpha')
        if name.startswith('materials/') and (not np.array_equal(pixels[0], pixels[-1]) or not np.array_equal(pixels[:,0], pixels[:,-1])):
            raise ValueError(f'{name}: non-periodic texture edges')
        path = out/name
        if check:
            if not path.is_file() or not np.array_equal(np.asarray(Image.open(path).convert('RGBA')), pixels):
                raise ValueError(f'{path}: derived pixels differ; rebuild required')
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        # Rebuild .import policy too; these sidecars are ignored by the repository.
        write_import_policy(path, name)
    if check:
        if json.loads((out/'manifest.json').read_text()) != metadata:
            raise ValueError('manifest differs; rebuild required')
    else:
        (out/'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        hero_review(outputs['characters/hero.png'], preview_dir)
        p3_actor_preview(actor_sheets, preview_dir)
        repeated = Image.new('RGB', (1152,768))
        for i, name in enumerate(['roof','plaster','wood','stone','road','grass']):
            tile = outputs[f'materials/{name}.png']
            tile_preview = tile.resize((128,128), Image.Resampling.NEAREST)
            for y in range(3):
                for x in range(3):
                    repeated.paste(tile_preview, (i%3*384+x*128,i//3*384+y*128))
        repeated.save(preview_dir/'materials-repeat.png')
    print(f'HD2D: {len(frames)} frames, 8 directions, source bounds, hard alpha, periodic edges and derived pixels validated; visual review still required.')
    return 0
