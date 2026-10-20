extends SceneTree
## Real scene + real HTTP. Run via smoke_test.py to guarantee disposable resources.

var app
var checks: int = 0
var failed: bool = false
var mode: String

func _initialize() -> void:
	_run.call_deferred()

func check(condition: bool, message: String) -> void:
	checks += 1
	if condition:
		print("PASS [%s] %s" % [mode, message])
	else:
		failed = true
		printerr("SMOKE FAIL [%s] %s" % [mode, message])
		quit(1)

func pause(seconds: float = 0.06) -> void:
	await create_timer(seconds).timeout

func settle() -> void:
	var deadline: int = Time.get_ticks_msec() + 15000
	await pause()
	while (app.busy or app.syncing) and Time.get_ticks_msec() < deadline:
		await pause(0.02)
	check(not app.busy and not app.syncing, "UI request completed")

func buttons(parent: Node, prefix: String) -> Array[Button]:
	var found: Array[Button] = []
	for child in parent.get_children():
		if child is Button and child.text.begins_with(prefix):
			found.append(child)
		found.append_array(buttons(child, prefix))
	return found

func button(prefix: String, index: int = 0) -> Button:
	var found: Array[Button] = buttons(app.modal, prefix)
	check(found.size() > index, "button exists: " + prefix)
	return found[index] if found.size() > index else null

func click(prefix: String, index: int = 0) -> void:
	var target: Button = button(prefix, index)
	if target == null:
		return
	check(not target.disabled, "button enabled: " + prefix)
	if target.disabled:
		return
	# Execute the actual connected UI closure, including its async HTTP mutation.
	target.pressed.emit()
	await settle()

func state() -> Dictionary:
	var response: Dictionary = await app.api.request("/api/game/state")
	check(response.ok, "read real backend state")
	return response.get("data", {})

func modal_text() -> String:
	var parts: Array[String] = []
	for child in app.modal_body.get_children():
		if child is Label:
			parts.append(child.text)
	return "\n".join(PackedStringArray(parts))

func hud_matches(data: Dictionary) -> void:
	var p: Dictionary = data.player
	check(app.player == p, "client player equals server snapshot")
	check(app.character_name.text == p.name and app.world.actor_name == p.name, "name HUD/world synchronized")
	check(app.hp_bar.value == float(p.hp) and app.hp_bar.max_value == float(p.hp_max), "HP HUD synchronized")
	check(app.mp_bar.value == float(p.mp) and app.mp_bar.max_value == float(p.mp_max), "MP HUD synchronized")
	check(app.gold_label.text.contains(str(int(p.gold))) and app.character_meta.text.contains(str(int(p.level))), "gold/level HUD synchronized")

func escape() -> void:
	# Real Godot key dispatch reaches Control._unhandled_key_input; focus must not
	# consume Esc in a LineEdit. WASD tests below likewise use Input.parse_input_event.
	var focus: Control = root.gui_get_focus_owner()
	if focus != null:
		focus.release_focus()
	var key := InputEventKey.new()
	key.keycode = KEY_ESCAPE
	key.physical_keycode = KEY_ESCAPE
	key.pressed = true
	Input.parse_input_event(key)
	await pause()
	key = key.duplicate()
	key.pressed = false
	Input.parse_input_event(key)
	await settle()

func key_move(code: Key, duration: float = 0.24) -> void:
	var key := InputEventKey.new()
	key.keycode = code
	key.physical_keycode = code
	key.pressed = true
	Input.parse_input_event(key)
	await pause(duration)
	key = key.duplicate()
	key.pressed = false
	Input.parse_input_event(key)
	await pause()

