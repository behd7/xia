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
var scene_review := OS.get_environment("XIA_HD2D_SCENE_REVIEW") == "1"
var matrix_time := -1.0
var matrix_revision := 0
var matrix_slug := ""
var ui_revision := 0
var ui_active := false
var ui_edge := false
var ui_saved_npcs: Array = []
var review_light := 0
var sign_review_index := -1
var sign_text_hidden := false
var profile_enabled := OS.get_environment("XIA_HD2D_PROFILE") == "1"
var no_captures := OS.get_environment("XIA_HD2D_NO_CAPTURES") == "1"
var profile_frames: FileAccess
var profile_events: FileAccess
var profile_stage := "startup"
var native_sign_count := 0
var capture_count := 0
var capture_usec := 0
var snapshot_usec := 0
var cached_signs: Dictionary = {}
var sign_query_elapsed := 0.2
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
const CameraLimits = preload("res://scripts/hd2d/camera_limits.gd")

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
	var cap := OS.get_environment("XIA_HD2D_MAX_FPS")
	Engine.max_fps = 60 if cap.is_empty() else maxi(0, int(cap))
	if profile_enabled:
		matrix_time = 43200.0
	root.size = Vector2i(1440, 900)
	app = load("res://scenes/hd2d/game.tscn").instantiate()
	root.add_child(app)
	app.profile_enabled = profile_enabled
	app.world_presenter.view.profile_enabled = profile_enabled
	app.world_presenter.view.occlusion.profile_enabled = true
	native_sign_count = app.world_presenter.view.scenery.find_children("牌匾文字_*", "Label3D", true, false).size()
	if profile_enabled:
		profile_frames = FileAccess.open(output.path_join("profile_frames.csv"), FileAccess.WRITE)
		profile_frames.store_line("seconds,interval_ms,submit_ms,gpu_ms,draw_calls")
		profile_events = FileAccess.open(output.path_join("profile_events.jsonl"), FileAccess.WRITE)
	RenderingServer.viewport_set_measure_render_time(app.world_presenter.viewport.get_viewport_rid(), true)
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

func _physics_process(delta: float) -> bool:
	if app == null or finishing or samples == null or profile_enabled:
		return false
	sign_query_elapsed += delta
	if sign_query_elapsed >= 0.2:
		sign_query_elapsed = 0.0
		cached_signs = sign_snapshot(app.world_presenter.view)
	return false

func _process(_delta: float) -> bool:
	if app == null or finishing or samples == null:
		return false
	if profile_events != null and not app.profile_events.is_empty():
		for event: Dictionary in app.profile_events:
			event["seconds"] = (int(event.at_usec) - started) / 1000000.0
			profile_events.store_line(JSON.stringify(event))
		app.profile_events.clear()
		profile_events.flush()
	var stage_request := output.path_join("stage.request")
	if profile_enabled and FileAccess.file_exists(stage_request):
		profile_stage = FileAccess.get_file_as_string(stage_request)
		DirAccess.remove_absolute(stage_request)
	var ui_request := output.path_join("ui.request")
	if FileAccess.file_exists(ui_request):
		var command: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(ui_request))
		DirAccess.remove_absolute(ui_request)
		ui_revision = int(command.revision)
		if command.get("reset", false):
			ui_active = false
			matrix_time = -1
			app.world.npcs.assign(ui_saved_npcs)
			app.refresh()
		else:
			if not ui_active:
				ui_saved_npcs = app.world.npcs.duplicate(true)
			app.world.npcs.assign(ui_saved_npcs.duplicate(true))
			ui_active = true
			ui_edge = bool(command.edge)
			matrix_time = 43200.0
			app.zoom = 2
			app.world.actor_pos = Vector2(8,8) if ui_edge else Layout.GATHER_ANCHOR
	var matrix_request := output.path_join("matrix.request")
	if OS.get_environment("XIA_HD2D_NPC_MATRIX") == "1" and FileAccess.file_exists(matrix_request):
		var command: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(matrix_request))
		DirAccess.remove_absolute(matrix_request)
		matrix_time = float(command.seconds)
		matrix_slug = str(command.slug)
		for npc: Dictionary in app.world.npcs:
			npc["deviation"] = {}
		app.world.tick_schedule(matrix_time)
		for npc: Dictionary in app.world.npcs:
			if npc.slug == matrix_slug:
				app.world.actor_pos = npc.pos if command.near else Vector2(8, 8)
		matrix_revision = int(command.revision)
	if matrix_time >= 0:
		app.clock = {"game_sec": matrix_time, "time_scale": 0.0}
	if ui_active:
		for i: int in app.world.npcs.size():
			var npc: Dictionary = app.world.npcs[i]
			npc["deviation"] = {}
			npc["bubble"] = "清溪路远客官慢行，江湖相逢请多保重。"
			npc["bubble_until"] = Time.get_ticks_msec() / 1000.0 + 10.0
			if ui_edge:
				npc.erase("home_to_work_pts")
				npc["pos"] = Vector2(72 + i % 3 * 24, 72 + i / 3 * 24)
	if scene_review:
		app.world.npcs.clear()
		app.world.mobs.clear()
		app.clock = {"game_sec": [43200.0, 64800.0, 79200.0][review_light], "time_scale": 0.0}
	var now := Time.get_ticks_usec()
	var seconds := (now - started) / 1000000.0
	if seconds > 3:
		frames.store_line(str((now - last_tick) / 1000.0))
		if profile_frames != null:
			var rid: RID = app.world_presenter.viewport.get_viewport_rid()
			profile_frames.store_line("%.6f,%.3f,%.6f,%.6f,%d" % [seconds, (now-last_tick)/1000.0, RenderingServer.viewport_get_measured_render_time_cpu(rid), RenderingServer.viewport_get_measured_render_time_gpu(rid), Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME)])
	last_tick = now
	var step: float = app.world.actor_pos.distance_to(previous)
	if step < 32:
		distance += step
	previous = app.world.actor_pos
	if seconds - last_sample >= 0.2:
		last_sample = seconds
		var snapshot_start := Time.get_ticks_usec()
		samples.store_line(JSON.stringify(snapshot()))
		samples.flush()
		snapshot_usec += Time.get_ticks_usec() - snapshot_start
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

