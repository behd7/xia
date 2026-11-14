extends Control
## Offline P1 fixture. Never calls the backend or replaces the playable main scene.
const World = preload("res://scripts/town_world.gd")
const Projector = preload("res://scripts/hd2d/projection.gd")
const Layout = preload("res://scripts/hd2d/fixture_layout.gd")
var world: Node2D
var area: Control
var viewport: SubViewport
var view: Node3D
var overlay: Control
var hero_label: Label
var target_label: Label
var status: Label
var caption: Label
var actions: HBoxContainer
var panel: PanelContainer
var input: LineEdit
var zoom := 2
var clock_anchor := 12.0*3600
var clock_received := 0.0
var clock_scale := 60.0
var baseline := false
var runtime := 0.0
var output := ""
var duration := 0.0
var frame_file: FileAccess
var sample_file: FileAccess
var last_frame_usec := 0
var started_usec := 0
var sample_time := 0.0
var move_distance := 0.0
var input_events := 0
var rendered_frames := 0
var next_capture := 2.0
var capture_interval := 30.0
var finishing := false
var camera_panel: PanelContainer
var pitch_slider: HSlider
var yaw_slider: HSlider
var fov_slider: HSlider
var camera_readout: Label
var camera_dragging := false

func _ready() -> void:
	# World mouse events must reach _unhandled_input; native panels/buttons
	# still consume their own events before it. Decorative paper is not a hit box.
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	baseline = OS.get_environment("XIA_HD2D_BASELINE") == "1"
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	duration = float(OS.get_environment("XIA_HD2D_DURATION"))
	if not OS.get_environment("XIA_HD2D_CAPTURE_INTERVAL").is_empty():
		capture_interval = maxf(5.0, float(OS.get_environment("XIA_HD2D_CAPTURE_INTERVAL")))
	clock_received = Time.get_ticks_msec()/1000.0
	if not output.is_empty():
		DirAccess.make_dir_recursive_absolute(output)
		frame_file = FileAccess.open(output.path_join("frames.csv"),FileAccess.WRITE)
		sample_file = FileAccess.open(output.path_join("samples.jsonl"),FileAccess.WRITE)
	last_frame_usec = Time.get_ticks_usec()
	started_usec = last_frame_usec
	RenderingServer.frame_post_draw.connect(func(): rendered_frames += 1)
	if OS.get_environment("XIA_HD2D_NO_VSYNC") == "1":
		DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
		Engine.max_fps = 120
	if not OS.get_environment("XIA_HD2D_MAX_FPS").is_empty():
		Engine.max_fps = maxi(1, int(OS.get_environment("XIA_HD2D_MAX_FPS")))
	_bind_scene()
	print("HD2D_READY ", JSON.stringify({"device": RenderingServer.get_video_adapter_name(),
		"backend": RenderingServer.get_current_rendering_method(), "baseline": baseline,
		"window": [get_window().size.x, get_window().size.y]}))

func _bind_scene() -> void:
	area = $WorldArea
	caption = $Caption
	actions = $Actions
	status = $Footer/Status
	panel = $TestPanel
	input = $TestPanel/Content/Input
	world = World.new()
	world._ensure_grid()
	world.actor_pos = Vector2(512, 422)
	for scale_value: int in [1, 2, 3]:
		actions.get_node("Zoom%d" % scale_value).pressed.connect(func(): zoom = scale_value)
	$Actions/Time.pressed.connect(cycle_time)
	$Actions/Occlusion.pressed.connect(toggle_occlusion)
	$Actions/TestPanel.pressed.connect(toggle_panel)
	$TestPanel/Content/Close.pressed.connect(toggle_panel)
	if baseline:
		$WorldArea/WorldContainer.queue_free()
		$WorldArea/Overlay.hide()
		$Actions/Camera.hide()
		$Title.text = "侠 · 清溪客栈   /   原 2D 基线"
		area.add_child(world)
		world.ui_font = theme.default_font
	else:
		# Offline layout only; the game's authoritative grid remains unchanged.
		Layout.prepare(world)
		viewport = $WorldArea/WorldContainer/WorldViewport
		view = $WorldArea/WorldContainer/WorldViewport/TownView3D
		overlay = $WorldArea/Overlay
		hero_label = $WorldArea/Overlay/HeroLabel
		target_label = $WorldArea/Overlay/TargetLabel
		view.setup(world)
		view.quality = OS.get_environment("XIA_HD2D_QUALITY") if not OS.get_environment("XIA_HD2D_QUALITY").is_empty() else "standard"
		_bind_camera_panel()

