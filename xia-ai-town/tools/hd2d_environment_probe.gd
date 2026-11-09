extends "res://tools/hd2d_probe.gd"
## P3 full-map GPU inventory, solid geometry, local reveal and native gallery.
const Layout = preload("res://scripts/town_layout.gd")
const TownView = preload("res://scripts/hd2d/town_view.gd")
var world: Node2D
var view: Node3D
var draws := 0
var clock_seconds := 12 * 3600.0

func configure(zoom: int, focus: Vector2, elevation: float, azimuth: float = 0.0) -> void:
	view.set_pitch(elevation)
	view.set_yaw(azimuth)
	var reference := Vector2(actor.global_position.x, actor.global_position.z) / Projector.SCALE
	Projector.configure(camera, Vector2(root.size), zoom, focus, elevation, azimuth, false, fov, reference)
	# Match TownView.update: changing zoom also moves the far-DOF threshold.
	view.lighting.update(clock_seconds, fixtures, -camera.to_local(actor.global_position).z, actor.global_position)

func _drawn() -> void:
	draws += 1

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = 60
	root.size = Vector2i(1440, 900)
	world = World.new()
	world.render_2d = false
	root.add_child(world)
	view = load("res://scenes/hd2d/town_view.tscn").instantiate()
	root.add_child(view)
	view.setup(world, true)
	view.quality = OS.get_environment("XIA_HD2D_QUALITY")
	view.update(world, Vector2(root.size), 12 * 3600.0, 0.0)
	scene = view
	camera = view.camera
	actor = view.hero
	fixtures = view.scenery
	RenderingServer.frame_post_draw.connect(_drawn)
	for other: Node3D in view.population.values():
		other.hide()
	await pause()
	_inventory()
	var ordinary: Material = actor.sprite.material_override
	var diagnostic: ShaderMaterial = Actor.body_material.duplicate()
	diagnostic.set_shader_parameter("unshaded", true)
	actor.sprite.material_override = diagnostic
	actor.sprite.modulate = Color(4, 0, 0, 1)
	await _houses()
	await _gate_clearance()
	actor.sprite.material_override = ordinary
	actor.sprite.modulate = Color.WHITE
	await _gallery()
	check(draws > 100, "continuous_real_gpu_draw", {"frame_post_draw": draws})
	var report := {"device": RenderingServer.get_video_adapter_name(),
		"backend": RenderingServer.get_current_rendering_method(), "quality": view.quality,
		"window": [root.size.x, root.size.y], "viewport": [root.size.x, root.size.y],
		"rendered_frames": draws, "results": results, "passed": not failed,
		"placed": fixtures.world_assets.placed,
		"limits": "P3 environment inventory/geometry/readability and native gallery; separate real-client OS interaction, character production and P5 remain"}
	var file := FileAccess.open(output.path_join("probe.json"), FileAccess.WRITE)
	file.store_string(JSON.stringify(report))
	file.close()
	RenderingServer.frame_post_draw.disconnect(_drawn)
	view.queue_free()
	world.queue_free()
	await process_frame
	quit(1 if failed else 0)

