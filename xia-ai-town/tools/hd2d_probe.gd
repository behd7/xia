extends SceneTree
## GPU proof of hard geometry + the CURRENT actor-only depth-inverted outline.
## No scenery translucency. Runtime queries run in a real physics callback.
const Projector = preload("res://scripts/hd2d/projection.gd")
const Actor = preload("res://scripts/hd2d/actors.gd")
const Occlusion = preload("res://scripts/hd2d/occlusion.gd")
const Scenery = preload("res://scripts/hd2d/scenery.gd")
const World = preload("res://scripts/town_world.gd")
const FADE_SHADER = preload("res://scripts/hd2d/shaders/scenery.gdshader")
var scene: Node3D
var camera: Camera3D
var actor: Node3D
var second: Node3D
var fixtures: Node3D
var material: ShaderMaterial
var output: String
var results: Array = []
var failed := false
var pitch := Projector.fixture_pitch()
var orthogonal := Projector.fixture_orthogonal()
var fov := Projector.fixture_fov()
var driver: QueryDriver

class QueryDriver extends Node:
	var job: Callable
	var calls := 0
	var outside_physics := 0
	func _physics_process(_delta: float) -> void:
		if job.is_valid():
			var next := job
			job = Callable()
			calls += 1
			outside_physics += int(not Engine.is_in_physics_frame())
			next.call()

func _initialize() -> void:
	_run.call_deferred()

func check(condition: bool, label: String, detail: Dictionary = {}) -> void:
	results.append({"name": label, "passed": condition, "detail": detail})
	if not condition:
		failed = true
		printerr("HD2D_FAIL: ", label, " ", detail)
	else:
		print("HD2D_PROBE_PASS: ", label, " ", detail)

func pause() -> void:
	for _i in range(8):
		await process_frame
	await physics_frame

func shot(label: String) -> Image:
	await pause()
	await RenderingServer.frame_post_draw
	var image := root.get_texture().get_image()
	if image.save_png(output.path_join(label + ".png")) != OK:
		check(false, "save_" + label)
	return image

func physics_job(job: Callable) -> void:
	if not is_instance_valid(driver):
		driver = QueryDriver.new()
		root.add_child(driver)
	driver.job = job
	while driver.job.is_valid():
		await process_frame

func settle(occlusion: RefCounted, actors: Array, seconds: float = 0.8) -> void:
	for _i in range(ceili(seconds / 0.02)):
		await physics_job(func(): occlusion.query(scene, camera, actors, actors.size(), 0.02))
		occlusion.advance(actors, 0.02)

func groups(helper: RefCounted, who: Node3D) -> Dictionary:
	var found := {}
	await physics_job(func(): found.merge(helper.blocking_groups(scene, camera, who)))
	return found

func red(image: Image) -> int:
	var count := 0
	for y in range(image.get_height()):
		for x in range(image.get_width()):
			var c := image.get_pixel(x, y)
			if c.r > c.g + 0.12 and c.r > c.b + 0.12:
				count += 1
	return count

func pixel(image: Image, at: Vector2, height: float) -> Color:
	return image.get_pixelv(Vector2i(camera.unproject_position(Projector.point(at, height))))

func place(who: Node3D, at: Vector2) -> void:
	who.sync(at, 0.0, 0)
	who._set_frame(0)

func outline_evidence(label: String, helper: RefCounted, actors: Array) -> Dictionary:
	# Hide only body sprites, not scenery, shadows or transforms. Any change in
	# the before/after backgrounds means the occluder itself was modified.
	helper.reset()
	for who: Node3D in actors:
		who.set_outline(0.0)
		# Keep the same draw-pass topology in the zero/active-strength pair.
		# Adding a pass can reorder coplanar fence faces on Compatibility.
		who.sprite.material_overlay = who.outline_material
		who.sprite.set_instance_shader_parameter("outline_strength", 0.0)
		who.sprite.hide()
	var background_before := await shot(label + "-background-before")
	for who: Node3D in actors: who.sprite.show()
	var solid := await shot(label + "-solid")
	await settle(helper, actors)
	var outlined := await shot(label + "-outline")
	for who: Node3D in actors: who.sprite.hide()
	var background_after := await shot(label + "-background-after")
	for who: Node3D in actors: who.sprite.show()
	var bounds: Array[Rect2] = []
	for who: Node3D in actors: bounds.append(who.screen_rect(camera).grow(2))
	var changed := 0
	var outside := 0
	for y in range(solid.get_height()):
		for x in range(solid.get_width()):
			var a := solid.get_pixel(x, y)
			var b := outlined.get_pixel(x, y)
			if Vector3(a.r-b.r, a.g-b.g, a.b-b.b).length() <= 0.02: continue
			changed += 1
			var contained := false
			for rect: Rect2 in bounds: contained = contained or rect.has_point(Vector2(x, y))
			outside += int(not contained)
	return {"outline_pixels": changed, "changed_outside_body": outside,
		"scenery_pixel_identical": background_before.get_data() == background_after.get_data()}

