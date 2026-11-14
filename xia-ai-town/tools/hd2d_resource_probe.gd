extends SceneTree
## R11: two concurrent native-size viewports. No backend, no performance claim.
const World = preload("res://scripts/town_world.gd")
const View = preload("res://scenes/hd2d/town_view.tscn")
const Projector = preload("res://scripts/hd2d/projection.gd")
var results: Array[Dictionary] = []
var failures := 0
var output := ""
var gpu := false
var draws := 0

func _initialize() -> void:
	_run.call_deferred()

func check(ok: bool, label: String, detail: Dictionary = {}) -> void:
	results.append({"name": label, "passed": ok, "detail": detail})
	if not ok:
		failures += 1
		printerr("HD2D_FAIL: ", label, " ", detail)

func _drawn() -> void:
	draws += 1

func _make_world(label: String) -> Dictionary:
	var world := World.new()
	world.render_2d = false
	world.scenic_layout = true
	world.actor_pos = Vector2(824, 580)
	root.add_child(world)
	var viewport := SubViewport.new()
	viewport.name = label
	viewport.size = Vector2i(1440, 900)
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(viewport)
	var view := View.instantiate()
	viewport.add_child(view)
	view.setup(world, true)
	view.quality = "light"
	view.update(world, Vector2(viewport.size), 43200.0, 0.0)
	return {"world": world, "viewport": viewport, "view": view}

func _settle() -> void:
	for i: int in range(8):
		await process_frame
	if gpu:
		await RenderingServer.frame_post_draw

func _shot(pair: Dictionary, label: String) -> Image:
	await _settle()
	var image: Image = pair.viewport.get_texture().get_image()
	check(image.get_size() == Vector2i(1440, 900), "native_image_" + label)
	check(image.save_png(output.path_join(label + ".png")) == OK, "save_image_" + label)
	return image

func _changed(a: Image, b: Image) -> int:
	var count := 0
	# Sample a fixed grid, not only an average that can hide cross-world changes.
	for y: int in range(0, a.get_height(), 3):
		for x: int in range(0, a.get_width(), 3):
			var ca := a.get_pixel(x, y)
			var cb := b.get_pixel(x, y)
			if Vector3(ca.r-cb.r, ca.g-cb.g, ca.b-cb.b).length() > 0.04:
				count += 1
	return count

func _resources(a: Dictionary, b: Dictionary) -> void:
	var sa: Node3D = a.view.scenery
	var sb: Node3D = b.view.scenery
	check(a.view.get_world_3d() != b.view.get_world_3d(), "independent_world3d")
	check(a.view.lighting.environment != b.view.lighting.environment, "independent_environment")
	check(a.view.lighting.attributes != b.view.lighting.attributes, "independent_camera_attributes")
	check(a.view.camera.attributes == a.view.lighting.attributes and b.view.camera.attributes == b.view.lighting.attributes, "camera_uses_own_attributes")
	check(sa._material_copies.size() > 20 and sa._material_copies.size() == sb._material_copies.size(), "same_material_inventory")
	for source_id: int in sa._material_copies:
		var source: ShaderMaterial = instance_from_id(source_id)
		var ma: ShaderMaterial = sa._material_copies[source_id]
		var mb: ShaderMaterial = sb._material_copies.get(source_id)
		check(mb != null and ma != mb and ma != source and mb != source, "material_isolated_" + ma.resource_name)
		if mb == null:
			continue
		check(ma.shader == mb.shader and ma.shader == source.shader, "shader_shared_" + ma.resource_name)
		check(ma.get_shader_parameter("albedo_texture") == mb.get_shader_parameter("albedo_texture") and ma.get_shader_parameter("albedo_texture") == source.get_shader_parameter("albedo_texture"), "texture_shared_" + ma.resource_name)
		var old_b: Variant = mb.get_shader_parameter("glow")
		var old_source: Variant = source.get_shader_parameter("glow")
		var old_a: Variant = ma.get_shader_parameter("glow")
		ma.set_shader_parameter("glow", 0.8765)
		check(mb.get_shader_parameter("glow") == old_b and source.get_shader_parameter("glow") == old_source, "parameter_write_stays_local_" + ma.resource_name)
		ma.set_shader_parameter("glow", old_a)
	var unique_materials: Dictionary = {}
	var shared_meshes := 0
	var owned := true
	for node: Node in sa.find_children("*", "", true, false):
		owned = owned and node.owner != null and node.owner.is_ancestor_of(node)
	check(owned, "authored_descendant_owners_preserved")
	for mesh: MeshInstance3D in sa.find_children("*", "MeshInstance3D", true, false):
		var other: MeshInstance3D = sb.get_node(sa.get_path_to(mesh))
		if mesh.has_meta("upright_origin"):
			check(mesh.mesh != other.mesh, "upright_quad_isolated_" + str(mesh.name))
		else:
			shared_meshes += 1
			check(mesh.mesh == other.mesh, "static_mesh_shared_" + str(mesh.name))
		for surface: int in range(mesh.mesh.get_surface_count()):
			var mat: Material = mesh.get_active_material(surface)
			if mat is ShaderMaterial:
				unique_materials[mat.get_instance_id()] = true
				check(sa.materials.values().has(mat), "surface_reuses_world_material_" + str(mesh.name))
	check(shared_meshes > 100 and unique_materials.size() == sa._material_copies.size(), "no_per_mesh_shader_copies", {"static_meshes": shared_meshes, "unique_materials": unique_materials.size()})
	check(not sa.uprights.is_empty(), "upright_fixture_present")
	if not sa.uprights.is_empty():
		var qa: MeshInstance3D = sa.uprights[0]
		var qb: MeshInstance3D = sb.get_node(sa.get_path_to(qa))
		var before_b: Vector2 = qb.mesh.size
		var ray_b: Shape3D = qb.get_node("VisualOccluder/RayShape").shape
		sa.set_pitch(55.0)
		check(qa.mesh.size != before_b and qb.mesh.size == before_b, "pitch_does_not_mutate_other_quad")
		check(qb.get_node("VisualOccluder/RayShape").shape == ray_b and qa.get_node("VisualOccluder/RayShape").shape != ray_b, "pitch_rebuilds_only_local_ray_shape")
		sa.set_pitch(Projector.GAME_PITCH)

