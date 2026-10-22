extends Node2D
class_name TownWorld
## Procedural Qingxi town. Coordinates and seeded terrain follow DESIGN.md §4.
## All public positions use server world pixels; no textures or networking needed.

const TILE: int = 16
const WORLD_SIZE: Vector2 = Vector2(1600, 960)
const GRASS: int = 1
const ROAD: int = 2
const BUILDING: int = 3
const WATER: int = 4
const TREE: int = 5
const CLIFF: int = 6
const SAND: int = 7
const WALL: int = 8
const WELL: int = 9
const TOWN: int = 10
const BUILDINGS: Array[Dictionary] = [
	{"rect": Rect2(64, 64, 160, 96), "name": "剑阳道馆", "kind": "sect", "id": "jianyang"},
	{"rect": Rect2(288, 64, 160, 96), "name": "玄铁堂", "kind": "sect", "id": "xuantie"},
	{"rect": Rect2(512, 64, 160, 96), "name": "烟雨小筑", "kind": "sect", "id": "yanyu"},
	{"rect": Rect2(64, 256, 128, 96), "name": "药铺", "kind": "shop", "id": "clinic"},
	{"rect": Rect2(240, 256, 128, 96), "name": "铁匠铺", "kind": "shop", "id": "smith"},
	{"rect": Rect2(416, 256, 192, 128), "name": "客栈", "kind": "inn", "id": "inn"},
	{"rect": Rect2(1280, 288, 160, 112), "name": "破庙", "kind": "", "id": "temple"},
]
# Static presentation defaults; runtime positions come from the server schedule.
const NPC_DEFAULTS := {
	"gu_changfeng": {"name": "顾长风", "robe": Color("647e83"), "pos": Vector2(144, 183)},
	"tie_wushuang": {"name": "铁无双", "robe": Color("8b7061"), "pos": Vector2(368, 183)},
	"su_wanqing": {"name": "苏婉清", "robe": Color("7b8c83"), "pos": Vector2(592, 183)},
	"chen_bansong": {"name": "陈半松", "robe": Color("91a68b"), "pos": Vector2(128, 375)},
	"wang_dazhu": {"name": "王大柱", "robe": Color("93725a"), "pos": Vector2(304, 375)},
	"liu_xiaodie": {"name": "柳小蝶", "robe": Color("b68b8b"), "pos": Vector2(566, 407)},
	"zhang_mantou": {"name": "张馒头", "robe": Color("858578"), "pos": Vector2(752, 427)},
}

# Art assets: 1 art pixel == 1 world pixel, so integer zoom stays crisp.
const TERRAIN_ATLAS := preload("res://assets/atlas/terrain.png")
const PROPS_ATLAS := preload("res://assets/atlas/props.png")
const PARTS_ATLAS := preload("res://assets/atlas/parts.png")
const BUILDING_DIR: String = "res://assets/buildings/"
const CHARACTER_DIR: String = "res://assets/characters/"
const MOB_DIR: String = "res://assets/mobs/"
const HERO_SHEET := preload("res://assets/characters/玩家初始.png")
const CHAR_CELL: int = 32
const TERRAIN_CELL: int = 16
const PROP_CELL: int = 32
const PART_CELL: int = 64
const CHAR_ROWS: Array[String] = ["down", "left", "right", "up"]

# Terrain atlas cells, matching docs/ART-PROMPTS.md.
const T_DIRT := 0
const T_PAVING := 1
const T_GRASS := 2
const T_BRUSH := 3
const T_SAND := 4
const T_WATER := 5
const T_CLIFF := 6
const T_TRAIL := 7
const T_BRICK := 8
const T_PLASTER := 10
const T_PLINTH := 11
const T_YARD := 12
const T_PLANK := 13

# Props atlas cells.
const P_PINE := 0
const P_DEAD_TREE := 1
const P_SHRUB := 2
const P_BRAMBLE := 3
const P_BOULDER := 4
const P_OUTCROP := 5
const P_WELL := 6
const P_FENCE := 7
const P_TABLE := 8
const P_RACK := 9
const P_LANTERN := 10
const P_BALE := 11
const P_BARREL := 12
const P_HERBS := 13
const P_DUMMY := 14
const P_MAT := 15

# Additional wilderness & scenery props (草木山石2, indices 16..31).
const P_PINE2 := 16
const P_CYPRESS := 17
const P_BARE_TREE := 18
const P_DRY_GRASS := 19
const P_MOSSY_BOULDER := 20
const P_SLAB := 21
const P_LICHEN_ROCK := 22
const P_LOG := 23
const P_STUMP := 24
const P_BAMBOO := 25
const P_THORN_BUSH := 26
const P_FLOWER_SHRUB := 27
const P_PUDDLE := 28
const P_CAIRN := 29
const P_WHEEL := 30
const P_FIREWOOD := 31

# Building parts atlas cells used standalone (gate arch, wall coping).
const RB_GATE := 12
const RB_FOUNDATION := 8