func _bind_camera_panel() -> void:
	camera_panel = $CameraPanel
	camera_readout = $CameraPanel/Content/Readout
	pitch_slider = $CameraPanel/Content/PitchSlider
	yaw_slider = $CameraPanel/Content/YawSlider
	fov_slider = $CameraPanel/Content/FovSlider
	$CameraPanel/Content/Title.text = ("正交对照" if view.orthogonal else "透视视角") + " · 仅样板"
	fov_slider.editable = not view.orthogonal
	pitch_slider.value_changed.connect(func(value: float):
		view.set_pitch(value)
		_sync_camera_controls())
	yaw_slider.value_changed.connect(func(value: float):
		view.set_yaw(value)
		_sync_camera_controls())
	fov_slider.value_changed.connect(func(value: float):
		view.set_fov(value)
		_sync_camera_controls())
	$Actions/Camera.pressed.connect(toggle_camera_panel)
	$CameraPanel/Content/Buttons/Reset.pressed.connect(reset_camera)
	$CameraPanel/Content/Buttons/Close.pressed.connect(toggle_camera_panel)
	_sync_camera_controls()

func toggle_camera_panel() -> void:
	if camera_panel and not panel.visible:
		camera_panel.visible = not camera_panel.visible

func _sync_camera_controls() -> void:
	if pitch_slider:
		pitch_slider.set_value_no_signal(view.pitch)
		yaw_slider.set_value_no_signal(view.yaw)
		fov_slider.set_value_no_signal(view.fov)
		camera_readout.text = "俯角 %.1f° · 方位 %.1f°\n%s" % [view.pitch, view.yaw, "正交参考深度对照" if view.orthogonal else "垂直 FOV %.1f°" % view.fov]

func reset_camera() -> void:
	if view:
		view.reset_camera()
		_sync_camera_controls()

func _process(delta: float) -> void:
	if finishing:
		return
	var ticks := Time.get_ticks_usec()
	var frame_ms := (ticks-last_frame_usec)/1000.0
	last_frame_usec = ticks
	runtime = (ticks-started_usec)/1000000.0
	actions.position.x = maxf(560, size.x-actions.size.x-24)
	var direction := Vector2.ZERO
	if not panel.visible:
		direction = Vector2(float(Input.is_physical_key_pressed(KEY_D) or Input.is_physical_key_pressed(KEY_RIGHT))-float(Input.is_physical_key_pressed(KEY_A) or Input.is_physical_key_pressed(KEY_LEFT)),
			float(Input.is_physical_key_pressed(KEY_S) or Input.is_physical_key_pressed(KEY_DOWN))-float(Input.is_physical_key_pressed(KEY_W) or Input.is_physical_key_pressed(KEY_UP))).normalized()
	var previous: Vector2 = world.actor_pos
	if not baseline:
		direction = Projector.camera_relative_direction(direction, view.yaw)
	var step := direction * 150.0 * minf(delta, 0.04)
	for axis: Vector2 in [Vector2(step.x, 0), Vector2(0, step.y)]:
		if world.can_walk(world.actor_pos + axis):
			world.actor_pos += axis
	move_distance += world.actor_pos.distance_to(previous)
	world.moving = direction != Vector2.ZERO
	world.walk_time += delta if world.moving else 0.0
	var seconds := clock_anchor + (Time.get_ticks_msec()/1000.0-clock_received)*clock_scale
	if baseline:
		world.scale = Vector2.ONE * zoom
		world.position = (area.size*0.5-(world.actor_pos+Vector2(0,-68))*zoom).round()
	else:
		view.zoom = zoom
		view.update(world, area.size, seconds, delta)
		for entry: Array in [[hero_label, view.hero], [target_label, view.target]]:
			var label: Label = entry[0]
			var rect: Rect2 = entry[1].screen_rect(view.camera)
			label.position = (rect.position + Vector2(rect.size.x/2-label.size.x/2,-25)).round()
			label.visible = not view.camera.is_position_behind(entry[1].global_position) and rect.intersects(Rect2(Vector2.ZERO, area.size))
	var hour := fposmod(seconds/3600,24)
	var location: String = world.nearby().get("name", "路旁") if baseline else Layout.location(world.actor_pos, world)
	status.text = "位置 %.1f, %.1f · %s · %d× · %02d:%02d · 窗口 %s / 世界区 %s · %s" % [world.actor_pos.x,world.actor_pos.y,location,zoom,int(hour),int(fmod(seconds/60,60)),str(get_window().size),str(Vector2i(area.size)),"2D" if baseline else view.quality]
	if runtime > 3 and frame_file:
		frame_file.store_line(str(frame_ms))
	if int(runtime) > int(sample_time) and sample_file:
		sample_time = runtime
		sample_file.store_line(JSON.stringify({"seconds": runtime,"position": [world.actor_pos.x,world.actor_pos.y],"zoom":zoom,
			"fps":Engine.get_frames_per_second(),"static_memory":Performance.get_monitor(Performance.MEMORY_STATIC),
			"video_memory":Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED),"nodes":Performance.get_monitor(Performance.OBJECT_NODE_COUNT),
			"draw_calls":Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),"window":[get_window().size.x,get_window().size.y],
			"viewport":[int(area.size.x),int(area.size.y)],"panel":panel.visible,"text":input.text,
			"actor_frame": -1 if baseline else view.hero.sprite.frame,"actor_direction":-1 if baseline else view.hero.facing,"rendered_frames":rendered_frames,
			"pitch":null if baseline else view.pitch,"yaw":null if baseline else view.yaw,"hour":hour,
			"projection":"2d" if baseline else ("orthogonal" if view.orthogonal else "perspective"),"fov":null if baseline else view.fov,
			"occlusion_enabled":false if baseline else view.occlusion.enabled,
			"outlines":{} if baseline else view.occlusion.strengths()}))
		sample_file.flush()
	if not output.is_empty() and runtime >= next_capture and runtime > 3.5:
		next_capture += capture_interval
		capture("view-%04d" % int(runtime))
	if duration > 0 and runtime >= duration:
		finish()