func outline_ok(evidence: Dictionary) -> bool:
	return evidence.outline_pixels > 20 and evidence.changed_outside_body == 0 and evidence.scenery_pixel_identical

func wall(group: StringName, at: Vector2, dimensions := Vector2(320,320), height: float = 160.0) -> MeshInstance3D:
	var node := MeshInstance3D.new()
	var quad := QuadMesh.new()
	quad.size = dimensions * Projector.SCALE
	node.mesh = quad
	node.position = Projector.point(at, height)
	node.material_override = material
	fixtures.add_child(node)
	fixtures.register_occluder(node, group)
	return node

func configure(zoom: int, focus: Vector2, elevation: float, azimuth: float = 0.0) -> void:
	Projector.configure(camera, Vector2(root.size), zoom, focus, elevation, azimuth, orthogonal, fov)

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	Engine.max_fps = 60
	Engine.time_scale = 0.0 # Freeze wind/water TIME, not the actual GPU/physics callbacks.
	scene = Node3D.new()
	root.add_child(scene)
	var env_node := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color.BLACK
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color.WHITE
	env.ambient_light_energy = 1.0
	env_node.environment = env
	scene.add_child(env_node)
	camera = Camera3D.new()
	scene.add_child(camera)
	actor = load("res://scenes/hd2d/actor.tscn").instantiate()
	actor.name = "Hero"
	scene.add_child(actor)
	second = load("res://scenes/hd2d/actor.tscn").instantiate()
	second.name = "Target"
	scene.add_child(second)
	var diagnostic: ShaderMaterial = Actor.body_material.duplicate()
	diagnostic.set_shader_parameter("unshaded", true)
	for who: Node3D in [actor, second]:
		who.set_pitch(pitch)
		who.set_outline_kind("hero" if who == actor else "npc")
		who.sprite.material_override = diagnostic
		who.sprite.shaded = false
		who.sprite.modulate = Color(4, 0, 0, 1)
	second.hide()
	fixtures = Scenery.new()
	scene.add_child(fixtures)
	material = ShaderMaterial.new()
	material.shader = FADE_SHADER
	var white := Image.create(4, 4, false, Image.FORMAT_RGBA8)
	white.fill(Color.WHITE)
	material.set_shader_parameter("albedo_texture", ImageTexture.create_from_image(white))
	await pause()
	check(root.size == Vector2i(1440,900), "native_window", {"size": root.size})
	var front := wall(&"test/front", Vector2(512,384))
	for zoom: int in [1,2,3]:
		await _wall_case("z%d" % zoom, front, zoom, pitch, 0.0)
	var upright := material.duplicate() as ShaderMaterial
	upright.set_shader_parameter("upright", true)
	front.material_override = upright
	front.set_meta("alpha_image", white)
	front.set_meta("alpha_quad", front.mesh.size)
	for angle: Vector2 in [Vector2(38,30), Vector2(30,-135), Vector2(65,45), Vector2(45,-90), Vector2(45,90), Vector2(45,180)]:
		await _wall_case("orbit-p%d-y%d" % [int(angle.x), int(angle.y)], front, 2, angle.x, angle.y)
	front.material_override = material
	front.rotation.y = 0.0
	actor.set_pitch(pitch)
	actor.set_yaw(0.0)
	configure(2, Vector2(512,354), pitch)
	await _selection_checks(front)
	await _perspective_checks()
	await _house_checks()
	await _tree_checks()
	check(driver.calls > 100 and driver.outside_physics == 0, "all_query_jobs_use_physics_callback", {"calls": driver.calls, "outside_physics": driver.outside_physics})
	var file := FileAccess.open(output.path_join("probe.json"), FileAccess.WRITE)
	file.store_string(JSON.stringify({"device": RenderingServer.get_video_adapter_name(), "backend": RenderingServer.get_current_rendering_method(),
		"window": [root.size.x,root.size.y], "pitch": pitch, "fov": fov, "projection": "orthogonal" if orthogonal else "perspective",
		"results": results, "passed": not failed, "occlusion_mode": "actor_depth_inverted_outline",
		"limits": "GPU geometry/outline/static-scenery probes; not aesthetics, OS interaction, performance or P5"}))
	file.close()
	quit(1 if failed else 0)

