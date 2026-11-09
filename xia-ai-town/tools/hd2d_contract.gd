extends SceneTree
const World = preload("res://scripts/town_world.gd")
const Projector = preload("res://scripts/hd2d/projection.gd")
func _initialize() -> void:
	var world := World.new()
	world._ensure_grid()
	var hash := HashingContext.new()
	hash.start(HashingContext.HASH_SHA256)
	hash.update(world._grid)
	var grid_hash := hash.finish().hex_encode()
	var walk := PackedByteArray()
	walk.resize(1600*960)
	for y: int in range(960):
		for x: int in range(1600):
			walk[y*1600+x] = int(world.can_walk(Vector2(x,y)))
	hash.start(HashingContext.HASH_SHA256)
	hash.update(walk)
	var walk_hash := hash.finish().hex_encode()
	var result := {"grid_sha256":grid_hash,"walk_sha256":walk_hash,"size":[1600,960],"grid_cells":world._grid.size(),"buildings":[]}
	for b: Dictionary in world.BUILDINGS:
		var r: Rect2 = b.rect
		result.buildings.append({"id":b.id,"rect":[r.position.x,r.position.y,r.size.x,r.size.y]})
	print("HD2D_CONTRACT ",JSON.stringify(result))
	var fixture_path := "res://tools/hd2d_contract.json"
	if FileAccess.file_exists(fixture_path):
		var expected: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(fixture_path))
		if grid_hash != expected.grid_sha256 or walk_hash != expected.walk_sha256 or result.buildings != expected.buildings:
			printerr("HD2D_FAIL: frozen 2D grid/walk/footprints changed")
			world.free()
			quit(1)
			return
	else:
		printerr("HD2D_FAIL: missing frozen contract fixture")
		world.free()
		quit(1)
		return
	if not Projector.point(Vector2(1600,960)).is_equal_approx(Vector3(100,0,60)):
		printerr("HD2D_FAIL: coordinate mapping")
		quit(1)
		return
	var body := Projector.upright_height(36)*cos(deg_to_rad(Projector.PITCH))/Projector.SCALE*2
	if absf(body-72) > 0.001:
		printerr("HD2D_FAIL: body height contract")
		quit(1)
		return
	world.free()
	print("HD2D_CONTRACT_PASS: full 1,536,000 walk samples, 6000 grid cells, 7 footprints, scale/height")
	_scene_checks.call_deferred()