func movement() -> void:
	app.world.actor_pos = Vector2(792, 440)
	for entry in [[KEY_W, Vector2.UP], [KEY_A, Vector2.LEFT], [KEY_S, Vector2.DOWN], [KEY_D, Vector2.RIGHT]]:
		app.world.actor_pos = Vector2(792, 440)
		var before: Vector2 = app.world.actor_pos
		await key_move(entry[0])
		var displacement: Vector2 = app.world.actor_pos - before
		check(displacement.dot(entry[1]) > 8, "WASD moves: " + OS.get_keycode_string(entry[0]))
		check(app.world.can_walk(app.world.actor_pos), "movement remains walkable")
	# Inn south wall ends at y=384; the foot collider must never enter its tiles.
	app.world.actor_pos = Vector2(520, 400)
	await key_move(KEY_W, 0.4)
	check(app.world.actor_pos.y >= 387 and app.world.actor_pos.y < 400, "W collides with inn wall")
	check(not app.world.can_walk(Vector2(520, 376)), "inn interior blocks walking")
	check(not app.world.can_walk(Vector2(1200, 410)), "pond is solid")
	check(not app.world.can_walk(Vector2(500, 490)), "town wall is solid")
	check(not app.world.can_walk(Vector2(-1, 440)), "world boundary is solid")
	app.world.actor_pos = Vector2(792, 440)
	app.open_character()
	var before: Vector2 = app.world.actor_pos
	await key_move(KEY_D)
	check(app.world.actor_pos == before, "modal blocks movement")
	await escape()
	check(app.modal == null, "Esc closes ordinary panel")

func base() -> void:
	check(app.player.is_empty() and app.modal_kind == "new_game", "empty database opens first-game form")
	check(app.online and not app.clock.is_empty() and not app.world.mobs.is_empty(), "backend connection, clock and mobs loaded")
	var name_input: LineEdit = app.modal.find_child("NewGameName", true, false)
	name_input.text = "联调侠客"
	await click("踏入江湖")
	check(app.modal == null and app.player.name == "联调侠客", "new-game callback creates character")
	hud_matches(await state())
	check(app.player.gold == 100 and app.player.level == 1, "new character default economy/level")
	check(app.world_info.get("premise", []).size() == 4, "world premise loaded from game state")
	app.open_chronicle()
	check(app.modal_kind == "chronicle", "chronicle panel opens on demand")
	check(modal_text().contains("断岳刀"), "chronicle lists the artifact rumor")
	await escape()
	check(app.modal == null, "Esc closes chronicle")
	app.open_sect("jianyang", "剑阳道馆")
	check(button("确认拜入").disabled, "level-one sect join is disabled")
	var denied: Dictionary = await app.api.request("/api/sect/join", {"sect_id": "jianyang"})
	check(not denied.ok and int(denied.status) == 400, "backend enforces level-three join gate")
	await escape()
	app.open_character()
	check(button("＋").disabled, "zero free points disables allocation")
	await escape()
	await movement()
	app.world.actor_pos = Vector2(128, 370)
	app.interact()
	await settle()
	check(app.modal_kind == "shop" and app.shop_id == "clinic", "door interaction opens real clinic")
	await click("购入 · 15")
	check(app.player.gold == 85 and app.player.inventory.size() == 1, "purchase costs 15 and updates inventory")
	hud_matches(await state())
	await click("卖出")
	await click("卖出 · 7")
	check(app.player.gold == 92 and app.player.inventory.is_empty(), "odd-price potion sells for floor half: 7")
	hud_matches(await state())
	await click("购入")
	await click("购入 · 15")
	await escape()
	app.world.actor_pos = Vector2(304, 370)
	app.interact()
	await settle()
	check(app.shop_id == "smith" and button("购入 · 80").disabled, "smith disables unaffordable equipment")
	await escape()