func sign_snapshot(view: Node3D) -> Dictionary:
	if not Engine.is_in_physics_frame():
		push_error("Sign diagnostics must query in the physics phase")
		return {}
	var result: Dictionary = {}
	for label: Label3D in view.scenery.find_children("牌匾文字_*", "Label3D", true, false):
		var width: float = label.font.get_string_size(label.text, HORIZONTAL_ALIGNMENT_LEFT, -1, label.font_size).x * label.pixel_size
		var height: float = float(label.get_meta("logical_height")) / 16.0
		var clear := 0
		var projected: Array = []
		for x: float in [-0.5, 0.0, 0.5]:
			for y: float in [-0.5, 0.0, 0.5]:
				var at := label.global_position + Vector3(x * width, y * height, 0)
				if view.occlusion._ray_groups(view, view.camera, at, true).is_empty():
					clear += 1
				projected.append(view.camera.unproject_position(at))
		var rect := Rect2(projected[0], Vector2.ZERO)
		for at: Vector2 in projected:
			rect = rect.expand(at)
		var on_screen: bool = not view.camera.is_position_behind(label.global_position) and Rect2(Vector2.ZERO, Vector2(view.get_viewport().size)).encloses(rect)
		var overlay_overlap := false
		for native: Label in app.world_presenter.overlay.labels.values():
			if native.visible and Rect2(native.position, native.size).intersects(rect.grow(4.0)):
				overlay_overlap = true
		result[str(label.get_meta("building_id"))] = {"text": label.text, "clear_samples": clear, "on_screen": on_screen,
			"size": [rect.size.x, rect.size.y], "depth_test": not label.no_depth_test, "billboard": label.billboard,
			"cutout": label.alpha_cut == Label3D.ALPHA_CUT_DISCARD, "overlay_overlap": overlay_overlap, "visible": label.visible}
	return result

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
	var label_rects: Dictionary = {}
	var labels: Dictionary = {}
	for key: String in app.world_presenter.overlay.labels:
		var label: Label = app.world_presenter.overlay.labels[key]
		if label.visible:
			labels[key] = label.text
			label_rects[key] = [label.position.x, label.position.y, label.size.x, label.size.y]
	var focus: Control = app.get_viewport().gui_get_focus_owner()
	var focus_inside: bool = focus != null and app.modal != null and app.modal.is_ancestor_of(focus)
	var input_text := ""
	if app.modal != null:
		var input = app.modal.find_child("DialogueInput", true, false)
		if input != null:
			input_text = input.text
	var footprint := CameraLimits.ground_rect(view.camera, Vector2(view.get_viewport().size))
	var hero_frame: Rect2 = view.hero.occlusion_rect(view.camera)
	var backend := RenderingServer.get_current_rendering_method()
	var signs := cached_signs
	return {"profile_stage": profile_stage, "max_fps": Engine.max_fps, "no_captures": no_captures,
		"profile": {"view_frames": view.profile_frames, "view_usec": view.profile_usec, "main_frames": app.profile_frames, "main_usec": app.profile_usec, "main_max_usec": app.profile_max_usec, "occlusion": view.occlusion.profile, "capture_count": capture_count, "capture_usec": capture_usec, "snapshot_usec": snapshot_usec},
		"pipeline_compilations": {"canvas": Performance.get_monitor(Performance.PIPELINE_COMPILATIONS_CANVAS), "mesh": Performance.get_monitor(Performance.PIPELINE_COMPILATIONS_MESH), "surface": Performance.get_monitor(Performance.PIPELINE_COMPILATIONS_SURFACE), "draw": Performance.get_monitor(Performance.PIPELINE_COMPILATIONS_DRAW), "specialization": Performance.get_monitor(Performance.PIPELINE_COMPILATIONS_SPECIALIZATION)},
		"physics_separate_thread": ProjectSettings.get_setting("physics/3d/run_on_separate_thread", false), "physics_last_second_max_ms": Performance.get_monitor(Performance.TIME_PHYSICS_PROCESS)*1000.0,"ui_revision": ui_revision, "label_rects": label_rects, "placement_failures": app.world_presenter.overlay.placement_failures, "overlay_layout_usec": app.world_presenter.overlay.layout_usec,
		"focus_text": focus.text if focus is Button else "", "modal_opens": app.focus_scope.open_count,
		"focus_inside_modal": focus_inside, "focus_name": str(focus.name) if focus != null else "",
		"cpu_process_ms": Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0,
		"matrix_revision": matrix_revision, "matrix_slug": matrix_slug, "dialogue_slug": app.dialogue_slug, "matrix_time": matrix_time, "features": {"renderer": backend, "dof_supported": backend != "gl_compatibility",
		"dof_requested": view.lighting.attributes.dof_blur_far_enabled,
		"sun_shadow": view.lighting.sun.shadow_enabled, "native_signs": native_sign_count},
		"camera_ground_enclosed": Layout.SCENERY_BOUNDS.grow(-31).encloses(footprint),
		"hero_on_screen": Rect2(Vector2.ZERO, Vector2(view.get_viewport().size)).encloses(hero_frame),
		"fog_enabled": view.lighting.environment.fog_enabled,
		"ime": DisplayServer.ime_get_text(), "input_text": input_text, "seconds": (Time.get_ticks_usec() - started) / 1000000.0, "position": [app.world.actor_pos.x, app.world.actor_pos.y],
		"hero_height": view.hero.position.y, "ground_height": Layout.height_at(app.world.actor_pos) / 16.0,
		"camera_depth": -view.camera.to_local(view.hero.global_position).z, "foreground_clip": view.foreground_clip_depth,
		"zoom": app.zoom, "pitch": view.pitch, "yaw": view.yaw, "fov": view.fov, "actor_direction": view.hero.facing,
		"occlusion_enabled": view.occlusion.enabled,
		"scroll": app.modal.find_child("ContentScroll", true, false).scroll_vertical if app.modal != null else 0,
		"modal": app.modal_kind, "panel": app.modal != null, "busy": app.busy, "online": app.online,
		"target": view.current_target, "npcs": npcs, "population": view.population.keys(), "actor_assets": actor_assets, "labels": labels, "bubbles": bubbles,
		"buttons": controls, "text": text_content(app.modal) if app.modal != null else "",
		"gold": app.player.get("gold", -1), "battle": app.battle, "outlines": view.occlusion.strengths(), "signs": signs,
		"window": [root.size.x, root.size.y], "viewport": [view.get_viewport().size.x, view.get_viewport().size.y],
		"hud_writes": app.hud_writes, "map_static_draws": app.minimap.static_draw_count, "map_dynamic_draws": app.minimap.dynamic_draw_count,
		"rendered_frames": rendered_frames, "static_memory": Performance.get_monitor(Performance.MEMORY_STATIC),
		"video_memory": Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED),
		"draw_calls": Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		"gpu_ms": RenderingServer.viewport_get_measured_render_time_gpu(app.world_presenter.viewport.get_viewport_rid()),
		"cpu_ms": RenderingServer.viewport_get_measured_render_time_cpu(app.world_presenter.viewport.get_viewport_rid())}