func _scene_checks() -> void:
	# Instantiate the whole fixture: this catches runtime property/enum errors
	# that an editor import of an unused scene cannot catch. Not a GPU test.
	var prototype = load("res://scenes/hd2d/prototype.tscn").instantiate()
	root.size = Vector2i(1440,900)
	root.add_child(prototype)
	await process_frame
	await process_frame
	prototype.reset_camera()
	var hero = prototype.view.hero
	prototype.view.set_yaw(0.0)
	if hero.sprite.billboard != BaseMaterial3D.BILLBOARD_FIXED_Y or hero.sprite.no_depth_test:
		printerr("HD2D_FAIL: fixed-Y/depth contract")
		quit(1)
		return
	for direction: int in range(8):
		hero.sync(Vector2(512,422),0.5,direction)
		if hero.frame/hero.sprite.hframes != direction:
			printerr("HD2D_FAIL: idle direction")
			quit(1)
			return
		hero.sync(Vector2(513,423),0.13,direction)
		if hero.frame/hero.sprite.hframes != direction or hero.frame%hero.sprite.hframes < 2:
			printerr("HD2D_FAIL: walking direction")
			quit(1)
			return
	# Equal travel must give equal gait regardless of frame timing, including
	# all eight poses; stopping and orbiting must not advance the remembered gait.
	var visited := {}
	hero.sync(Vector2(512,422),0.0,0)
	hero.sync(Vector2(512,422),0.0,0)
	for i: int in range(1,79):
		hero.sync(Vector2(512+i,422),0.001 if i%2==0 else 0.03)
		visited[hero.frame%hero.sprite.hframes] = true
	if visited.size()!=hero.metadata.walk_columns.size():
		printerr("HD2D_FAIL: distance-driven gait skips walk poses")
		quit(1)
		return
	var one_cycle: int = hero.frame
	hero.sync(Vector2(512,422),0.0,0)
	hero.sync(Vector2(512,422),0.0,0)
	for i: int in range(1,7):
		hero.sync(Vector2(512+i*13,422),0.087)
	if hero.frame!=one_cycle:
		printerr("HD2D_FAIL: gait depends on frame timing instead of travel")
		quit(1)
		return
	hero.sync(Vector2(590,422),0.5)
	var shadow_rotation: Vector3 = hero.body_shadow.rotation
	hero.set_yaw(97)
	if hero.frame%hero.sprite.hframes>=2 or hero.body_shadow.rotation!=shadow_rotation:
		printerr("HD2D_FAIL: stopped gait/orbit rotates ground shadow")
		quit(1)
		return
	hero.set_yaw(0)
	print("HD2D_GAIT_PASS: eight walk poses, equal travel/equal phase, stopped idle, camera-independent shadow")
	# Fixed-Y geometry retains its world foot. Perspective bounds must contain
	# all four transformed frame corners, including the nearer top shoulders.
	hero.sync(Vector2(512,422), 0.0, 0)
	var camera: Camera3D = prototype.view.camera
	var pixels := Vector2(prototype.viewport.size)
	for elevation: float in [Projector.MIN_PITCH, Projector.PITCH, Projector.MAX_PITCH]:
		prototype.view.set_pitch(elevation)
		for azimuth: float in [0.0, Projector.YAW, 90.0, -123.0, -180.0]:
			prototype.view.set_yaw(azimuth)
			var back := Projector.ground_back(azimuth)
			for upright: MeshInstance3D in prototype.view.scenery.uprights:
				var shape: CollisionShape3D = upright.get_child(0).get_child(0)
				var height := upright.mesh.get_aabb().size.y
				if not upright.global_basis.z.is_equal_approx(back) or absf(height - Projector.upright_height(float(upright.get_meta("upright_height")), elevation)) > 0.001 or not shape.shape.backface_collision or not shape.shape.get_faces() == upright.mesh.get_faces():
					printerr("HD2D_FAIL: upright CPU/visual/ray shape mismatch")
					quit(1)
					return
			for zoom: int in [1,2,3]:
				var focus := Vector2(512,422) - Vector2(back.x,back.z)*68.0
				Projector.configure(camera, pixels, zoom, focus, elevation, azimuth, false, Projector.FOV, Vector2(512,422))
				if camera.projection != Camera3D.PROJECTION_PERSPECTIVE or camera.keep_aspect != Camera3D.KEEP_HEIGHT:
					printerr("HD2D_FAIL: perspective/vertical FOV")
					quit(1)
					return
				var foot: Vector3 = hero.global_position
				var projected_width := camera.unproject_position(foot + Projector.right(azimuth)*Projector.SCALE).x - camera.unproject_position(foot).x
				if absf(projected_width - zoom) > 0.002:
					printerr("HD2D_FAIL: zoom not calibrated at player foot depth")
					quit(1)
					return
				for frame: int in range(hero.metadata.frames.size()):
					hero._set_frame(frame)
					var info: Dictionary = hero.metadata.frames[frame]
					var foot_local := Vector3((float(info.foot[0])-32)*hero.sprite.pixel_size, (40-float(info.foot[1]))*hero.sprite.pixel_size, 0)
					var rect: Rect2 = hero.screen_rect(camera)
					if not hero.sprite.to_global(foot_local).is_equal_approx(foot) or not rect.is_finite() or rect.size.x <= 0 or rect.size.y <= 0:
						printerr("HD2D_FAIL: orbit changes foot/finite body bounds")
						quit(1)
						return
					for x: float in [float(info.bounds[0]), float(info.bounds[0])+float(info.bounds[2])]:
						for y: float in [float(info.bounds[1]), float(info.bounds[1])+float(info.bounds[3])]:
							var vertex: Vector3 = hero.sprite.to_global(Vector3((x-32)*hero.sprite.pixel_size, (40-y)*hero.sprite.pixel_size, 0))
							if not rect.grow(0.002).has_point(camera.unproject_position(vertex)):
								printerr("HD2D_FAIL: perspective label rectangle misses a transformed corner")
								quit(1)
								return
				for at: Vector3 in hero.occlusion_points():
					var origin := Projector.ray_origin(camera, at)
					if absf(camera.to_local(origin).z + camera.near) > 0.001 or (at-origin).normalized().dot((at-camera.global_position).normalized()) < 0.99999:
						printerr("HD2D_FAIL: occlusion ray is not the rendered perspective ray")
						quit(1)
						return
			for direction: int in range(8):
				hero.sync(Vector2(512,422),0.0,direction)
				if hero.frame / hero.sprite.hframes != direction:
					printerr("HD2D_FAIL: camera-relative idle facing")
					quit(1)
					return
				var idle_frame: int = hero.frame
				hero.set_yaw(azimuth + 17.0)
				if hero.frame != idle_frame:
					printerr("HD2D_FAIL: camera rotation changed remembered facing")
					quit(1)
					return
				hero.set_yaw(azimuth)
				var screen_axis := Vector2(-sin(direction * PI / 4.0), cos(direction * PI / 4.0))
				var world_axis := Projector.camera_relative_direction(screen_axis, azimuth)
				hero.sync(Vector2(512,422) + world_axis, 0.13)
				if hero.frame / hero.sprite.hframes != direction or not Projector.screen_relative_direction(world_axis, azimuth).is_equal_approx(screen_axis) or not is_equal_approx(world_axis.length(), 1.0):
					printerr("HD2D_FAIL: camera-relative walking direction/speed")
					quit(1)
					return
			hero.sync(Vector2(512,422), 0.0, 0)
			for at: Vector3 in hero.occlusion_points():
				if absf((at-hero.global_position).dot(back)) > 0.001:
					printerr("HD2D_FAIL: occlusion envelope left the fixed-Y billboard plane")
					quit(1)
					return
	prototype.reset_camera()
	var back := Projector.ground_back(Projector.YAW)
	var focus := Vector2(512,422) - Vector2(back.x,back.z)*68.0
	for field_of_view: float in [Projector.MIN_FOV, Projector.FOV, Projector.MAX_FOV]:
		for zoom: int in [1,2,3]:
			Projector.configure(camera,pixels,zoom,focus,Projector.PITCH,Projector.YAW,false,field_of_view,Vector2(512,422))
			hero.sync(Vector2(512,422),0.0,0)
			hero._set_frame(0)
			var near_size: Vector2 = hero.screen_rect(camera).size
			var foot: Vector3 = hero.global_position
			var reference_width := camera.unproject_position(foot + Projector.right(Projector.YAW)*Projector.SCALE).x - camera.unproject_position(foot).x
			hero.sync(Vector2(512,422)-Vector2(back.x,back.z)*96.0,0.0,0)
			hero._set_frame(0)
			var far_size: Vector2 = hero.screen_rect(camera).size
			if far_size.x >= near_size.x or far_size.y >= near_size.y or absf(reference_width-zoom) > 0.002 or (zoom == 2 and (near_size.y < 64 or near_size.y > 80)):
				printerr("HD2D_FAIL: perspective depth, reference zoom or 2x body height ",near_size," / ",far_size)
				quit(1)
				return
			hero.sync(Vector2(512,422),0.0,0)
			hero._set_frame(0)
			Projector.configure(camera,pixels,zoom,focus,Projector.PITCH,Projector.YAW,true,field_of_view,Vector2(512,422))
			var orthogonal_size: Vector2 = hero.screen_rect(camera).size
			hero.sync(Vector2(512,422)-Vector2(back.x,back.z)*96.0,0.0,0)
			hero._set_frame(0)
			if not orthogonal_size.is_equal_approx(hero.screen_rect(camera).size):
				printerr("HD2D_FAIL: orthogonal comparison changed size with depth")
				quit(1)
				return
	hero.sync(Vector2(512,422),0.0,0)
	prototype.view.set_pitch(90.0)
	prototype.view.set_yaw(540.0)
	prototype.view.set_fov(90.0)
	if prototype.view.pitch != Projector.MAX_PITCH or prototype.view.yaw != -180.0 or prototype.view.fov != Projector.MAX_FOV:
		printerr("HD2D_FAIL: camera range guards")
		quit(1)
		return
	prototype.reset_camera()
	if prototype.view.pitch != Projector.PITCH or prototype.view.yaw != Projector.YAW or prototype.view.fov != Projector.FOV or prototype.pitch_slider.value != Projector.PITCH or prototype.yaw_slider.value != Projector.YAW or prototype.fov_slider.value != Projector.FOV:
		printerr("HD2D_FAIL: camera/UI reset")
		quit(1)
		return
	print("HD2D_ORBIT_PASS: 3600 perspective frame/pitch/yaw/zoom foot+corner cases, converging rays, depth scaling, FOV/zoom/body calibration, orthogonal comparison, clamp/wrap/reset")
	for mesh: MeshInstance3D in prototype.view.scenery.occluders:
		var group: StringName = mesh.get_meta("fade_group")
		var body: StaticBody3D = mesh.get_child(0)
		var shape: CollisionShape3D = body.get_child(0)
		if not prototype.view.scenery.fade_groups[group].has(mesh) or body.collision_layer != 2 or not shape.shape.backface_collision:
			printerr("HD2D_FAIL: component registry/ray-only double-sided shape")
			quit(1)
			return
	var scenery = prototype.view.scenery
	var obstacles := {prototype.world.BUILDING: 0, prototype.world.TREE: 0}
	for tag: int in prototype.world._grid:
		if tag in [prototype.world.WATER, prototype.world.CLIFF, prototype.world.WALL, prototype.world.WELL]:
			printerr("HD2D_FAIL: invisible old-map obstacle in P1")
			quit(1)
			return
		if obstacles.has(tag):
			obstacles[tag] += 1
	for at: Vector2 in [Vector2(144, 100), Vector2(300, 300), Vector2(100, 490), Vector2(720, 420), Vector2(1200, 800), Vector2(1560, 920)]:
		if not prototype.world.can_walk(at):
			printerr("HD2D_FAIL: expanded P1 walk range blocked ", at)
			quit(1)
			return
	if obstacles[prototype.world.BUILDING] != 96 or obstacles[prototype.world.TREE] != 2 or prototype.world.can_walk(Vector2(512, 320)) or prototype.world.can_walk(Vector2(2, 2)):
		printerr("HD2D_FAIL: P1 inn/tree/boundary collision")
		quit(1)
		return
	for index: int in range(2):
		var parts: Array = scenery.fade_groups[StringName("prop/tree/%d" % index)]
		if parts.size() != 2:
			printerr("HD2D_FAIL: tree trunk/closed-crown count")
			quit(1)
			return
		for part: MeshInstance3D in parts:
			var extent := part.mesh.get_aabb().size
			if scenery.uprights.has(part) or minf(extent.x, minf(extent.y, extent.z)) <= 0 or part.has_meta("alpha_quad") or part.has_meta("alpha_image"):
				printerr("HD2D_FAIL: tree geometry has no volume or still uses cards")
				quit(1)
				return
	print("HD2D_LAYOUT_TREE_PASS: open P1 grid, 96 inn tiles + 2 visible tree cells, boundaries, rooted trunk and closed bough crown per tree")
	if scenery.house == null or scenery.house_data.mesh_count != 1 or scenery.house_data.surface_count != 9 or scenery.house.position != Projector.point(prototype.world.BUILDINGS[5].rect.get_center()):
		printerr("HD2D_FAIL: merged Blender house placement/count")
		quit(1)
		return
	var structure: MeshInstance3D = scenery.house.find_child("structure", true, false)
	# The plinth is part of the shared mesh: inspect its ground vertices rather
	# than requiring the former stand-alone plinth/front/back objects.
	var base_bounds := AABB()
	var base_found := false
	for vertex: Vector3 in structure.mesh.get_faces():
		if absf(vertex.y) < 0.0001:
			base_bounds = base_bounds.expand(vertex) if base_found else AABB(vertex, Vector3.ZERO)
			base_found = true
	var expected_base := AABB(Vector3(-96, 0, -64) * Projector.SCALE, Vector3(192, 0, 128) * Projector.SCALE)
	# Godot quantizes imported meshes within their full AABB. Compare endpoint
	# positions with a 0.005 logical-pixel tolerance (under 0.02px even at 3x),
	# rather than relative float equality on an accumulated bounds size.
	var import_epsilon := 0.005 * Projector.SCALE
	if not base_found or (base_bounds.position-expected_base.position).length() > import_epsilon or (base_bounds.end-expected_base.end).length() > import_epsilon:
		printerr("HD2D_FAIL: Blender ground footprint/up/local axes")
		quit(1)
		return
	var triangles := 0
	for mesh: MeshInstance3D in scenery.house.find_children("*", "MeshInstance3D", true, false):
		var spec: Dictionary = scenery.house_data.components[String(mesh.name)]
		if mesh.material_override != null or mesh.mesh.get_surface_count() != spec.materials.size() or mesh.get_meta("fade_group") != StringName(spec.group) or not mesh.transform.is_equal_approx(Transform3D.IDENTITY):
			printerr("HD2D_FAIL: Blender component/material slots/transform")
			quit(1)
			return
		for index: int in range(mesh.mesh.get_surface_count()):
			var material := mesh.get_surface_override_material(index) as ShaderMaterial
			var key := mesh.mesh.surface_get_material(index).resource_name.trim_prefix("XIA_P1_")
			var texture: Texture2D = material.get_shader_parameter("albedo_texture") if material != null else null
			if material == null or material.shader != scenery.FADE_SHADER or not spec.materials.has(key) or texture == null or texture.resource_path != scenery.ROOT + String(scenery.house_data.materials[key].texture) + ".png":
				printerr("HD2D_FAIL: Blender named surface not bound to pixel/fade shader")
				quit(1)
				return
		var faces: PackedVector3Array = mesh.mesh.get_faces()
		# Real recessed backing, joinery, casing and sill remain in the merged
		# mesh. Flat door/window cards cannot satisfy this contract.
		if spec.materials.has("door") or spec.materials.has("window") or not spec.materials.has("wood_recess"):
			printerr("HD2D_FAIL: opening details regressed to a flat card")
			quit(1)
			return
		for door: bool in [true, false]:
			var layers: Array = [53.5, 58.0, 69.0, 73.0] if door else [58.5, 64.5, 69.0, 70.0]
			for layer: float in layers:
				var found := false
				for vertex: Vector3 in faces:
					# Separate the center door from the two windows by local X,
					# keeping the original import-quantization tolerance.
					if (absf(vertex.x / Projector.SCALE) < 30.0) != door:
						continue
					if absf(vertex.z / Projector.SCALE - layer) < 0.005:
						found = true
						break
				if not found:
					printerr("HD2D_FAIL: missing physical opening layer ", mesh.name, " / ", layer)
					quit(1)
					return
		triangles += faces.size()/3
		for index: int in range(0, faces.size(), 3):
			if (faces[index+1]-faces[index]).cross(faces[index+2]-faces[index]).length() < 0.00000001:
				printerr("HD2D_FAIL: imported degenerate triangle")
				quit(1)
				return
	if triangles != int(scenery.house_data.triangles):
		printerr("HD2D_FAIL: imported triangle count ", triangles)
		quit(1)
		return
	print("HD2D_HOUSE_PASS: one merged mesh / 9 named surfaces, local axes/origin, recessed solid door/window joinery, pixel/fade materials, no degenerate triangles, ", triangles, " triangles")
	prototype.view.set_pitch(Projector.PITCH)
	prototype.toggle_panel()
	var before: Vector2 = prototype.world.actor_pos
	var event := InputEventKey.new()
	event.physical_keycode = KEY_D
	event.pressed = true
	Input.parse_input_event(event)
	await process_frame
	await process_frame
	event.pressed = false
	Input.parse_input_event(event)
	if prototype.world.actor_pos != before:
		printerr("HD2D_FAIL: panel allows movement")
		quit(1)
		return
	prototype.toggle_panel()
	root.size = Vector2i(1100,720)
	await process_frame
	await process_frame
	if prototype.viewport.size != Vector2i(prototype.area.size) or prototype.viewport.scaling_3d_scale != 1.0:
		printerr("HD2D_FAIL: native viewport sizing")
		quit(1)
		return
	prototype.toggle_camera_panel()
	await process_frame
	var panel_rect: Rect2 = prototype.camera_panel.get_global_rect()
	if panel_rect.end.x > root.size.x or panel_rect.end.y > root.size.y or panel_rect.position.x < 0:
		printerr("HD2D_FAIL: camera panel clipped at 1100x720")
		quit(1)
		return
	var reference_depth := -camera.to_local(hero.global_position).z
	if absf(prototype.view.lighting.attributes.dof_blur_far_distance - reference_depth - 15.0) > 0.002:
		printerr("HD2D_FAIL: far DOF does not follow perspective reference depth")
		quit(1)
		return
	print("HD2D_SCENE_PASS: camera-relative eight-way movement, fixed-Y body/foot/prop shapes, local-occlusion registry/double-sided ray-only shapes, modal movement block, native resize, depth-relative far DOF")
	prototype.queue_free()
	await process_frame
	var legacy := World.new()
	root.add_child(legacy)
	var fixture: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://tools/hd2d_contract.json"))
	var hash := HashingContext.new()
	hash.start(HashingContext.HASH_SHA256)
	hash.update(legacy.canvas._static_texture.get_image().get_data())
	if hash.finish().hex_encode() != str(fixture.legacy_static_sha256):
		printerr("HD2D_FAIL: splitting the legacy canvas changed the frozen 1600x960 static art")
		quit(1)
		return
	legacy.queue_free()
	await process_frame
	print("HD2D_LEGACY_CANVAS_PASS: frozen 1,536,000 static pixels unchanged after state/layout split")
	await _full_map_checks()
	quit()

