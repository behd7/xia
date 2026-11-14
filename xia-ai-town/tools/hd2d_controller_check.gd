extends SceneTree
## Exercises the same substep controller Main calls; dt is explicit, not wall time.
const World = preload("res://scripts/town_world.gd")
const View = preload("res://scripts/hd2d/town_view.gd")
var failures := 0
var checks := 0

func check(ok: bool, message: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		printerr("CONTROLLER_FAIL: ", message)

func _initialize() -> void:
	var world := World.new()
	world.render_2d = false
	world.scenic_layout = true
	world.initialize()
	for fps: int in [20, 30, 60, 144]:
		world.actor_pos = Vector2(8, 8)
		for _tick: int in fps * 10:
			world.advance_actor(Vector2.RIGHT, 1.0 / fps)
		var travelled := world.actor_pos.x - 8
		print("CONTROLLER_SPEED %dFPS %.4fpx / 10s" % [fps, travelled])
		check(absf(travelled - 1500) <= 15, "speed %d" % fps)
		var view := View.new()
		view._smooth_follow(Vector2.ZERO, 0, 0)
		for _tick: int in fps / 2:
			view._smooth_follow(Vector2(1000, 500), 64, 1.0 / fps)
		var expected := Vector2(1000, 500) * (1.0 - exp(-4.0))
		check(view.focus.distance_to(expected) < 1.0, "horizontal smoothing %d" % fps)
		check(absf(view.focus_height - 64 * (1 - exp(-4.0))) < 1.0, "height smoothing %d" % fps)
		view.reset_follow()
		view._smooth_follow(Vector2(300, 200), 80, 1.0 / fps)
		check(view.focus == Vector2(300, 200) and view.focus_height == 80, "teleport reset %d" % fps)
		view.free()
	# Deliberate long frame cannot tunnel through the new inn, including corners.
	world.actor_pos = Vector2(496, 608)
	world.advance_actor(Vector2.UP, 2.0)
	check(world.actor_pos.y >= 563 and world.can_walk(world.actor_pos), "long frame inn collision")
	world.actor_pos = Vector2(680, 624)
	world.advance_actor(Vector2.UP, 2.0)
	check(world.actor_pos.distance_to(Vector2(680, 324)) < 1 and world.can_walk(world.actor_pos), "north passage")
	world.actor_pos = Vector2(8, 8)
	world.advance_actor(Vector2.RIGHT, 0.5)
	check(absf(world.actor_pos.x - 83) < 1, "elapsed time not discarded")
	world.free()
	if "--fault-speed" in OS.get_cmdline_user_args(): check(false, "isolated expected failure")
	print("HD2D_CONTROLLER_%s: %d checks" % ["PASS" if failures == 0 else "FAIL", checks])
	quit(1 if failures else 0)
