extends Control
## Native-pixel names and bubbles. This overlay never enters 3D post-processing.
const UI = preload("res://scripts/ui_kit.gd")
const Projector = preload("res://scripts/hd2d/projection.gd")
var text_writes := 0
var labels: Dictionary = {}
var _scenery: Node3D
var _sign_nodes: Array[Node] = []
var _sign_rects: Array[Rect2] = []
var _occupied: Array[Rect2] = []
var _hud_nodes: Array[Control] = []
var placement_failures := 0
var layout_usec := 0

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
		label.add_theme_color_override("font_outline_color", Color("20332c"))
		label.add_theme_constant_override("outline_size", 2)
	add_child(label)
	labels[key] = label
	return label

func _project_signs(view: Node3D) -> void:
	if _scenery != view.scenery:
		_scenery = view.scenery
		_sign_nodes = _scenery.find_children("牌匾文字_*", "Label3D", true, false)
		_hud_nodes.clear()
		var ancestor := get_parent()
		while ancestor != null and not ancestor.has_node("PlayerCard"):
			ancestor = ancestor.get_parent()
		if ancestor != null:
			for name: String in ["PlayerCard", "AtlasCard", "ActionDock", "Toast"]:
				var node := ancestor.get_node_or_null(name)
				if node is Control:
					_hud_nodes.append(node)
			_hud_nodes.append(ancestor.context_label)
	_sign_rects.clear()
	for label: Label3D in _sign_nodes:
		if view.camera.is_position_behind(label.global_position):
			continue
		var width: float = float(label.get_meta("logical_width")) * Projector.SCALE
		var height := 28.0 * Projector.SCALE
		var rect := Rect2(view.camera.unproject_position(label.global_position), Vector2.ZERO)
		for x: float in [-0.5, 0.5]:
			for y: float in [-0.5, 0.5]:
				rect = rect.expand(view.camera.unproject_position(label.global_position + Vector3(x * width, y * height, 0)))
		if rect.intersects(Rect2(Vector2.ZERO, size)):
			_sign_rects.append(rect.grow(4.0))

func _fits_label(rect: Rect2) -> bool:
	if not Rect2(Vector2(2,2), size - Vector2(4,4)).encloses(rect):
		return false
	for obstacle: Rect2 in _occupied:
		if rect.grow(2).intersects(obstacle):
			return false
	return true

func _try_slot(label: Label, at: Vector2) -> bool:
	at = at.clamp(Vector2(2,2), (size - label.size - Vector2(2,2)).max(Vector2(2,2))).round()
	if not _fits_label(Rect2(at, label.size)):
		return false
	label.position = at
	_occupied.append(Rect2(at, label.size).grow(2))
	return true

func _place_label(label: Label, preferred: Vector2, below: float) -> void:
	if _try_slot(label, preferred) or _try_slot(label, Vector2(preferred.x, below)):
		return
	# Expand lazily: ordinary labels allocate no candidate array every frame.
	for ring: int in range(1, 10):
		for offset: Vector2 in [Vector2(0,-1), Vector2(-1,0), Vector2(1,0), Vector2(0,1), Vector2(-1,-1), Vector2(1,-1), Vector2(-1,1), Vector2(1,1)]:
			if _try_slot(label, preferred + offset * Vector2(32,24) * ring):
				return
	var best := INF
	var slot := preferred.clamp(Vector2.ZERO, (size - label.size).max(Vector2.ZERO)).round()
	for y: int in range(2, int(size.y - label.size.y), 24):
		for x: int in range(2, int(size.x - label.size.x), 32):
			var at := Vector2(x,y)
			var distance := at.distance_squared_to(preferred)
			if distance < best and _fits_label(Rect2(at, label.size)):
				best = distance
				slot = at
	if best == INF:
		placement_failures += 1
	label.position = slot
	_occupied.append(Rect2(slot, label.size).grow(2))

func _priority(entry: Dictionary, target: String) -> int:
	return 0 if entry.key == "hero" else (1 if entry.key == target else 2)

func update(view: Node3D, _delta: float) -> void:
	var started := Time.get_ticks_usec()
	_project_signs(view)
	_occupied.assign(_sign_rects)
	for node: Control in _hud_nodes:
		if is_instance_valid(node) and node.is_visible_in_tree():
			_occupied.append(Rect2(node.global_position - global_position, node.size).grow(4))
	placement_failures = 0
	var entries: Array[Dictionary] = view.actor_entries.duplicate()
	entries.sort_custom(func(a: Dictionary, b: Dictionary):
		var left := _priority(a, view.current_target)
		var right := _priority(b, view.current_target)
		return left < right if left != right else str(a.key) < str(b.key))
	var keep: Dictionary = {}
	for entry: Dictionary in entries:
		var key: String = entry.key
		var actor: Node3D = entry.actor
		var label := _label(key)
		var bubble := _label(key + "/bubble", true)
		keep[key] = true
		keep[key + "/bubble"] = true
		var marker := "◆ " if key == "hero" else ("◇ " if key == view.current_target else "")
		if UI.set_text(label, marker + str(entry.name)):
			text_writes += 1
		var depth: float = -view.camera.to_local(actor.global_position).z
		var rect: Rect2 = actor.screen_rect(view.camera) if depth > view.camera.near else Rect2()
		var on_screen: bool = depth > view.camera.near and depth < view.camera.far and rect.intersects(Rect2(Vector2.ZERO, size))
		label.visible = on_screen
		label.size = label.get_minimum_size()
		if on_screen:
			_place_label(label, Vector2(rect.get_center().x - label.size.x * 0.5, rect.position.y - label.size.y - 5), rect.end.y + 5)
		var text: String = str(entry.get("bubble", "")).replace("\n", " ").strip_edges()
		bubble.visible = on_screen and not text.is_empty() and float(entry.get("bubble_until", 0.0)) > Time.get_ticks_msec() / 1000.0
		if bubble.visible:
			if text.length() > 26:
				text = text.substr(0, 25) + "…"
			if UI.set_text(bubble, text.substr(0, 13) + ("\n" + text.substr(13) if text.length() > 13 else "")):
				text_writes += 1
			bubble.size = bubble.get_minimum_size()
			_place_label(bubble, Vector2(rect.get_center().x - bubble.size.x * 0.5, label.position.y - bubble.size.y - 5), maxf(rect.end.y, label.position.y + label.size.y) + 5)
	for key: String in labels.keys():
		if not keep.has(key):
			labels[key].queue_free()
			labels.erase(key)
	layout_usec = Time.get_ticks_usec() - started