func _wall_case(label: String, front: MeshInstance3D, zoom: int, elevation: float, azimuth: float) -> void:
	var back := Projector.ground_back(azimuth)
	var offset := Vector2(back.x, back.z)
	actor.set_pitch(elevation)
	actor.set_yaw(azimuth)
	actor.set_outline(0.0)
	front.rotation.y = deg_to_rad(azimuth)
	configure(zoom, Vector2(512,354), elevation, azimuth)
	place(actor, Vector2(512,384) + offset)
	front.hide()
	var reference := red(await shot(label + "-reference"))
	front.show()
	var outside_image := await shot(label + "-outside-1px")
	var outside := red(outside_image)
	actor.set_outline(1.0)
	var forced := await shot(label + "-uncovered-forced-outline")
	check(forced.get_data() == outside_image.get_data(), label + "-inverted_depth_never_outlines_uncovered_body")
	actor.set_outline(0.0)
	place(actor, Vector2(512,384) - offset * 4.0)
	var inside := red(await shot(label + "-inside-4px"))
	check(reference > 20 and outside >= reference * 0.97 and inside <= reference * 0.02, label + "-hard_geometry", {"reference": reference, "outside": outside, "inside": inside})
	var helper := Occlusion.new()
	var evidence := await outline_evidence(label, helper, [actor])
	check(outline_ok(evidence), label + "-outline_without_scenery_transparency", evidence)
	place(actor, Vector2(512,384) + offset)
	await settle(helper, [actor])
	var restored := await shot(label + "-restored")
	check(is_zero_approx(actor.outline) and restored.get_data() == outside_image.get_data(), label + "-restores_uncovered_body", {"strength": actor.outline, "same_pixels": restored.get_data() == outside_image.get_data(), "groups": await groups(helper, actor)})
	helper.reset()

