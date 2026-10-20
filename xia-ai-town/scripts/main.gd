extends Control

const UI = preload("res://scripts/ui_kit.gd")
const ApiClient = preload("res://scripts/api.gd")
const WorldScene = preload("res://scripts/town_world.gd")
const MapView = preload("res://scripts/town_map.gd")

var api: Node
var world: Node2D
var player: Dictionary = {}
var battle: Dictionary = {}
var clock: Dictionary = {}
var clock_received: float = 0.0
var world_info: Dictionary = {}
var online: bool = false
var busy: bool = false
var syncing: bool = false
var state_version: int = 0
var modal: Control
var modal_body: VBoxContainer
var modal_kind: String = ""
var shop_id: String = ""
var shop_data: Dictionary = {}
var battle_log: Array[String] = []
var camera_initialized: bool = false
var zoom: float = 1.35
var encounter_cooldown: float = 0.0
var toast_until: float = 0.0
var character_name: Label
var character_meta: Label
var hp_label: Label
var mp_label: Label
var hp_bar: ProgressBar
var mp_bar: ProgressBar
var exp_bar: ProgressBar
var gold_label: Label
var region_label: Label
var world_premise_label: Label
var time_label: Label
var connection_label: Label
var context_label: Label
var toast_label: Label
var minimap: Control
var modal_status: Label
var world_view: Control
var shortcuts: Array[Button] = []
var people_npcs: Array = []
var npc_state: Dictionary = {}
var greet_inside: Dictionary = {}
var greet_busy: Dictionary = {}
var dialogue_id: int = 0
var dialogue_slug: String = ""
var dialogue_name: String = ""
var dialogue_affinity: int = -1
var dialogue_footer: Control

func _ready() -> void:
	theme = UI.make_theme()
	api = ApiClient.new()
	api.name = "Api"
	add_child(api)
	_build_scene()
	_bind_inputs()
	var timer := Timer.new()
	timer.wait_time = 10.0
	timer.timeout.connect(refresh)
	add_child(timer)
	timer.start()
	refresh()

func _bind_inputs() -> void:
	var bindings := {"move_left": [KEY_A, KEY_LEFT], "move_right": [KEY_D, KEY_RIGHT], "move_up": [KEY_W, KEY_UP], "move_down": [KEY_S, KEY_DOWN]}
	for action in bindings:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
		for key in bindings[action]:
			var event := InputEventKey.new()
			event.physical_keycode = key
			InputMap.action_add_event(action, event)

func _panel(parent: Node, at: Vector2, dimensions: Vector2, color: Color = UI.PAPER) -> PanelContainer:
	var panel := PanelContainer.new()
	parent.add_child(panel)
	panel.position = at
	panel.size = dimensions
	panel.add_theme_stylebox_override("panel", UI.box(color))
	return panel

func _vbox(parent: Node, gap: int = 10) -> VBoxContainer:
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", gap)
	parent.add_child(v)
	return v

