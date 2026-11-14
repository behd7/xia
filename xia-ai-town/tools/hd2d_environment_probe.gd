extends "res://tools/hd2d_probe.gd"
## Full-map GPU inventory, hard geometry, actor-only outlines and native gallery.
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
const TownView = preload("res://scripts/hd2d/town_view.gd")
const CameraLimits = preload("res://scripts/hd2d/camera_limits.gd")
var world: Node2D
var view: Node3D
var draws := 0
var clock_seconds := 12 * 3600.0

func configure(zoom: int, focus: Vector2, elevation: float, azimuth: float = 0.0) -> void:
	view.set_pitch(elevation)
	view.set_yaw(azimuth)
	var reference := Vector2(actor.global_position.x, actor.global_position.z) / Projector.SCALE
	CameraLimits.configure(camera, Vector2(root.size), zoom, focus, elevation, azimuth, false, fov, reference, Layout.height_at(reference), Layout.height_at(reference), actor.occlusion_bounds)
	# Match TownView.update: changing zoom also moves the far-DOF threshold.
	view.lighting.update(clock_seconds, fixtures, -camera.to_local(actor.global_position).z, actor.global_position)

func _drawn() -> void:
	draws += 1

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = 60
	Engine.time_scale = 0.0
	root.size = Vector2i(1440, 900)
	world = World.new()
	world.render_2d = false
	world.scenic_layout = true
	root.add_child(world)
	view = load("res://scenes/hd2d/town_view.tscn").instantiate()
	root.add_child(view)
	view.setup(world, true)
	view.set_physics_process(false)
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
	await _foreground_clipping()
	actor.sprite.material_override = ordinary
	actor.sprite.modulate = Color.WHITE
	await _gallery()
	await _boundary_views()
	check(draws > 100, "continuous_real_gpu_draw", {"frame_post_draw": draws})
	var report := {"device": RenderingServer.get_video_adapter_name(),
		"backend": RenderingServer.get_current_rendering_method(), "quality": view.quality,
		"window": [root.size.x, root.size.y], "viewport": [root.size.x, root.size.y],
		"rendered_frames": draws, "results": results, "passed": not failed,
		"placed": fixtures.world_assets.placed,
		"limits": "Environment inventory/hard geometry/actor-only outlines and GPU gallery; not aesthetic approval, real-client OS input, performance or P5"}
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
	var grass_count := 0
	var grass_valid := true
	for patch: MultiMeshInstance3D in fixtures.find_children("*", "MultiMeshInstance3D", true, false):
		grass_count += patch.multimesh.instance_count
		grass_valid = grass_valid and patch.multimesh.buffer.size() == patch.multimesh.instance_count * 12 and patch.multimesh.get_aabb().has_volume()
	check(grass_valid and grass_count > 2000, "grass_authored_buffers_survive_save_and_load", {"instances": grass_count})
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
		var key := "building/" + ("inn_town" if building.id == "inn" else str(building.id))
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

	for at: Vector2 in [Vector2(240,224), Vector2(824,576), Vector2(1310,720)]:
		actor.sync(at, 0.0)
		check(is_equal_approx(actor.position.y, Layout.height_at(at) * Projector.SCALE), "terrain_foot_%d_%d" % [int(at.x), int(at.y)])

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

