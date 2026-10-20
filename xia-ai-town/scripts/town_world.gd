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
		if is_instance_valid(_ink):
			_ink.queue_redraw()
		queue_redraw()

var _grid: PackedByteArray = PackedByteArray()
var _ink: Node2D
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
		})
	_ensure_grid()
	_ink = Node2D.new()
	_ink.name = "StaticTownCanvas"
	_ink.show_behind_parent = true
	add_child(_ink)
	_ink.draw.connect(_draw_static)
	_ink.queue_redraw()
	queue_redraw()


func _process(delta: float) -> void:
	# Main owns movement and walk_time. Refresh at 30 Hz for external mob updates.
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
		entry["pos"] = schedule_position(entry, now_game)


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


func _rect(at: Vector2, size: Vector2, color: Color) -> void:
	_ink.draw_rect(Rect2(at, size), color)


func _line(a: Vector2, b: Vector2, color: Color, width: float = 1.0) -> void:
	_ink.draw_line(a, b, color, width)


func _label(canvas: Node2D, text: String, center: Vector2, size: int, color: Color, plate: bool = false) -> void:
	var font: Font = _font()
	var width: float = ceilf(font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
	if plate:
		canvas.draw_rect(Rect2(center.x - width / 2 - 5, center.y - size + 1, width + 10, size + 6), Color("263e38e8"))
		canvas.draw_line(Vector2(center.x - width / 2 - 3, center.y + 5), Vector2(center.x + width / 2 + 3, center.y + 5), Color("9c9b6d"))
	canvas.draw_string(font, center + Vector2(-width / 2 + 1, 1), text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, Color("1c302caa"))
	canvas.draw_string(font, center + Vector2(-width / 2, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, color)


func _draw_static() -> void:
	_rect(Vector2.ZERO, WORLD_SIZE, Color("718d72"))
	# One cached CanvasItem holds the entire detailed landscape.
	for y: int in range(60):
		for x: int in range(100):
			var at: Vector2 = Vector2(x * 16, y * 16)
			var tile: int = _grid[y * 100 + x]
			var n: int = _noise(x, y)
			match tile:
				TOWN:
					_rect(at, Vector2(16, 16), Color("9aa58a") if n % 3 else Color("94a087"))
					if n % 4 == 0:
						_rect(at + Vector2(n % 11, 9), Vector2(3, 1), Color("7f967b"))
				ROAD:
					_draw_paving(at, n)
				SAND:
					_rect(at, Vector2(16, 16), Color("b7af8b") if n % 3 else Color("b0aa88"))
					_rect(at + Vector2(n % 13, n % 11), Vector2(2, 1), Color("959780"))
				GRASS, TREE, CLIFF:
					_rect(at, Vector2(16, 16), Color("778f72") if n % 3 else Color("7e9677"))
					if n % 3 == 0:
						var blade: Vector2 = at + Vector2(3 + n % 9, 6 + n % 7)
						_line(blade, blade + Vector2(-2, -3), Color("526e58"))
						_line(blade, blade + Vector2(1, -4), Color("8fa582"))
					if tile == GRASS and n % 19 == 0:
						_rect(at + Vector2(5, 6), Vector2(2, 2), Color("d0c19b"))
						_rect(at + Vector2(9, 9), Vector2(2, 2), Color("b39391"))
	# Low planted borders frame the town without creating invisible obstacles.
	for x: int in range(24, 1552, 26):
		_draw_shrub(Vector2(x, 42), x % 3 == 0)
	for building: Dictionary in BUILDINGS:
		_draw_building(building)
	_draw_training_ground()
	_draw_pond()
	_draw_well()
	_draw_wall()
	for y: int in range(34, 59):
		for x: int in range(1, 99):
			var tile: int = _grid[y * 100 + x]
			if tile == TREE:
				_draw_tree(Vector2(x * 16 + 8, y * 16 + 12), _noise(x, y))
			elif tile == CLIFF:
				_draw_rock(Vector2(x * 16, y * 16), _noise(x, y), y >= 51)
	_draw_town_details()
	_label(_ink, "东 山 林 地", Vector2(416, 563), 15, Color("dae1bb"), true)
	_label(_ink, "后 山 崖", Vector2(1232, 563), 15, Color("d5d1b5"), true)


func _draw_paving(at: Vector2, n: int) -> void:
	_rect(at, Vector2(16, 16), Color("717e72"))
	_rect(at + Vector2(1, 1), Vector2(14, 6), Color("a5af96") if n % 2 else Color("9fa991"))
	_rect(at + Vector2(1, 9), Vector2(6, 6), Color("929f89"))
	_rect(at + Vector2(9, 9), Vector2(6, 6), Color("a8af98"))
	_line(at + Vector2(2, 2), at + Vector2(13, 2), Color("bdc1a5"))
	if n % 4 == 0:
		_rect(at + Vector2(0, 6), Vector2(3, 3), Color("6d8a69"))


func _draw_building(building: Dictionary) -> void:
	var r: Rect2 = building["rect"]
	var at: Vector2 = r.position
	var size: Vector2 = r.size
	var temple: bool = building["id"] == "temple"
	var warm: bool = building["id"] in ["smith", "inn", "temple"]
	var roof: Color = Color("62635b") if warm else Color("4e6968")
	var wall_color: Color = Color("b3af91") if temple else Color("d0c6a6")
	var wood: Color = Color("675044")
	_rect(at + Vector2(5, 8), size + Vector2(7, 5), Color("314b3c55"))
	_rect(at, size, Color("5d6b5b"))
	_rect(at + Vector2(3, 4), size - Vector2(6, 8), wall_color)
	var eaves_y: float = at.y + size.y * 0.54
	_rect(Vector2(at.x + 3, eaves_y), Vector2(size.x - 6, 4), Color("8c8067"))
	# Raised stone foundation and two small entry steps remain inside footprint.
	_rect(Vector2(at.x, r.end.y - 9), Vector2(size.x, 9), Color("7d897b"))
	_line(Vector2(at.x + 1, r.end.y - 8), Vector2(r.end.x - 1, r.end.y - 8), Color("b3b59b"))
	var door_x: float = r.get_center().x
	_rect(Vector2(door_x - 13, eaves_y + 5), Vector2(26, r.end.y - eaves_y - 12), Color("3b3930"))
	_rect(Vector2(door_x - 11, eaves_y + 6), Vector2(10, r.end.y - eaves_y - 14), wood)
	_rect(Vector2(door_x + 1, eaves_y + 6), Vector2(10, r.end.y - eaves_y - 14), wood.lightened(0.08))
	_rect(Vector2(door_x - 3, r.end.y - 24), Vector2(1, 3), Color("d3b57a"))
	_rect(Vector2(door_x + 2, r.end.y - 24), Vector2(1, 3), Color("d3b57a"))
	for side: int in [-1, 1]:
		var wx: float = door_x + side * (size.x * 0.30) - 11
		var wy: float = eaves_y + 9
		_rect(Vector2(wx - 2, wy - 2), Vector2(26, 25), wood)
		_rect(Vector2(wx, wy), Vector2(22, 21), Color("9d9f83"))
		for bar: int in range(3, 22, 5):
			_line(Vector2(wx + bar, wy), Vector2(wx + bar, wy + 21), Color("5e6657"))
		_line(Vector2(wx, wy + 10), Vector2(wx + 22, wy + 10), wood)
		var post_x: float = at.x + 7 if side == -1 else r.end.x - 11
		_rect(Vector2(post_x, eaves_y - 2), Vector2(5, r.end.y - eaves_y - 5), wood)
		_rect(Vector2(post_x, eaves_y), Vector2(1, r.end.y - eaves_y - 8), Color("a48566"))
	# Roof silhouette uses upturned corners and overlapping rows of small tiles.
	var outline: PackedVector2Array = PackedVector2Array([
		at + Vector2(0, 4), at + Vector2(8, 10), at + Vector2(size.x / 2, 0),
		at + Vector2(size.x - 8, 10), at + Vector2(size.x, 4),
		Vector2(r.end.x, eaves_y + 5), Vector2(at.x, eaves_y + 5)])
	_ink.draw_colored_polygon(outline, roof.darkened(0.25))
	var roof_height: int = int(eaves_y - at.y)
	for row: int in range(11, roof_height, 6):
		var inset: float = maxf(2, 10 - row * 0.12)
		_rect(at + Vector2(inset, row), Vector2(size.x - inset * 2, 5), roof.lightened(float(row % 3) * 0.035))
		_line(at + Vector2(inset, row + 4), at + Vector2(size.x - inset, row + 4), roof.darkened(0.27))
		for col: int in range(12 + (row % 2) * 3, int(size.x) - 9, 9):
			_line(at + Vector2(col, row), at + Vector2(col - 1, row + 3), roof.lightened(0.20))
	_line(at + Vector2(10, 7), at + Vector2(size.x / 2, 2), Color("a4a591"), 2)
	_line(at + Vector2(size.x / 2, 2), at + Vector2(size.x - 10, 7), Color("9d9e89"), 2)
	_rect(Vector2(at.x, eaves_y + 1), Vector2(size.x, 3), Color("374c48"))
	for col: int in range(3, int(size.x) - 2, 6):
		_rect(Vector2(at.x + col, eaves_y + 3), Vector2(3, 2), Color("8d9a87"))
	_label(_ink, building["name"], Vector2(door_x, eaves_y + 4), 12, Color("eddfad"), true)
	_draw_lantern(Vector2(door_x - 24, eaves_y + 5))
	_draw_lantern(Vector2(door_x + 24, eaves_y + 5))
	if temple:
		_line(at + Vector2(17, 22), at + Vector2(35, 45), Color("3b4e49"), 3)
		_line(Vector2(r.end.x - 20, eaves_y + 15), Vector2(r.end.x - 26, r.end.y - 15), Color("79765f"))
	else:
		_draw_planter(Vector2(at.x + 3, r.end.y - 2))
		_draw_planter(Vector2(r.end.x - 10, r.end.y - 2))


func _draw_lantern(at: Vector2) -> void:
	_line(at, at + Vector2(0, 4), Color("725b43"))
	_rect(at + Vector2(-4, 4), Vector2(8, 11), Color("aa6651"))
	_rect(at + Vector2(-2, 5), Vector2(4, 9), Color("cf9364"))
	_rect(at + Vector2(-4, 4), Vector2(8, 1), Color("554d3b"))
	_rect(at + Vector2(-4, 14), Vector2(8, 1), Color("554d3b"))
	_line(at + Vector2(0, 15), at + Vector2(0, 19), Color("d3b178"))


func _draw_shrub(at: Vector2, flowers: bool = false) -> void:
	_rect(at + Vector2(-7, -3), Vector2(14, 6), Color("637d60"))
	_rect(at + Vector2(-5, -6), Vector2(10, 6), Color("7c956e"))
	_rect(at + Vector2(-3, -7), Vector2(5, 2), Color("98a57d"))
	if flowers:
		_rect(at + Vector2(-3, -5), Vector2(2, 2), Color("c5ad9d"))
		_rect(at + Vector2(4, -3), Vector2(2, 2), Color("d0c492"))


func _draw_planter(at: Vector2) -> void:
	_rect(at + Vector2(0, -6), Vector2(8, 7), Color("8b7760"))
	_rect(at + Vector2(-1, -7), Vector2(10, 2), Color("b4a087"))
	_draw_shrub(at + Vector2(4, -9), true)


func _draw_tree(at: Vector2, n: int) -> void:
	_rect(at + Vector2(-9, -2), Vector2(20, 7), Color("324d3c55"))
	_rect(at + Vector2(-2, -12), Vector2(5, 14), Color("635e46"))
	_rect(at + Vector2(-1, -10), Vector2(1, 11), Color("a08b60"))
	var foliage: Color = Color("476b54") if n % 2 else Color("527559")
	_rect(at + Vector2(-10, -23), Vector2(21, 14), foliage.darkened(0.15))
	_rect(at + Vector2(-13, -21), Vector2(25, 9), foliage)
	_rect(at + Vector2(-7, -29), Vector2(15, 17), foliage)
	_rect(at + Vector2(-4, -31), Vector2(9, 4), foliage.lightened(0.10))
	_rect(at + Vector2(-9, -24), Vector2(8, 3), Color("7c9469"))
	_rect(at + Vector2(3, -20), Vector2(5, 3), Color("6e8960"))
	_rect(at + Vector2(-9, -13), Vector2(7, 2), Color("385b46"))


func _draw_rock(at: Vector2, n: int, ridge: bool) -> void:
	var stone: Color = Color("7c8273") if n % 2 else Color("858878")
	_rect(at + Vector2(0, 1), Vector2(16, 15), Color("4b6054"))
	_rect(at + Vector2(1, 2), Vector2(14, 8 if ridge else 11), stone)
	_rect(at + Vector2(3, 0), Vector2(10, 3), Color("a4a58a"))
	_line(at + Vector2(3, 4), at + Vector2(6, 8), Color("656f60"))
	_line(at + Vector2(6, 8), at + Vector2(5, 14), Color("566456"))
	if ridge:
		_line(at + Vector2(11, 10), at + Vector2(11, 15), Color("8d907b"))
	else:
		_rect(at + Vector2(1, 11), Vector2(5, 2), Color("698366"))


func _draw_training_ground() -> void:
	var r: Rect2 = Rect2(832, 80, 288, 192)
	_ink.draw_rect(r.grow(-3), Color("8a9078"), false, 2)
	_ink.draw_rect(r.grow(-8), Color("c6bb96"), false, 1)
	_ink.draw_circle(r.get_center(), 43, Color("a1a183"), false, 1, false)
	_ink.draw_circle(r.get_center(), 39, Color("c9bd99"), false, 1, false)
	_line(r.get_center() + Vector2(-50, 0), r.get_center() + Vector2(50, 0), Color("c7bd9a"))
	for x: int in [854, 1098]:
		_rect(Vector2(x - 3, 105), Vector2(6, 25), Color("715e46"))
		_rect(Vector2(x - 12, 111), Vector2(24, 4), Color("8d7553"))
		_rect(Vector2(x - 5, 102), Vector2(10, 6), Color("ab9167"))
	for x: int in range(856, 926, 15):
		_line(Vector2(x, 249), Vector2(x + 3, 218), Color("685c45"), 2)
		_line(Vector2(x + 1, 224), Vector2(x + 4, 214), Color("c6c7ae"), 2)
	_rect(Vector2(846, 246), Vector2(88, 4), Color("6a5841"))
	_label(_ink, "练 武 场", Vector2(976, 103), 13, Color("535f4e"))


func _draw_pond() -> void:
	var r: Rect2 = Rect2(1152, 384, 112, 64)
	_ink.draw_rect(r, Color("536f65"))
	_ink.draw_rect(r.grow(-3), Color("71988b"))
	for y: int in range(391, 445, 9):
		var shift: int = (y * 7) % 23
		_line(Vector2(1160 + shift, y), Vector2(1181 + shift, y), Color("a3bc9d"))
		_line(Vector2(1211 - shift / 2.0, y + 2), Vector2(1248 - shift / 2.0, y + 2), Color("85a898"))
	for x: int in range(1153, 1265, 12):
		_rect(Vector2(x, 382), Vector2(10, 4), Color("9b9e84"))
		_rect(Vector2(x, 446), Vector2(10, 3), Color("91977f"))
	for at: Vector2 in [Vector2(1171, 406), Vector2(1238, 429)]:
		_ink.draw_circle(at, 5, Color("6e8d68"))
		_rect(at + Vector2(0, -2), Vector2(2, 2), Color("d1b2a5"))
	for x: int in [1149, 1266]:
		for y: int in [395, 437]:
			_line(Vector2(x, y), Vector2(x - 2, y - 10), Color("6c8057"))
			_line(Vector2(x + 2, y), Vector2(x + 4, y - 13), Color("8b9665"))


func _draw_well() -> void:
	_rect(Vector2(704, 400), Vector2(32, 32), Color("6b796b"))
	_rect(Vector2(706, 403), Vector2(28, 26), Color("a7ad97"))
	_rect(Vector2(711, 407), Vector2(18, 16), Color("354f49"))
	_rect(Vector2(714, 411), Vector2(12, 9), Color("648e80"))
	_line(Vector2(706, 415), Vector2(710, 415), Color("657663"))
	_line(Vector2(729, 419), Vector2(734, 419), Color("657663"))
	_rect(Vector2(705, 393), Vector2(3, 30), Color("776249"))
	_rect(Vector2(732, 393), Vector2(3, 30), Color("776249"))
	_rect(Vector2(702, 392), Vector2(36, 4), Color("8e7958"))
	_line(Vector2(720, 395), Vector2(720, 414), Color("c1b18a"))
	_rect(Vector2(718, 410), Vector2(6, 6), Color("9b8661"))


func _draw_wall() -> void:
	for x: int in range(0, 1600, 16):
		if x >= 768 and x < 832:
			continue
		_rect(Vector2(x, 480), Vector2(16, 32), Color("717d6d"))
		for row: int in range(4):
			var shift: int = 7 if row % 2 else 0
			_rect(Vector2(x + shift, 482 + row * 7), Vector2(8 if shift else 14, 5), Color("8c9580") if (x + row) % 3 else Color("9ca18a"))
		_rect(Vector2(x, 478), Vector2(16, 3), Color("a9ae96"))
		if x % 32 == 0:
			_rect(Vector2(x + 2, 474), Vector2(10, 5), Color("87947e"))
		_rect(Vector2(x, 512), Vector2(16, 4), Color("4e6b5260"))
	# Gate pillars occupy wall cells; the original 64px road opening stays clear.
	for x: int in [753, 832]:
		_rect(Vector2(x, 469), Vector2(15, 43), Color("626f60"))
		_rect(Vector2(x + 2, 471), Vector2(11, 39), Color("a2a58b"))
		_rect(Vector2(x - 2, 468), Vector2(19, 4), Color("b8b89a"))
	# Raised arch is a canopy drawn above the passage, never an invisible blocker.
	_ink.draw_polyline(PackedVector2Array([Vector2(758, 468), Vector2(772, 456), Vector2(800, 452), Vector2(828, 456), Vector2(842, 468)]), Color("50695e"), 7)
	_ink.draw_polyline(PackedVector2Array([Vector2(756, 465), Vector2(772, 453), Vector2(800, 449), Vector2(828, 453), Vector2(844, 465)]), Color("a1ab8a"), 2)
	_label(_ink, "清溪镇", Vector2(800, 468), 13, Color("eddfb5"), true)


func _draw_town_details() -> void:
	# Non-solid small furnishings stay clear of server spawn and all doorways.
	for at: Vector2 in [Vector2(60, 242), Vector2(208, 242), Vector2(393, 244), Vector2(640, 241), Vector2(1270, 279), Vector2(1444, 279)]:
		_draw_shrub(at, true)
	# Apothecary drying trays, smith's anvil, inn tea tables.
	for y: int in [363, 374]:
		_rect(Vector2(73, y), Vector2(24, 7), Color("7f7255"))
		_rect(Vector2(75, y + 1), Vector2(20, 4), Color("a1a078"))
		for x: int in range(77, 94, 5):
			_rect(Vector2(x, y + 2), Vector2(2, 2), Color("5b7653"))
	_rect(Vector2(343, 365), Vector2(12, 9), Color("5e645a"))
	_rect(Vector2(338, 361), Vector2(22, 5), Color("9a9e8d"))
	_line(Vector2(347, 354), Vector2(352, 361), Color("866d4c"), 2)
	for at: Vector2 in [Vector2(445, 402), Vector2(492, 419)]:
		_rect(at + Vector2(-11, -3), Vector2(22, 8), Color("817359"))
		_rect(at + Vector2(-10, -4), Vector2(20, 2), Color("b09c78"))
		_rect(at + Vector2(-3, -6), Vector2(3, 3), Color("d7cba8"))
		_rect(at + Vector2(5, -5), Vector2(3, 2), Color("d7cba8"))
		_rect(at + Vector2(-7, 10), Vector2(14, 3), Color("7a6a50"))
	# A subtle boundary avoids the appearance of an endless playable plain.
	_ink.draw_rect(Rect2(Vector2.ZERO, WORLD_SIZE), Color("405c494d"), false, 3)


func _draw() -> void:
	# Stable foot-depth ordering for the hero, scheduled NPCs and server mobs.
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
				draw_actor(at, Color("d0c2a1"), true)
				_label(self, actor_name, at + Vector2(0, -32), 12, Color("faf0cb"), true)
			"npc":
				draw_actor(at, data["robe"])
				_label(self, data["name"], at + Vector2(0, -31), 11, Color("e8e4c7"), true)
				if float(data.get("bubble_until", 0.0)) > Time.get_ticks_msec() / 1000.0:
					_draw_bubble(at, str(data.get("bubble", "")))
			"mob":
				_draw_mob(at, data)


func _draw_bubble(at: Vector2, text: String) -> void:
	var clean: String = text.replace("\n", " ").strip_edges()
	if clean.is_empty():
		return
	var limit: int = 14
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
		width = maxf(width, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, 12).x)
	width = ceilf(width) + 16.0
	var line_height: float = 15.0
	var height: float = lines.size() * line_height + 8.0
	var origin: Vector2 = Vector2(at.x - width / 2.0, at.y - 45.0 - height)
	draw_rect(Rect2(origin, Vector2(width, height)), Color("f3f0e7f2"))
	draw_rect(Rect2(origin, Vector2(width, height)), Color("8f9b86"), false, 1.0)
	var tip: Vector2 = Vector2(at.x, origin.y + height + 5.0)
	draw_colored_polygon(PackedVector2Array([
		Vector2(at.x - 5, origin.y + height),
		Vector2(at.x + 5, origin.y + height), tip]), Color("f3f0e7f2"))
	draw_line(Vector2(at.x - 5, origin.y + height), tip, Color("8f9b86"), 1.0)
	draw_line(Vector2(at.x + 5, origin.y + height), tip, Color("8f9b86"), 1.0)
	for i: int in range(lines.size()):
		var line_width: float = font.get_string_size(lines[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 12).x
		draw_string(font, origin + Vector2((width - line_width) / 2.0, 15.0 + i * line_height),
			lines[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color("2b3a34"))


func draw_actor(at: Vector2, robe: Color, hero: bool = false) -> void:
	var bob: float = roundf(sin(walk_time * 12.0)) if hero and moving else 0.0
	var stride: float = roundf(sin(walk_time * 12.0) * 2.0) if hero and moving else 0.0
	draw_rect(Rect2(at + Vector2(-7, -2), Vector2(15, 4)), Color("203d3655"))
	if hero:
		draw_arc(at + Vector2(0, 1), 10, 0, TAU, 20, Color("e2d4a077"), 1, false)
	draw_rect(Rect2(at + Vector2(-4, -5 + stride), Vector2(3, 6)), Color("3d4841"))
	draw_rect(Rect2(at + Vector2(2, -5 - stride), Vector2(3, 6)), Color("3d4841"))
	var top: Vector2 = at + Vector2(0, bob)
	draw_colored_polygon(PackedVector2Array([top + Vector2(-5, -19), top + Vector2(5, -19), top + Vector2(8, -5), top + Vector2(-7, -5)]), robe.darkened(0.18))
	draw_rect(Rect2(top + Vector2(-4, -18), Vector2(8, 10)), robe)
	draw_line(top + Vector2(-3, -18), top + Vector2(2, -12), robe.lightened(0.24), 2)
	draw_line(top + Vector2(3, -18), top + Vector2(-1, -12), robe.darkened(0.25), 1)
	draw_rect(Rect2(top + Vector2(-5, -11), Vector2(11, 2)), Color("6a5d45"))
	draw_rect(Rect2(top + Vector2(-8, -16), Vector2(3, 8)), robe.darkened(0.13))
	draw_rect(Rect2(top + Vector2(6, -16), Vector2(3, 8)), robe.darkened(0.13))
	draw_rect(Rect2(top + Vector2(-7, -8), Vector2(2, 3)), Color("c6aa86"))
	draw_rect(Rect2(top + Vector2(6, -8), Vector2(2, 3)), Color("c6aa86"))
	draw_rect(Rect2(top + Vector2(-4, -26), Vector2(9, 9)), Color("333c34"))
	draw_rect(Rect2(top + Vector2(-3, -23), Vector2(7, 6)), Color("d4b690"))
	draw_rect(Rect2(top + Vector2(-3, -27), Vector2(6, 4)), Color("354039"))
	draw_rect(Rect2(top + Vector2(-1, -30), Vector2(4, 3)), Color("354039"))
	draw_rect(Rect2(top + Vector2(2, -22), Vector2(1, 1)), Color("4d493b"))
	if hero:
		draw_rect(Rect2(top + Vector2(-4, -24), Vector2(9, 2)), Color("9c644f"))
		draw_line(top + Vector2(8, -23), top + Vector2(10, -5), Color("5b645b"), 2)
		draw_line(top + Vector2(7, -22), top + Vector2(6, -25), Color("c0b289"), 2)


func _draw_mob(at: Vector2, mob: Dictionary) -> void:
	var type: String = str(mob.get("type", "wolf"))
	if type == "wolf":
		draw_rect(Rect2(at + Vector2(-11, -1), Vector2(23, 4)), Color("253c3355"))
		draw_rect(Rect2(at + Vector2(-10, -11), Vector2(18, 9)), Color("8a8b78"))
		draw_rect(Rect2(at + Vector2(-7, -13), Vector2(12, 5)), Color("a4a28b"))
		draw_rect(Rect2(at + Vector2(5, -14), Vector2(7, 9)), Color("8a8b78"))
		draw_colored_polygon(PackedVector2Array([at + Vector2(5, -13), at + Vector2(5, -19), at + Vector2(9, -14)]), Color("666f5f"))
		draw_rect(Rect2(at + Vector2(9, -10), Vector2(5, 4)), Color("b6ad91"))
		draw_rect(Rect2(at + Vector2(10, -12), Vector2(2, 1)), Color("d0b37f"))
		draw_line(at + Vector2(-10, -10), at + Vector2(-15, -15), Color("777e69"), 3)
		for x: int in [-7, 4]:
			draw_rect(Rect2(at + Vector2(x, -5), Vector2(3, 6)), Color("616c59"))
	else:
		draw_actor(at, Color("8c5f53") if type == "boss" else Color("7b7561"))
		draw_line(at + Vector2(10, -20), at + Vector2(13, -4), Color("c2bea5"), 2)
		if type == "boss":
			draw_rect(Rect2(at + Vector2(-5, -25), Vector2(11, 3)), Color("a25845"))
	var title: String = "%s  Lv.%s" % [str(mob.get("name", "野狼")), str(mob.get("level", 1))]
	_label(self, title, at + Vector2(0, -33), 10, Color("e2c0a1"), true)