func _build_scene() -> void:
	var background := ColorRect.new()
	background.color = Color("9cae81")
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(background)
	world_view = Control.new()
	world_view.name = "TownViewport"
	world_view.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	world_view.offset_top = 86
	world_view.offset_bottom = -98
	world_view.clip_contents = true
	world_view.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(world_view)
	world = WorldScene.new()
	world.name = "Town"
	world.ui_font = theme.default_font
	world_view.add_child(world)
	var top := _panel(self, Vector2.ZERO, Vector2(size.x, 86), Color("f3f0e7"))
	top.name = "Header"
	top.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
	top.offset_bottom = 86
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 20)
	top.add_child(header)
	var seal := UI.label("侠", 36, UI.RED)
	seal.custom_minimum_size.x = 52
	seal.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	header.add_child(seal)
	var title := _vbox(header, 0)
	title.add_child(UI.label("清 溪 志", 24))
	title.add_child(UI.label("Q I N G X I   C H R O N I C L E S", 9, UI.MUTED))
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(spacer)
	var locale := _vbox(header, 1)
	region_label = UI.label("清溪镇", 22)
	locale.add_child(region_label)
	locale.add_child(UI.label("一方天地，自在江湖", 12, UI.MUTED))
	header.add_child(VSeparator.new())
	time_label = UI.label("——时  ·  第 — 日", 16)
	header.add_child(time_label)
	header.add_child(VSeparator.new())
	connection_label = UI.label("○  正在连接", 13, UI.MUTED)
	header.add_child(connection_label)
	var settings_button := UI.button("设置", open_settings)
	settings_button.name = "ConnectionSettings"
	header.add_child(settings_button)
	var character := _panel(self, Vector2(24, 110), Vector2(274, 290))
	character.name = "PlayerCard"
	var cv := _vbox(character, 9)
	cv.add_child(UI.label("行 走 江 湖", 12, UI.MUTED))
	character_name = UI.label("无名客", 28)
	cv.add_child(character_name)
	character_meta = UI.label("初入江湖  /  尚无门派", 13, UI.MUTED)
	cv.add_child(character_meta)
	cv.add_child(UI.rule())
	hp_label = UI.label("气血  — / —", 13)
	cv.add_child(hp_label)
	hp_bar = UI.bar(0, 1, UI.RED)
	cv.add_child(hp_bar)
	mp_label = UI.label("内力  — / —", 13)
	cv.add_child(mp_label)
	mp_bar = UI.bar(0, 1, Color("668c8d"))
	cv.add_child(mp_bar)
	exp_bar = UI.bar(0, 1, UI.GOLD)
	cv.add_child(exp_bar)
	gold_label = UI.label("银两  —", 15, UI.GOLD)
	cv.add_child(gold_label)
	var guide := _panel(self, Vector2(24, 420), Vector2(274, 208), Color("ebeadd"))
	var gv := _vbox(guide, 8)
	gv.add_child(UI.label("江 湖 小 笺", 13, UI.JADE))
	gv.add_child(UI.paragraph("去药铺备些回春散，再出镇寻狼试剑。等级达到三重，可前往三派驻地拜师。", 13, UI.INK))
	gv.add_child(UI.rule())
	world_premise_label = UI.paragraph("后山传闻 · 黑旋风与断岳刀", 12, UI.MUTED)
	world_premise_label.name = "WorldPremise"
	gv.add_child(world_premise_label)
	var chronicle := UI.button("清溪镇志", open_chronicle)
	chronicle.custom_minimum_size.y = 36
	gv.add_child(chronicle)
	var atlas := _panel(self, Vector2(size.x - 254, 110), Vector2(230, 240))
	atlas.name = "AtlasCard"
	atlas.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	atlas.offset_left = -254
	atlas.offset_right = -24
	atlas.offset_top = 110
	atlas.offset_bottom = 350
	var av := _vbox(atlas, 8)
	av.add_child(UI.label("清 溪 舆 图", 16))
	minimap = MapView.new()
	minimap.custom_minimum_size = Vector2(196, 118)
	minimap.name = "Minimap"
	av.add_child(minimap)
	av.add_child(UI.label("北 · 三派驻镇     南 · 山林试剑", 11, UI.MUTED))
	av.add_child(UI.button("展开舆图   M", open_map))
	var people := _panel(self, Vector2.ZERO, Vector2(230, 176), Color("ebeadd"))
	people.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT)
	people.offset_left = -254
	people.offset_right = -24
	people.offset_top = 394
	people.offset_bottom = 570
	var pv := _vbox(people, 8)
	pv.add_child(UI.label("镇 中 故 人", 14))
	pv.add_child(UI.paragraph("七位江湖客，各有一段往事。", 14, UI.INK))
	pv.add_child(UI.button("查看人物名录", open_people))
	pv.add_child(UI.label("日程作息 · 交情渐深", 11, UI.MUTED))
	var bottom := _panel(self, Vector2.ZERO, Vector2(size.x, 98), Color("f3f0e7"))
	bottom.name = "ActionDock"
	bottom.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	bottom.offset_top = -98
	var dock := HBoxContainer.new()
	dock.add_theme_constant_override("separation", 14)
	bottom.add_child(dock)
	var walk := _vbox(dock, 4)
	walk.custom_minimum_size.x = 268
	walk.add_child(UI.label("W A S D  /  方向键     移步", 13))
	walk.add_child(UI.label("E 交互    ·    Esc 返回    ·    滚轮缩放", 12, UI.MUTED))
	for entry in [["角色   I", open_character], ["行囊   B", open_inventory], ["武学   K", open_skills]]:
		var btn := UI.button(entry[0], entry[1])
		btn.custom_minimum_size.x = 112
		shortcuts.append(btn)
		dock.add_child(btn)
	var fill := Control.new()
	fill.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	dock.add_child(fill)
	context_label = UI.label("沿石径漫步，近前按 E 交互", 14, UI.JADE)
	context_label.custom_minimum_size.x = 270
	context_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	dock.add_child(context_label)
	toast_label = UI.label("", 16, UI.PAPER)
	toast_label.name = "Toast"
	toast_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	toast_label.add_theme_stylebox_override("normal", UI.box(Color("33463e"), UI.JADE, 3, 14))
	toast_label.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM)
	toast_label.offset_left = -340
	toast_label.offset_right = 340
	toast_label.offset_top = -170
	toast_label.offset_bottom = -120
	toast_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	toast_label.hide()
	add_child(toast_label)

func _process(delta: float) -> void:
	if world == null:
		return
	encounter_cooldown = maxf(0, encounter_cooldown - delta)
	var direction := Vector2.ZERO
	if modal == null and not busy and not player.is_empty() and battle.is_empty():
		direction = Input.get_vector("move_left", "move_right", "move_up", "move_down")
		var step := direction * 150.0 * minf(delta, 0.04)
		var next: Vector2 = world.actor_pos + Vector2(step.x, 0)
		if world.can_walk(next):
			world.actor_pos = next
		next = world.actor_pos + Vector2(0, step.y)
		if world.can_walk(next):
			world.actor_pos = next
	world.moving = direction.length_squared() > 0
	world.walk_time += delta if world.moving else 0.0
	world.scale = Vector2.ONE * zoom
	var target: Vector2 = world_view.size * 0.5 - (world.actor_pos + Vector2(0, -68)) * zoom
	world.position = world.position.lerp(target, minf(delta * 8, 1)) if camera_initialized else target
	camera_initialized = true
	world.queue_redraw()
	minimap.actor_pos = world.actor_pos
	minimap.queue_redraw()
	region_label.text = world.region_name()
	var near: Dictionary = world.nearby()
	context_label.text = "E  ·  %s" % near.get("name", "交互") if not near.is_empty() else "沿石径漫步，近前按 E 交互"
	if not clock.is_empty():
		var sec: float = float(clock.get("game_sec", 0)) + (Time.get_ticks_msec() / 1000.0 - clock_received) * float(clock.get("time_scale", 60))
		var hour := int(sec / 3600) % 24
		var periods := ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
		time_label.text = "%s时  %02d:%02d · 第 %d 日" % [periods[int((hour + 1) / 2) % 12], hour, int(sec / 60) % 60, int(sec / 86400) + 1]
		world.tick_schedule(sec, dialogue_slug)
		_check_greet()
	if toast_label.visible and Time.get_ticks_msec() / 1000.0 > toast_until:
		toast_label.hide()
	if direction != Vector2.ZERO and encounter_cooldown <= 0:
		for mob in world.mobs:
			if mob.get("alive", false) and world.actor_pos.distance_to(Vector2(mob.x, mob.y)) < 17:
				start_battle(str(mob.id))
				break