func _houses() -> void:
	var helper := Occlusion.new()
	for building: Dictionary in Layout.BUILDINGS:
		var rect: Rect2 = building.rect
		var behind := Vector2(rect.get_center().x, rect.position.y - 16)
		actor.set_pitch(38)
		actor.set_yaw(30)
		place(actor, behind)
		configure(2, behind, 38, 30)
		helper.reset()
		fixtures.hide()
		var reference := await shot(str(building.id) + "-body-reference")
		fixtures.show()
		var solid := await shot(str(building.id) + "-hard-solid")
		actor.sprite.hide()
		var background := await shot(str(building.id) + "-hard-background")
		actor.sprite.show()
		var leaked := _red_body(solid, background, reference)
		check(leaked == 0, str(building.id) + "_solid_geometry_blocks_body", {"visible_body_pixels": leaked})
		var evidence := await outline_evidence(str(building.id), helper, [actor])
		check(outline_ok(evidence), str(building.id) + "_outline_without_scenery_transparency", evidence)
		helper.reset()
	for pose: Vector3 in [Vector3(65,175,1), Vector3(30,-75,3)]:
		var rect: Rect2 = Layout.BUILDINGS[1].rect
		var back := Projector.ground_back(pose.y)
		var behind := rect.get_center() - Vector2(back.x,back.z)*100
		actor.set_pitch(pose.x)
		actor.set_yaw(pose.y)
		place(actor, behind)
		configure(int(pose.z), behind, pose.x, pose.y)
		var label := "new-house-p%d-y%d-z%d" % [int(pose.x),int(pose.y),int(pose.z)]
		var evidence := await outline_evidence(label, helper, [actor])
		check(outline_ok(evidence), label + "_outline", evidence)
		helper.reset()

func _gate_clearance() -> void:
	await physics_frame
	var walkable := true
	for y: int in range(884, 955):
		walkable = walkable and world.can_walk(Vector2(Layout.GATE_AT.x, y))
	var clear_height := Projector.upright_height(40, Projector.MAX_PITCH) / Projector.SCALE
	var blocked: Array = []
	for height: float in [1.0, 35.0, 65.0, clear_height]:
		var query := PhysicsRayQueryParameters3D.create(Projector.point(Layout.GATE_AT - Vector2(0, 42), height + Layout.height_at(Layout.GATE_AT)), Projector.point(Layout.GATE_AT + Vector2(0, 42), height + Layout.height_at(Layout.GATE_AT)), 2)
		query.hit_back_faces = true
		var hit := view.get_world_3d().direct_space_state.intersect_ray(query)
		if not hit.is_empty():
			blocked.append({"height": height, "hit": str(hit)})
	check(walkable and blocked.is_empty(), "scenic_gate_road_and_human_clearance_open", {"clear_height": clear_height, "blocked": blocked})
	var wall_meshes := [fixtures.find_child("镇界城墙", true, false), fixtures.find_child("镇界瓦压顶", true, false), fixtures.find_child("镇界垛柱", true, false)]
	var wall_nodes_valid := true
	for node: Node in wall_meshes:
		wall_nodes_valid = wall_nodes_valid and node is MeshInstance3D and fixtures.occluders.has(node)
	check(wall_nodes_valid and not view.has_node("Camera/ViewBoundary"), "perimeter_wall_replaces_screen_edge_fog", {"wall_meshes": wall_meshes.size(), "occluders": fixtures.occluders.size()})
	var west_blocked: bool = not world.can_walk(Vector2(Layout.WALL_INSET, 480))
	var north_blocked: bool = not world.can_walk(Vector2(800, Layout.WALL_INSET))
	var east_blocked: bool = not world.can_walk(Vector2(Layout.WORLD_SIZE.x - Layout.WALL_INSET, 480))
	var south_blocked: bool = not world.can_walk(Vector2(640, Layout.WORLD_SIZE.y - Layout.WALL_INSET))
	check(west_blocked and north_blocked and east_blocked and south_blocked and world.can_walk(Layout.GATE_AT), "perimeter_collision_closed_except_gate", {"west": west_blocked, "north": north_blocked, "east": east_blocked, "south": south_blocked, "gate": world.can_walk(Layout.GATE_AT)})
	# The water micro-motion is material-only; cell boundaries never move.
	var water: ShaderMaterial = fixtures.world_assets.material("water")
	check(bool(water.get_shader_parameter("water_motion")), "pond_and_well_water_micro_motion_enabled")

