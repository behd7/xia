extends SceneTree
## P2 instrumented real Main. Only this disposable harness has teleport/capture keys.
var app
var output: String
var duration := 90.0
var started := 0
var rendered_frames := 0
var input_events := 0
var distance := 0.0
var previous := Vector2.ZERO
var last_tick := 0
var last_sample := 0.0
var frames: FileAccess
var samples: FileAccess
var finishing := false

class ProbeInput extends Node:
	var probe
	func _input(event: InputEvent) -> void:
		if event is InputEventKey or event is InputEventMouseButton:
			probe.input_events += 1
		if event is InputEventKey and event.pressed and not event.echo:
			probe.handle_key(event.physical_keycode)

func _initialize() -> void:
	_run.call_deferred()

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	var scratch := OS.get_environment("XIA_SMOKE_ROOT")
	if scratch.is_empty() or not ProjectSettings.globalize_path("user://").begins_with(scratch + "/") or not OS.get_environment("XIA_API_URL").begins_with("http://127.0.0.1:") or OS.get_environment("XIA_API_URL").ends_with(":8080"):
		printerr("HD2D_FAIL: P2 runner requires disposable resources")
		quit(1)
		return
	duration = maxf(60, float(OS.get_environment("XIA_HD2D_DURATION")))
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = 60
	root.size = Vector2i(1440, 900)
	app = load("res://scenes/hd2d/game.tscn").instantiate()
	root.add_child(app)
	var listener := ProbeInput.new()
	listener.probe = self
	root.add_child(listener)
	frames = FileAccess.open(output.path_join("frames.csv"), FileAccess.WRITE)
	samples = FileAccess.open(output.path_join("samples.jsonl"), FileAccess.WRITE)
	started = Time.get_ticks_usec()
	last_tick = started
	previous = app.world.actor_pos
	RenderingServer.frame_post_draw.connect(_drawn)

func _drawn() -> void:
	rendered_frames += 1

func _process(_delta: float) -> bool:
	if app == null or finishing or samples == null:
		return false
	var now := Time.get_ticks_usec()
	var seconds := (now - started) / 1000000.0
	if seconds > 3:
		frames.store_line(str((now - last_tick) / 1000.0))
	last_tick = now
	var step: float = app.world.actor_pos.distance_to(previous)
	if step < 32:
		distance += step
	previous = app.world.actor_pos
	if seconds - last_sample >= 0.2:
		last_sample = seconds
		samples.store_line(JSON.stringify(snapshot()))
		samples.flush()
	var request := output.path_join("capture.request")
	if FileAccess.file_exists(request):
		DirAccess.remove_absolute(request)
		capture()
	if seconds >= duration:
		finish()
	return false

func buttons(node: Node, result: Array) -> void:
	if node is Button and node.is_visible_in_tree() and not node.disabled:
		var rect: Rect2 = node.get_global_rect()
		# A scrolled-off control is not an available OS click target.
		var visible_rect := rect
		var parent := node.get_parent()
		while parent is Control:
			if parent.clip_contents:
				visible_rect = visible_rect.intersection(parent.get_global_rect())
			parent = parent.get_parent()
		if visible_rect.has_area() and visible_rect.has_point(rect.get_center()):
			result.append({"text": node.text, "center": [rect.get_center().x, rect.get_center().y]})
	for child: Node in node.get_children():
		buttons(child, result)

func text_content(node: Node) -> String:
	var text := str(node.text) + "\n" if node is Label else ""
	for child: Node in node.get_children():
		text += text_content(child)
	return text