func advanced() -> void:
	check(app.player.level == 3 and app.player.free_points == 3, "runner fixture loaded at level three")
	app.open_character()
	var strength: int = int(app.player.stats.str)
	await click("＋", 0)
	check(int(app.player.stats.str) == strength + 1 and app.player.free_points == 2, "allocation closure applies strength once")
	await click("＋", 1)
	check(app.player.stats.con == 101 and app.player.free_points == 1, "allocation closure applies constitution independently")
	hud_matches(await state())
	await escape()
	await app.open_shop("smith")
	await click("购入 · 80")
	check(app.player.gold == 920, "iron sword purchased")
	await escape()
	app.open_inventory()
	var attack: int = int(app.player.atk)
	await click("装备")
	check(app.player.equipped.weapon.name == "铁剑" and app.player.atk == attack + 8, "equip callback adds sword attack")
	hud_matches(await state())
	await escape()
	await app.open_shop("smith")
	await click("卖出")
	check(button("卖出 · 40").disabled, "equipped sword cannot be sold")
	await escape()
	app.open_character()
	await click("卸下")
	check(app.player.equipped.weapon == null and app.player.atk == attack, "unequip restores attack")
	hud_matches(await state())
	await escape()
	app.world.actor_pos = Vector2(144, 182)
	app.interact()
	check(app.modal_kind == "sect", "sect doorway interaction")
	await click("确认拜入剑阳派")
	check(app.player.sect.id == "jianyang" and app.modal_kind == "skills", "level-three joins sect through UI")
	check(app.player.stats.str == strength + 4 and app.player.stats.int == 22, "one-time sect attribute bonuses")
	check(app.player.skills.size() == 2, "sect grants passive and entry active")
	await click("卸出招式")
	check(not app.player.skills[1].equipped, "active skill unequipped")
	await click("装备招式")
	check(app.player.skills[1].equipped, "active skill re-equipped")
	var cultivation: int = int(app.player.cultivation)
	await click("修习", 1)
	check(app.player.skills[1].level == 2 and app.player.cultivation == cultivation - 20, "active skill upgrade spends cultivation")
	await click("修习", 0)
	check(app.player.skills[0].level == 2 and app.player.atk == attack + 10, "passive upgrade increases derived attack")
	hud_matches(await state())
	await escape()
	app.open_sect("yanyu", "烟雨小筑")
	check(button("确认拜入").disabled, "existing sect locks a second join")
	var denied: Dictionary = await app.api.request("/api/sect/join", {"sect_id": "yanyu"})
	check(not denied.ok and int(denied.status) == 400, "backend rejects second sect")
	await escape()
	await begin("boss_1")
	await escape()
	check(app.modal_kind == "battle" and app.battle.active, "Esc cannot hide active battle")
	var hp: float = float(app.battle.enemy.hp)
	await click("普攻")
	check(app.battle.active and float(app.battle.enemy.hp) < hp and app.battle.round == 2, "normal attack advances real battle")
	var mp: float = float(app.player.mp)
	await click("赤阳斩")
	check(app.battle.active and app.player.mp == mp - 8 and app.battle.round == 3, "equipped upgraded skill consumes MP")
	var quantity: int = int(app.player.inventory[0].qty)
	var wounded_hp: float = float(app.player.hp)
	await click("小回春散")
	check(app.battle.active and app.battle.potions_used == 1 and app.battle.round == 4, "potion consumes one combat turn")
	check(float(app.player.hp) > wounded_hp, "potion restores real player HP")
	check(app.player.inventory.is_empty() or int(app.player.inventory[0].qty) < quantity or app.player.inventory[0].type != "potion", "potion inventory consumed")
	var saved: Dictionary = await state()
	check(saved.battle.id == app.battle.id and saved.battle.round == 4, "active battle persisted for next process")
	hud_matches(saved)
	# Exit with this battle active: runner restarts both Godot and the backend.

func begin(id: String) -> void:
	var mob: Dictionary = {}
	for candidate in app.world.mobs:
		if candidate.id == id:
			mob = candidate
	check(not mob.is_empty() and mob.alive, "target is alive: " + id)
	app.world.actor_pos = Vector2(mob.x, mob.y)
	await app.start_battle(id)
	await settle()
	check(app.battle.get("active", false) and app.modal_kind == "battle", "battle-start UI opens: " + id)