func handle_key(key: Key) -> void:
	if key == KEY_F12:
		capture()
		return
	if app.modal != null or app.busy:
		return
	match key:
		KEY_L:
			if scene_review: review_light = (review_light + 1) % 3
		KEY_0:
			if scene_review: app.world.actor_pos = app._walkable_position(Layout.GATE_AT - Vector2(0, 44))
		KEY_1:
			if scene_review:
				app.world.actor_pos = app._walkable_position(Vector2(8, 8))
				app.zoom = 3
		KEY_2:
			if scene_review:
				sign_review_index = (sign_review_index + 1) % Layout.BUILDINGS.size()
				var building: Dictionary = Layout.BUILDINGS[sign_review_index]
				var rect: Rect2 = building.rect
				var offset: Vector3 = Layout.BUILDING_SIGNS[building.id].offset
				app.world.actor_pos = app._walkable_position(Vector2(rect.get_center().x + offset.x, rect.end.y + 28))
		KEY_3:
			if scene_review: app.world.actor_pos = Vector2(680, 624)
		KEY_4:
			# Review-only text toggle; geometry, lighting and camera stay identical.
			if scene_review:
				sign_text_hidden = not sign_text_hidden
				for label: Label3D in app.world_presenter.view.scenery.find_children("牌匾文字_*", "Label3D", true, false):
					label.visible = not sign_text_hidden
		KEY_6:
			if scene_review: app.world.actor_pos = app._walkable_position(Vector2(76, 76))
		KEY_7:
			if scene_review: app.world.actor_pos = app._walkable_position(Vector2(1524, 76))
		KEY_8:
			if scene_review: app.world.actor_pos = app._walkable_position(Vector2(76, 884))
		KEY_9:
			if scene_review: app.world.actor_pos = app._walkable_position(Vector2(1524, 884))
		KEY_F1: app.world.actor_pos = Vector2(496, 604)
		KEY_F2: app.world.actor_pos = Vector2(496, 412)
		KEY_F3: app.world.actor_pos = Vector2(240, 502)
		KEY_F4: app.world.actor_pos = Vector2(832, 624)
		KEY_F5:
			for npc: Dictionary in app.world.npcs:
				if npc.slug == "zhang_mantou":
					app.world.actor_pos = app._walkable_position(npc.pos + Vector2(0, 25))
		KEY_F7: app.world_presenter.view.occlusion.enabled = not app.world_presenter.view.occlusion.enabled
		KEY_F8:
			if scene_review: app.world.actor_pos = Vector2(1304, 448)
		KEY_F10:
			if scene_review: app.world.actor_pos = Vector2(272, 232)
		KEY_F11:
			if scene_review: app.world.actor_pos = Vector2(1310, 768)
		KEY_F9: app.world.show_bubble("zhang_mantou", "清溪路远，客官慢行。", 1.5)
		KEY_F6:
			for mob: Dictionary in app.world.mobs:
				if mob.id == "wolf_1":
					app.world.actor_pos = app._walkable_position(Vector2(mob.x, mob.y) + Vector2(0, 23))