func _check_greet() -> void:
	if modal != null or busy or not battle.is_empty():
		return
	for npc: Dictionary in world.npcs:
		var npc_id: int = int(npc.get("npc_id", 0))
		if npc_id == 0:
			continue
		var slug: String = str(npc.get("slug", ""))
		var inside: bool = world.actor_pos.distance_to(Vector2(npc["pos"])) <= world.sense_radius
		var was_inside: bool = bool(greet_inside.get(slug, false))
		greet_inside[slug] = inside
		if inside and not was_inside and bool(npc.get("greet_ready", false)) and not bool(greet_busy.get(npc_id, false)):
			_request_greet(npc_id, slug)

func _request_greet(npc_id: int, slug: String) -> void:
	greet_busy[npc_id] = true
	var response: Dictionary = await api.request("/api/npc/%d/greet" % npc_id, {}, 20.0)
	greet_busy.erase(npc_id)
	if not response.ok or not bool(response.data.get("greet", false)):
		return
	world.show_bubble(slug, str(response.data.get("reply", "")))
	_mark_greeted(slug, int(response.data.get("affinity", 0)))

func _mark_greeted(slug: String, affinity: int) -> void:
	for npc: Dictionary in world.npcs:
		if str(npc.get("slug", "")) == slug:
			npc["greet_ready"] = false
	for npc: Variant in people_npcs:
		if npc is Dictionary and str(npc.get("slug", "")) == slug:
			npc["greet_ready"] = false
			if affinity != 0:
				npc["affinity"] = affinity

func _unhandled_key_input(event: InputEvent) -> void:
	if not event is InputEventKey or not event.pressed or event.echo:
		return
	if event.keycode == KEY_ESCAPE:
		close_modal()
		get_viewport().set_input_as_handled()
		return
	if modal != null or busy:
		return
	match event.physical_keycode:
		KEY_I: open_character()
		KEY_B: open_inventory()
		KEY_K: open_skills()
		KEY_M: open_map()
		KEY_E: interact()

func _unhandled_input(event: InputEvent) -> void:
	if modal == null and event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			zoom = minf(zoom + 0.1, 2.2)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			zoom = maxf(zoom - 0.1, 0.85)

func notify(text: String) -> void:
	toast_label.text = text
	toast_label.show()
	move_child(toast_label, -1)
	toast_until = Time.get_ticks_msec() / 1000.0 + 4.5

func _connection(ok: bool) -> void:
	online = ok
	connection_label.text = "●  江湖已连接" if ok else "○  连接中断"
	connection_label.add_theme_color_override("font_color", UI.JADE if ok else UI.RED)

func refresh() -> void:
	if syncing or busy:
		return
	syncing = true
	var requested_version := state_version
	var response: Dictionary = await api.request("/api/game/state")
	if requested_version != state_version or busy:
		syncing = false
		return
	_connection(response.ok)
	if not response.ok:
		syncing = false
		if modal == null and player.is_empty():
			open_settings()
		notify(response.error)
		return
	var data: Dictionary = response.data
	clock = data.get("clock", {})
	clock_received = Time.get_ticks_msec() / 1000.0
	world_info = data.get("world", {})
	if not world_info.get("premise", []).is_empty():
		world_premise_label.text = "后山传闻 · 黑旋风与断岳刀"
	world.mobs = data.get("mobs", [])
	minimap.mobs = world.mobs
	people_npcs = data.get("npcs", [])
	if data.get("player") != null:
		var first := player.is_empty()
		_apply_player(data.player, first)
		if data.get("battle") != null:
			var changed: bool = battle != data.battle
			if battle.is_empty():
				battle_log.assign(["重返战局，继续未竟的交锋。"])
			battle = data.battle
			if (changed or modal == null) and modal_kind != "settings":
				open_battle()
		elif battle.get("active", false):
			battle = {}
			_apply_player(data.player, true)
			encounter_cooldown = 3.0
			if modal_kind == "battle":
				_remove_modal()
			notify("战局已在服务器结束，行装与位置已同步。")
	elif modal == null or modal_kind == "settings":
		open_new_game()
	var detail: Dictionary = await api.request("/api/npcs/state")
	syncing = false
	if detail.ok and requested_version == state_version and not busy:
		npc_state = detail.data
		world.apply_state(detail.data)

func _apply_player(data: Dictionary, move_actor: bool = false) -> void:
	player = data
	world.actor_name = str(player.name)
	if move_actor:
		world.actor_pos = _walkable_position(Vector2(player.pos.x, player.pos.y))
	character_name.text = str(player.name)
	var sect: Variant = player.get("sect")
	character_meta.text = "第 %d 重  /  %s" % [int(player.level), "江湖散人" if sect == null else str(sect.name)]
	hp_label.text = "气血    %d / %d" % [int(player.hp), int(player.hp_max)]
	mp_label.text = "内力    %d / %d" % [int(player.mp), int(player.mp_max)]
	hp_bar.max_value = float(player.hp_max)
	hp_bar.value = float(player.hp)
	mp_bar.max_value = float(player.mp_max)
	mp_bar.value = float(player.mp)
	exp_bar.max_value = float(player.exp_next)
	exp_bar.value = float(player.exp)
	exp_bar.tooltip_text = "经验 %d / %d" % [int(player.exp), int(player.exp_next)]
	gold_label.text = "银两  %d 两    ·    修炼 %d" % [int(player.gold), int(player.cultivation)]

func _walkable_position(point: Vector2) -> Vector2:
	if world.can_walk(point):
		return point
	for radius in range(8, 257, 8):
		for direction in [Vector2.DOWN, Vector2.RIGHT, Vector2.LEFT, Vector2.UP]:
			var candidate: Vector2 = point + direction * radius
			if world.can_walk(candidate):
				return candidate
	return Vector2(792, 440)