func _grasses(a: Dictionary, b: Dictionary) -> void:
	var sa: Node3D = a.view.scenery
	var sb: Node3D = b.view.scenery
	var count := 0
	var shared_mesh: Mesh
	for pa: MultiMeshInstance3D in sa.find_children("*", "MultiMeshInstance3D", true, false):
		var pb: MultiMeshInstance3D = sb.get_node(sa.get_path_to(pa))
		var ma := pa.multimesh
		var mb := pb.multimesh
		var data: PackedFloat32Array = ma.get_meta("authored_buffer")
		var bounds: AABB = ma.get_meta("authored_bounds")
		count += ma.instance_count
		check(ma.instance_count > 0 and data.size() == ma.instance_count * 12 and bounds.has_volume(), "grass_authored_metadata_" + str(pa.name))
		var enclosed := true
		for i: int in range(ma.instance_count):
			var n := i * 12
			var t := Transform3D(Basis(Vector3(data[n],data[n+4],data[n+8]), Vector3(data[n+1],data[n+5],data[n+9]), Vector3(data[n+2],data[n+6],data[n+10])), Vector3(data[n+3],data[n+7],data[n+11]))
			enclosed = enclosed and bounds.encloses(t * ma.mesh.get_aabb())
		check(enclosed, "grass_bounds_contain_every_blade_" + str(pa.name))
		if shared_mesh == null:
			shared_mesh = ma.mesh
		check(ma.mesh == mb.mesh and ma.mesh == shared_mesh, "grass_geometry_shared_" + str(pa.name))
		# Grass shader/geometry are immutable; per-instance transforms are not.
		if gpu:
			check(ma != mb and ma.buffer == data and mb.buffer == data, "grass_gpu_buffer_restored_and_isolated_" + str(pa.name))
			check(ma.custom_aabb == bounds and mb.custom_aabb == bounds, "grass_gpu_aabb_restored_" + str(pa.name))
			var before := mb.get_instance_transform(0)
			var changed := before
			changed.origin.x += 0.25
			ma.set_instance_transform(0, changed)
			check(ma.get_instance_transform(0).is_equal_approx(changed) and mb.get_instance_transform(0).is_equal_approx(before), "grass_write_stays_local_" + str(pa.name))
			ma.set_instance_transform(0, before)
	check(count > 2000, "grass_population_present", {"instances": count, "gpu_buffer_checks": gpu})

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	gpu = DisplayServer.get_name() != "headless"
	if gpu and output.is_empty():
		printerr("HD2D_FAIL: GPU resource probe requires XIA_HD2D_OUTPUT")
		quit(1)
		return
	if not output.is_empty() and DirAccess.make_dir_recursive_absolute(output) != OK:
		printerr("HD2D_FAIL: cannot create resource report directory: ", output)
		quit(1)
		return
	Engine.max_fps = 60
	# Freeze shader TIME as well as gameplay; B must remain pixel-identical.
	Engine.time_scale = 0.0
	root.size = Vector2i(1440, 900)
	RenderingServer.frame_post_draw.connect(_drawn)
	var a := _make_world("WorldA")
	var b := _make_world("WorldB")
	_resources(a, b)
	_grasses(a, b)
	await _settle()
	var env_b: float = b.view.lighting.environment.ambient_light_energy
	var sun_b: float = b.view.lighting.sun.light_energy
	var depth_b: float = b.view.lighting.attributes.dof_blur_far_distance
	var glow_b: Variant = b.view.scenery.lantern_material.get_shader_parameter("glow")
	var baseline_a: Image
	var baseline_b: Image
	if gpu:
		baseline_a = await _shot(a, "a-baseline")
		baseline_b = await _shot(b, "b-baseline")
		check(baseline_a.get_data() == baseline_b.get_data(), "matching_worlds_render_identically")
	a.view.lighting.update(79200.0, a.view.scenery, 40.0)
	check(b.view.lighting.environment.ambient_light_energy == env_b and b.view.lighting.sun.light_energy == sun_b, "night_lighting_does_not_leak")
	check(b.view.lighting.attributes.dof_blur_far_distance == depth_b and a.view.lighting.attributes.dof_blur_far_distance == 55.0, "camera_depth_does_not_leak")
	check(b.view.scenery.lantern_material.get_shader_parameter("glow") == glow_b and a.view.scenery.lantern_material.get_shader_parameter("glow") != glow_b, "lantern_glow_does_not_leak")
	if gpu:
		var night := await _shot(a, "a-night")
		var same_b := await _shot(b, "b-after-a-night")
		check(_changed(baseline_a, night) > 1000, "gpu_a_night_actually_changed")
		check(baseline_b.get_data() == same_b.get_data(), "gpu_b_unchanged_after_a_night")
	a.view.update(a.world, Vector2(a.viewport.size), 43200.0, 0.0)
	var roof: ShaderMaterial = a.view.scenery.materials["p3/roof"]
	var tint: Variant = roof.get_shader_parameter("tint")
	roof.set_shader_parameter("tint", Color(1.0, 0.0, 1.0))
	if gpu:
		var material_a := await _shot(a, "a-material")
		var same_b := await _shot(b, "b-after-a-material")
		check(_changed(baseline_a, material_a) > 100, "gpu_a_material_actually_changed")
		check(baseline_b.get_data() == same_b.get_data(), "gpu_b_unchanged_after_a_material")
	roof.set_shader_parameter("tint", tint)
	if gpu:
		for patch: MultiMeshInstance3D in a.view.scenery.find_children("*", "MultiMeshInstance3D", true, false):
			patch.hide()
		var without_grass := await _shot(a, "a-grass-hidden")
		var changed := _changed(baseline_a, without_grass)
		check(changed > 200, "gpu_grass_has_distributed_visible_pixels", {"changed_grid_samples": changed})
	a.viewport.queue_free()
	a.world.queue_free()
	await _settle()
	check(not is_instance_valid(a.view) and is_instance_valid(b.view), "free_a_preserves_b")
	if gpu:
		var surviving := await _shot(b, "b-after-a-free")
		check(baseline_b.get_data() == surviving.get_data(), "gpu_b_unchanged_after_a_free")
		check(draws > 10, "real_gpu_frames", {"frames": draws})
	var report := {"device": RenderingServer.get_video_adapter_name(), "backend": RenderingServer.get_current_rendering_method(), "quality": "light", "gpu": gpu, "passed": failures == 0, "results": results, "rendered_frames": draws, "scope": "R11 resource isolation; headless skips GPU buffers/images; NOT gameplay, performance or P5"}
	if not output.is_empty():
		var file := FileAccess.open(output.path_join("probe.json"), FileAccess.WRITE)
		if file == null:
			failures += 1
			printerr("HD2D_FAIL: cannot open resource report: ", output)
		else:
			file.store_string(JSON.stringify(report))
			file.close()
	b.viewport.queue_free()
	b.world.queue_free()
	await process_frame
	RenderingServer.frame_post_draw.disconnect(_drawn)
	print("HD2D_RESOURCE_CHECKS: %d failures=%d gpu=%s" % [results.size(), failures, gpu])
	quit(1 if failures else 0)