var actor_pos: Vector2 = Vector2(792, 440)
var actor_name: String = "无名客"
var npcs: Array[Dictionary] = []
var schedule: Dictionary = {}
var sense_radius: float = 48.0
var gather_anchor_pos: Vector2 = Vector2(768, 440)
var mobs: Array = []
var walk_time: float = 0.0
var moving: bool = false
var ui_font: Font:
	set(value):
		ui_font = value
		queue_redraw()

var _grid: PackedByteArray = PackedByteArray()
var _static_texture: ImageTexture
var _static_ready: bool = false
var _sheets: Dictionary = {}
var _mob_sheets: Dictionary = {}
var _time: float = 0.0
var hero_facing: String = "down"
var _hero_last: Vector2 = Vector2.ZERO
var _random_state: int = 0
var _last_actor_pos: Vector2 = Vector2.ZERO
var _redraw_elapsed: float = 0.0


func _ready() -> void:
	texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	for key: String in NPC_DEFAULTS:
		var default: Dictionary = NPC_DEFAULTS[key]
		npcs.append({
			"slug": key, "id": key, "name": default["name"],
			"pos": default["pos"], "robe": default["robe"],
			"facing": "down", "moving": false,
		})
	_ensure_grid()
	_hero_last = actor_pos
	_bake_static()


func _process(delta: float) -> void:
	# Main owns movement and walk_time. Refresh at 30 Hz for external mob updates.
	_time += delta
	if actor_pos.distance_squared_to(_hero_last) > 0.25:
		hero_facing = _facing_from(actor_pos - _hero_last)
		_hero_last = actor_pos
	_redraw_elapsed += delta
	if actor_pos != _last_actor_pos or moving or _redraw_elapsed >= 1.0 / 30.0:
		_last_actor_pos = actor_pos
		_redraw_elapsed = 0.0
		queue_redraw()


func can_walk(p: Vector2) -> bool:
	_ensure_grid()
	# A small foot collision box avoids clipping solid tiles while keeping trails open.
	for offset: Vector2 in [Vector2(-5, -3), Vector2(5, -3), Vector2(-5, 3), Vector2(5, 3)]:
		var foot: Vector2 = p + offset
		if foot.x < 0 or foot.y < 0 or foot.x >= WORLD_SIZE.x or foot.y >= WORLD_SIZE.y:
			return false
		var tile: int = _grid[int(foot.y / TILE) * 100 + int(foot.x / TILE)]
		if tile in [BUILDING, WATER, TREE, CLIFF, WALL, WELL]:
			return false
	return true


func nearby() -> Dictionary:
	for building: Dictionary in BUILDINGS:
		var area: Rect2 = building["rect"]
		var kind: String = building["kind"]
		if not kind.is_empty() and area.grow(32).has_point(actor_pos):
			return {"id": building["id"], "name": building["name"], "kind": kind}
	for npc: Dictionary in npcs:
		var at: Vector2 = npc["pos"]
		if actor_pos.distance_to(at) <= 38.0:
			return {"id": npc["slug"], "name": npc["name"], "kind": "npc"}
	return {}


func apply_state(state: Dictionary) -> void:
	schedule = state
	sense_radius = float(state.get("sense_radius", 48.0))
	var anchor: Variant = state.get("gather_anchor", [768, 440])
	if anchor is Array and anchor.size() >= 2:
		gather_anchor_pos = Vector2(float(anchor[0]), float(anchor[1]))
	var by_slug: Dictionary = {}
	for entry: Dictionary in npcs:
		by_slug[entry["slug"]] = entry
	for remote: Variant in state.get("npcs", []):
		if not remote is Dictionary:
			continue
		var slug: String = str(remote.get("slug", ""))
		if not by_slug.has(slug):
			continue
		var entry: Dictionary = by_slug[slug]
		var home_work: Array = _vector_list(remote.get("home_to_work", []))
		var work_gather: Array = _vector_list(remote.get("work_to_gather", []))
		if home_work.is_empty() or work_gather.is_empty():
			continue
		entry["npc_id"] = int(remote.get("id", 0))
		entry["poi"] = str(remote.get("poi", ""))
		entry["paused_until"] = remote.get("paused_until")
		entry["greet_ready"] = bool(remote.get("greet_ready", false))
		entry["deviation"] = remote.get("deviation") if remote.get("deviation") is Dictionary else {}
		entry["home_to_work_pts"] = home_work
		entry["work_to_gather_pts"] = work_gather
		var server_pos: Variant = remote.get("pos", [])
		if server_pos is Array and server_pos.size() >= 2:
			entry["server_pos"] = Vector2(float(server_pos[0]), float(server_pos[1]))


func tick_schedule(now_game: float, freeze_slug: String = "") -> void:
	for entry: Dictionary in npcs:
		if str(entry["slug"]) == freeze_slug:
			continue
		if not entry.has("home_to_work_pts"):
			continue  # 无日程参数（离线/默认项）保持静止
		var previous: Vector2 = entry["pos"]
		var next: Vector2 = schedule_position(entry, now_game)
		entry["pos"] = next
		var step: Vector2 = next - previous
		entry["moving"] = step.length_squared() > 0.25
		if bool(entry["moving"]):
			entry["facing"] = _facing_from(step)