func mutate(path: String, body: Dictionary, success: String = "", redraw: Callable = Callable()) -> bool:
	if busy:
		return false
	busy = true
	state_version += 1
	_set_request_buttons(true)
	if is_instance_valid(modal_status):
		modal_status.text = "正在办理，请稍候……"
	var response: Dictionary = await api.request(path, body)
	busy = false
	_set_request_buttons(false)
	_connection(response.ok or response.has("status"))
	if is_instance_valid(modal_status):
		modal_status.text = "" if response.ok else str(response.error)
	if not response.ok:
		notify(str(response.error))
		return false
	if response.data.get("player") != null:
		_apply_player(response.data.player)
	if not success.is_empty():
		notify(success)
	if redraw.is_valid():
		redraw.call()
	return true

func _set_request_buttons(disabled: bool) -> void:
	for button in get_tree().get_nodes_in_group("request_buttons"):
		if is_instance_valid(button):
			button.disabled = disabled or bool(button.get_meta("unavailable", false))

func _action(text: String, callback: Callable, unavailable: bool = false, primary: bool = false) -> Button:
	var button := UI.button(text, callback, primary)
	button.add_to_group("request_buttons")
	button.set_meta("unavailable", unavailable)
	button.disabled = unavailable or busy
	return button

func _remove_modal() -> void:
	if is_instance_valid(modal):
		remove_child(modal)
		modal.queue_free()
	modal = null
	modal_status = null
	modal_kind = ""
	dialogue_slug = ""

func close_modal() -> void:
	if busy:
		notify("正在办理，请稍候。")
		return
	if modal_kind == "battle" and not battle.is_empty():
		if battle.get("active", false):
			notify("交锋尚未结束，可选择轻功脱身。")
		else:
			finish_battle()
		return
	if modal_kind == "new_game":
		return
	if modal_kind == "settings" and battle.get("active", false):
		open_battle()
		return
	_remove_modal()

func _new_modal(title: String, subtitle: String, kind: String, width: float = 780.0) -> VBoxContainer:
	_remove_modal()
	modal_kind = kind
	modal = Control.new()
	modal.name = "Modal"
	modal.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(modal)
	var shade := ColorRect.new()
	shade.color = Color(0.08, 0.15, 0.13, 0.66)
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	modal.add_child(shade)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	modal.add_child(center)
	var panel := PanelContainer.new()
	panel.custom_minimum_size = Vector2(width, minf(size.y - 150, 650))
	panel.add_theme_stylebox_override("panel", UI.box(UI.PAPER, Color("a3ad95"), 5, 26))
	center.add_child(panel)
	var content := _vbox(panel, 12)
	var heading := HBoxContainer.new()
	content.add_child(heading)
	var text := UI.label(title, 28)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(text)
	if kind != "new_game" and kind != "battle":
		heading.add_child(UI.button("返回  Esc", close_modal))
	content.add_child(UI.paragraph(subtitle, 14))
	content.add_child(UI.rule())
	var scroll := ScrollContainer.new()
	scroll.name = "ContentScroll"
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	content.add_child(scroll)
	modal_body = _vbox(scroll, 12)
	modal_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	modal_status = UI.paragraph("", 14, UI.RED)
	modal_status.name = "RequestStatus"
	content.add_child(modal_status)
	return modal_body

func open_settings() -> void:
	if busy:
		return
	var body := _new_modal("江湖驿站", "设置后端地址，连接你的江湖存档。", "settings", 660)
	body.add_child(UI.label("服务器地址", 16))
	var address := LineEdit.new()
	address.name = "ServerAddress"
	address.text = api.base_url
	address.custom_minimum_size.y = 48
	body.add_child(address)
	body.add_child(UI.paragraph("本机使用 http://127.0.0.1:8000；局域网可填写服务器的 IP 和端口。"))
	body.add_child(UI.button("连接江湖", func():
		var value := address.text.strip_edges()
		if not value.begins_with("http://") and not value.begins_with("https://"):
			modal_status.text = "地址须以 http:// 或 https:// 开头。"
			return
		state_version += 1
		api.configure(value)
		_remove_modal()
		player = {}
		battle = {}
		refresh(), true))
	body.add_child(UI.rule())
	body.add_child(UI.paragraph("移动在本地推演，交互时保存位置；角色、装备与战斗进度由服务器自动保存。"))

func open_new_game() -> void:
	var body := _new_modal("初 入 江 湖", "山水有相逢，侠客请留名。", "new_game", 660)
	body.add_child(UI.label("一剑一壶酒，一程一江湖。", 26, UI.JADE))
	body.add_child(UI.paragraph("这里是清溪镇。北有三派驻地，南有山林险径。购置行装、磨炼武艺，走出你自己的江湖。", 17, UI.INK))
	var input := LineEdit.new()
	input.name = "NewGameName"
	input.placeholder_text = "输入名号（至多十二字）"
	input.max_length = 12
	input.custom_minimum_size.y = 52
	body.add_child(input)
	var create := func():
		if input.text.strip_edges().is_empty():
			modal_status.text = "请先留下你的名号。"
			return
		if await mutate("/api/game/new", {"name": input.text.strip_edges()}, "江湖路远，珍重前行。"):
			world.actor_pos = Vector2(player.pos.x, player.pos.y)
			_remove_modal()
			refresh()
	body.add_child(_action("踏入江湖", create, false, true))
	input.text_submitted.connect(func(_text: String): create.call())
	body.add_child(UI.paragraph("单人存档 · 事件后自动保存"))
	input.grab_focus()

