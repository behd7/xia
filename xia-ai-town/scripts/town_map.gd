extends Control
## Retained canvas commands, not a raster/viewport cache. Only points redraw on movement.
const Scenic = preload("res://scripts/hd2d/scenic_layout.gd")
const Layout = preload("res://scripts/town_layout.gd")

class StaticLayer extends Control:
	var map: Control
	func _draw() -> void:
		map._draw_static(self)

var static_layer: StaticLayer
var static_draw_count := 0
var dynamic_draw_count := 0
var _mob_points := PackedVector2Array()
var actor_pos := Vector2(792, 440):
	set(value):
		if actor_pos != value:
			actor_pos = value
			queue_redraw()
var mobs: Array = []:
	set(value):
		mobs = value
		var points := PackedVector2Array()
		for mob: Dictionary in value:
			if mob.get("alive", false):
				points.append(Vector2(mob.x, mob.y))
		if points != _mob_points:
			_mob_points = points
			queue_redraw()
var detailed := false:
	set(value):
		if detailed != value:
			detailed = value
			_invalidate_static()
var scenic_layout := false:
	set(value):
		if scenic_layout != value:
			scenic_layout = value
			_invalidate_static()

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	static_layer = StaticLayer.new()
	static_layer.name = "StaticMap"
	static_layer.map = self
	static_layer.show_behind_parent = true
	static_layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(static_layer)
	static_layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	resized.connect(_invalidate_static)
	theme_changed.connect(_invalidate_static)
	_invalidate_static()

func update_state(point: Vector2, creatures: Array) -> void:
	actor_pos = point
	# Recheck alive/position values: dictionaries may have been mutated in place.
	mobs = creatures

func _invalidate_static() -> void:
	if is_instance_valid(static_layer):
		static_layer.queue_redraw()
	queue_redraw()

func _draw() -> void:
	dynamic_draw_count += 1
	var inset := Vector2(8, 8) if scenic_layout else Vector2(4, 4)
	var factor := (size - inset * 2) / Scenic.WORLD_SIZE
	for at: Vector2 in _mob_points:
		draw_circle(inset + at * factor, (3.0 if detailed else 1.5) if scenic_layout else (4.0 if detailed else 2.5), Color("a75342"))
	var point := inset + actor_pos * factor
	draw_circle(point, 6 if detailed else 4, Color("f8f3df"))
	if scenic_layout:
		draw_colored_polygon(PackedVector2Array([point + Vector2(0,-5), point + Vector2(4,4), point + Vector2(0,2), point + Vector2(-4,4)]), Color("a34e3f"))
	else:
		draw_circle(point, 4 if detailed else 2.6, Color("b7543c"))

func _draw_static(painter: Control) -> void:
	static_draw_count += 1
	if scenic_layout:
		_draw_scenic_static(painter)
		return
	var inset := Vector2(4, 4)
	var area := size - inset * 2
	var factor := area / Layout.WORLD_SIZE
	painter.draw_style_box(_box(Color("d9ddc7")), Rect2(Vector2.ZERO, size))
	painter.draw_rect(Rect2(inset + Vector2(0, 512) * factor, Vector2(area.x, area.y * 0.4667)), Color("96aa7a"))
	painter.draw_rect(Rect2(inset + Vector2(848, 512) * factor, Vector2(752, 448) * factor), Color("9b9e86"))
	for road in [Rect2(32, 192, 1536, 32), Rect2(768, 192, 64, 432), Rect2(128, 608, 1328, 32)]:
		painter.draw_rect(Rect2(inset + road.position * factor, road.size * factor), Color("c8c3a7"))
	painter.draw_rect(Rect2(inset + Vector2(0, 480) * factor, Vector2(768, 32) * factor), Color("657561"))
	painter.draw_rect(Rect2(inset + Vector2(832, 480) * factor, Vector2(768, 32) * factor), Color("657561"))
	painter.draw_rect(Rect2(inset + Vector2(1152, 384) * factor, Vector2(112, 64) * factor), Color("78999a"))
	painter.draw_rect(Rect2(inset + Vector2(832, 80) * factor, Vector2(288, 192) * factor), Color("b7af8b"))
	for building: Dictionary in Layout.BUILDINGS:
		var rect: Rect2 = building.rect
		painter.draw_rect(Rect2(inset + rect.position * factor, rect.size * factor), Color("596d63"))
	if detailed:
		var font := get_theme_default_font()
		for i in Layout.BUILDINGS.size():
			var at: Vector2 = inset + (Layout.BUILDINGS[i].rect.position + Vector2(0, -12)) * factor
			painter.draw_string(font, at, str(Layout.BUILDINGS[i].name), HORIZONTAL_ALIGNMENT_LEFT, -1, 15, Color("293b37"))
		painter.draw_string(font, inset + Vector2(155, 810) * factor, "东 山 林 地", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color("f3f0e5"))
		painter.draw_string(font, inset + Vector2(1050, 810) * factor, "后 山 崖", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color("f3f0e5"))

func _box(color: Color) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = color
	box.border_color = Color("a8b19a")
	box.set_border_width_all(1)
	return box

func _draw_scenic_static(painter: Control) -> void:
	var inset := Vector2(8, 8)
	var area := size - inset * 2
	var factor := area / Scenic.WORLD_SIZE
	painter.draw_style_box(_box(Color("c1ccb0")), Rect2(Vector2.ZERO, size))
	# Hand-drawn looking contours indicate the north terrace and eastern hill.
	for contour: int in range(3):
		var line := PackedVector2Array()
		for x: int in range(0, 1601, 32):
			line.append(inset + Vector2(x, 102 + contour * 44 + sin(x/146.0)*13) * factor)
		painter.draw_polyline(line, Color("aab99a"), 1.0, true)
	for road: Dictionary in Scenic.ROAD_PATHS:
		var line := PackedVector2Array()
		for at: Vector2 in road.points:
			line.append(inset + at * factor)
		painter.draw_polyline(line, Color("e1dcc7"), float(road.width) * factor.y, true)
	var pond := Rect2(inset + Scenic.POND.position * factor, Scenic.POND.size * factor)
	var pond_style := _box(Color("789c99"))
	pond_style.set_corner_radius_all(14 if detailed else 5)
	painter.draw_style_box(pond_style, pond)
	for building: Dictionary in Scenic.BUILDINGS:
		var r := Rect2(inset + building.rect.position * factor, building.rect.size * factor)
		painter.draw_rect(Rect2(r.position + Vector2(2, 3), r.size), Color("7e8e77"))
		painter.draw_rect(r, Color("465c53"))
		painter.draw_line(r.position + Vector2(1, r.size.y*.5), r.position + Vector2(r.size.x-1, r.size.y*.5), Color("899980"), 1)
		if detailed:
			painter.draw_string(get_theme_default_font(), r.position + Vector2(0, -6), str(building.name), HORIZONTAL_ALIGNMENT_LEFT, -1, 15, Color("293b37"))
	if detailed:
		painter.draw_string(get_theme_default_font(), inset + Vector2(145,855) * factor, "东山林地", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("546c56"))
		painter.draw_string(get_theme_default_font(), inset + Vector2(1060,860) * factor, "后山石径", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("546c56"))
