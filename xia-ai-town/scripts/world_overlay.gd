extends Control
## Native-pixel names and bubbles. This overlay never enters 3D post-processing.
const UI = preload("res://scripts/ui_kit.gd")
var labels: Dictionary = {}
var visibility_cache: Dictionary = {}
var query_elapsed := 1.0

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)

func _label(key: String, bubble: bool = false) -> Label:
	if labels.has(key):
		return labels[key]
	var label := UI.label("", 13 if bubble else 14, UI.INK if bubble else Color("f1ead7"))
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	if bubble:
		label.add_theme_stylebox_override("normal", UI.box(Color("f3f0e7f2"), Color("8f9b86"), 2, 8))
	else:
		label.add_theme_color_override("font_shadow_color", Color("20332c"))
		label.add_theme_constant_override("shadow_offset_x", 1)
		label.add_theme_constant_override("shadow_offset_y", 1)
	add_child(label)
	labels[key] = label
	return label

func update(view: Node3D, delta: float) -> void:
	query_elapsed += delta
	var refresh_visibility := query_elapsed >= 0.10
	if refresh_visibility:
		query_elapsed = 0.0
	var keep: Dictionary = {}
	for entry: Dictionary in view.actor_entries:
		var key: String = entry.key
		var actor: Node3D = entry.actor
		var label := _label(key)
		var bubble := _label(key + "/bubble", true)
		keep[key] = true
		keep[key + "/bubble"] = true
		label.text = str(entry.name)
		var depth: float = -view.camera.to_local(actor.global_position).z
		var rect: Rect2 = actor.screen_rect(view.camera) if depth > view.camera.near else Rect2()
		var on_screen: bool = depth > view.camera.near and depth < view.camera.far and rect.intersects(Rect2(Vector2.ZERO, size))
		var protected: bool = key == "hero" or key == view.current_target
		if on_screen and (refresh_visibility or not visibility_cache.has(key) or protected):
			visibility_cache[key] = view.label_visible(actor, protected)
		label.visible = on_screen and bool(visibility_cache.get(key, false))
		label.size = label.get_minimum_size()
		label.position = Vector2(clampf(rect.get_center().x - label.size.x * 0.5, 0, maxf(0, size.x - label.size.x)), maxf(0, rect.position.y - label.size.y - 5)).round()
		var text: String = str(entry.get("bubble", "")).replace("\n", " ").strip_edges()
		bubble.visible = label.visible and not text.is_empty() and float(entry.get("bubble_until", 0.0)) > Time.get_ticks_msec() / 1000.0
		if bubble.visible:
			if text.length() > 26:
				text = text.substr(0, 25) + "…"
			bubble.text = text.substr(0, 13) + ("\n" + text.substr(13) if text.length() > 13 else "")
			bubble.size = bubble.get_minimum_size()
			bubble.position = Vector2(clampf(rect.get_center().x - bubble.size.x * 0.5, 0, maxf(0, size.x - bubble.size.x)), maxf(0, label.position.y - bubble.size.y - 5)).round()
	for key: String in labels.keys():
		if not keep.has(key):
			labels[key].queue_free()
			labels.erase(key)
			visibility_cache.erase(key)