func open_character() -> void:
	if player.is_empty() or busy:
		return
	var body := _new_modal("侠 客 · %s" % player.name, "一身修为，皆在步履之间。", "character")
	UI.row(body, "境界 / 经验", "第 %d 重  ·  %d / %d" % [int(player.level), int(player.exp), int(player.exp_next)])
	UI.row(body, "可分配属性", "%d 点" % int(player.free_points), UI.GOLD)
	body.add_child(UI.rule())
	var names := {"str": "臂力", "con": "根骨", "agi": "身法", "int": "悟性"}
	for key in names:
		var row := UI.row(body, names[key], str(int(player.stats[key])))
		row.add_child(_action("＋", func(): await mutate("/api/game/allocate", {"stats": {key: 1}}, "修为精进。", open_character), int(player.free_points) <= 0))
	body.add_child(UI.rule())
	UI.row(body, "攻击 / 防御 / 速度", "%d  /  %d  /  %.1f" % [int(player.atk), int(player.def), float(player.spd)])
	UI.row(body, "闪避 / 暴击", "%d%%  /  %d%%" % [roundi(float(player.dodge) * 100), roundi(float(player.crit) * 100)])
	body.add_child(UI.rule())
	for slot in {"weapon": "兵器", "armor": "衣甲", "accessory": "配饰"}:
		var item: Variant = player.equipped.get(slot)
		var row := UI.row(body, {"weapon": "兵器", "armor": "衣甲", "accessory": "配饰"}[slot], "未装备" if item == null else str(item.name))
		if item != null:
			row.add_child(_action("卸下", func(): await mutate("/api/game/unequip", {"slot": slot}, "已卸下装备。", open_character)))

func open_inventory() -> void:
	if player.is_empty() or busy:
		return
	var body := _new_modal("行 囊", "随身物件，皆是江湖过往。药剂可在战斗中使用。", "inventory")
	UI.row(body, "随身银两", "%d 两" % int(player.gold), UI.GOLD)
	if player.inventory.is_empty():
		body.add_child(UI.paragraph("行囊尚空。不妨到药铺与铁匠铺备些行装。", 18))
	for item in player.inventory:
		var row := HBoxContainer.new()
		body.add_child(row)
		var details := _vbox(row, 4)
		details.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		details.add_child(UI.label("%s  ×%d%s" % [item.name, int(item.qty), "  ·  已装备" if item.get("slot") != null else ""], 18))
		details.add_child(UI.paragraph(str(item.desc)))
		if item.type in ["weapon", "armor", "accessory"] and item.get("slot") == null:
			row.add_child(_action("装备", func(): await mutate("/api/game/equip", {"item_id": item.item_id}, "行装已备。", open_inventory)))
		body.add_child(UI.rule())

func open_skills() -> void:
	if player.is_empty() or busy:
		return
	var body := _new_modal("武 学", "内练一口气，外练手中锋。最多装备三门主动招式。", "skills")
	UI.row(body, "修炼点", str(player.cultivation), UI.GOLD)
	if player.skills.is_empty():
		body.add_child(UI.label("尚无师承", 24, UI.JADE))
		body.add_child(UI.paragraph("等级达到三重后，可前往剑阳道馆、玄铁堂或烟雨小筑门前，按 E 拜师。一生只入一派。", 18, UI.INK))
	for skill in player.skills:
		var details := _vbox(body, 5)
		details.add_child(UI.label("%s  ·  第 %d 层%s" % [skill.name, int(skill.level), "  [未解锁]" if skill.locked else ""], 18))
		details.add_child(UI.paragraph("%s  ·  内力 %d  ·  %s" % ["内功" if skill.kind == "passive" else "招式", int(skill.mp_cost), skill.effect_desc]))
		var actions := HBoxContainer.new()
		details.add_child(actions)
		if skill.kind == "active":
			actions.add_child(_action("卸出招式" if skill.equipped else "装备招式", func(): await mutate("/api/game/skill/equip", {"skill_id": skill.skill_id, "equip": not skill.equipped}, "招式已调整。", open_skills), bool(skill.locked)))
		actions.add_child(_action("修习 · %d 点" % int(skill.upgrade_cost), func(): await mutate("/api/game/skill/upgrade", {"skill_id": skill.skill_id}, "武学精进。", open_skills), not skill.upgradable or int(player.cultivation) < int(skill.upgrade_cost)))
		body.add_child(UI.rule())

func interact() -> void:
	if busy or player.is_empty() or modal != null:
		return
	var near: Dictionary = world.nearby()
	if near.is_empty():
		notify("走近铺面或人物，再按 E 交互。")
		return
	match near.get("kind", ""):
		"shop": open_shop(str(near.id))
		"sect": open_sect(str(near.id), str(near.name))
		"npc": open_dialogue(str(near.id))
		_: open_people()

func open_shop(id: String) -> void:
	if busy or player.is_empty():
		return
	busy = true
	notify("正在查看货架……")
	var response: Dictionary = await api.request("/api/shop/" + id)
	busy = false
	if not response.ok:
		notify(response.error)
		return
	shop_id = id
	shop_data = response.data
	_render_shop()

func _render_shop(selling: bool = false) -> void:
	var body := _new_modal(str(shop_data.name), "物有所值，童叟无欺。一次交易一件物品。", "shop")
	var tabs := HBoxContainer.new()
	body.add_child(tabs)
	tabs.add_child(UI.button("购入", func(): _render_shop(false), not selling))
	tabs.add_child(UI.button("卖出", func(): _render_shop(true), selling))
	UI.row(body, "随身银两", "%d 两" % int(player.gold), UI.GOLD)
	var items: Array = player.inventory if selling else shop_data.get("items", [])
	if items.is_empty():
		body.add_child(UI.paragraph("没有可卖出的物品。"))
	for item in items:
		var row := HBoxContainer.new()
		body.add_child(row)
		var desc := _vbox(row, 4)
		desc.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		desc.add_child(UI.label(str(item.name) + (" ×%d" % int(item.qty) if selling else ""), 18))
		desc.add_child(UI.paragraph(str(item.desc)))
		var price := int(item.price) / 2 if selling else int(item.price)
		var unavailable: bool = (item.get("slot") != null) if selling else int(player.gold) < price
		var route := "/api/shop/%s/%s" % [shop_id, "sell" if selling else "buy"]
		var payload := {"item_id": item.item_id, "qty": 1}
		if not selling:
			payload["pos"] = {"x": world.actor_pos.x, "y": world.actor_pos.y}
		row.add_child(_action(("卖出" if selling else "购入") + " · %d 两" % price, func(): await mutate(route, payload, "交易已成。", func(): _render_shop(selling)), unavailable))
		body.add_child(UI.rule())
	if selling:
		body.add_child(UI.paragraph("装备中的物品须先卸下，卖出价格为原价一半。"))