func _foreground_clipping() -> void:
	# Regress the actual 1× peripheral-roof cut, then exercise the shader with
	# GPU readbacks both inside and beyond the narrow camera clearance band.
	world.actor_pos = Vector2(832, 624)
	view.set_pitch(38)
	view.set_yaw(30)
	view.zoom = 1
	view.initialized = false
	view.update(world, Vector2(root.size), 12 * 3600.0, 0.0)
	view.occlusion.reset()
	var temple: MeshInstance3D = fixtures.find_child("temple_structure", true, false)
	var nearest := INF
	for vertex: Vector3 in temple.mesh.get_faces():
		nearest = minf(nearest, -camera.to_local(temple.to_global(vertex)).z)
	check(nearest > view.foreground_clip_depth + 2.0, "wide_view_keeps_peripheral_temple_complete", {"nearest_roof_or_wall_depth": nearest, "clip_depth": view.foreground_clip_depth})
	fixtures.hide()
	actor.hide()
	var panel := MeshInstance3D.new()
	var quad := QuadMesh.new()
	quad.size = Vector2(0.8, 0.8)
	panel.mesh = quad
	var white := Image.create(1, 1, false, Image.FORMAT_RGBA8)
	white.fill(Color.WHITE)
	var diagnostic := ShaderMaterial.new()
	diagnostic.shader = FADE_SHADER
	diagnostic.set_shader_parameter("albedo_texture", ImageTexture.create_from_image(white))
	diagnostic.set_shader_parameter("tint", Color(4, 0, 0, 1))
	diagnostic.set_shader_parameter("glow", 1.0)
	panel.material_override = diagnostic
	scene.add_child(panel)
	for depth: float in [6.0, 12.0]:
		panel.global_transform = camera.global_transform * Transform3D(Basis.IDENTITY, Vector3(0, 0, -depth))
		diagnostic.set_shader_parameter("foreground_clip_depth", 0.0)
		var baseline := red(await shot("foreground-depth-%d-solid" % int(depth)))
		diagnostic.set_shader_parameter("foreground_clip_depth", view.foreground_clip_depth)
		var visible := red(await shot("foreground-depth-%d-clipped" % int(depth)))
		var expected := visible == 0 if depth == 6.0 else visible >= baseline * 0.98
		check(baseline > 100 and expected, "foreground_near_removed" if depth == 6.0 else "foreground_far_preserved", {"baseline_pixels": baseline, "visible_pixels": visible, "view_depth": depth})
	panel.queue_free()
	fixtures.show()
	actor.show()
	await process_frame

func _gallery() -> void:
	for building: Dictionary in Layout.BUILDINGS:
		var rect: Rect2 = building.rect
		var at := Vector2(rect.get_center().x, rect.end.y + 32)
		actor.set_pitch(38)
		actor.set_yaw(30)
		place(actor, at)
		configure(2, at - Vector2(20, 35), 38, 30)
		await shot("gallery-" + str(building.id))
	var points := {"well": Layout.WELL_AT + Vector2(0, 42), "pond": Vector2(1320, 430),
		"yard": Vector2(952, 256), "gate": Layout.GATE_AT - Vector2(0, 40),
		"forest": Vector2(360, 820), "cliffs": Vector2(1310, 800)}
	for title: String in points:
		var at: Vector2 = points[title]
		place(actor, at)
		configure(2, at - Vector2(20, 30), 38, 30)
		await shot("gallery-" + title)
	place(actor, Vector2(824, 580))
	configure(1, Vector2(800, 430), 55, 0)
	await shot("gallery-town-overview")
	clock_seconds = 22 * 3600.0
	for title: String in ["gate", "pond", "forest"]:
		var at: Vector2 = points[title]
		place(actor, at)
		configure(2, at - Vector2(20, 30), 38, 30)
		view.lighting.update(22 * 3600.0, fixtures, -camera.to_local(actor.global_position).z, actor.global_position)
		await shot("gallery-night-" + title)

