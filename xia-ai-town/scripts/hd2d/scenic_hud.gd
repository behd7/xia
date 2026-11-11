extends RefCounted
## Compact in-world HUD. Main remains owner of all input and server state.
const UI = preload("res://scripts/ui_kit.gd")
const MapView = preload("res://scripts/town_map.gd")
const PAPER := Color("eee8d8")
const MUTED := Color("c2c9b5")

static func _panel(main: Control, name: String, preset: int, offsets: Rect2) -> PanelContainer:
	var panel := PanelContainer.new()
	panel.name = name
	main.add_child(panel)
	panel.set_anchors_and_offsets_preset(preset)
	panel.offset_left = offsets.position.x
	panel.offset_top = offsets.position.y
	panel.offset_right = offsets.end.x
	panel.offset_bottom = offsets.end.y
	var style := UI.box(Color(0.13, 0.19, 0.16, 0.87), Color(0.65, 0.68, 0.52, 0.44), 2, 12)
	panel.add_theme_stylebox_override("panel", style)
	return panel

static func _button(text: String, callback: Callable) -> Button:
	var button := UI.button(text, callback)
	button.custom_minimum_size.y = 34
	button.add_theme_font_size_override("font_size", 14)
	for state: String in ["normal", "hover", "pressed"]:
		var fill := Color(0.16, 0.23, 0.19, 0.86) if state == "normal" else Color("4b604c")
		button.add_theme_stylebox_override(state, UI.box(fill, Color(0.65, 0.68, 0.52, 0.45), 2, 9))
		button.add_theme_color_override("font_" + ("color" if state == "normal" else state + "_color"), PAPER)
	return button

static func build(main: Control) -> void:
	var player := _panel(main, "PlayerCard", Control.PRESET_TOP_LEFT, Rect2(20, 20, 258, 168))
	var cv: VBoxContainer = main._vbox(player, 5)
	var name_row := HBoxContainer.new()
	cv.add_child(name_row)
	main.character_name = UI.label("无名客", 23, PAPER)
	main.character_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	main.character_name.clip_text = true
	main.character_name.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	name_row.add_child(main.character_name)
	main.gold_label = UI.label("银两 —", 13, Color("d1b87b"))
	name_row.add_child(main.gold_label)
	main.character_meta = UI.label("初入江湖 / 尚无门派", 12, MUTED)
	main.character_meta.clip_text = true
	main.character_meta.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	cv.add_child(main.character_meta)
	main.hp_label = UI.label("气血 — / —", 12, PAPER)
	cv.add_child(main.hp_label)
	main.hp_bar = UI.bar(0, 1, Color("b66a52"))
	cv.add_child(main.hp_bar)
	main.mp_label = UI.label("内力 — / —", 12, PAPER)
	cv.add_child(main.mp_label)
	main.mp_bar = UI.bar(0, 1, Color("89aaa1"))
	cv.add_child(main.mp_bar)
	main.exp_bar = UI.bar(0, 1, Color("bfa66a"))
	main.exp_bar.tooltip_text = "修为"
	main.exp_bar.custom_minimum_size.y = 2
	cv.add_child(main.exp_bar)
	var atlas := _panel(main, "AtlasCard", Control.PRESET_TOP_RIGHT, Rect2(-248, 20, 228, 226))
	var av: VBoxContainer = main._vbox(atlas, 6)
	var title := HBoxContainer.new()
	av.add_child(title)
	main.region_label = UI.label("清溪镇", 19, PAPER)
	main.region_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.add_child(main.region_label)
	title.add_child(_button("舆图 M", main.open_map))
	main.minimap = MapView.new()
	main.minimap.scenic_layout = true
	main.minimap.custom_minimum_size = Vector2(202, 122)
	main.minimap.name = "Minimap"
	av.add_child(main.minimap)
	main.time_label = UI.label("午时 12:00 · 第 1 日", 12, MUTED)
	av.add_child(main.time_label)
	main.connection_label = UI.label("○ 正在连接", 11, MUTED)
	av.add_child(main.connection_label)
	var dock := HBoxContainer.new()
	dock.name = "ActionDock"
	main.add_child(dock)
	dock.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_LEFT)
	dock.offset_left = 20
	dock.offset_top = -54
	dock.offset_right = 620
	dock.offset_bottom = -20
	dock.add_theme_constant_override("separation", 6)
	for entry: Array in [["角色 I", main.open_character], ["行囊 B", main.open_inventory], ["武学 K", main.open_skills], ["镇志", main.open_chronicle], ["人物", main.open_people], ["视角 C", func():
		if main.modal == null and not main.busy: main.world_presenter.toggle_camera_panel()], ["设置", main.open_settings]]:
		var button := _button(entry[0], entry[1])
		if entry[0] == "设置": button.name = "ConnectionSettings"
		main.shortcuts.append(button)
		dock.add_child(button)
	main.context_label = UI.label("WASD 移步 · E 交互 · 滚轮缩放", 14, PAPER)
	main.context_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	main.context_label.add_theme_color_override("font_shadow_color", Color(0.05, 0.1, 0.07, 0.95))
	main.context_label.add_theme_constant_override("shadow_offset_x", 1)
	main.context_label.add_theme_constant_override("shadow_offset_y", 2)
	main.add_child(main.context_label)
	main.context_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	main.context_label.offset_left = -230
	main.context_label.offset_right = 230
	main.context_label.offset_top = -95
	main.context_label.offset_bottom = -68
	main.context_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	# Premise stays available in the chronicle; retain Main's update binding.
	main.world_premise_label = UI.label("", 12)
	main.world_premise_label.name = "WorldPremise"
	main.world_premise_label.hide()
	main.add_child(main.world_premise_label)
	main.toast_label = UI.label("", 16, PAPER)
	main.toast_label.name = "Toast"
	main.toast_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	main.toast_label.add_theme_stylebox_override("normal", UI.box(Color("33463e"), UI.JADE, 2, 12))
	main.add_child(main.toast_label)
	main.toast_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	main.toast_label.offset_left = -330
	main.toast_label.offset_right = 330
	main.toast_label.offset_top = -150
	main.toast_label.offset_bottom = -108
	main.toast_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	main.toast_label.hide()