func open_sect(id: String, place: String) -> void:
	var descriptions := {"jianyang": ["剑阳派", "以剑入道，刚正凌厉。入门臂力 +3，悟性 +2。"], "xuantie": ["玄铁堡", "横刀立马，厚重沉稳。入门根骨 +3，悟性 +2。"], "yanyu": ["烟雨楼", "身随烟雨，剑走轻灵。入门身法 +3，悟性 +2。"]}
	var data: Array = descriptions.get(id, [place, ""])
	var body := _new_modal(str(data[0]), place + " · 拜入师门", "sect")
	body.add_child(UI.label(str(data[0]), 32, UI.JADE))
	body.add_child(UI.paragraph(str(data[1]), 19, UI.INK))
	body.add_child(UI.paragraph("当前入门条件：等级达到三重，且尚无门派。一生只可加入一派，请慎重选择。", 17))
	var join := _action("确认拜入" + str(data[0]), func():
		if await mutate("/api/sect/join", {"sect_id": id}, "礼成，今日起便是同门。"):
			open_skills(), int(player.level) < 3 or player.get("sect") != null, true)
	body.add_child(join)
	body.add_child(UI.paragraph("也可走近掌门对话，交情到了再提拜师。", 13))

func open_people() -> void:
	if people_npcs.is_empty():
		var response: Dictionary = await api.request("/api/game/state")
		if response.ok:
			people_npcs = response.data.get("npcs", [])
	var body := _new_modal("清溪人物录", "照面即是缘，江湖总有再会时。", "people")
	for npc in people_npcs:
		var place: String = str(npc.get("poi", ""))
		var paused: Variant = npc.get("paused_until")
		if paused != null and float(paused) > Time.get_unix_time_from_system():
			place += "（正忙）"
		UI.row(body, "%s  ·  %s" % [str(npc.name), _role_text(str(npc.role))],
			"%s  ·  好感%s" % [place, _affinity_hearts(int(npc.get("affinity", 0)))])
		var heart := _heart_for(str(npc.get("slug", "")))
		if not heart.is_empty():
			body.add_child(UI.paragraph("心事：" + heart, 13, UI.MUTED))
		body.add_child(UI.rule())
	if people_npcs.is_empty():
		body.add_child(UI.paragraph("故人尚未聚齐，稍后再来。", 15))

func _role_text(role: String) -> String:
	return {"master": "掌门", "clinic": "药铺大夫", "smith": "铁匠铺主", "inn": "掌柜之女", "temple": "疯乞丐"}.get(role, role)

func _affinity_hearts(affinity: int) -> String:
	var filled: int = 4 if affinity >= 80 else 3 if affinity >= 60 else 2 if affinity >= 40 else 1 if affinity >= 20 else 0
	return "❤".repeat(filled) + ("" if filled > 0 else "淡")

func _find_npc(slug: String) -> Dictionary:
	for npc in people_npcs:
		if str(npc.get("slug", "")) == slug:
			return npc
	return {}

func _heart_for(slug: String) -> String:
	for npc: Variant in npc_state.get("npcs", []):
		if npc is Dictionary and str(npc.get("slug", "")) == slug:
			return str(npc.get("heart", ""))
	return ""

func open_dialogue(slug: String) -> void:
	if busy or player.is_empty():
		return
	var npc := _find_npc(slug)
	if npc.is_empty():
		notify("此人尚未来到镇中。")
		return
	dialogue_id = int(npc.id)
	dialogue_name = str(npc.name)
	dialogue_affinity = int(npc.get("affinity", 0))
	var body := _new_modal(dialogue_name, "%s  ·  %s  ·  好感%s" % [_role_text(str(npc.role)), str(npc.get("poi", "")), _affinity_hearts(dialogue_affinity)], "dialogue", 820)
	dialogue_slug = slug
	_append_talk(dialogue_name, "……你走上前，拱手见礼。")
	_render_dialogue_footer("main")

func _dialogue_dock() -> VBoxContainer:
	return modal_body.get_parent().get_parent()

func _render_dialogue_footer(mode: String) -> void:
	if dialogue_footer != null:
		dialogue_footer.queue_free()
	dialogue_footer = VBoxContainer.new()
	dialogue_footer.add_theme_constant_override("separation", 10)
	_dialogue_dock().add_child(dialogue_footer)
	# 首条空格，给对话气泡区留足高度
	var spacer := Control.new()
	spacer.custom_minimum_size = Vector2(0, 12)
	dialogue_footer.add_child(spacer)
	if mode == "gift":
		dialogue_footer.add_child(UI.label("赠君一物 · 行囊", 15, UI.JADE))
		var owned: Array = []
		for item in player.get("inventory", []):
			if item.get("slot") == null:
				owned.append(item)
		if owned.is_empty():
			dialogue_footer.add_child(UI.paragraph("行囊里没有可相赠的物件，先去备些来。", 15))
		else:
			var wrap := HBoxContainer.new()
			wrap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			dialogue_footer.add_child(wrap)
			var items_box := _vbox(wrap, 6)
			for item in owned:
				items_box.add_child(_action("%s  ×%d" % [item.name, int(item.qty)],
					func(): dialog_gift(int(item.item_id), str(item.name)), false, true))
		dialogue_footer.add_child(_action("先打住 · 返回", func(): _render_dialogue_footer("main")))
		return
	var shortcuts_row := HBoxContainer.new()
	dialogue_footer.add_child(shortcuts_row)
	shortcuts_row.add_child(_action("打招呼", func(): send_talk("（打招呼）")))
	shortcuts_row.add_child(_action("打听消息", func(): send_talk("（打听）")))
	shortcuts_row.add_child(_action("门派拜师", dialog_apprentice))
	shortcuts_row.add_child(_action("切磋", dialog_challenge))
	shortcuts_row.add_child(_action("送礼", func(): _render_dialogue_footer("gift")))
	var inbox := HBoxContainer.new()
	inbox.add_theme_constant_override("separation", 10)
	dialogue_footer.add_child(inbox)
	var input := LineEdit.new()
	input.name = "DialogueInput"
	input.placeholder_text = "开口说话……"
	input.custom_minimum_size.y = 46
	input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	input.text_submitted.connect(func(_text: String): send_talk(input.text, true))
	inbox.add_child(input)
	var send := _action("作 揖", func(): send_talk(input.text, true), false, true)
	send.custom_minimum_size.x = 96
	inbox.add_child(send)
	input.grab_focus()

