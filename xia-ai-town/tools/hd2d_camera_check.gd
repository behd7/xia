extends SceneTree
## Fixed playable-camera contract. P1's freely adjustable fixture is separate.
const World = preload("res://scripts/town_world.gd")
const View = preload("res://scenes/hd2d/town_view.tscn")
const Projector = preload("res://scripts/hd2d/projection.gd")
var checks := 0
var failures := 0

func check(ok: bool, label: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		printerr("CAMERA_FAIL: ", label)

func _initialize() -> void:
	_run.call_deferred()

func _run() -> void:
	var world := World.new()
	world.render_2d = false
	world.scenic_layout = true
	world.actor_pos = Vector2(832, 648)
	root.add_child(world)
	var viewport := SubViewport.new()
	viewport.size = Vector2i(1440, 900)
	viewport.own_world_3d = true
	root.add_child(viewport)
	var view := View.instantiate()
	viewport.add_child(view)
	view.setup(world, true)
	view.quality = "light"
	check(Projector.GAME_PITCH == 30.0 and Projector.GAME_YAW == 0.0 and Projector.FOV == 35.0,
		"approved gameplay constants: pitch30/yaw0/FOV35")
	check(view.pitch == 30.0, "fixed gameplay pitch")
	check(view.yaw == 0.0, "fixed gameplay yaw")
	check(view.fov == 35.0 and not view.orthogonal, "fixed perspective FOV")
	for pixels: Vector2i in [Vector2i(1100, 720), Vector2i(1440, 900), Vector2i(2560, 1080)]:
		viewport.size = pixels
		for zoom: int in [1, 2, 3]:
			view.zoom = zoom
			view.update(world, Vector2(pixels), 43200.0, 0.0)
			check(view.camera.projection == Camera3D.PROJECTION_PERSPECTIVE and is_equal_approx(view.camera.fov, 35.0), "projection/FOV at %s x%d" % [pixels, zoom])
			check(is_equal_approx(view.camera.rotation_degrees.x, -30.0) and is_zero_approx(view.camera.rotation_degrees.y), "actual camera pose at %s x%d" % [pixels, zoom])
			check(view.foreground_clip_depth >= 0.0 and view.foreground_clip_depth <= 8.0, "bounded close-foreground clipping")
	view.set_pitch(55.0)
	view.set_yaw(90.0)
	view.set_fov(40.0)
	view.reset_camera()
	check(view.pitch == 30.0 and view.yaw == 0.0 and view.fov == 35.0, "full-map reset does not restore P1 pose")
	viewport.queue_free()
	world.queue_free()
	await process_frame
	print("HD2D_CAMERA_CHECKS: %d failures=%d" % [checks, failures])
	quit(1 if failures else 0)