func _selection_checks(front: MeshInstance3D) -> void:
	place(actor, Vector2(512,380))
	var helper := Occlusion.new()
	var neighbor := wall(&"test/unrelated", Vector2(730,384), Vector2(80,320))
	var evidence := await outline_evidence("components", helper, [actor])
	check(outline_ok(evidence), "shared_material_changes_only_actor_pixels", evidence)
	front.rotation.y = PI
	await pause()
	check((await groups(helper, actor)).has(&"test/front"), "double_sided_render_and_ray_agree")
	front.rotation.y = 0.0
	var middle := wall(&"test/middle", Vector2(512,388))
	var behind := wall(&"test/behind", Vector2(512,376))
	await pause()
	var layers := await groups(helper, actor)
	check(layers.has(&"test/front") and layers.has(&"test/middle") and not layers.has(&"test/behind"), "all_foreground_layers_not_background", {"groups": layers})
	evidence = await outline_evidence("multi-layer", helper, [actor])
	check(outline_ok(evidence), "outline_over_multiple_opaque_layers", evidence)
	for mesh: MeshInstance3D in [front, middle, behind, neighbor]: mesh.hide()
	helper.reset()
	var stable: Array = actor.occlusion_points()
	var consistent := true
	for frame: int in range(actor.metadata.frames.size()):
		actor._set_frame(frame)
		consistent = consistent and actor.occlusion_points() == stable
	check(consistent and stable.size() == 12, "stable_full_body_samples_all_animation_frames")
	actor._set_frame(0)
	var shoulder: Vector3 = stable.back()
	var origin := Projector.ray_origin(camera, shoulder)
	var intersection := shoulder.lerp(origin, (384*Projector.SCALE-shoulder.z)/(origin.z-shoulder.z))
	var side := wall(&"test/shoulder", Vector2(intersection.x/Projector.SCALE,384), Vector2(0.8,320))
	await settle(helper, [actor])
	check(actor.outline == 1.0 and (await groups(helper, actor)).has(&"test/shoulder"), "side_only_occluder_detected")
	side.hide()
	var cutout := wall(&"test/alpha-hole", Vector2(512,384))
	var empty := Image.create(4,4,false,Image.FORMAT_RGBA8)
	empty.fill(Color.TRANSPARENT)
	var transparent := material.duplicate() as ShaderMaterial
	transparent.set_shader_parameter("albedo_texture", ImageTexture.create_from_image(empty))
	cutout.material_override = transparent
	cutout.set_meta("alpha_image", empty)
	cutout.set_meta("alpha_quad", cutout.mesh.size)
	helper.reset()
	await settle(helper, [actor])
	check(helper.strengths().is_empty(), "alpha_holes_do_not_trigger_outline")
	cutout.hide()
	front.show()
	second.show()
	place(actor, Vector2(452,380))
	place(second, Vector2(572,380))
	evidence = await outline_evidence("dual", helper, [actor,second])
	check(outline_ok(evidence) and actor.outline == 1.0 and second.outline == 1.0, "two_independent_actor_outlines_opaque_wall_between", evidence)
	place(actor, Vector2(452,385))
	await settle(helper, [actor,second], 0.1)
	check(actor.outline == 1.0 and second.outline == 1.0, "restore_hysteresis_short_clear")
	await settle(helper, [actor,second])
	check(is_zero_approx(actor.outline) and second.outline == 1.0, "uncovered_actor_restores_independently")
	await settle(helper, [actor], 0.02)
	check(is_zero_approx(second.outline), "removed_target_restores_without_touching_other_actors")
	place(actor, Vector2(505,380))
	helper.reset()
	await settle(helper, [actor], 0.06)
	check(actor.outline > 0.0 and actor.outline < 1.0, "outline_fade_in_is_continuous", {"strength": actor.outline})
	await settle(helper, [actor])
	helper.enabled = false
	await settle(helper, [actor], 0.08)
	check(actor.outline > 0.0 and actor.outline < 1.0, "outline_fade_out_is_continuous", {"strength": actor.outline})
	await settle(helper, [actor])
	second.hide()
	check(helper.strengths().is_empty() and red(await shot("disabled-solid")) == 0, "disable_keeps_real_occlusion")
	front.hide()

func _perspective_checks() -> void:
	configure(2, Vector2(512,354), pitch)
	place(actor, Vector2(620,380))
	var clear := await shot("perspective-off-axis-clear")
	var clear_pixels := red(clear)
	var bounds: Rect2 = actor.screen_rect(camera).grow(1.5)
	var contained := true
	for y in range(clear.get_height()):
		for x in range(clear.get_width()):
			var c := clear.get_pixel(x,y)
			if c.r > c.g+0.12 and c.r > c.b+0.12 and not bounds.has_point(Vector2(x,y)): contained = false
	check(clear_pixels > 20 and contained, "perspective_body_inside_projected_four_corner_bounds")
	if not orthogonal:
		place(actor, Vector2(620,284))
		var far_pixels := red(await shot("perspective-off-axis-far"))
		check(far_pixels > 20 and clear_pixels > far_pixels*1.1, "perspective_far_actor_naturally_smaller", {"near": clear_pixels, "far": far_pixels})
		place(actor, Vector2(620,380))
		var origin := Projector.ray_origin(camera, actor.global_position)
		var hit := actor.global_position.lerp(origin, (650*Projector.SCALE-actor.global_position.z)/(origin.z-actor.global_position.z))
		var cover := wall(&"test/perspective", Vector2(hit.x/Projector.SCALE,650), Vector2(60,320))
		var solid := red(await shot("perspective-off-axis-solid"))
		var helper := Occlusion.new()
		var evidence := await outline_evidence("perspective-off-axis", helper, [actor])
		check(solid == 0 and outline_ok(evidence) and (await groups(helper, actor)).has(&"test/perspective"), "perspective_converging_rays_and_outline", evidence)
		var clipped_clear := true
		for depth: float in [-1.0, camera.near*0.5, camera.far+1.0]:
			var clipped := camera.project_position(camera.unproject_position(actor.global_position), depth)
			var found := {}
			await physics_job(func(): found.merge(helper._ray_groups(scene, camera, clipped)))
			clipped_clear = clipped_clear and found.is_empty()
		check(clipped_clear, "clipped_or_behind_camera_targets_do_not_trigger")
		helper.reset()
		cover.hide()