func _boundary_views() -> void:
	var original_quality: String = view.quality
	for quality: String in ["light", "standard"]:
		view.quality = quality
		view.update(world, Vector2(root.size), 12 * 3600.0, 0.0)
		check(not view.lighting.environment.fog_enabled, "town_has_no_boundary_or_distance_fog_" + quality)
	view.quality = original_quality
	var ground: MeshInstance3D = fixtures.find_child("缓坡草地", true, false)
	var bounds := ground.mesh.get_aabb()
	var authored := Rect2(Vector2(bounds.position.x, bounds.position.z) / Projector.SCALE, Vector2(bounds.size.x, bounds.size.z) / Projector.SCALE)
	check(authored.is_equal_approx(Layout.SCENERY_BOUNDS) and bounds.position.y > Layout.SCENERY_FLOOR * Projector.SCALE,
		"non_walkable_scenic_ground_covers_camera_envelope", {"ground_bounds": authored, "logical_map": Layout.WORLD_SIZE})
	var poses: Array[Dictionary] = [
		{"name": "south-gate", "at": Layout.GATE_AT - Vector2(0,44), "zoom": 2, "pitch": 38.0, "yaw": 30.0, "fov": 35.0, "size": Vector2i(1440,900)},
		{"name": "south-reverse", "at": Layout.GATE_AT - Vector2(0,44), "zoom": 1, "pitch": 30.0, "yaw": -150.0, "fov": 40.0, "size": Vector2i(1440,900)},
		{"name": "northwest", "at": Vector2(76,76), "zoom": 1, "pitch": 30.0, "yaw": 135.0, "fov": 40.0, "size": Vector2i(1440,900)},
		{"name": "northeast", "at": Vector2(1524,76), "zoom": 3, "pitch": 65.0, "yaw": -45.0, "fov": 30.0, "size": Vector2i(1440,900)},
		{"name": "southwest", "at": Vector2(76,884), "zoom": 2, "pitch": 38.0, "yaw": 90.0, "fov": 35.0, "size": Vector2i(1440,900)},
		{"name": "southeast", "at": Vector2(1524,884), "zoom": 3, "pitch": 65.0, "yaw": -135.0, "fov": 40.0, "size": Vector2i(1440,900)},
		{"name": "small-window", "at": Vector2(76,480), "zoom": 1, "pitch": 30.0, "yaw": 0.0, "fov": 40.0, "size": Vector2i(1100,720)},
		{"name": "outer-corner", "at": Vector2(8,8), "zoom": 3, "pitch": 65.0, "yaw": 30.0, "fov": 40.0, "size": Vector2i(1100,720)},
		{"name": "ultrawide", "at": Vector2(1524,480), "zoom": 1, "pitch": 30.0, "yaw": 180.0, "fov": 40.0, "size": Vector2i(2560,1080)},
	]
	for pose: Dictionary in poses:
		root.size = pose.size
		await process_frame
		world.actor_pos = pose.at
		view.zoom = pose.zoom
		view.set_pitch(pose.pitch)
		view.set_yaw(pose.yaw)
		view.set_fov(pose.fov)
		view.initialized = false
		view.update(world, Vector2(root.size), 12 * 3600.0, 0.0)
		var image := await shot("boundary-" + str(pose.name))
		fixtures.hide()
		actor.hide()
		var sky := await shot("boundary-" + str(pose.name) + "-background")
		fixtures.show()
		actor.show()
		var missed := 0
		var sampled := 0
		for y: int in range(2, image.get_height(), 24):
			for x: int in range(2, image.get_width(), 24):
				if x > 26 and x < image.get_width() - 26 and y > 26 and y < image.get_height() - 26:
					continue
				var color := image.get_pixel(x,y)
				var background := sky.get_pixel(x,y)
				if Vector3(color.r-background.r, color.g-background.g, color.b-background.b).length() < 0.04:
					missed += 1
				sampled += 1
		var footprint := CameraLimits.ground_rect(camera, Vector2(root.size))
		var full_body: bool = Rect2(Vector2.ZERO, Vector2(root.size)).encloses(actor.occlusion_rect(camera))
		check(missed == 0 and Layout.SCENERY_BOUNDS.grow(-31).encloses(footprint) and full_body,
			"camera_border_has_ground_" + str(pose.name), {"sampled_border_pixels": sampled, "background_pixels": missed, "full_body_in_view": full_body, "ground_footprint": footprint, "window": root.size})
	root.size = Vector2i(1440,900)
	view.reset_camera()
	view.zoom = 2
	await process_frame
