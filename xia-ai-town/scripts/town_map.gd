extends Control

var actor_pos := Vector2(792, 440)
var mobs: Array = []
var detailed: bool = false

const BUILDINGS := [Rect2(64, 64, 160, 96), Rect2(288, 64, 160, 96), Rect2(512, 64, 160, 96), Rect2(64, 256, 128, 96), Rect2(240, 256, 128, 96), Rect2(416, 256, 192, 128), Rect2(1280, 288, 160, 112)]

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE

func _draw() -> void:
	var inset := Vector2(4, 4)
	var area := size - inset * 2
	var factor := area / Vector2(1600, 960)
	draw_style_box(_box(Color("d9ddc7")), Rect2(Vector2.ZERO, size))
	draw_rect(Rect2(inset + Vector2(0, 512) * factor, Vector2(area.x, area.y * 0.4667)), Color("96aa7a"))
	draw_rect(Rect2(inset + Vector2(848, 512) * factor, Vector2(752, 448) * factor), Color("9b9e86"))
	for road in [Rect2(32, 192, 1536, 32), Rect2(768, 192, 64, 432), Rect2(128, 608, 1328, 32)]:
		draw_rect(Rect2(inset + road.position * factor, road.size * factor), Color("c8c3a7"))
	draw_rect(Rect2(inset + Vector2(0, 480) * factor, Vector2(768, 32) * factor), Color("657561"))
	draw_rect(Rect2(inset + Vector2(832, 480) * factor, Vector2(768, 32) * factor), Color("657561"))
	draw_rect(Rect2(inset + Vector2(1152, 384) * factor, Vector2(112, 64) * factor), Color("78999a"))
	draw_rect(Rect2(inset + Vector2(832, 80) * factor, Vector2(288, 192) * factor), Color("b7af8b"))
	for building in BUILDINGS:
		draw_rect(Rect2(inset + building.position * factor, building.size * factor), Color("596d63"))
	for mob in mobs:
		if mob.get("alive", false):
			draw_circle(inset + Vector2(mob.x, mob.y) * factor, 2.5 if not detailed else 4, Color("a75342"))
	var point := inset + actor_pos * factor
	draw_circle(point, 6 if detailed else 4, Color("f8f3df"))
	draw_circle(point, 4 if detailed else 2.6, Color("b7543c"))
	if detailed:
		var font := get_theme_default_font()
		var names := ["剑阳道馆", "玄铁堂", "烟雨小筑", "药铺", "铁匠铺", "客栈", "破庙"]
		for i in BUILDINGS.size():
			var at: Vector2 = inset + (BUILDINGS[i].position + Vector2(0, -12)) * factor
			draw_string(font, at, names[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 15, Color("293b37"))
		draw_string(font, inset + Vector2(155, 810) * factor, "东 山 林 地", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color("f3f0e5"))
		draw_string(font, inset + Vector2(1050, 810) * factor, "后 山 崖", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color("f3f0e5"))

func _box(color: Color) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = color
	box.border_color = Color("a8b19a")
	box.set_border_width_all(1)
	return box