func _house_checks() -> void:
	fixtures.hide()
	var world := World.new()
	world._ensure_grid()
	var house := Scenery.new()
	house.pitch = pitch
	scene.add_child(house)
	house.build(world)
	var helper := Occlusion.new()
	var poses: Array = []
	for zoom: int in [1,2,3]: poses.append(["real-house-z%d" % zoom, zoom, pitch, 0.0, Vector2(512,240)])
	for azimuth: float in [30.0,-90.0,90.0,180.0]:
		var back := Projector.ground_back(azimuth)
		poses.append(["real-house-y%d" % int(azimuth), 2, 38.0, azimuth, Vector2(512,320)-Vector2(back.x,back.z)*(112.0 if absf(azimuth)==90.0 else 80.0)])
	for pose: Array in poses:
		helper.reset()
		actor.set_pitch(pose[2])
		actor.set_yaw(pose[3])
		house.set_pitch(pose[2])
		house.set_yaw(pose[3])
		place(actor, pose[4])
		configure(pose[1], pose[4], pose[2], pose[3])
		var solid := await shot(pose[0] + "-hard")
		actor.sprite.hide()
		var background := await shot(pose[0] + "-hard-background")
		actor.sprite.show()
		check(solid.get_data() == background.get_data(), pose[0] + "-hard_geometry_blocks_body")
		var evidence := await outline_evidence(pose[0], helper, [actor])
		check(outline_ok(evidence) and (await groups(helper, actor)).has(&"inn/structure"), pose[0] + "-outline_and_unchanged_roof", evidence)
	helper.reset()
	actor.set_pitch(pitch)
	actor.set_yaw(0.0)
	place(actor, Vector2(505,240))
	place(second, Vector2(540,244))
	second.show()
	configure(2, Vector2(512,240), pitch)
	var dual := await outline_evidence("real-house-dual", helper, [actor,second])
	check(outline_ok(dual) and actor.outline == 1.0 and second.outline == 1.0, "real_house_dual_outline", dual)
	second.hide()
	place(actor, Vector2(512,422))
	await settle(helper, [actor])
	check(is_zero_approx(actor.outline), "real_house_front_restores_outline")
	helper.reset()
	world.free()
	house.hide()

func _tree_checks() -> void:
	var pine := Scenery.new()
	scene.add_child(pine)
	preload("res://scripts/hd2d/tree.gd").build(pine, Vector2(512,384), 0)
	var helper := Occlusion.new()
	for orientation: Vector2 in [Vector2(38,30),Vector2(38,-60),Vector2(38,120),Vector2(38,-150),Vector2(30,30),Vector2(65,30)]:
		helper.reset()
		var back := Projector.ground_back(orientation.y)
		var behind := Vector2(512,384)-Vector2(back.x,back.z)*(55.0/tan(deg_to_rad(orientation.x)))
		actor.set_pitch(orientation.x)
		actor.set_yaw(orientation.y)
		place(actor, behind)
		configure(2, behind, orientation.x, orientation.y)
		pine.hide()
		var label := "pine-p%d-y%d" % [int(orientation.x), int(orientation.y)]
		var reference := red(await shot(label + "-reference"))
		pine.show()
		var solid := red(await shot(label + "-solid"))
		check(reference > 20 and solid < reference*0.80, label + "-real_volume_blocks_body", {"reference": reference, "visible": solid})
		var evidence := await outline_evidence(label, helper, [actor])
		check(outline_ok(evidence) and (await groups(helper, actor)).has(&"prop/tree/0"), label + "-outline_without_changing_tree", evidence)
	var back := Projector.ground_back(30)
	var front := Vector2(512,384)+Vector2(back.x,back.z)*70
	place(actor, front)
	actor.set_pitch(38)
	actor.set_yaw(30)
	configure(2, front, 38, 30)
	await settle(helper, [actor])
	check(helper.strengths().is_empty(), "pine_in_background_restores_outline")