func capture() -> void:
	await RenderingServer.frame_post_draw
	var file := output.path_join("manual-%09d" % Time.get_ticks_msec())
	if not no_captures:
		var capture_start := Time.get_ticks_usec()
		root.get_texture().get_image().save_png(file + ".png")
		capture_usec += Time.get_ticks_usec() - capture_start
		capture_count += 1
	var state := snapshot()
	if not profile_enabled:
		# The next physics phase has the just-drawn camera/actor transforms.
		# Keep real diagnostic rays out of the render callback as well.
		await physics_frame
		state["signs"] = sign_snapshot(app.world_presenter.view)
	var stream := FileAccess.open(file + ".json", FileAccess.WRITE)
	stream.store_string(JSON.stringify(state))

func finish() -> void:
	finishing = true
	RenderingServer.frame_post_draw.disconnect(_drawn)
	frames.close()
	samples.close()
	if profile_frames != null:
		profile_frames.close()
		profile_events.close()
	var data := snapshot()
	data.merge({"device": RenderingServer.get_video_adapter_name(), "backend": RenderingServer.get_current_rendering_method(),
		"duration": data.seconds, "distance": distance, "input_events": input_events, "quality": app.world_presenter.view.quality,
		"scope": "Scenic full Main + disposable backend; NPC/mob presentation excluded; NOT P5" if scene_review else "P2 real Main + disposable backend; SSE fixture replaces model only; NOT P5"})
	var report := FileAccess.open(output.path_join("metrics.json"), FileAccess.WRITE)
	report.store_string(JSON.stringify(data))
	print("HD2D_GAME_DONE ", JSON.stringify(data))
	quit()