func _inventory() -> void:
	var assets: RefCounted = fixtures.world_assets
	for actor_key: String in ["gu_changfeng", "tie_wushuang", "su_wanqing", "chen_bansong", "wang_dazhu", "liu_xiaodie", "zhang_mantou", "wolf", "bandit", "boss"]:
		var actor: Node3D = load("res://scenes/hd2d/actor.tscn").instantiate()
		actor.configure_p3(actor_key)
		root.add_child(actor)
		check(actor.p3_key == actor_key and actor.metadata.columns == 10 and actor.metadata.directions.size() == 8 and actor.metadata.frames.size() == 80,
			"p3_actor_%s_10x8_manifest" % actor_key)
		actor.queue_free()
	var materials_valid := true
	var rays_valid := true
	var triangles_valid := true
	for mesh: MeshInstance3D in fixtures.occluders:
		var shape: CollisionShape3D = mesh.get_child(0).get_child(0)
		rays_valid = rays_valid and shape.shape.backface_collision and mesh.get_child(0).collision_layer == 2
		for i: int in range(mesh.mesh.get_surface_count()):
			var mat := mesh.get_active_material(i) as ShaderMaterial
			materials_valid = materials_valid and mat != null
			if mat != null:
				var texture: Texture2D = mat.get_shader_parameter("albedo_texture")
				materials_valid = materials_valid and texture != null and texture.resource_path.begins_with("res://assets/hd2d/")
		var faces := mesh.mesh.get_faces()
		for i: int in range(0, faces.size(), 3):
			if (faces[i+1] - faces[i]).cross(faces[i+2] - faces[i]).length() < 0.00000001:
				triangles_valid = false
	check(materials_valid, "all_environment_surfaces_use_new_pixel_materials")
	check(rays_valid, "all_environment_ray_shapes_double_sided_visual_layer_only", {"meshes": fixtures.occluders.size()})
	check(triangles_valid, "all_imported_environment_triangles_non_degenerate")
	for building: Dictionary in Layout.BUILDINGS:
		if building.id == "inn":
			continue
		var key := "building/" + str(building.id)
		check(int(assets.placed.get(key, 0)) == 1, key + "_produced_once")
	for prop: Dictionary in Layout.furniture():
		var key := "prop/" + str(assets.data.prop_keys[int(prop.index)])
		if int(prop.index) == Layout.P_LANTERN:
			key = "prop/lantern_post"
		check(assets.placed.has(key), "furniture_%d_at_%d_%d" % [int(prop.index), int(prop.at.x), int(prop.at.y)])
	var cells := 0
	for tag: int in world._grid:
		if tag == Layout.CLIFF:
			cells += 1
	check(int(assets.placed.get("terrain/cliff_cells", 0)) == cells,
		"all_frozen_cliff_cells_have_3d_surface", {"cells": cells})
	check(assets.placed.has("prop/well") and assets.placed.has("town/gate"), "well_and_gate_present")

func _red_body(image: Image, background: Image, reference: Image) -> int:
	var rect := Rect2i(actor.occlusion_rect(camera).grow(3)).intersection(Rect2i(Vector2i.ZERO, image.get_size()))
	var count := 0
	for y: int in range(rect.position.y, rect.end.y):
		for x: int in range(rect.position.x, rect.end.x):
			var ref := reference.get_pixel(x, y)
			var color := image.get_pixel(x, y)
			var bg := background.get_pixel(x, y)
			if ref.r > ref.g + 0.18 and ref.r > ref.b + 0.18 and color.r - bg.r > 0.01:
				count += 1
	return count

func readability(title: String, scenery: Node3D, actors: Array, _native_background: bool = false) -> Dictionary:
	# Full-map doors, bronze reliefs and flowers can also be red. Build the
	# reference with ALL scenery hidden so only the diagnostic actor defines
	# the pixel mask; actual contrast uses the unchanged native background.
	var visible := await shot(title + "-visible")
	for who: Node3D in actors:
		who.sprite.hide()
	var background := await shot(title + "-background")
	scenery.hide()
	for who: Node3D in actors:
		who.sprite.show()
	var reference := await shot(title + "-body-reference")
	scenery.show()
	var total := 0
	var retained := 0
	var contrasts := PackedFloat32Array()
	var rect := Rect2i(actor.occlusion_rect(camera).grow(3)).intersection(Rect2i(Vector2i.ZERO, visible.get_size()))
	for y: int in range(rect.position.y, rect.end.y):
		for x: int in range(rect.position.x, rect.end.x):
			var ref := reference.get_pixel(x, y)
			if ref.r <= ref.g + 0.18 or ref.r <= ref.b + 0.18:
				continue
			var base := background.get_pixel(x, y).srgb_to_linear()
			var ideal := ref.srgb_to_linear()
			var now := visible.get_pixel(x, y).srgb_to_linear()
			var reference_signal := Vector3(ideal.r - base.r, ideal.g - base.g, ideal.b - base.b)
			if reference_signal.length_squared() <= 0.000025:
				continue
			var actual := Vector3(now.r - base.r, now.g - base.g, now.b - base.b)
			var ratio := actual.dot(reference_signal) / reference_signal.length_squared()
			contrasts.append(ratio)
			total += 1
			if ratio >= 0.65:
				retained += 1
	contrasts.sort()
	return {"reference_pixels": total, "readable_pixels": retained, "coverage": float(retained) / maxi(total, 1),
		"contrast_p10": contrasts[int(contrasts.size() * 0.1)] if not contrasts.is_empty() else 0.0,
		"contrast_median": contrasts[int(contrasts.size() * 0.5)] if not contrasts.is_empty() else 0.0}