func _input(event: InputEvent) -> void:
	if event is InputEventKey or event is InputEventMouseButton:
		input_events += 1
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_MIDDLE and not event.pressed:
		camera_dragging = false
	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		if panel.visible:
			toggle_panel()
			get_viewport().set_input_as_handled()
		elif camera_panel and camera_panel.visible:
			camera_panel.hide()
			get_viewport().set_input_as_handled()

func _unhandled_input(event: InputEvent) -> void:
	if panel.visible:
		return
	if view and event is InputEventMouseMotion and camera_dragging:
		if not Input.is_mouse_button_pressed(MOUSE_BUTTON_MIDDLE):
			camera_dragging = false
			return
		view.set_yaw(view.yaw - event.relative.x * 0.3)
		view.set_pitch(view.pitch + event.relative.y * 0.15)
		_sync_camera_controls()
		get_viewport().set_input_as_handled()
	if event is InputEventMouseButton and event.pressed and area.get_global_rect().has_point(event.position):
		if view and event.button_index == MOUSE_BUTTON_MIDDLE:
			camera_dragging = true
			get_viewport().set_input_as_handled()
		elif event.button_index == MOUSE_BUTTON_WHEEL_UP:
			zoom = mini(3, zoom+1)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			zoom = maxi(1, zoom-1)
	if event is InputEventKey and event.pressed and not event.echo:
		match event.physical_keycode:
			KEY_O: toggle_occlusion()
			KEY_L: cycle_time()
			KEY_Q:
				if view: view.quality = "light" if view.quality == "standard" else "standard"
			KEY_F:
				if view: view.lighting.effects = not view.lighting.effects
			KEY_B: toggle_panel()
			KEY_J: cycle_pitch()
			KEY_C: toggle_camera_panel()
			KEY_HOME: reset_camera()
			KEY_R, KEY_F1: world.actor_pos = Vector2(512, 422)
			KEY_F2: world.actor_pos = Vector2(512, 240)
			KEY_F3: world.actor_pos = Vector2(404, 320)
			KEY_F4: world.actor_pos = Vector2(620, 320)
			KEY_F12: capture("manual-%07d" % int(runtime*1000))

