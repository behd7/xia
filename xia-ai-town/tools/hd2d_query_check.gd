extends SceneTree
## Real ray query in the physics phase, render-rate strength between queries.
const Occlusion = preload("res://scripts/hd2d/occlusion.gd")
var checks := 0
var failures := 0

class Actor extends Node3D:
	var outline := 0.0
	func occlusion_points() -> Array[Vector3]:
		return [global_position + Vector3.UP * 0.5]
	func set_outline(value: float) -> void:
		outline = value

class Driver extends Node3D:
	var occlusion = Occlusion.new()
	var actors: Array = []
	var camera: Camera3D
	var diagnostic_once := false
	func _physics_process(delta: float) -> void:
		occlusion.query(self, camera, actors, 1, delta)
		if diagnostic_once:
			diagnostic_once = false
			occlusion.blocking_groups(self, camera, actors[0])
	func _process(delta: float) -> void:
		occlusion.advance(actors, delta)

func check(ok: bool, label: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		push_error(label)

func _initialize() -> void:
	call_deferred("run")

func run() -> void:
	var driver := Driver.new()
	var actor := Actor.new()
	actor.name = "Subject"
	driver.add_child(actor)
	driver.actors = [actor]
	var camera := Camera3D.new()
	camera.position = Vector3(0, 1, 5)
	driver.add_child(camera)
	driver.camera = camera
	var wall := MeshInstance3D.new()
	wall.mesh = BoxMesh.new()
	wall.position = Vector3(0, 0.5, 2.5)
	wall.set_meta("fade_group", "wall")
	var body := StaticBody3D.new()
	body.collision_layer = 2
	body.collision_mask = 0
	var shape := CollisionShape3D.new()
	shape.shape = BoxShape3D.new()
	body.add_child(shape)
	wall.add_child(body)
	driver.add_child(wall)
	driver.occlusion.profile_enabled = true
	root.add_child(driver)
	camera.look_at(Vector3(0, 0.5, 0))
	await create_timer(0.35).timeout
	check(driver.occlusion.profile.batches >= 5, "runtime physics queries executed")
	check(driver.occlusion.profile.rays > 0, "real wall rays executed")
	check(driver.occlusion.profile.outside_physics == 0, "no runtime query outside physics phase")
	check(actor.outline == 1.0, "covered actor reaches full outline")
	driver.diagnostic_once = true
	await create_timer(0.07).timeout
	check(driver.occlusion.profile.diagnostic_rays > 0, "diagnostic rays have their own counter")
	wall.hide()
	await create_timer(0.18).timeout
	check(actor.outline == 1.0, "short clear interval preserves restore hysteresis")
	await create_timer(0.5).timeout
	check(actor.outline == 0.0, "uncovered actor restores within hold plus fade-out")
	wall.show()
	await create_timer(0.4).timeout
	check(actor.outline == 1.0, "new cover fades in without stale clear state")
	var batches: int = driver.occlusion.profile.batches
	var rays: int = driver.occlusion.profile.rays
	for _i in range(30):
		driver.occlusion.advance([actor], 1.0/120.0)
	check(driver.occlusion.profile.batches == batches and driver.occlusion.profile.rays == rays, "render updates consume cache without rays")
	driver.occlusion.enabled = false
	driver.occlusion.advance([actor], 0.31)
	check(actor.outline == 0.0, "disabled restores without waiting for physics")
	driver.occlusion.enabled = true
	await create_timer(0.35).timeout
	check(actor.outline == 1.0, "re-enable rechecks and fades in")
	driver.actors.clear()
	driver.occlusion.advance([], 0.01)
	check(actor.outline == 0.0 and driver.occlusion.strengths().is_empty(), "removed actor releases cached outline")
	# Pending actor can be freed between the render and physics phases.
	driver.set_physics_process(false)
	driver.actors = [actor]
	actor.free()
	driver.occlusion.query(driver, camera, driver.actors, 1, 1.0)
	driver.occlusion.advance(driver.actors, 0.1)
	check(driver.occlusion.strengths().is_empty(), "freed pending actor is safe")
	driver.free()
	print("HD2D_QUERY_CHECKS: %d failures=%d threaded=%s" % [checks, failures, ProjectSettings.get_setting("physics/3d/run_on_separate_thread", false)])
	quit(1 if failures else 0)