func _full_map_checks() -> void:
	var world := World.new()
	world.render_2d = false
	root.add_child(world)
	var view := preload("res://scenes/hd2d/town_view.tscn").instantiate()
	root.add_child(view)
	view.setup(world, true)
	await process_frame
	var assets: RefCounted = view.scenery.world_assets
	if assets == null or world.canvas != null:
		printerr("HD2D_FAIL: P3 map did not build or created the legacy canvas")
		quit(1)
		return
	for building: Dictionary in world.BUILDINGS:
		if building.id != "inn" and int(assets.placed.get("building/" + str(building.id), 0)) != 1:
			printerr("HD2D_FAIL: P3 building missing ", building.id)
			quit(1)
			return
		if str(building.kind).is_empty():
			continue
		var rect: Rect2 = building.rect
		world.actor_pos = Vector2(rect.get_center().x, rect.end.y + 24)
		var nearby: Dictionary = world.nearby()
		if not world.can_walk(world.actor_pos) or nearby.get("id", "") != str(building.id):
			printerr("HD2D_FAIL: original building interaction apron changed ", building.id)
			quit(1)
			return
	for furniture: Dictionary in World.Layout.furniture():
		var name := str(assets.data.prop_keys[int(furniture.index)])
		var key := "prop/lantern_post" if name == "lantern" else "prop/" + name
		if not assets.placed.has(key):
			printerr("HD2D_FAIL: P3 furniture missing ", key)
			quit(1)
			return
	if not world.can_walk(Vector2(800, 496)) or not assets.placed.has("prop/well") or not assets.placed.has("town/gate"):
		printerr("HD2D_FAIL: P3 well/gate or original road missing")
		quit(1)
		return
	print("HD2D_FULL_MAP_PASS: six new buildings, every original furniture identity, well/gate, original walkable shop/sect/inn aprons, no legacy canvas")
	view.queue_free()
	world.queue_free()
	await process_frame