func _append_talk(who: String, text: String) -> void:
	if text.is_empty():
		return
	if not who.is_empty():
		modal_body.add_child(UI.label(who, 16, UI.JADE if who == "你" else UI.RED))
	modal_body.add_child(UI.paragraph(text, 17))
	var scroll := modal_body.get_parent() as ScrollContainer
	if scroll != null:
		var target := scroll.get_v_scroll_bar().max_value
		scroll.scroll_vertical = int(target)

func send_talk(message: String, clear_input: bool = false) -> void:
	if busy or dialogue_id == 0:
		return
	var text := message.strip_edges()
	if text.is_empty():
		modal_status.text = "说点什么吧。"
		return
	if dialogue_footer != null:
		var input := dialogue_footer.find_child("DialogueInput", true, false)
		if input != null and clear_input:
			input.text = ""
			input.grab_focus()
	_append_talk("你", text)
	busy = true
	_set_request_buttons(true)
	modal_status.text = "%s沉吟许久……" % dialogue_name
	var response: Dictionary = await api.request("/api/npc/%d/talk" % dialogue_id, {"message": text}, 20.0)
	busy = false
	_set_request_buttons(false)
	_connection(response.ok)
	if not response.ok:
		modal_status.text = str(response.error)
		return
	var data: Dictionary = response.data
	_append_talk(dialogue_name, str(data.get("reply", "")))
	if data.has("player"):
		_apply_player(data.player)
	if dialogue_affinity >= 0 and data.has("affinity"):
		dialogue_affinity = int(data.get("affinity"))
		modal_status.text = "" if data.get("end", false) else ("好感" + _affinity_hearts(dialogue_affinity))

func dialog_apprentice() -> void:
	if busy or dialogue_id == 0:
		return
	busy = true
	_set_request_buttons(true)
	modal_status.text = "恭谨而立，静候应允……"
	var response: Dictionary = await api.request("/api/npc/%d/apprentice" % dialogue_id, {})
	busy = false
	_set_request_buttons(false)
	if not response.ok:
		modal_status.text = str(response.error)
		return
	var data: Dictionary = response.data
	_append_talk(dialogue_name, str(data.get("reply", "")))
	if data.has("player"):
		_apply_player(data.player)
		dialogue_affinity = int(data.get("affinity", dialogue_affinity))
	if data.get("joined", false):
		notify("礼成，今日起便是%s门下。" % str(data.get("sect", {}).get("name", "")))
	modal_status.text = ""

func dialog_challenge() -> void:
	if busy or dialogue_id == 0:
		return
	busy = true
	_set_request_buttons(true)
	modal_status.text = "剑拔弩张，切磋以待……"
	var response: Dictionary = await api.request("/api/npc/%d/challenge" % dialogue_id, {"x": int(world.actor_pos.x), "y": int(world.actor_pos.y)}, 20.0)
	busy = false
	_set_request_buttons(false)
	if not response.ok:
		modal_status.text = str(response.error)
		return
	_apply_player(response.data.get("player", {}))
	battle = response.data.get("battle", {})
	battle_log.assign(["与%s切磋比试。" % battle.get("enemy", {}).get("name", dialogue_name)])
	open_battle()

func dialog_gift(item_id: int, item_name: String) -> void:
	if busy or dialogue_id == 0:
		return
	busy = true
	_set_request_buttons(true)
	modal_status.text = "%s接过%s细看。" % [dialogue_name, item_name]
	var response: Dictionary = await api.request("/api/npc/%d/gift" % dialogue_id, {"item_id": item_id})
	busy = false
	_set_request_buttons(false)
	if not response.ok:
		modal_status.text = str(response.error)
		return
	var data: Dictionary = response.data
	_apply_player(data.get("player", {}))
	dialogue_affinity = int(data.get("affinity", dialogue_affinity))
	_append_talk(dialogue_name, str(data.get("reply", "")))
	modal_status.text = "好感" + _affinity_hearts(dialogue_affinity)
	_render_dialogue_footer("main")

func open_map() -> void:
	if busy:
		return
	var body := _new_modal("清 溪 舆 图", "北访三派，南行山林。朱砂为你，赤点为野怪。", "map", 900)
	var map := MapView.new()
	map.custom_minimum_size = Vector2(790, 370)
	map.actor_pos = world.actor_pos
	map.mobs = world.mobs
	map.detailed = true
	body.add_child(map)
	body.add_child(UI.paragraph("药铺与铁匠铺在镇西，客栈位于中央石道旁。出南门可前往东山林地；后山崖的山贼更为凶险。"))

func open_chronicle() -> void:
	if busy:
		return
	var body := _new_modal("清 溪 镇 志", "镇上传言，未必是实；江湖事，终要自己走一遭。", "chronicle")
	var premise: Array = world_info.get("premise", [])
	for line in premise:
		body.add_child(UI.paragraph("· " + str(line), 16, UI.INK))
	if premise.is_empty():
		body.add_child(UI.paragraph("镇志尚未传到此处，稍后再看。", 16))
	body.add_child(UI.rule())
	body.add_child(UI.paragraph("镇上都说，神器现世之日三派会重开山门；但拜师仍要看掌门对你的交情与你的境界。", 15, UI.MUTED))
	if int(world_info.get("stage", 0)) >= 2 and world_info.get("announcement") != null:
		body.add_child(UI.rule())
		body.add_child(UI.paragraph(str(world_info["announcement"]), 17, UI.JADE))