func schedule_position(entry: Dictionary, now_game: float) -> Vector2:
	var sod: float = fposmod(now_game, 86400.0)
	var day: int = int(now_game / 86400.0)
	var phase: Dictionary = _phase_at(sod, entry.get("deviation", {}))
	var kind: String = str(phase.get("kind", "home"))
	var start: float = float(phase.get("start", 0.0))
	var end: float = float(phase.get("end", 86400.0))
	var t: float = (sod - start) / (end - start) if end > start else 0.0
	var home_work: Array = entry.get("home_to_work_pts", [])
	var work_gather: Array = entry.get("work_to_gather_pts", [])
	var nid: int = int(entry.get("npc_id", 0))
	match kind:
		"home":
			return home_work[0] + _pacing(nid, day, sod)
		"work":
			return home_work[-1] + _pacing(nid, day, sod)
		"gather":
			return gather_anchor_pos + _pacing(nid, day, sod)
		"walk_home_to_work":
			return _point_along(home_work, t)
		"walk_work_to_home":
			return _point_along(_reversed(home_work), t)
		"walk_work_to_gather":
			return _point_along(work_gather, t)
		_:
			return _point_along(_reversed(work_gather), t)


func _facing_from(step: Vector2) -> String:
	if absf(step.x) > absf(step.y):
		return "right" if step.x > 0.0 else "left"
	return "down" if step.y > 0.0 else "up"


func _phase_at(sod: float, deviation: Dictionary) -> Dictionary:
	var kind: String = str(deviation.get("kind", "none"))
	if not kind.is_empty() and kind != "none":
		var shift: int = clampi(int(deviation.get("shift", 0)), 0, 3600)
		for p: Dictionary in _deviated_phases(kind, shift):
			if float(p["start"]) <= sod and sod < float(p["end"]):
				return p
		return {"kind": "home", "start": 70200.0, "end": 86400.0}
	for p: Variant in schedule.get("phases", []):
		if p is Dictionary and float(p["start"]) <= sod and sod < float(p["end"]):
			return p
	return {"kind": "home", "start": 70200.0, "end": 86400.0}

func _deviated_phases(kind: String, shift: int) -> Array:
	# 与服务端 schedule._day_phases 完全一致；偏离只平移原时段。
	match kind:
		"gather_early":
			return [
				{"kind": "home", "start": 0.0, "end": 23400.0},
				{"kind": "walk_home_to_work", "start": 23400.0, "end": 25200.0},
				{"kind": "work", "start": 25200.0, "end": float(39600 - shift)},
				{"kind": "walk_work_to_gather", "start": float(39600 - shift), "end": float(40200 - shift)},
				{"kind": "gather", "start": float(40200 - shift), "end": float(49800 - shift)},
				{"kind": "walk_gather_to_work", "start": float(49800 - shift), "end": float(50400 - shift)},
				{"kind": "work", "start": float(50400 - shift), "end": 68400.0},
				{"kind": "walk_work_to_home", "start": 68400.0, "end": 70200.0},
				{"kind": "home", "start": 70200.0, "end": 86400.0},
			]
		"gather_late":
			return [
				{"kind": "home", "start": 0.0, "end": 23400.0},
				{"kind": "walk_home_to_work", "start": 23400.0, "end": 25200.0},
				{"kind": "work", "start": 25200.0, "end": float(39600 + shift)},
				{"kind": "walk_work_to_gather", "start": float(39600 + shift), "end": float(40200 + shift)},
				{"kind": "gather", "start": float(40200 + shift), "end": float(49800 + shift)},
				{"kind": "walk_gather_to_work", "start": float(49800 + shift), "end": float(50400 + shift)},
				{"kind": "work", "start": float(50400 + shift), "end": 68400.0},
				{"kind": "walk_work_to_home", "start": 68400.0, "end": 70200.0},
				{"kind": "home", "start": 70200.0, "end": 86400.0},
			]
		"stay_work":
			return [
				{"kind": "home", "start": 0.0, "end": 23400.0},
				{"kind": "walk_home_to_work", "start": 23400.0, "end": 25200.0},
				{"kind": "work", "start": 25200.0, "end": 68400.0},
				{"kind": "walk_work_to_home", "start": 68400.0, "end": 70200.0},
				{"kind": "home", "start": 70200.0, "end": 86400.0},
			]
		"home_early":
			return [
				{"kind": "home", "start": 0.0, "end": 23400.0},
				{"kind": "walk_home_to_work", "start": 23400.0, "end": 25200.0},
				{"kind": "work", "start": 25200.0, "end": 39600.0},
				{"kind": "walk_work_to_gather", "start": 39600.0, "end": 40200.0},
				{"kind": "gather", "start": 40200.0, "end": 49800.0},
				{"kind": "walk_gather_to_work", "start": 49800.0, "end": 50400.0},
				{"kind": "work", "start": 50400.0, "end": float(68400 - shift)},
				{"kind": "walk_work_to_home", "start": float(68400 - shift), "end": float(70200 - shift)},
				{"kind": "home", "start": float(70200 - shift), "end": 86400.0},
			]
	return []