func _houses() -> void:
	var helper := Occlusion.new()
	for building: Dictionary in Layout.BUILDINGS:
		if building.id == "inn":
			continue
		var rect: Rect2 = building.rect
		var behind := Vector2(rect.get_center().x, rect.position.y - 16)
		actor.set_pitch(38)
		actor.set_yaw(30)
		place(actor, behind)
		configure(2, behind, 38, 30)
		helper.reset(fixtures.fade_groups)
		fixtures.hide()
		var reference := await shot(str(building.id) + "-body-reference")
		fixtures.show()
		var solid := await shot(str(building.id) + "-solid")
		actor.sprite.hide()
		var background := await shot(str(building.id) + "-solid-background")
		actor.sprite.show()
		var leaked := _red_body(solid, background, reference)
		check(leaked == 0, str(building.id) + "_solid_geometry_blocks_body", {"visible_body_pixels": leaked})
		settle(helper, [actor], 0.8, fixtures.fade_groups)
		var readable := await readability(str(building.id) + "-local", fixtures, [actor], true)
		check(readable.reference_pixels > 20 and readable.coverage >= 0.90,
			str(building.id) + "_local_reveal_full_body", readable)
		helper.reset(fixtures.fade_groups)
	# Extreme rear view and 3x near a new building, in addition to default 2x.
	for pose: Vector3 in [Vector3(65, 175, 1), Vector3(30, -75, 3)]:
		var rect: Rect2 = Layout.BUILDINGS[1].rect
		var back := Projector.ground_back(pose.y)
		var behind := rect.get_center() - Vector2(back.x, back.z) * 100
		actor.set_pitch(pose.x)
		actor.set_yaw(pose.y)
		place(actor, behind)
		configure(int(pose.z), behind, pose.x, pose.y)
		settle(helper, [actor], 0.8, fixtures.fade_groups)
		var readable := await readability("new-house-p%d-y%d-z%d" % [int(pose.x), int(pose.y), int(pose.z)], fixtures, [actor], true)
		check(readable.reference_pixels > 20 and readable.coverage >= 0.90,
			"new_house_extreme_pose_%d_%d_%d" % [int(pose.x), int(pose.y), int(pose.z)], readable)
		helper.reset(fixtures.fade_groups)

func _gate_clearance() -> void:
	await physics_frame
	var walkable := true
	for y: int in range(460, 542):
		walkable = walkable and world.can_walk(Vector2(800, y))
	var clear_height := Projector.upright_height(40, Projector.MAX_PITCH) / Projector.SCALE
	var blocked: Array = []
	for height: float in [1.0, 35.0, 65.0, clear_height]:
		var query := PhysicsRayQueryParameters3D.create(Projector.point(Vector2(800, 455), height), Projector.point(Vector2(800, 545), height), 2)
		query.hit_back_faces = true
		var hit := view.get_world_3d().direct_space_state.intersect_ray(query)
		if not hit.is_empty():
			blocked.append({"height": height, "hit": str(hit)})
	check(walkable and blocked.is_empty(), "original_gate_road_and_human_clearance_open", {"clear_height": clear_height, "blocked": blocked})
	# The water micro-motion is material-only; cell boundaries never move.
	var water: ShaderMaterial = fixtures.world_assets.material("water")
	check(bool(water.get_shader_parameter("water_motion")), "pond_and_well_water_micro_motion_enabled")

func _gallery() -> void:
	for other: Node3D in view.population.values():
		other.show()
	for building: Dictionary in Layout.BUILDINGS:
		var rect: Rect2 = building.rect
		var at := Vector2(rect.get_center().x, rect.end.y + 32)
		actor.set_pitch(38)
		actor.set_yaw(30)
		place(actor, at)
		configure(2, at - Vector2(20, 35), 38, 30)
		await shot("gallery-" + str(building.id))
	var points := {"well": Vector2(720, 447), "pond": Vector2(1208, 475),
		"yard": Vector2(976, 240), "gate": Vector2(800, 548),
		"forest": Vector2(360, 670), "cliffs": Vector2(1250, 740)}
	for title: String in points:
		var at: Vector2 = points[title]
		place(actor, at)
		configure(2, at - Vector2(20, 30), 38, 30)
		await shot("gallery-" + title)
	place(actor, Vector2(512, 422))
	configure(1, Vector2(760, 320), 55, 0)
	await shot("gallery-town-overview")
	clock_seconds = 22 * 3600.0
	for title: String in ["gate", "pond", "forest"]:
		var at: Vector2 = points[title]
		place(actor, at)
		configure(2, at - Vector2(20, 30), 38, 30)
		view.lighting.update(22 * 3600.0, fixtures, -camera.to_local(actor.global_position).z, actor.global_position)
		await shot("gallery-night-" + title)
