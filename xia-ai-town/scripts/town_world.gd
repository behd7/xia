extends Node2D
class_name TownWorld
## Gameplay state and deterministic schedules, in original server world pixels.
## Node2D is retained for compatibility with the existing 2D entry and P1 probes;
## only the optional TownCanvas child loads/draws legacy art.
const Layout = preload("res://scripts/town_layout.gd")
const TILE = Layout.TILE
const WORLD_SIZE = Layout.WORLD_SIZE
const GRASS = Layout.GRASS
const ROAD = Layout.ROAD
const BUILDING = Layout.BUILDING
const WATER = Layout.WATER
const TREE = Layout.TREE
const CLIFF = Layout.CLIFF
const SAND = Layout.SAND
const WALL = Layout.WALL
const WELL = Layout.WELL
const TOWN = Layout.TOWN
const BUILDINGS = Layout.BUILDINGS
const NPC_DEFAULTS = Layout.NPC_DEFAULTS

var actor_pos: Vector2 = Vector2(792, 440)
var actor_name: String = "无名客"
var npcs: Array[Dictionary] = []
var schedule: Dictionary = {}
var sense_radius: float = 48.0
var gather_anchor_pos: Vector2 = Vector2(768, 440)
var mobs: Array = []
var walk_time: float = 0.0
var moving: bool = false

var ui_font: Font
var render_2d := true
var canvas: Node2D
var _grid := PackedByteArray()
var hero_facing := "down"

func initialize() -> void:
	if npcs.is_empty():
		for key: String in NPC_DEFAULTS:
			var defaults: Dictionary = NPC_DEFAULTS[key]
			npcs.append({"slug": key, "id": key, "name": defaults.name,
				"pos": defaults.pos, "robe": defaults.robe, "facing": "down", "moving": false})
	_ensure_grid()

func _ready() -> void:
	initialize()
	if render_2d:
		canvas = load("res://scripts/town_canvas.gd").new()
		canvas.name = "TownCanvas"
		canvas.world = self
		add_child(canvas)

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
	if _grid.is_empty():
		_grid = Layout.create_grid()

# P1 fixture only; the real client never rewrites the shared gameplay layout.
func _fill_tiles(area: Rect2i, value: int) -> void:
	for y: int in range(area.position.y, area.end.y):
		for x: int in range(area.position.x, area.end.x):
			_grid[y * 100 + x] = value
