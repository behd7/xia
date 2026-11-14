extends SceneTree
## Headless correctness, not GPU/performance or physical-device acceptance.
const UI = preload("res://scripts/ui_kit.gd")
const Actions = preload("res://scripts/input_actions.gd")
const Overlay = preload("res://scripts/world_overlay.gd")
const Map = preload("res://scripts/town_map.gd")
var failures := 0
var checks := 0
var activations := 0

class UiMain extends "res://scripts/main.gd":
	func _ready() -> void:
		theme = UI.make_theme()
		_bind_inputs()
		_build_scene()
	func _process(_delta: float) -> void:
		pass

func check(ok: bool, detail: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		push_error("HD2D_UI_FAIL: " + detail)

func _initialize() -> void:
	_run.call_deferred()

func _activated() -> void:
	activations += 1

func _run() -> void:
	root.size = Vector2i(1440, 900)
	var app := UiMain.new()
	root.add_child(app)
	app.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	await process_frame
	await process_frame
	var origin: Button = app.shortcuts[1]
	origin.grab_focus()
	var original_focus_policy := origin.focus_behavior_recursive
	for attempt: int in range(10):
		var body := app._new_modal("焦点回归", "不改变原有业务按钮", "test")
		for name: String in ["第一项", "第二项", "第三项"]:
			var button := UI.button(name, _activated)
			button.name = name
			body.add_child(button)
		await process_frame
		await process_frame
		var focused := root.gui_get_focus_owner()
		check(focused != null and focused.name == "第一项", "first action receives focus")
		check(origin.get_focus_mode_with_override() == Control.FOCUS_NONE, "background focus recursively disabled")
		check(origin.get_mouse_filter_with_override() == Control.MOUSE_FILTER_IGNORE, "background GUI recursively disabled")
		for step: int in range(16):
			focused.find_next_valid_focus().grab_focus()
			focused = root.gui_get_focus_owner()
			check(app.modal.is_ancestor_of(focused), "Tab remains inside modal")
		for side: int in [SIDE_LEFT, SIDE_TOP, SIDE_RIGHT, SIDE_BOTTOM]:
			check(app.modal.is_ancestor_of(focused.find_valid_focus_neighbor(side)), "arrow focus remains in modal")
		app._remove_modal()
		check(root.gui_get_focus_owner() == origin, "original focus restored")
		check(origin.focus_behavior_recursive == original_focus_policy and origin.get_mouse_filter_with_override() != Control.MOUSE_FILTER_IGNORE, "recursive policies restored")
		await process_frame
	check(activations == 0, "navigation never activates background/business actions")
	for dimensions: Vector2i in [Vector2i(1100,720), Vector2i(1440,900), Vector2i(2560,1080)]:
		root.size = dimensions
		app.open_map()
		await process_frame
		await process_frame
		check(root.gui_get_focus_owner() != null and app.modal.is_ancestor_of(root.gui_get_focus_owner()), "map modal focused at " + str(dimensions))
		check(Rect2(Vector2.ZERO, Vector2(dimensions)).encloses(app.expanded_map.get_global_rect()), "map native bounds " + str(dimensions))
		app._remove_modal()
		await process_frame
	app._new_modal("输入框", "保留 caret 与焦点", "dialogue")
	app.player = {"inventory": []}
	app._render_dialogue_footer("main")
	await process_frame
	await process_frame
	var input: LineEdit = app.modal.find_child("DialogueInput", true, false)
	input.text = "保留全文abc"
	input.caret_column = 3
	app._refresh_modal_focus()
	check(root.gui_get_focus_owner() == input and input.caret_column == 3 and input.text == "保留全文abc", "focus refresh does not reset text/caret")
	app._render_dialogue_footer("gift")
	await process_frame
	await process_frame
	check(app.modal.is_ancestor_of(root.gui_get_focus_owner()), "replacement gift footer remains focused inside")
	app._remove_modal()
	await process_frame
	var counts := {}
	for action: String in Actions.KEYS:
		counts[action] = InputMap.action_get_events(action).size()
	Actions.install()
	for action: String in Actions.KEYS:
		check(InputMap.action_get_events(action).size() == counts[action], "idempotent binding " + action)
	var actions: Array = Actions.KEYS.keys() + Actions.MOUSE.keys()
	for i: int in actions.size():
		for other in actions.slice(i+1):
			for event: InputEvent in InputMap.action_get_events(actions[i]):
				for candidate: InputEvent in InputMap.action_get_events(other):
					check(not event.is_match(candidate, true), "distinct business actions " + actions[i] + "/" + other)
	var original := InputMap.action_get_events("open_map")
	InputMap.action_erase_events("open_map")
	var rebound := InputEventKey.new()
	rebound.physical_keycode = KEY_Y
	InputMap.action_add_event("open_map", rebound)
	app._bind_inputs()
	check(app.input_hints.open_map == "Y", "caption follows actual/custom binding")
	rebound.pressed = true
	app._unhandled_key_input(rebound)
	check(app.modal_kind == "map", "shortcut reads intent instead of raw M")
	app._remove_modal()
	rebound.echo = true
	app._unhandled_key_input(rebound)
	check(app.modal == null, "one-shot shortcut ignores repeat")
	InputMap.action_erase_events("open_map")
	for event: InputEvent in original:
		InputMap.action_add_event("open_map", event)
	app._bind_inputs()
	var overlay := Overlay.new()
	app.add_child(overlay)
	overlay.set_anchors_and_offsets_preset(Control.PRESET_TOP_LEFT)
	for dimensions: Vector2i in [Vector2i(1100,720), Vector2i(1440,900), Vector2i(2560,1080)]:
		overlay.size = dimensions
		for anchor: Vector2 in [Vector2(0,0), Vector2(dimensions) * 0.5, Vector2(dimensions)]:
			overlay._occupied.assign([Rect2(0,0,270,190), Rect2(dimensions.x-250,0,250,230), Rect2(dimensions.x*.5-100, dimensions.y*.5-40,200,40)])
			overlay.placement_failures = 0
			var placed: Array[Rect2] = []
			for i: int in range(8):
				for is_bubble: bool in [false, true]:
					var label: Label = overlay._label(str(i) + str(is_bubble), is_bubble)
					label.text = "七位江湖客聚集姓名" if not is_bubble else "江湖路远客官慢行\n完整原生双行气泡"
					label.size = label.get_minimum_size()
					overlay._place_label(label, anchor, anchor.y+80)
					var rect := Rect2(label.position, label.size)
					check(Rect2(Vector2.ZERO, Vector2(dimensions)).encloses(rect), "native label stays in viewport " + str(dimensions))
					for other: Rect2 in placed:
						check(not rect.intersects(other), "cluster labels never overlap")
					placed.append(rect)
			check(overlay.placement_failures == 0, "edge cluster finds all 16 slots")
	var map := Map.new()
	app.add_child(map)
	map.size = Vector2(220,120)
	map.scenic_layout = true
	map.update_state(Vector2(100,100), [{"x":20,"y":30,"alive":true}])
	check(map._mob_points == PackedVector2Array([Vector2(20,30)]), "initial point state synchronized")
	map.mobs[0].alive = false
	map.update_state(Vector2(100,100), map.mobs)
	check(map._mob_points.is_empty(), "in-place alive mutation invalidates dynamic map")
	var text := UI.label("相同")
	check(not UI.set_text(text, "相同") and UI.set_text(text, "更新"), "text changes only once")
	text.free()
	app.free()
	await process_frame
	print("HD2D_UI_CHECKS: %d, failures=%d" % [checks, failures])
	quit(1 if failures else 0)
