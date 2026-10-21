#!/usr/bin/env python3
"""Turn the hand-generated source sheets in `asserts/` into Godot-ready art.

Scale convention: 1 art pixel == 1 world pixel. A map tile is 16 world pixels,
props and characters are 32, so integer canvas zoom always lands on whole art
pixels and stays crisp.

Source layout (see docs/ART-PROMPTS.md):
    asserts/地形.png      4x4 terrain cells, magenta gutter between cells
    asserts/草木山石.png   4x4 props on magenta
    asserts/建筑.png       4x4 modular building parts on magenta
    asserts/char-*.png    6x4 walk frames on chroma green

Output (`assets/`, committed):
    assets/atlas/terrain.png      4x4 cells of 16px
    assets/atlas/props.png        4x4 cells of 32px
    assets/atlas/parts.png        4x4 cells of 32px
    assets/buildings/<id>.png     pre-composed houses at world size
    assets/characters/<slug>.png  4 rows (down/left/right/up) x 4 frames of 32px

Usage:
    uv run --with pillow python xia-ai-town/tools/build_art_assets.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "asserts"
OUT = ROOT / "assets"

TERRAIN_CELL = 16
PROP_CELL = 32
PART_CELL = 64
CHAR_CELL = 32
CHAR_ROWS = ("down", "left", "right", "up")
SOURCE_ROWS = {"left": 0, "down": 1, "up": 2}
CHAR_FRAMES = 4

# Terrain atlas indices, matching the cell order in docs/ART-PROMPTS.md.
T_DIRT, T_PAVING, T_GRASS, T_BRUSH, T_SAND, T_WATER, T_CLIFF, T_TRAIL = range(8)
T_BRICK, T_ROOF, T_PLASTER, T_PLINTH, T_YARD, T_PLANK, T_LITTER, T_GRAVEL = range(8, 16)

# Property parts atlas indices.
P_PINE, P_DEAD_TREE, P_SHRUB, P_BRAMBLE = range(4)
P_BOULDER, P_OUTCROP, P_WELL, P_FENCE = range(4, 8)
P_TABLE, P_RACK, P_LANTERN, P_BALE = range(8, 12)
P_BARREL, P_HERBS, P_DUMMY, P_MAT = range(12, 16)

# Building parts atlas indices.
B_RIDGE, B_SLOPE, B_EAVE, B_BEAM = range(4)
B_PLASTER_WALL, B_BRICK_WALL, B_DOOR, B_WINDOW = range(4, 8)
B_FOUNDATION, B_STEPS, B_POST, B_HANGING_LANTERN = range(8, 12)
B_GATE, B_BANNER, B_RUIN, B_CENSER = range(12, 16)

# id -> (name, width, height, roof part, wall part, extras)
BUILDINGS = {
    "jianyang": (160, 96, B_SLOPE, B_PLASTER_WALL, "warm"),
    "xuantie": (160, 96, B_SLOPE, B_BRICK_WALL, "warm"),
    "yanyu": (160, 96, B_SLOPE, B_PLASTER_WALL, "cold"),
    "clinic": (128, 96, B_SLOPE, B_PLASTER_WALL, "warm"),
    "smith": (128, 96, B_SLOPE, B_BRICK_WALL, "warm"),
    "inn": (192, 112, B_SLOPE, B_PLASTER_WALL, "warm"),
    "temple": (160, 112, B_SLOPE, B_BRICK_WALL, "temple"),
}


def bands(mask: np.ndarray, gap: int = 6) -> list[tuple[int, int]]:
    """Collapse a boolean projection into inclusive index bands."""
    idx = np.where(mask)[0]
    if idx.size == 0:
        return []
    out: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for value in idx[1:]:
        value = int(value)
        if value - prev > gap:
            out.append((start, prev))
            start = value
        prev = value
    out.append((start, prev))
    return out


def content_mask(image: Image.Image, background: tuple[int, int, int], tolerance: int = 40) -> np.ndarray:
    pixels = np.array(image.convert("RGB")).astype(np.int16)
    return np.abs(pixels - np.array(background, dtype=np.int16)).sum(axis=2) > tolerance


def cut_grid(image: Image.Image, rows: int, cols: int) -> list[Image.Image]:
    """Split a sheet into equal cells using its non-background bounding box."""
    background = tuple(int(v) for v in np.array(image.convert("RGB"))[2, 2])
    mask = content_mask(image, background)
    row_span = np.where(mask.sum(axis=1) > 4)[0]
    col_span = np.where(mask.sum(axis=0) > 4)[0]
    top, bottom = int(row_span.min()), int(row_span.max()) + 1
    left, right = int(col_span.min()), int(col_span.max()) + 1
    cell_w = (right - left) / cols
    cell_h = (bottom - top) / rows
    cells: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            box = (
                int(round(left + c * cell_w)),
                int(round(top + r * cell_h)),
                int(round(left + (c + 1) * cell_w)),
                int(round(top + (r + 1) * cell_h)),
            )
            cells.append(image.crop(box))
    return cells


def key_magenta(image: Image.Image) -> Image.Image:
    """Remove the magenta backdrop and de-spill its tint from the edges."""
    rgba = np.array(image.convert("RGBA")).astype(np.int16)
    rgb = rgba[:, :, :3]
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    magenta = (red + blue) / 2.0 - green
    alpha = rgba[:, :, 3].astype(np.float32)
    alpha = np.where(magenta > 70, 0.0, alpha * np.clip((70.0 - magenta) / 45.0, 0.0, 1.0))
    spill = np.clip(magenta, 0, None)[:, :, None] * 0.6
    despilled = np.clip(rgb - spill, 0, 255)
    return Image.fromarray(np.dstack([despilled, alpha]).astype(np.uint8), "RGBA")


def key_green(image: Image.Image) -> Image.Image:
    """Remove the chroma-green character backdrop."""
    rgba = np.array(image.convert("RGBA")).astype(np.int16)
    rgb = rgba[:, :, :3]
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    dominance = green - np.maximum(red, blue)
    alpha = rgba[:, :, 3].astype(np.float32)
    alpha = np.where(dominance > 55, 0.0, alpha * np.clip((55.0 - dominance) / 35.0, 0.0, 1.0))
    spill = np.clip(dominance, 0, None)[:, :, None] * np.array([0.0, 0.55, 0.0])
    despilled = np.clip(rgb - spill, 0, 255)
    return Image.fromarray(np.dstack([despilled, alpha]).astype(np.uint8), "RGBA")


def trim(image: Image.Image) -> Image.Image:
    bbox = image.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return image.crop(bbox) if bbox else image


def fit(image: Image.Image, width: int, height: int, anchor: str = "bottom") -> Image.Image:
    """Scale to fit a cell keeping aspect, then anchor the result."""
    ratio = min(width / image.width, height / image.height)
    size = (max(1, int(round(image.width * ratio))), max(1, int(round(image.height * ratio))))
    scaled = image.resize(size, Image.LANCZOS)
    return place(scaled, width, height, anchor)


def place(image: Image.Image, width: int, height: int, anchor: str = "bottom") -> Image.Image:
    cell = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = (width - image.width) // 2
    y = height - image.height if anchor == "bottom" else (height - image.height) // 2
    cell.paste(image, (x, y), image)
    return cell


def atlas(cells: list[Image.Image], rows: int, cols: int) -> Image.Image:
    size = cells[0].size
    sheet = Image.new("RGBA", (size[0] * cols, size[1] * rows), (0, 0, 0, 0))
    for index, cell in enumerate(cells):
        sheet.paste(cell, ((index % cols) * size[0], (index // cols) * size[1]), cell)
    return sheet


def build_terrain() -> Image.Image:
    """Terrain is opaque; a small inset throws away the magenta cell gutter."""
    source = Image.open(SRC / "地形.png").convert("RGBA")
    cells = []
    for cell in cut_grid(source, 4, 4):
        inset = int(round(min(cell.width, cell.height) * 0.055))
        inner = cell.crop((inset, inset, cell.width - inset, cell.height - inset)).convert("RGB")
        cells.append(inner.resize((TERRAIN_CELL, TERRAIN_CELL), Image.BOX).convert("RGBA"))
    return atlas(cells, 4, 4)


def build_parts(filename: str, cell_size: int) -> Image.Image:
    """Cut out, trim and pack props / building parts into square cells.

    Aspect ratio is preserved: a roof is wider than it is tall, and squashing it
    into a square cell is what turns shingles into vertical smears later on.
    """
    source = Image.open(SRC / filename).convert("RGBA")
    cells = [fit(trim(key_magenta(cell)), cell_size, cell_size) for cell in cut_grid(source, 4, 4)]
    return atlas(cells, 4, 4)


def cell_of(sheet: Image.Image, index: int) -> Image.Image:
    cols = sheet.width // PART_CELL
    return sheet.crop(((index % cols) * PART_CELL, (index // cols) * PART_CELL,
                       (index % cols) * PART_CELL + PART_CELL, (index // cols) * PART_CELL + PART_CELL))


def band(image: Image.Image, top: int, height: int) -> Image.Image:
    return image.crop((0, top, image.width, top + height))


def tile_region(source: Image.Image, width: int, height: int) -> Image.Image:
    """Repeat a texture region across an area, cropping the last row/column."""
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(0, height, source.height):
        for x in range(0, width, source.width):
            out.paste(source, (x, y))
    return out


def scaled(image: Image.Image, width: int, height: int) -> Image.Image:
    """Resize a trimmed part to an exact band size (used for uniform textures)."""
    return image.resize((max(1, width), max(1, height)), Image.LANCZOS)


def compose_house(parts: Image.Image, building_id: str) -> Image.Image:
    """Assemble a house from the modular parts.

    Parts keep their art proportions (a roof is wide and short), so the shingle
    pattern stays rectilinear instead of being smeared into stripes. Uniform
    textures (plaster, brick, stone) are tiled instead of stretched.
    """
    width, height, roof, wall, mood = BUILDINGS[building_id]
    slope = trim(cell_of(parts, B_SLOPE))
    plaster = trim(cell_of(parts, B_PLASTER_WALL))
    brick = trim(cell_of(parts, B_BRICK_WALL))
    door = trim(cell_of(parts, B_DOOR))
    window = trim(cell_of(parts, B_WINDOW))
    foundation = trim(cell_of(parts, B_FOUNDATION))
    steps = trim(cell_of(parts, B_STEPS))
    lantern = trim(cell_of(parts, B_HANGING_LANTERN))
    censer = trim(cell_of(parts, B_CENSER))
    ruin = trim(cell_of(parts, B_RUIN))

    base_h = 10
    wall_h = 30
    roof_h = height - wall_h - base_h
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Roof: ridge + shingle field + rafter eave live in one part. Fit it to the
    # roof band height and repeat it sideways (the shingle columns repeat, the
    # rows do not -- stretching or repeating vertically smears the pattern).
    # The shingle cell has opaque side borders; trim them so repeated columns seam cleanly.
    inset_x = max(1, round(slope.width * 0.07))
    shingles = slope.crop((inset_x, 0, slope.width - inset_x, slope.height))
    roof_img = shingles.resize((max(1, round(shingles.width * roof_h / shingles.height)), roof_h), Image.LANCZOS)
    canvas.paste(tile_region(roof_img, width + 12, roof_h), (-6, 0))

    wall_src = brick if wall == B_BRICK_WALL else plaster
    patch = scaled(wall_src, 32, 16) if wall == B_BRICK_WALL else scaled(wall_src, 40, wall_h)
    canvas.paste(tile_region(patch, width, wall_h), (0, roof_h))
    canvas.paste(tile_region(scaled(foundation, 28, base_h), width, base_h), (0, height - base_h))

    door_img = scaled(door, 24, wall_h - 2)
    door_x = (width - 24) // 2
    canvas.paste(door_img, (door_x, height - base_h - door_img.height), door_img)
    step_img = scaled(steps, 34, base_h)
    canvas.paste(step_img, (door_x - 5, height - base_h), step_img)

    win = scaled(window, 22, 20)
    for side in (-1, 1):
        x = width // 2 + side * (width // 4) - win.width // 2
        canvas.paste(win, (x, roof_h + 3), win)
        hang = scaled(lantern, 11, 17)
        canvas.paste(hang, (x + win.width // 2 - hang.width // 2, roof_h - 13), hang)

    if mood == "temple":
        broken = scaled(ruin, 26, 26)
        canvas.paste(broken, (width // 2 - broken.width - 4, roof_h + 1), broken)
        incense = scaled(censer, 20, 20)
        canvas.paste(incense, (width // 2 + 14, height - base_h - incense.height + 2), incense)
    return canvas


def character_frames(source: Image.Image) -> dict[str, list[Image.Image]]:
    """Slice one walk sheet into normalised frames per facing."""
    background = tuple(int(v) for v in np.array(source.convert("RGB"))[2, 2])
    mask = content_mask(source, background, 90)
    row_bands = bands(mask.any(axis=1))
    if len(row_bands) < 3:
        raise SystemExit(f"character sheet rows not detected: {row_bands}")
    frames: dict[str, list[Image.Image]] = {}
    for facing, index in SOURCE_ROWS.items():
        top, bottom = row_bands[index]
        strip = source.crop((0, top, source.width, bottom + 1))
        col_bands = bands(content_mask(strip, background, 90).any(axis=0), gap=12)
        pieces = []
        for left, right in col_bands[:CHAR_FRAMES]:
            piece = trim(key_green(strip.crop((left, 0, right + 1, strip.height))))
            if piece.width >= 8 and piece.height >= 8:
                pieces.append(piece)
        if not pieces:
            raise SystemExit(f"no frames found for {facing}")
        tallest = max(piece.height for piece in pieces)
        scale = (CHAR_CELL - 2) / tallest
        normalised = []
        for piece in pieces:
            size = (max(1, round(piece.width * scale)), max(1, round(piece.height * scale)))
            normalised.append(place(piece.resize(size, Image.LANCZOS), CHAR_CELL, CHAR_CELL))
        while len(normalised) < CHAR_FRAMES:
            normalised.append(normalised[-1])
        frames[facing] = normalised[:CHAR_FRAMES]
    frames["right"] = [frame.transpose(Image.FLIP_LEFT_RIGHT) for frame in frames["left"]]
    return frames


def build_character(path: Path) -> Image.Image:
    frames = character_frames(Image.open(path).convert("RGBA"))
    sheet = Image.new("RGBA", (CHAR_CELL * CHAR_FRAMES, CHAR_CELL * len(CHAR_ROWS)), (0, 0, 0, 0))
    for row, facing in enumerate(CHAR_ROWS):
        for col, frame in enumerate(frames[facing]):
            sheet.paste(frame, (col * CHAR_CELL, row * CHAR_CELL), frame)
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate sources without writing")
    args = parser.parse_args()

    props_atlas = build_parts("草木山石.png", PROP_CELL)
    parts_atlas = build_parts("建筑.png", PART_CELL)
    outputs: dict[Path, Image.Image] = {
        OUT / "atlas" / "terrain.png": build_terrain(),
        OUT / "atlas" / "props.png": props_atlas,
        OUT / "atlas" / "parts.png": parts_atlas,
    }
    for building_id in BUILDINGS:
        outputs[OUT / "buildings" / f"{building_id}.png"] = compose_house(parts_atlas, building_id)
    for path in sorted(SRC.glob("char-*.png")):
        slug = path.stem.removeprefix("char-").removesuffix("-sprite")
        outputs[OUT / "characters" / f"{slug}.png"] = build_character(path)

    for path, image in outputs.items():
        print(f"{path.relative_to(ROOT)}  {image.width}x{image.height}")
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