func _lcg(npc_id: int, day: int, slot: int, axis: int) -> int:
	var s: int = (npc_id * 1103515245 + day * 12345 + slot * 1013904223 + axis * 2654435761) & 0xFFFFFFFF
	return (s * 1664525 + 1013904223) & 0xFFFFFFFF


func _slot_offset(npc_id: int, day: int, slot: int) -> Vector2:
	var sx: int = _lcg(npc_id, day, slot, 0)
	var sy: int = _lcg(npc_id, day, slot, 1)
	var x: float = float((sx >> 8) & 0xFFFF) / 65535.0 * 16.0 - 8.0
	var y: float = float((sy >> 8) & 0xFFFF) / 65535.0 * 12.0 - 6.0
	return Vector2(x, y)


func _pacing(npc_id: int, day: int, sec_of_day: float) -> Vector2:
	var cfg: Dictionary = schedule.get("pacing", {"slot": 600.0, "fade": 120.0})
	var slot_time: float = float(cfg.get("slot", 600.0))
	var fade: float = float(cfg.get("fade", 120.0))
	var slot: int = int(sec_of_day / slot_time) if slot_time > 0.0 else 0
	var prev: int = slot if slot == 0 else slot - 1
	var current: Vector2 = _slot_offset(npc_id, day, slot)
	var previous: Vector2 = _slot_offset(npc_id, day, prev)
	var t: float = minf(1.0, fposmod(sec_of_day, slot_time) / fade) if fade > 0.0 else 1.0
	t = t * t * (3.0 - 2.0 * t)
	return previous.lerp(current, t)


func _vector_list(raw: Variant) -> Array:
	var out: Array[Vector2] = []
	if not raw is Array:
		return out
	for p: Variant in raw:
		if p is Array and p.size() >= 2:
			out.append(Vector2(float(p[0]), float(p[1])))
	return out


func _reversed(points: Array) -> Array:
	var out: Array = points.duplicate()
	out.reverse()
	return out


func _point_along(points: Array, t: float) -> Vector2:
	if points.is_empty():
		return Vector2(768, 440)
	if points.size() == 1:
		return points[0]
	var total: float = 0.0
	for i: int in range(points.size() - 1):
		total += points[i].distance_to(points[i + 1])
	if total <= 0.0:
		return points[0]
	var d: float = clampf(t, 0.0, 1.0) * total
	for i: int in range(points.size() - 1):
		var seg: float = points[i].distance_to(points[i + 1])
		if d <= seg or i == points.size() - 2:
			var r: float = d / seg if seg > 0.0 else 0.0
			return points[i].lerp(points[i + 1], r)
		d -= seg
	return points[-1]


func show_bubble(slug: String, text: String, seconds: float = 6.0) -> void:
	var clean: String = text.strip_edges()
	if clean.is_empty():
		return
	for entry: Dictionary in npcs:
		if str(entry["slug"]) == slug:
			entry["bubble"] = clean
			entry["bubble_until"] = Time.get_ticks_msec() / 1000.0 + seconds
			return


func region_name() -> String:
	for building: Dictionary in BUILDINGS:
		var area: Rect2 = building["rect"]
		if area.grow(20).has_point(actor_pos):
			return building["name"]
	if Rect2(832, 80, 288, 192).has_point(actor_pos):
		return "练武场"
	if Rect2(672, 352, 224, 128).has_point(actor_pos):
		return "镇口"
	if actor_pos.y >= 512:
		return "东山林地" if actor_pos.x < 800 else "后山崖"
	return "清溪镇"


func _ensure_grid() -> void:
	if not _grid.is_empty():
		return
	_grid.resize(6000)
	_grid.fill(GRASS)
	_fill_tiles(Rect2i(1, 2, 98, 28), TOWN)
	_fill_tiles(Rect2i(2, 12, 96, 2), ROAD)
	_fill_tiles(Rect2i(48, 12, 4, 27), ROAD)
	_fill_tiles(Rect2i(8, 38, 83, 2), ROAD)
	for building: Dictionary in BUILDINGS:
		var r: Rect2 = building["rect"]
		_fill_tiles(Rect2i(int(r.position.x / 16), int(r.position.y / 16), int(r.size.x / 16), int(r.size.y / 16)), BUILDING)
	_fill_tiles(Rect2i(52, 5, 18, 12), SAND)
	_fill_tiles(Rect2i(72, 24, 7, 4), WATER)
	_fill_tiles(Rect2i(44, 25, 2, 2), WELL)
	_fill_tiles(Rect2i(0, 30, 100, 2), WALL)
	_fill_tiles(Rect2i(48, 30, 4, 2), ROAD)
	_random_state = 20261016
	for y: int in range(34, 59):
		for x: int in range(1, 46):
			if _grid[y * 100 + x] == GRASS and _mulberry32() < 0.13:
				_grid[y * 100 + x] = TREE
	_random_state = 4396
	for y: int in range(34, 59):
		for x: int in range(54, 99):
			if _grid[y * 100 + x] == GRASS and (y >= 51 or _mulberry32() < 0.09):
				_grid[y * 100 + x] = CLIFF