func cycle_pitch() -> void:
	if view:
		var index: int = Projector.PITCH_PRESETS.find(view.pitch)
		view.set_pitch(Projector.PITCH_PRESETS[(index+1) % Projector.PITCH_PRESETS.size()])
		_sync_camera_controls()

func cycle_time() -> void:
	clock_anchor = 18.0*3600 if clock_anchor < 17*3600 else (22.0*3600 if clock_anchor < 21*3600 else 12.0*3600)
	clock_received = Time.get_ticks_msec()/1000.0

func toggle_occlusion() -> void:
	if view:
		view.occlusion.enabled = not view.occlusion.enabled
		caption.text = "遮挡人物描边：%s · 可在 1600×960 样板中自由绕行" % ("开启" if view.occlusion.enabled else "关闭（检查真实遮挡）")

func toggle_panel() -> void:
	panel.visible = not panel.visible
	if panel.visible:
		camera_dragging = false
		if camera_panel:
			camera_panel.hide()
		input.grab_focus()
	else:
		input.release_focus()

func capture(file: String) -> void:
	if output.is_empty():
		return
	await RenderingServer.frame_post_draw
	get_viewport().get_texture().get_image().save_png(output.path_join(file+".png"))
	var snapshot := {"position":[world.actor_pos.x,world.actor_pos.y],"zoom":zoom,"panel":panel.visible,"text":input.text,
		"window":[get_window().size.x,get_window().size.y],"viewport":[int(area.size.x),int(area.size.y)],
		"pitch":null if baseline else view.pitch,"yaw":null if baseline else view.yaw,"buttons":[],"camera_controls":{},
		"actor_frame":-1 if baseline else view.hero.sprite.frame,"actor_direction":-1 if baseline else view.hero.facing,
		"projection":"2d" if baseline else ("orthogonal" if view.orthogonal else "perspective"),"fov":null if baseline else view.fov,
		"hour":fposmod((clock_anchor+(Time.get_ticks_msec()/1000.0-clock_received)*clock_scale)/3600.0,24.0),
		"occlusion_mode":"none" if baseline else "actor_outline",
		"occlusion_enabled":false if baseline else view.occlusion.enabled,
		"outlines":{} if baseline else view.occlusion.strengths()}
	for button: Button in actions.get_children():
		var r := button.get_global_rect()
		snapshot.buttons.append({"text":button.text,"center":[r.get_center().x,r.get_center().y]})
	if camera_panel:
		snapshot.camera_controls = {"visible":camera_panel.visible}
		for entry: Array in [["pitch",pitch_slider],["yaw",yaw_slider],["fov",fov_slider]]:
			var r: Rect2 = entry[1].get_global_rect()
			snapshot.camera_controls[entry[0]] = [r.position.x, r.position.y, r.size.x, r.size.y]
	var report := FileAccess.open(output.path_join(file+".json"),FileAccess.WRITE)
	report.store_string(JSON.stringify(snapshot))

func finish() -> void:
	if finishing:
		return
	finishing = true
	if not output.is_empty():
		var data := {"device":RenderingServer.get_video_adapter_name(),"backend":RenderingServer.get_current_rendering_method(),
			"quality":"baseline" if baseline else view.quality,"duration":runtime,"baseline":baseline,
			"occlusion_mode":"none" if baseline else "actor_outline",
			"pitch":null if baseline else view.pitch,"yaw":null if baseline else view.yaw,
			"projection":"2d" if baseline else ("orthogonal" if view.orthogonal else "perspective"),"fov":null if baseline else view.fov,
			"window":[get_window().size.x,get_window().size.y],"viewport":[int(area.size.x),int(area.size.y)],
			"distance":move_distance,"input_events":input_events,"frame_clock":"monotonic_usec (not smoothed process delta)",
			"vsync":DisplayServer.window_get_vsync_mode(),"rendered_frames":rendered_frames,
			"scope":"P1 offline visual fixture, NOT full-game/P5 interaction acceptance"}
		frame_file.flush()
		sample_file.flush()
		var file := FileAccess.open(output.path_join("metrics.json"),FileAccess.WRITE)
		file.store_string(JSON.stringify(data))
	get_tree().quit()

func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		camera_dragging = false
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		finish()

func _exit_tree() -> void:
	if not baseline and is_instance_valid(world):
		world.free()