func start_battle(mob_id: String) -> void:
	if busy or not battle.is_empty() or modal != null:
		return
	busy = true
	state_version += 1
	var response: Dictionary = await api.request("/api/battle/start", {"target_type": "mob", "target_id": mob_id, "pos": {"x": world.actor_pos.x, "y": world.actor_pos.y}})
	busy = false
	if not response.ok:
		notify(response.error)
		encounter_cooldown = 3
		return
	_apply_player(response.data.player)
	battle = response.data.battle
	battle_log.assign(["与%s狭路相逢。" % battle.enemy.name])
	open_battle()

func open_battle() -> void:
	var active: bool = battle.get("active", false)
	var body := _new_modal("狭 路 相 逢" if active else "此 战 已 了", "第 %d 回合  ·  %s先手  ·  已用药 %d / 3" % [int(battle.get("round", 1)), "你" if battle.get("player_first", true) else "对方", int(battle.get("potions_used", 0))], "battle", 880)
	var statuses := HBoxContainer.new()
	body.add_child(statuses)
	for side in [battle.player, battle.enemy]:
		var card := PanelContainer.new()
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		statuses.add_child(card)
		var info := _vbox(card, 8)
		info.add_child(UI.label("%s  ·  第 %d 重" % [side.name, int(side.level)], 23))
		info.add_child(UI.label("气血 %d / %d" % [int(side.hp), int(side.hp_max)], 15))
		info.add_child(UI.bar(side.hp, side.hp_max, UI.RED))
		if side.has("mp"):
			info.add_child(UI.label("内力 %d / %d" % [int(side.mp), int(side.mp_max)], 14, UI.MUTED))
	var buffs: Dictionary = battle.get("buffs", {})
	for side in ["player", "enemy"]:
		var buff: Dictionary = buffs.get(side, {})
		var effect: Array[String] = []
		if buff.get("def_rounds", 0) > 0:
			effect.append("防御提升 · %d 回合" % int(buff.def_rounds))
		if buff.get("dodge_rounds", 0) > 0:
			effect.append("闪避提升 · %d 回合" % int(buff.dodge_rounds))
		if not effect.is_empty():
			body.add_child(UI.label(("你：" if side == "player" else "对方：") + " / ".join(effect), 14, UI.JADE))
	body.add_child(UI.rule())
	for line in battle_log.slice(maxi(0, battle_log.size() - 7)):
		body.add_child(UI.paragraph(line, 16, UI.INK))
	if active:
		var actions := HBoxContainer.new()
		body.add_child(actions)
		actions.add_child(_action("普攻 · 出招", func(): battle_action("attack"), false, true))
		actions.add_child(_action("轻功 · 脱身", func(): battle_action("flee")))
		actions.add_child(UI.button("驿站 · 连接设置", open_settings))
		for skill in player.get("skills", []):
			if skill.kind == "active" and skill.equipped and not skill.locked:
				var cd := int(battle.get("skill_cd", {}).get(str(skill.skill_id), 0))
				body.add_child(_action("%s  ·  内力 %d%s" % [skill.name, int(skill.mp_cost), "  · 冷却 %d 回合" % cd if cd > 0 else ""], func(): battle_action("skill", int(skill.skill_id)), int(player.mp) < int(skill.mp_cost) or cd > 0))
		for item in player.get("inventory", []):
			if item.type == "potion":
				body.add_child(_action("%s ×%d  ·  %s" % [item.name, int(item.qty), item.desc], func(): battle_action("item", int(item.item_id)), int(battle.get("potions_used", 0)) >= 3))
	else:
		body.add_child(UI.button("收剑 · 继续前行", finish_battle, true))

func battle_action(action: String, id: int = -1) -> void:
	if busy or not battle.get("active", false):
		return
	busy = true
	state_version += 1
	_set_request_buttons(true)
	modal_status.text = "双方交锋中……"
	var payload := {"action": action}
	if id >= 0:
		payload["id"] = id
	var response: Dictionary = await api.request("/api/battle/action", payload)
	busy = false
	_set_request_buttons(false)
	if not response.ok:
		modal_status.text = response.error
		notify(response.error)
		_connection(response.has("status"))
		await refresh()
		return
	_connection(true)
	_apply_player(response.data.player, not response.data.battle.get("active", false))
	battle = response.data.battle
	for entry in response.data.get("log", []):
		battle_log.append(str(entry.text))
	var rewards: Variant = response.data.get("rewards")
	if rewards != null:
		battle_log.append("所得：经验 %d · 银两 %d · 修炼 %d" % [int(rewards.exp), int(rewards.gold), int(rewards.cultivation)])
		for drop in rewards.get("drops", []):
			battle_log.append("拾得 %s ×%d" % [drop.name, int(drop.qty)])
		if not rewards.get("level_ups", []).is_empty():
			battle_log.append("境界精进！可前往角色面板分配属性。")
		if bool(rewards.get("world_announced", false)):
			notify("你取回断岳刀的消息，已经传遍了清溪镇。")
	open_battle()

func finish_battle() -> void:
	if busy or battle.get("active", false):
		return
	if battle.get("result") == "fled":
		var enemy: Dictionary = battle.get("enemy", {})
		for mob in world.mobs:
			if mob.id == enemy.get("id"):
				var away: Vector2 = (world.actor_pos - Vector2(mob.x, mob.y)).normalized()
				if away.is_zero_approx():
					away = Vector2.DOWN
				world.actor_pos = _walkable_position(world.actor_pos + away * 48)
				break
	battle = {}
	encounter_cooldown = 3.0
	_remove_modal()
	refresh()