func _fill_tiles(r: Rect2i, value: int) -> void:
	for y: int in range(r.position.y, r.end.y):
		for x: int in range(r.position.x, r.end.x):
			_grid[y * 100 + x] = value


func _imul32(a: int, b: int) -> int:
	# Low 32 bits without overflowing signed int64 on unsigned JS operands.
	return ((a & 65535) * b + (((a >> 16) * b & 65535) << 16)) & 0xffffffff


func _mulberry32() -> float:
	_random_state = (_random_state + 0x6d2b79f5) & 0xffffffff
	var t: int = _imul32(_random_state ^ (_random_state >> 15), _random_state | 1)
	t = t ^ ((t + _imul32(t ^ (t >> 7), t | 61)) & 0xffffffff)
	return float((t ^ (t >> 14)) & 0xffffffff) / 4294967296.0


func _noise(x: int, y: int, salt: int = 0) -> int:
	return absi((x * 374761393 + y * 668265263 + salt * 69069) ^ ((x + salt) * 1274126177)) % 997


func _font() -> Font:
	return ui_font if ui_font != null else ThemeDB.fallback_font


func _label(canvas: CanvasItem, text: String, center: Vector2, size: int, color: Color, plate: bool = false) -> void:
	var font: Font = _font()
	var width: float = ceilf(font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
	if plate:
		canvas.draw_rect(Rect2(center.x - width / 2 - 5, center.y - size + 1, width + 10, size + 6), Color("263e38e8"))
		canvas.draw_line(Vector2(center.x - width / 2 - 3, center.y + 5), Vector2(center.x + width / 2 + 3, center.y + 5), Color("9c9b6d"))
	canvas.draw_string(font, center + Vector2(-width / 2 + 1, 1), text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, Color("1c302caa"))
	canvas.draw_string(font, center + Vector2(-width / 2, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, color)


# --- static layer -----------------------------------------------------------
# The whole landscape is composited once into a single world-sized texture:
# terrain tiles from the atlas, then props and houses on top. Gameplay sprites
# (hero, NPCs, mobs) are drawn every frame in _draw() so they can depth-sort.


func _terrain_index(tile: int, y: int) -> int:
	match tile:
		TOWN:
			return T_DIRT
		ROAD:
			return T_PAVING if y <= 30 else T_TRAIL
		SAND:
			return T_SAND
		WATER:
			return T_WATER
		CLIFF:
			return T_CLIFF
		BUILDING:
			return T_PLANK
		WALL:
			return T_BRICK
		WELL:
			return T_PAVING
		_:
			return T_GRASS


func _bake_static() -> void:
	var atlas: Image = TERRAIN_ATLAS.get_image()
	var canvas: Image = Image.create(int(WORLD_SIZE.x), int(WORLD_SIZE.y), false, Image.FORMAT_RGBA8)
	canvas.fill(Color("718d72"))
	for y: int in range(60):
		for x: int in range(100):
			var index: int = _terrain_index(_grid[y * 100 + x], y)
			var src: Rect2i = Rect2i((index % 4) * TERRAIN_CELL, (index / 4) * TERRAIN_CELL, TERRAIN_CELL, TERRAIN_CELL)
			canvas.blit_rect(atlas, src, Vector2i(x * TILE, y * TILE))
	# Training yard: packed earth with a single painted circle (the yard tile bakes
	# its own circle, so tiling it would repeat the ring every 16px).
	_fill_bake(canvas, Rect2i(832, 80, 288, 192), T_DIRT)
	_draw_yard_circle(canvas, Vector2(976, 176), 44)
	_fill_bake(canvas, Rect2i(832, 80, 288, 6), T_PLINTH)
	_fill_bake(canvas, Rect2i(832, 266, 288, 6), T_PLINTH)
	_fill_bake(canvas, Rect2i(832, 80, 6, 192), T_PLINTH)
	_fill_bake(canvas, Rect2i(1114, 80, 6, 192), T_PLINTH)
	for building: Dictionary in BUILDINGS:
		var rect: Rect2 = building["rect"]
		_blend_file(canvas, "%s%s.png" % [BUILDING_DIR, building["id"]], rect.position)
	# Pond rim (tiles 72..79 x 24..28) so the water reads as a contained body.
	_fill_bake(canvas, Rect2i(1152, 380, 112, 4), T_PLINTH)
	_fill_bake(canvas, Rect2i(1152, 448, 112, 4), T_PLINTH)
	_fill_bake(canvas, Rect2i(1148, 380, 4, 72), T_PLINTH)
	_fill_bake(canvas, Rect2i(1264, 380, 4, 72), T_PLINTH)
	# Town wall: brick courses with a pale stone coping along the top, and the
	# gate arch bridged over the road opening at x=768..832.
	_fill_bake(canvas, Rect2i(0, 470, 1600, 6), T_PLINTH)
	_fill_bake(canvas, Rect2i(768, 470, 64, 6), T_PAVING)
	_blend_part(canvas, RB_GATE, Vector2(768, 448))
	# Wilderness: terrain first, then clustered pines, cypresses, diverse rocks and forest undergrowth.
	for y: int in range(34, 59):
		for x: int in range(1, 99):
			var tile: int = _grid[y * 100 + x]
			var n: int = _noise(x, y)
			if tile == TREE:
				match n % 6:
					0:
						_blend_prop(canvas, P_PINE if n % 2 == 0 else P_PINE2, Vector2(x * TILE - 8, y * TILE - 16))
					1:
						_blend_prop(canvas, P_CYPRESS if n % 2 == 0 else P_BAMBOO, Vector2(x * TILE - 8, y * TILE - 16))
					2:
						_blend_prop(canvas, P_BRAMBLE if n % 2 == 0 else P_THORN_BUSH, Vector2(x * TILE - 8, y * TILE - 14))
					3:
						_blend_prop(canvas, P_FLOWER_SHRUB if n % 2 == 0 else P_DRY_GRASS, Vector2(x * TILE - 8, y * TILE - 14))
					4:
						_blend_prop(canvas, P_LOG if n % 2 == 0 else P_STUMP, Vector2(x * TILE - 8, y * TILE - 14))
					5:
						if y > 46:
							_blend_prop(canvas, P_BARE_TREE, Vector2(x * TILE - 8, y * TILE - 16))
			elif tile == CLIFF:
				# Diverse boulders, lichen rocks, flat slabs and stone cairns keep the ridge rich.
				if n % 3 == 0:
					var rock_prop: int = P_BOULDER
					match n % 5:
						0: rock_prop = P_OUTCROP if y >= 52 else P_BOULDER
						1: rock_prop = P_MOSSY_BOULDER
						2: rock_prop = P_SLAB
						3: rock_prop = P_LICHEN_ROCK
						4: rock_prop = P_CAIRN
					_blend_prop(canvas, rock_prop, Vector2(x * TILE - 8, y * TILE - 16))
	# Planted border along the north edge of town.
	for x: int in range(24, 1552, 26):
		_blend_prop(canvas, P_SHRUB if x % 3 else P_BRAMBLE, Vector2(x - 8, 26))
	_blend_prop(canvas, P_WELL, Vector2(704, 400))
	_draw_town_furniture(canvas)
	_static_texture = ImageTexture.create_from_image(canvas)
	_static_ready = true
	queue_redraw()


func _fill_bake(canvas: Image, area: Rect2i, index: int) -> void:
	var atlas: Image = TERRAIN_ATLAS.get_image()
	var src: Rect2i = Rect2i((index % 4) * TERRAIN_CELL, (index / 4) * TERRAIN_CELL, TERRAIN_CELL, TERRAIN_CELL)
	for y: int in range(area.position.y, area.end.y, TERRAIN_CELL):
		for x: int in range(area.position.x, area.end.x, TERRAIN_CELL):
			var w: int = mini(TERRAIN_CELL, area.end.x - x)
			var h: int = mini(TERRAIN_CELL, area.end.y - y)
			canvas.blit_rect(atlas, Rect2i(src.position, Vector2i(w, h)), Vector2i(x, y))


func _draw_yard_circle(canvas: Image, center: Vector2, radius: int) -> void:
	# Two painted rings blended into the packed earth (Image.set_pixel overwrites,
	# so the mix is done by hand).
	var paint: Color = Color(0.47, 0.44, 0.33)
	for step: int in range(2):
		var r: float = float(radius - step * 5)
		for deg: int in range(0, 360):
			var rad: float = deg_to_rad(float(deg))
			var at: Vector2 = center + Vector2(cos(rad) * r, sin(rad) * r * 0.62)
			var px: int = clampi(int(at.x), 0, canvas.get_width() - 1)
			var py: int = clampi(int(at.y), 0, canvas.get_height() - 1)
			var alpha: float = 0.5 - step * 0.18
			canvas.set_pixel(px, py, canvas.get_pixel(px, py).lerp(paint, alpha))


func _blend_prop(canvas: Image, index: int, at: Vector2) -> void:
	var atlas: Image = PROPS_ATLAS.get_image()
	var src: Rect2i = Rect2i((index % 4) * PROP_CELL, (index / 4) * PROP_CELL, PROP_CELL, PROP_CELL)
	canvas.blend_rect(atlas, src, Vector2i(at))


func _blend_part(canvas: Image, index: int, at: Vector2) -> void:
	var atlas: Image = PARTS_ATLAS.get_image()
	var src: Rect2i = Rect2i((index % 4) * PART_CELL, (index / 4) * PART_CELL, PART_CELL, PART_CELL)
	canvas.blend_rect(atlas, src, Vector2i(at))


func _blend_file(canvas: Image, path: String, at: Vector2) -> void:
	if not ResourceLoader.exists(path):
		return
	var texture: Texture2D = load(path)
	var image: Image = texture.get_image()
	canvas.blend_rect(image, Rect2i(Vector2i.ZERO, image.get_size()), Vector2i(at))


func _draw_town_furniture(canvas: Image) -> void:
	_blend_prop(canvas, P_TABLE, Vector2(420, 386))
	_blend_prop(canvas, P_RACK, Vector2(836, 90))
	_blend_prop(canvas, P_DUMMY, Vector2(1082, 92))
	_blend_prop(canvas, P_BALE, Vector2(1096, 136))
	_blend_prop(canvas, P_BARREL, Vector2(56, 372))
	_blend_prop(canvas, P_HERBS, Vector2(72, 352))
	_blend_prop(canvas, P_MAT, Vector2(456, 272))
	_blend_prop(canvas, P_LANTERN, Vector2(1264, 300))
	# Fence segments frame the south road and the training yard.
	for x: int in range(880, 1120, 48):
		_blend_prop(canvas, P_FENCE, Vector2(x, 268))
	_blend_prop(canvas, P_SHRUB, Vector2(60, 226))
	_blend_prop(canvas, P_BRAMBLE, Vector2(208, 226))
	_blend_prop(canvas, P_SHRUB, Vector2(393, 228))
	_blend_prop(canvas, P_BRAMBLE, Vector2(640, 225))
	_blend_prop(canvas, P_SHRUB, Vector2(1270, 263))
	_blend_prop(canvas, P_BRAMBLE, Vector2(1444, 263))
	# Town scenery details: bamboo & flowers at Yanyu, firewood at Smith/Inn, broken wheel & cairn at ruins.
	_blend_prop(canvas, P_BAMBOO, Vector2(504, 116))
	_blend_prop(canvas, P_FLOWER_SHRUB, Vector2(674, 122))
	_blend_prop(canvas, P_FLOWER_SHRUB, Vector2(194, 318))
	_blend_prop(canvas, P_FIREWOOD, Vector2(232, 332))
	_blend_prop(canvas, P_FIREWOOD, Vector2(406, 338))
	_blend_prop(canvas, P_STUMP, Vector2(1116, 236))
	_blend_prop(canvas, P_WHEEL, Vector2(1254, 370))
	_blend_prop(canvas, P_CAIRN, Vector2(852, 606))
	_blend_prop(canvas, P_PUDDLE, Vector2(744, 608))
	_blend_prop(canvas, P_WHEEL, Vector2(688, 620))
	_blend_prop(canvas, P_DRY_GRASS, Vector2(648, 622))


# --- frame drawing ----------------------------------------------------------


func _draw() -> void:
	if not _static_ready:
		return
	draw_texture(_static_texture, Vector2.ZERO)
	for building: Dictionary in BUILDINGS:
		var rect: Rect2 = building["rect"]
		_label(self, building["name"], Vector2(rect.get_center().x, rect.position.y + rect.size.y * 0.60), 12, Color("eddfad"), true)
	_label(self, "清 溪 镇", Vector2(800, 470), 13, Color("eddfb5"), true)
	_label(self, "练 武 场", Vector2(976, 74), 13, Color("3c4a3d"), true)
	_label(self, "东 山 林 地", Vector2(416, 563), 15, Color("e6ecc9"), true)
	_label(self, "后 山 崖", Vector2(1232, 563), 15, Color("e2ddc0"), true)
	var actors: Array[Dictionary] = []
	for npc: Dictionary in npcs:
		actors.append({"pos": npc["pos"], "kind": "npc", "data": npc})
	for value: Variant in mobs:
		if not value is Dictionary:
			continue
		var mob: Dictionary = value
		if not bool(mob.get("alive", true)):
			continue
		actors.append({"pos": Vector2(float(mob.get("x", 0)), float(mob.get("y", 0))), "kind": "mob", "data": mob})
	actors.append({"pos": actor_pos, "kind": "hero", "data": {}})
	actors.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return Vector2(a["pos"]).y < Vector2(b["pos"]).y)
	for entry: Dictionary in actors:
		var at: Vector2 = entry["pos"]
		var data: Dictionary = entry["data"]
		match entry["kind"]:
			"hero":
				_draw_character(HERO_SHEET, at, hero_facing, int(walk_time * 9.0) % 4 if moving else 0, true)
				_label(self, actor_name, at + Vector2(0, -34), 12, Color("faf0cb"), true)
			"npc":
				var sheet: Texture2D = _character_sheet(str(data.get("name", "")))
				if sheet != null:
					_draw_character(sheet, at, str(data.get("facing", "down")), int(_time * 8.0) % 4 if bool(data.get("moving", false)) else 0, false)
				_label(self, data["name"], at + Vector2(0, -34), 11, Color("e8e4c7"), true)
				if float(data.get("bubble_until", 0.0)) > Time.get_ticks_msec() / 1000.0:
					_draw_bubble(at, str(data.get("bubble", "")))
			"mob":
				_draw_mob(at, data)


func _character_sheet(name: String) -> Texture2D:
	if _sheets.has(name):
		return _sheets[name]
	var path: String = "%s%s.png" % [CHARACTER_DIR, name]
	var sheet: Texture2D = load(path) if ResourceLoader.exists(path) else null
	_sheets[name] = sheet
	return sheet


func _draw_character(sheet: Texture2D, at: Vector2, facing: String, frame: int, hero: bool) -> void:
	var row: int = CHAR_ROWS.find(facing)
	if row < 0:
		row = 0
	draw_rect(Rect2(at + Vector2(-9, -4), Vector2(18, 5)), Color("203d3640"))
	if hero:
		draw_arc(at + Vector2(0, 1), 11, 0, TAU, 20, Color("e2d4a077"), 1, false)
	var origin: Vector2 = Vector2(at.x - CHAR_CELL * 0.5, at.y - CHAR_CELL + 2)
	draw_texture_rect_region(sheet, Rect2(origin, Vector2(CHAR_CELL, CHAR_CELL)),
		Rect2(frame * CHAR_CELL, row * CHAR_CELL, CHAR_CELL, CHAR_CELL))


func _draw_bubble(at: Vector2, text: String) -> void:
	var clean: String = text.replace("\n", " ").strip_edges()
	if clean.is_empty():
		return
	var limit: int = 13
	var lines: Array[String] = []
	var cursor: int = 0
	while cursor < clean.length() and lines.size() < 2:
		var remain: int = clean.length() - cursor
		if remain <= limit or lines.size() == 1:
			var tail: String = clean.substr(cursor, limit)
			if remain > limit:
				tail = tail.substr(0, tail.length() - 1) + "…"
			lines.append(tail)
			break
		lines.append(clean.substr(cursor, limit))
		cursor += limit
	var font: Font = _font()
	var width: float = 0.0
	for line: String in lines:
		width = maxf(width, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x)
	width = ceilf(width) + 16.0
	var line_height: float = 17.0
	var height: float = lines.size() * line_height + 9.0
	var origin: Vector2 = Vector2(roundf(at.x - width / 2.0), roundf(at.y - 50.0 - height))
	draw_rect(Rect2(origin, Vector2(width, height)), Color("f3f0e7f2"))
	draw_rect(Rect2(origin, Vector2(width, height)), Color("8f9b86"), false, 1.0)
	var tip: Vector2 = Vector2(at.x, origin.y + height + 5.0)
	draw_colored_polygon(PackedVector2Array([
		Vector2(at.x - 5, origin.y + height),
		Vector2(at.x + 5, origin.y + height), tip]), Color("f3f0e7f2"))
	draw_line(Vector2(at.x - 5, origin.y + height), tip, Color("8f9b86"), 1.0)
	draw_line(Vector2(at.x + 5, origin.y + height), tip, Color("8f9b86"), 1.0)
	for i: int in range(lines.size()):
		var line_width: float = font.get_string_size(lines[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x
		draw_string(font, origin + Vector2(roundf((width - line_width) / 2.0), 17.0 + i * line_height),
			lines[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color("2b3a34"))


func _mob_sheet(type: String) -> Texture2D:
	if _mob_sheets.has(type):
		return _mob_sheets[type]
	var path: String = "%s%s.png" % [MOB_DIR, type]
	var sheet: Texture2D = load(path) if ResourceLoader.exists(path) else null
	_mob_sheets[type] = sheet
	return sheet


func _draw_mob(at: Vector2, mob: Dictionary) -> void:
	var type: String = str(mob.get("type", "wolf"))
	var sheet: Texture2D = _mob_sheet(type)
	if sheet != null:
		var frame: int = int(_time * 4.0) % 4
		var row: int = 0
		var scale: float = 1.25 if type == "boss" else 1.0
		var shadow_w: float = 18.0 * scale
		draw_rect(Rect2(at + Vector2(-shadow_w * 0.5, -4), Vector2(shadow_w, 5)), Color("203d3640"))
		var origin: Vector2 = Vector2(at.x - CHAR_CELL * 0.5, at.y - CHAR_CELL + 2)
		draw_texture_rect_region(sheet, Rect2(origin, Vector2(CHAR_CELL, CHAR_CELL)),
			Rect2(frame * CHAR_CELL, row * CHAR_CELL, CHAR_CELL, CHAR_CELL))
	else:
		var body: Color = Color("8c5f53") if type == "boss" else Color("7b7561")
		var scale: float = 1.25 if type == "boss" else 1.0
		draw_rect(Rect2(at + Vector2(-8 * scale, -3), Vector2(16 * scale, 5)), Color("203d3640"))
		draw_rect(Rect2(at + Vector2(-5 * scale, -15 * scale), Vector2(10 * scale, 13 * scale)), body.darkened(0.2))
		draw_rect(Rect2(at + Vector2(-4 * scale, -14 * scale), Vector2(8 * scale, 11 * scale)), body)
		draw_rect(Rect2(at + Vector2(-3 * scale, -21 * scale), Vector2(6 * scale, 6 * scale)), Color("d4b690"))
		draw_rect(Rect2(at + Vector2(-4 * scale, -23 * scale), Vector2(8 * scale, 4 * scale)), Color("354039"))
		draw_line(at + Vector2(5 * scale, -18 * scale), at + Vector2(10 * scale, -4 * scale), Color("c2bea5"), 2)
		if type == "boss":
			draw_rect(Rect2(at + Vector2(-5, -25), Vector2(11, 3)), Color("a25845"))
	var title: String = "%s  Lv.%s" % [str(mob.get("name", "野狼")), str(mob.get("level", 1))]
	_label(self, title, at + Vector2(0, -34), 10, Color("e2c0a1"), true)