func resume_battle() -> void:
	check(app.modal_kind == "battle" and app.battle.get("active", false), "new Godot process restores active battle automatically")
	check(app.battle.round == 4 and app.battle.potions_used == 1 and app.player.mp == 192, "round, potion and MP survive backend restart")
	hud_matches(await state())
	for turn in range(12):
		if not app.battle.active:
			break
		await click("普攻")
	check(not app.battle.active and app.battle.result == "win", "bounded attacks finish boss victory")
	check(app.battle_log.any(func(line: String): return line.contains("所得")), "victory UI renders rewards")
	var saved: Dictionary = await state()
	check(saved.battle == null and app.player.gold == 1020, "server closes victorious battle and grants gold")
	check(app.player.inventory.any(func(item: Dictionary): return item.name == "玄铁剑"), "guaranteed boss drop delivered")
	hud_matches(saved)
	await escape()
	check(app.modal == null and app.battle.is_empty(), "Esc on ended battle clears modal and local battle")
	# Finish a second battle through a separate real HTTP client call, leaving the
	# scene's active snapshot stale; an actual action button must recover from 400.
	await begin("wolf_2")
	var external: Dictionary = {}
	for turn in range(5):
		external = await app.api.request("/api/battle/action", {"action": "attack"})
		check(external.ok, "external real-server attack")
		if not external.data.battle.active:
			break
	check(external.data.battle.result == "win" and app.battle.active, "server ended battle while frontend still active")
	await click("普攻")
	check(app.battle.is_empty() and app.modal == null, "stale action error refresh clears obsolete active battle")
	hud_matches(await state())
	check(app.world.can_walk(app.world.actor_pos), "server-finished recovery places actor on walkable tile")

func defeat() -> void:
	check(app.player.hp == 1 and app.player.stats.agi == 0, "deterministic defeat fixture loaded")
	await begin("wolf_3")
	check(not app.battle.player_first, "enemy guaranteed to act first")
	await click("普攻")
	check(not app.battle.active and app.battle.result == "lose", "real combat defeat and revival")
	var saved: Dictionary = await state()
	check(saved.battle == null and app.player.hp == ceilf(float(app.player.hp_max) / 2), "server revives at half HP and closes battle")
	var server_pos := Vector2(saved.player.pos.x, saved.player.pos.y)
	check(server_pos.distance_to(Vector2(520, 384)) <= 64, "server revival remains near inn")
	if not app.world.can_walk(server_pos):
		print("FINDING: server revival %s is solid; Godot repaired it to %s" % [server_pos, app.world.actor_pos])
	check(app.world.can_walk(app.world.actor_pos), "client revival position is walkable")
	check(app.world.actor_pos.distance_to(server_pos) <= 32, "revival repair remains near server position")
	await escape()
	check(app.modal == null and app.battle.is_empty(), "Esc dismisses defeat result")
	var before: Vector2 = app.world.actor_pos
	await key_move(KEY_S)
	check(app.world.actor_pos.y > before.y + 8 and app.world.can_walk(app.world.actor_pos), "revived actor can walk away using S")
	hud_matches(await state())

func _run() -> void:
	mode = OS.get_environment("XIA_SMOKE_MODE")
	var scratch: String = OS.get_environment("XIA_SMOKE_ROOT")
	var address: String = OS.get_environment("XIA_API_URL")
	# Fail closed when launched manually without the isolating runner.
	if scratch.is_empty() or not ProjectSettings.globalize_path("user://").begins_with(scratch + "/") or not address.begins_with("http://127.0.0.1:") or address.ends_with(":8000"):
		printerr("SMOKE FAIL: run smoke_test.py; disposable userdata/backend required")
		quit(1)
		return
	create_timer(60).timeout.connect(func():
		printerr("SMOKE FAIL: harness watchdog expired")
		quit(1))
	root.size = Vector2i(1440, 900)
	var scene: PackedScene = load("res://scenes/main.tscn")
	app = scene.instantiate()
	root.add_child(app)
	await settle()
	check(app.api.base_url == address, "API points only at isolated backend")
	match mode:
		"base": await base()
		"advanced": await advanced()
		"resume": await resume_battle()
		"defeat": await defeat()
		_: check(false, "unknown harness mode")
	if not failed:
		print("SMOKE_DONE %s %d checks" % [mode, checks])
	quit(1 if failed else 0)
