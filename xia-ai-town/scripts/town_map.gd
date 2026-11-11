extends Control

var actor_pos := Vector2(792, 440)
var mobs: Array = []
var detailed: bool = false
var scenic_layout := false
const Scenic = preload("res://scripts/hd2d/scenic_layout.gd")

const Layout = preload("res://scripts/town_layout.gd")

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE

func _draw() -> void:
	if scenic_layout:
		_draw_scenic()
		return
	var inset := Vector2(4, 4)
	var area := size - inset * 2
	var factor := area / Layout.WORLD_SIZE
	draw_style_box(_box(Color("d9ddc7")), Rect2(Vector2.ZERO, size))
	draw_rect(Rect2(inset + Vector2(0, 512) * factor, Vector2(area.x, area.y * 0.4667)), Color("96aa7a"))
	draw_rect(Rect2(inset + Vector2(848, 512) * factor, Vector2(752, 448) * factor), Color("9b9e86"))
	for road in [Rect2(32, 192, 1536, 32), Rect2(768, 192, 64, 432), Rect2(128, 608, 1328, 32)]:
		draw_rect(Rect2(inset + road.position * factor, road.size * factor), Color("c8c3a7"))
	draw_rect(Rect2(inset + Vector2(0, 480) * factor, Vector2(768, 32) * factor), Color("657561"))
	draw_rect(Rect2(inset + Vector2(832, 480) * factor, Vector2(768, 32) * factor), Color("657561"))
	draw_rect(Rect2(inset + Vector2(1152, 384) * factor, Vector2(112, 64) * factor), Color("78999a"))
	draw_rect(Rect2(inset + Vector2(832, 80) * factor, Vector2(288, 192) * factor), Color("b7af8b"))
	for building: Dictionary in Layout.BUILDINGS:
		var rect: Rect2 = building.rect
		draw_rect(Rect2(inset + rect.position * factor, rect.size * factor), Color("596d63"))
	for mob in mobs:
		if mob.get("alive", false):
			draw_circle(inset + Vector2(mob.x, mob.y) * factor, 2.5 if not detailed else 4, Color("a75342"))
	var point := inset + actor_pos * factor
	draw_circle(point, 6 if detailed else 4, Color("f8f3df"))
	draw_circle(point, 4 if detailed else 2.6, Color("b7543c"))
	if detailed:
		var font := get_theme_default_font()
		for i in Layout.BUILDINGS.size():
			var at: Vector2 = inset + (Layout.BUILDINGS[i].rect.position + Vector2(0, -12)) * factor
			draw_string(font, at, str(Layout.BUILDINGS[i].name), HORIZONTAL_ALIGNMENT_LEFT, -1, 15, Color("293b37"))
		draw_string(font, inset + Vector2(155, 810) * factor, "东 山 林 地", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color("f3f0e5"))
		draw_string(font, inset + Vector2(1050, 810) * factor, "后 山 崖", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color("f3f0e5"))

func _box(color: Color) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = color
	box.border_color = Color("a8b19a")
	box.set_border_width_all(1)
	return box

func _draw_scenic() -> void:
	var inset := Vector2(8, 8)
	var area := size - inset * 2
	var factor := area / Scenic.WORLD_SIZE
	draw_style_box(_box(Color("c1ccb0")), Rect2(Vector2.ZERO, size))
	# Hand-drawn looking contours indicate the north terrace and eastern hill.
	for contour: int in range(3):
		var line := PackedVector2Array()
		for x: int in range(0, 1601, 32):
			line.append(inset + Vector2(x, 102 + contour * 44 + sin(x/146.0)*13) * factor)
		draw_polyline(line, Color("aab99a"), 1.0, true)
	for road: Dictionary in Scenic.ROAD_PATHS:
		var line := PackedVector2Array()
		for at: Vector2 in road.points:
			line.append(inset + at * factor)
		draw_polyline(line, Color("e1dcc7"), float(road.width) * factor.y, true)
	var pond := Rect2(inset + Scenic.POND.position * factor, Scenic.POND.size * factor)
	var pond_style := _box(Color("789c99"))
	pond_style.set_corner_radius_all(14 if detailed else 5)
	draw_style_box(pond_style, pond)
	for building: Dictionary in Scenic.BUILDINGS:
		var r := Rect2(inset + building.rect.position * factor, building.rect.size * factor)
		draw_rect(Rect2(r.position + Vector2(2, 3), r.size), Color("7e8e77"))
		draw_rect(r, Color("465c53"))
		draw_line(r.position + Vector2(1, r.size.y*.5), r.position + Vector2(r.size.x-1, r.size.y*.5), Color("899980"), 1)
		if detailed:
			draw_string(get_theme_default_font(), r.position + Vector2(0, -6), str(building.name), HORIZONTAL_ALIGNMENT_LEFT, -1, 15, Color("293b37"))
	for mob: Dictionary in mobs:
		if mob.get("alive", false):
			draw_circle(inset + Vector2(mob.x, mob.y) * factor, 3 if detailed else 1.5, Color("a75342"))
	var point := inset + actor_pos * factor
	draw_circle(point, 6 if detailed else 4, Color("f8f3df"))
	draw_colored_polygon(PackedVector2Array([point + Vector2(0,-5), point + Vector2(4,4), point + Vector2(0,2), point + Vector2(-4,4)]), Color("a34e3f"))
	if detailed:
		draw_string(get_theme_default_font(), inset + Vector2(145,855) * factor, "东山林地", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("546c56"))
		draw_string(get_theme_default_font(), inset + Vector2(1060,860) * factor, "后山石径", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("546c56"))