func snapshot() -> Dictionary:
	var view = app.world_presenter.view
	var controls: Array = []
	buttons(app, controls)
	var npcs: Dictionary = {}
	for npc: Dictionary in app.world.npcs:
		npcs[str(npc.slug)] = [npc.pos.x, npc.pos.y]
	var actor_assets: Dictionary = {}
	for key: String in app.world_presenter.view.population:
		actor_assets[key] = app.world_presenter.view.population[key].p3_key
	var bubbles: Dictionary = {}
	for npc: Dictionary in app.world.npcs:
		if not str(npc.get("bubble", "")).is_empty():
			bubbles[str(npc.slug)] = {"text": str(npc.bubble), "until": float(npc.bubble_until)}
	var labels: Dictionary = {}
	for key: String in app.world_presenter.overlay.labels:
		var label: Label = app.world_presenter.overlay.labels[key]
		if label.visible:
			labels[key] = label.text
	var sliders: Dictionary = {}
	for key: String in app.world_presenter.sliders:
		var rect: Rect2 = app.world_presenter.sliders[key].get_global_rect()
		sliders[key] = {"position": [rect.position.x, rect.position.y], "size": [rect.size.x, rect.size.y]}
	var input_text := ""
	if app.modal != null:
		var input = app.modal.find_child("DialogueInput", true, false)
		if input != null:
			input_text = input.text
	return {"sliders": sliders, "ime": DisplayServer.ime_get_text(), "input_text": input_text, "seconds": (Time.get_ticks_usec() - started) / 1000000.0, "position": [app.world.actor_pos.x, app.world.actor_pos.y],
		"zoom": app.zoom, "pitch": view.pitch, "yaw": view.yaw, "fov": view.fov, "actor_direction": view.hero.facing,
		"camera_panel": app.world_presenter.camera_panel.visible, "occlusion_enabled": view.occlusion.enabled,
		"scroll": app.modal.find_child("ContentScroll", true, false).scroll_vertical if app.modal != null else 0,
		"modal": app.modal_kind, "panel": app.modal != null, "busy": app.busy, "online": app.online,
		"target": view.current_target, "npcs": npcs, "population": view.population.keys(), "actor_assets": actor_assets, "labels": labels, "bubbles": bubbles,
		"buttons": controls, "text": text_content(app.modal) if app.modal != null else "",
		"gold": app.player.get("gold", -1), "battle": app.battle, "fade_groups": view.occlusion.strengths(),
		"window": [root.size.x, root.size.y], "viewport": [view.get_viewport().size.x, view.get_viewport().size.y],
		"rendered_frames": rendered_frames, "static_memory": Performance.get_monitor(Performance.MEMORY_STATIC),
		"video_memory": Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED),
		"draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME)}

func handle_key(key: Key) -> void:
	if key == KEY_F12:
		capture()
		return
	if app.modal != null or app.busy:
		return
	match key:
		KEY_F1: app.world.actor_pos = Vector2(512, 422)
		KEY_F2: app.world.actor_pos = Vector2(512, 240)
		KEY_F3: app.world.actor_pos = Vector2(128, 375)
		KEY_F4: app.world.actor_pos = Vector2(792, 440)
		KEY_F5:
			for npc: Dictionary in app.world.npcs:
				if npc.slug == "zhang_mantou":
					app.world.actor_pos = app._walkable_position(npc.pos + Vector2(0, 25))
		KEY_F7: app.world_presenter.view.occlusion.enabled = not app.world_presenter.view.occlusion.enabled
		KEY_F9: app.world.show_bubble("zhang_mantou", "清溪路远，客官慢行。", 1.5)
		KEY_F6:
			for mob: Dictionary in app.world.mobs:
				if mob.id == "wolf_1":
					app.world.actor_pos = app._walkable_position(Vector2(mob.x, mob.y) + Vector2(0, 23))

func capture() -> void:
	await RenderingServer.frame_post_draw
	var file := output.path_join("manual-%09d" % Time.get_ticks_msec())
	root.get_texture().get_image().save_png(file + ".png")
	var stream := FileAccess.open(file + ".json", FileAccess.WRITE)
	stream.store_string(JSON.stringify(snapshot()))

func finish() -> void:
	finishing = true
	RenderingServer.frame_post_draw.disconnect(_drawn)
	frames.close()
	samples.close()
	var data := snapshot()
	data.merge({"device": RenderingServer.get_video_adapter_name(), "backend": RenderingServer.get_current_rendering_method(),
		"duration": data.seconds, "distance": distance, "input_events": input_events, "quality": app.world_presenter.view.quality,
		"scope": "P2 real Main + disposable backend; SSE fixture replaces model only; NOT P5"})
	var report := FileAccess.open(output.path_join("metrics.json"), FileAccess.WRITE)
	report.store_string(JSON.stringify(data))
	print("HD2D_GAME_DONE ", JSON.stringify(data))
	quit()
