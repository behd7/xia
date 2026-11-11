extends SceneTree
## Real GPU readbacks. Hard geometry tests run with assistance DISABLED first.
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

func _initialize() -> void:
	_run.call_deferred()

func check(condition: bool, name: String, detail: Dictionary = {}) -> void:
	results.append({"name":name,"passed":condition,"detail":detail})
	if not condition:
		failed = true
		printerr("HD2D_FAIL: ",name," ",detail)
	else:
		print("HD2D_PROBE_PASS: ",name," ",detail)

func pause() -> void:
	for i: int in range(8):
		await process_frame
	await physics_frame

func shot(name: String) -> Image:
	await pause()
	await RenderingServer.frame_post_draw
	var image := root.get_texture().get_image()
	image.save_png(output.path_join(name+".png"))
	return image

func red(image: Image) -> int:
	var count := 0
	for y: int in range(image.get_height()):
		for x: int in range(image.get_width()):
			var c := image.get_pixel(x,y)
			if c.r > c.g+0.12 and c.r > c.b+0.12:
				count += 1
	return count

func readability(name: String, scenery: Node3D, actors: Array, native_background: bool = false) -> Dictionary:
	# Same-position actor/no-actor pairs isolate the body signal. Comparing a
	# fixed sRGB red threshold falsely labels translucent, desaturated pixels
	# as missing. Linear-light contrast also catches genuine opaque leftovers.
	var visible := await shot(name + "-visible")
	for who: Node3D in actors:
		who.sprite.hide()
	var background := await shot(name + "-background")
	var prior: Array = []
	for mesh: MeshInstance3D in scenery.occluders:
		# Keep opaque BACKGROUND geometry in the reference. Only the selected
		# foreground components are removed, otherwise the lighting/background
		# difference is wrongly counted as lost character contrast.
		if float(mesh.get_instance_shader_parameter("occlusion_fade")) > 0.0:
			prior.append([mesh, mesh.visible])
			mesh.hide()
	var reference_background := await shot(name + "-reference-background")
	for who: Node3D in actors:
		who.sprite.show()
	var reference := await shot(name + "-reference")
	for entry: Array in prior:
		entry[0].visible = entry[1]
	var total := 0
	var retained := 0
	var contrast := PackedFloat32Array()
	for y: int in range(reference.get_height()):
		for x: int in range(reference.get_width()):
			var ref := reference.get_pixel(x,y)
			if ref.r <= ref.g + 0.12 or ref.r <= ref.b + 0.12:
				continue
			var ref_linear := ref.srgb_to_linear()
			# Closed merged crowns contain background as well as foreground
			# surfaces. Hiding that mesh removes BOTH, unlike a thin test wall.
			# For volume tests compare the ideal opaque body's color against the
			# SAME native background readback. A fully blocked body still yields
			# zero signal; background greenery no longer inflates the reference.
			var ref_bg := (background if native_background else reference_background).get_pixel(x,y).srgb_to_linear()
			var reference_signal := Vector3(ref_linear.r-ref_bg.r, ref_linear.g-ref_bg.g, ref_linear.b-ref_bg.b)
			if reference_signal.length_squared() <= 0.000025:
				continue
			var now := visible.get_pixel(x,y).srgb_to_linear()
			var bg := background.get_pixel(x,y).srgb_to_linear()
			var observed := Vector3(now.r-bg.r, now.g-bg.g, now.b-bg.b)
			var ratio := observed.dot(reference_signal) / reference_signal.length_squared()
			total += 1
			contrast.append(ratio)
			# Keep >=90% of the reference body pixels with >=65% of their
			# unoccluded linear-light contrast, not just a barely visible outline.
			if ratio >= 0.65:
				retained += 1
	contrast.sort()
	return {"reference_pixels":total, "readable_pixels":retained,
		"coverage":float(retained)/maxi(total,1),
		"contrast_p10":contrast[int(contrast.size()*0.1)] if not contrast.is_empty() else 0.0,
		"contrast_median":contrast[int(contrast.size()*0.5)] if not contrast.is_empty() else 0.0}

func pixel(image: Image, at: Vector2, height: float) -> Color:
	var p := Vector2i(camera.unproject_position(Projector.point(at, height)))
	return image.get_pixelv(p)

func place(who: Node3D, at: Vector2) -> void:
	who.sync(at, 0.0, 0)
	who._set_frame(0)

func settle(occlusion: RefCounted, actors: Array, seconds: float = 0.8, groups: Dictionary = {}) -> void:
	var registry: Dictionary = fixtures.fade_groups if groups.is_empty() else groups
	for i: int in range(ceili(seconds / 0.02)):
		occlusion.update(scene, camera, actors, registry, 0.02)

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
	Projector.configure(camera,Vector2(root.size),zoom,focus,elevation,azimuth,orthogonal,fov)

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	if OS.get_environment("XIA_HD2D_NO_VSYNC") == "1":
		DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
		Engine.max_fps = 120
	if not OS.get_environment("XIA_HD2D_MAX_FPS").is_empty():
		Engine.max_fps = maxi(1, int(OS.get_environment("XIA_HD2D_MAX_FPS")))
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
	scene.add_child(actor)
	second = load("res://scenes/hd2d/actor.tscn").instantiate()
	scene.add_child(second)
	var diagnostic: ShaderMaterial = Actor.body_material.duplicate()
	diagnostic.set_shader_parameter("unshaded", true)
	for who: Node3D in [actor, second]:
		who.set_pitch(pitch)
		who.sprite.material_override = diagnostic
		who.sprite.shaded = false
		who.sprite.modulate = Color(4,0,0,1)
	second.hide()
	fixtures = Scenery.new()
	scene.add_child(fixtures)
	material = ShaderMaterial.new()
	material.shader = FADE_SHADER
	var white := Image.create(4,4,false,Image.FORMAT_RGBA8)
	white.fill(Color.WHITE)
	material.set_shader_parameter("albedo_texture",ImageTexture.create_from_image(white))
	var front := wall(&"test/front", Vector2(512,384))
	# The compositor sizes only this owned window; never force Window.size here.
	await create_timer(2.0).timeout
	await pause()
	check(root.size == Vector2i(1440,900), "native_window", {"size":str(root.size)})
	for zoom: int in [1,2,3]:
		configure(zoom,Vector2(512,354),pitch)
		place(actor, Vector2(512,385))
		front.hide()
		var reference := red(await shot("z%d-reference" % zoom))
		front.show()
		var occlusion := Occlusion.new()
		occlusion.reset(fixtures.fade_groups)
		var outside_image := await shot("z%d-outside-1px" % zoom)
		var outside := red(outside_image)
		place(actor, Vector2(512,380))
		var inside := red(await shot("z%d-inside-4px" % zoom))
		check(reference>20 and outside>=reference*0.97,"outside_1px_zoom_%d" % zoom,{"reference":reference,"visible":outside})
		check(inside<=reference*0.02,"inside_4px_zoom_%d" % zoom,{"reference":reference,"visible":inside})
		settle(occlusion, [actor])
		var revealed := await readability("z%d-local-fade" % zoom, fixtures, [actor])
		check(revealed.reference_pixels>20 and revealed.coverage>=0.90,"local_mask_reveals_body_zoom_%d" % zoom,revealed)
		place(actor, Vector2(512,385))
		settle(occlusion, [actor])
		var restored := await shot("z%d-behind-no-fade" % zoom)
		check(restored.get_data()==outside_image.get_data(),"no_fade_behind_actor_zoom_%d" % zoom)
	configure(2,Vector2(512,354),pitch)
	var helper := Occlusion.new()
	var neighbor := wall(&"test/unrelated", Vector2(730,384), Vector2(80,320))
	place(actor, Vector2(512,380))
	helper.reset(fixtures.fade_groups)
	var solid := await shot("components-solid")
	settle(helper, [actor])
	var faded := await shot("components-selected")
	var far_solid := pixel(solid, Vector2(612,384), 36)
	var far_faded := pixel(faded, Vector2(612,384), 36)
	var near_solid := pixel(solid, Vector2(512,384), 24)
	var near_faded := pixel(faded, Vector2(512,384), 24)
	check(far_solid.is_equal_approx(far_faded) and near_solid.g - near_faded.g > 0.2,"only_body_occluding_portion_fades",{"far_solid":str(far_solid),"far_faded":str(far_faded),"near_solid":str(near_solid),"near_faded":str(near_faded)})
	var center := Vector2i(actor.occlusion_rect(camera).get_center())
	var feathered := 0
	for x: int in range(center.x + 16, center.x + 90):
		var before := solid.get_pixel(x, center.y)
		var after := faded.get_pixel(x, center.y)
		if before.g - after.g > 0.06 and before.g - after.g < 0.65:
			feathered += 1
	check(feathered >= 2, "local_mask_has_soft_edge", {"transition_pixels": feathered})
	check(pixel(solid,Vector2(730,384),36)==pixel(faded,Vector2(730,384),36)
		and not helper.strengths().has(&"test/unrelated"),"shared_material_unrelated_component_unchanged")
	front.rotation.y = PI
	await pause()
	helper.reset(fixtures.fade_groups)
	settle(helper, [actor])
	check(helper.strengths().has(&"test/front"), "double_sided_render_and_ray_agree")
	front.rotation.y = 0.0
	await pause()
	# More than one opaque layer must be collected in ONE query, not exposed
	# successively as a roof fades. A surface behind the actor is never selected.
	var middle := wall(&"test/middle", Vector2(512,388))
	var behind := wall(&"test/behind", Vector2(512,376))
	await pause()
	helper.reset(fixtures.fade_groups)
	settle(helper, [actor])
	var layers := helper.strengths()
	check(layers.has(&"test/front") and layers.has(&"test/middle") and not layers.has(&"test/behind"),"all_foreground_layers_not_background",{"groups":layers})
	var multi := await readability("multi-layer-fade",fixtures,[actor])
	check(multi.reference_pixels>20 and multi.coverage>=0.90,"multi_layer_body_readable",multi)
	front.hide()
	middle.hide()
	behind.hide()
	neighbor.hide()
	# Stable envelope, including the character's sides, not just its centerline.
	var stable: Array = actor.occlusion_points()
	var consistent := true
	for frame: int in range(actor.metadata.frames.size()):
		actor._set_frame(frame)
		consistent = consistent and actor.occlusion_points() == stable
	check(consistent and stable.size()==12,"stable_full_body_samples_all_animation_frames")
	actor._set_frame(0)
	var shoulder: Vector3 = stable.back()
	var origin := Projector.ray_origin(camera,shoulder)
	var intersection := shoulder.lerp(origin,(384*Projector.SCALE-shoulder.z)/(origin.z-shoulder.z))
	var side_x := intersection.x / Projector.SCALE
	var side := wall(&"test/shoulder", Vector2(side_x,384), Vector2(0.8,320))
	await pause()
	helper.reset(fixtures.fade_groups)
	settle(helper, [actor])
	check(helper.strengths().has(&"test/shoulder"),"side_only_occluder_detected")
	side.hide()
	# Alpha cutout holes are not solid, including separate building components.
	var cutout := wall(&"test/alpha-hole", Vector2(512,384))
	var empty := Image.create(4,4,false,Image.FORMAT_RGBA8)
	empty.fill(Color.TRANSPARENT)
	var transparent := material.duplicate() as ShaderMaterial
	transparent.set_shader_parameter("albedo_texture",ImageTexture.create_from_image(empty))
	cutout.material_override = transparent
	cutout.set_meta("alpha_image",empty)
	cutout.set_meta("alpha_quad",cutout.mesh.size)
	await pause()
	helper.reset(fixtures.fade_groups)
	settle(helper, [actor])
	check(helper.strengths().is_empty(),"alpha_holes_do_not_select_components")
	cutout.hide()
	front.show()
	second.show()
	place(actor, Vector2(505,380))
	place(second, Vector2(522,380))
	await pause()
	helper.reset(fixtures.fade_groups)
	settle(helper, [actor,second])
	var dual := await readability("dual-local-fade",fixtures,[actor,second])
	check(dual.reference_pixels>40 and dual.coverage>=0.90,"dual_target_shared_component",dual)
	front.show()
	place(actor, Vector2(505,385))
	settle(helper, [actor,second])
	check(is_equal_approx(float(helper.strengths().get(&"test/front",0.0)),1.0),"shared_component_waits_for_last_target")
	# Removing the current target releases it; a brief clear interval is held.
	settle(helper, [actor], 0.1)
	check(is_equal_approx(float(helper.strengths().get(&"test/front",0.0)),1.0),"restore_hysteresis_short_clear")
	settle(helper, [actor], 0.9)
	check(helper.strengths().is_empty(),"target_removal_restores_component")
	place(actor, Vector2(505,380))
	settle(helper, [actor], 0.06)
	var entering: float = helper.strengths().get(&"test/front",0.0)
	check(entering>0.0 and entering<1.0,"fade_in_is_continuous",{"strength":entering})
	settle(helper, [actor])
	helper.enabled = false
	settle(helper, [actor], 0.08)
	var leaving: float = helper.strengths().get(&"test/front",0.0)
	check(leaving>0.0 and leaving<1.0,"fade_out_is_continuous",{"strength":leaving})
	settle(helper, [actor])
	place(actor, Vector2(452,380))
	place(second, Vector2(572,380))
	await pause()
	helper.enabled = true
	helper.reset(fixtures.fade_groups)
	var dual_solid := await shot("dual-local-solid")
	settle(helper, [actor,second])
	var separated := await readability("dual-local-separated",fixtures,[actor,second])
	var dual_faded: Image = Image.load_from_file(output.path_join("dual-local-separated-visible.png"))
	check(separated.reference_pixels > 40 and separated.coverage >= 0.90 and pixel(dual_solid,Vector2(512,384),24).is_equal_approx(pixel(dual_faded,Vector2(512,384),24)),"two_local_masks_leave_wall_between_them_opaque",separated)
	settle(helper, [actor], 0.9)
	var first_only := await shot("dual-local-first-only")
	var weights: Vector2 = front.get_instance_shader_parameter("occlusion_weights")
	check(is_equal_approx(weights.x,1.0) and weights.y == 0.0 and pixel(first_only,Vector2(572,384),24).is_equal_approx(pixel(dual_solid,Vector2(572,384),24)),"removed_target_mask_restores_while_player_mask_stays",{"weights":str(weights)})
	helper.enabled = false
	settle(helper,[actor],0.9)
	second.hide()
	check(helper.strengths().is_empty() and red(await shot("disabled-solid"))==0,"disable_restores_real_occlusion")
	await _orbit_wall_checks()
	await _perspective_checks()
	await _house_checks()
	await _tree_checks()
	var file := FileAccess.open(output.path_join("probe.json"),FileAccess.WRITE)
	file.store_string(JSON.stringify({"device":RenderingServer.get_video_adapter_name(),"backend":RenderingServer.get_current_rendering_method(),
		"window":[root.size.x,root.size.y],"pitch":pitch,"fov":fov,"projection":"orthogonal" if orthogonal else "perspective","results":results,"passed":not failed,"occlusion_mode":"local_soft",
		"limits":"Geometry/local-translucency/readability probes; aesthetics, motion and real OS input need separate review"}))
	file.close()
	quit(1 if failed else 0)

func _orbit_wall_checks() -> void:
	for mesh: MeshInstance3D in fixtures.occluders:
		mesh.hide()
	var screen_wall := wall(&"test/orbit-upright", Vector2(512,384))
	var upright_material := material.duplicate() as ShaderMaterial
	upright_material.set_shader_parameter("upright", true)
	screen_wall.material_override = upright_material
	screen_wall.set_meta("alpha_image", Image.create(4,4,false,Image.FORMAT_RGBA8))
	var image: Image = screen_wall.get_meta("alpha_image")
	image.fill(Color.WHITE)
	screen_wall.set_meta("alpha_quad", screen_wall.mesh.size)
	var helper := Occlusion.new()
	for angle: Vector2 in [Vector2(Projector.PITCH,Projector.YAW),Vector2(30,-135),Vector2(65,45),Vector2(45,-90),Vector2(45,90),Vector2(45,180)]:
		var elevation := angle.x
		var azimuth := angle.y
		var back := Projector.ground_back(azimuth)
		var offset := Vector2(back.x,back.z)
		actor.set_pitch(elevation)
		actor.set_yaw(azimuth)
		screen_wall.rotation.y = deg_to_rad(azimuth)
		configure(2,Vector2(512,354),elevation,azimuth)
		var name := "orbit-p%d-y%d" % [int(elevation),int(azimuth)]
		place(actor,Vector2(512,384)+offset)
		screen_wall.hide()
		var reference := red(await shot(name+"-reference"))
		screen_wall.show()
		helper.reset(fixtures.fade_groups)
		var outside := red(await shot(name+"-outside-1px"))
		place(actor,Vector2(512,384)-offset*4.0)
		var inside := red(await shot(name+"-inside-4px"))
		check(reference>20 and outside>=reference*0.97 and inside<=reference*0.02,name+"-hard_geometry",{"reference":reference,"outside":outside,"inside":inside})
		settle(helper,[actor])
		var body := await readability(name+"-fade",fixtures,[actor])
		check(body.reference_pixels>20 and body.coverage>=0.90 and helper.strengths().has(&"test/orbit-upright"),name+"-upright_ray_alpha_fade",body)
	screen_wall.hide()
	actor.set_pitch(pitch)
	actor.set_yaw(0.0)

func _perspective_checks() -> void:
	for mesh: MeshInstance3D in fixtures.occluders:
		mesh.hide()
	actor.set_pitch(pitch)
	actor.set_yaw(0.0)
	configure(2,Vector2(512,354),pitch)
	place(actor,Vector2(620,380))
	var clear := await shot("perspective-off-axis-clear")
	var clear_pixels := red(clear)
	var bounds: Rect2 = actor.screen_rect(camera).grow(1.5)
	var contained := true
	for y: int in range(clear.get_height()):
		for x: int in range(clear.get_width()):
			var color := clear.get_pixel(x,y)
			if color.r > color.g+0.12 and color.r > color.b+0.12 and not bounds.has_point(Vector2(x,y)):
				contained = false
	check(clear_pixels>20 and contained,"perspective_body_inside_projected_four_corner_bounds",{"red_pixels":clear_pixels,"bounds":str(bounds)})
	if not orthogonal:
		place(actor,Vector2(620,284))
		var far_pixels := red(await shot("perspective-off-axis-far"))
		check(far_pixels>20 and clear_pixels>far_pixels*1.1,"perspective_far_actor_naturally_smaller",{"near_pixels":clear_pixels,"far_pixels":far_pixels})
		place(actor,Vector2(620,380))
		var origin := Projector.ray_origin(camera,actor.global_position)
		var intersection := actor.global_position.lerp(origin,(650*Projector.SCALE-actor.global_position.z)/(origin.z-actor.global_position.z))
		var screen_wall := wall(&"test/perspective-off-axis",Vector2(intersection.x/Projector.SCALE,650),Vector2(60,320))
		var helper := Occlusion.new()
		helper.reset(fixtures.fade_groups)
		var solid := red(await shot("perspective-off-axis-solid"))
		settle(helper,[actor])
		var body := await readability("perspective-off-axis-fade",fixtures,[actor])
		check(solid==0 and helper.strengths().has(&"test/perspective-off-axis") and body.reference_pixels>20 and body.coverage>=0.90,"perspective_off_axis_converging_rays_reveal_body",{"opaque_pixels":solid,"body":body})
		var clipped_clear := true
		for depth: float in [-1.0, camera.near*0.5, camera.far+1.0]:
			var clipped := camera.project_position(camera.unproject_position(actor.global_position),depth)
			clipped_clear = clipped_clear and helper._ray_groups(scene,camera,clipped).is_empty()
		check(clipped_clear,"clipped_or_behind_camera_targets_do_not_fade_components")
		screen_wall.hide()

func _house_checks() -> void:
	fixtures.hide()
	var world := World.new()
	world._ensure_grid()
	var house := Scenery.new()
	house.pitch = pitch
	scene.add_child(house)
	house.build(world)
	var helper := Occlusion.new()
	place(actor, Vector2(512,240))
	configure(2,Vector2(512,172),pitch)
	await pause()
	settle(helper,[actor],0.8,house.fade_groups)
	var selected := helper.strengths()
	check(selected.has(&"inn/structure") and house.fade_groups[&"inn/structure"].size()==1,"real_house_selects_merged_structure",{"groups":selected,"total_groups":house.fade_groups.size()})
	var coherent := true
	for key: StringName in house.fade_groups:
		var expected: float = selected.get(key,0.0)
		for mesh: MeshInstance3D in house.fade_groups[key]:
			coherent = coherent and is_equal_approx(float(mesh.get_instance_shader_parameter("occlusion_fade")),expected)
	check(coherent,"real_house_group_members_receive_local_mask_strength")
	for zoom: int in [1,2,3]:
		configure(zoom,Vector2(512,172),pitch)
		helper.reset(house.fade_groups)
		var solid := await shot("real-house-z%d-solid" % zoom)
		actor.sprite.hide()
		var solid_background := await shot("real-house-z%d-solid-background" % zoom)
		actor.sprite.show()
		var rect: Rect2i = Rect2i(actor.screen_rect(camera).grow(2)).intersection(Rect2i(Vector2i.ZERO,root.size))
		var unexpected_body := 0
		for y: int in range(rect.position.y,rect.end.y):
			for x: int in range(rect.position.x,rect.end.x):
				var a := solid.get_pixel(x,y)
				var b := solid_background.get_pixel(x,y)
				if maxf(absf(a.r-b.r),maxf(absf(a.g-b.g),absf(a.b-b.b)))>0.02:
					unexpected_body += 1
		check(unexpected_body==0,"real_house_closed_fade_blocks_body_zoom_%d" % zoom,{"visible_body_pixels":unexpected_body})
		settle(helper,[actor],0.8,house.fade_groups)
		var body := await readability("real-house-z%d" % zoom,house,[actor])
		check(body.reference_pixels>20 and body.coverage>=0.90,"real_house_full_body_zoom_%d" % zoom,body)
		var faded: Image = Image.load_from_file(output+"/real-house-z%d-visible.png" % zoom)
		# This distant roof pixel belongs to the SAME selected mesh, but lies
		# outside the local body mask and must retain its ordinary appearance.
		var sample: Dictionary = house.house_data.probes.rear_roof
		var local: Array = sample.local
		var roof_at: Vector2 = world.BUILDINGS[5].rect.get_center() + Vector2(float(local[0]), float(local[2]))
		var height := float(local[1])
		var before := pixel(solid,roof_at,height)
		var after := pixel(faded,roof_at,height)
		check(helper.strengths().has(StringName(sample.group)) and before.v>0.1 and before.is_equal_approx(after),"real_house_same_mesh_distant_roof_unchanged_zoom_%d" % zoom,{"solid":before,"faded":after})
	configure(2,Vector2(512,172),pitch)
	place(actor, Vector2(505,240))
	place(second, Vector2(540,244))
	second.show()
	await pause()
	helper.reset(house.fade_groups)
	settle(helper,[actor,second],0.8,house.fade_groups)
	var dual := await readability("real-house-dual",house,[actor,second])
	check(dual.reference_pixels>40 and dual.coverage>=0.90,"real_house_dual_full_body",dual)
	second.hide()
	place(actor, Vector2(512,422))
	await pause()
	settle(helper,[actor],0.9,house.fade_groups)
	var no_building := true
	for key: StringName in helper.strengths():
		no_building = no_building and not String(key).begins_with("inn/")
	check(no_building,"real_house_front_does_not_fade_building",{"groups":helper.strengths()})
	# Actual house from both sides and behind: never infer success only from
	# a white wall or from opening the fade assistance.
	actor.set_pitch(Projector.PITCH)
	for azimuth: float in [Projector.YAW,-90.0,90.0,180.0]:
		var back := Projector.ground_back(azimuth)
		var at := Vector2(512,320)-Vector2(back.x,back.z)*(112.0 if absf(azimuth)==90.0 else 80.0)
		actor.set_yaw(azimuth)
		house.set_pitch(Projector.PITCH)
		house.set_yaw(azimuth)
		place(actor,at)
		configure(2,at,Projector.PITCH,azimuth)
		await pause()
		helper.reset(house.fade_groups)
		var name := "real-house-y%d" % int(azimuth)
		var solid := await shot(name+"-solid")
		actor.sprite.hide()
		var bg := await shot(name+"-background")
		actor.sprite.show()
		var rect: Rect2i = Rect2i(actor.screen_rect(camera).grow(2)).intersection(Rect2i(Vector2i.ZERO,root.size))
		var leaks := 0
		for y: int in range(rect.position.y,rect.end.y):
			for x: int in range(rect.position.x,rect.end.x):
				var a := solid.get_pixel(x,y)
				var b := bg.get_pixel(x,y)
				if maxf(absf(a.r-b.r),maxf(absf(a.g-b.g),absf(a.b-b.b)))>0.02:
					leaks += 1
		check(leaks==0,name+"-hard_geometry",{"visible_body_pixels":leaks})
		settle(helper,[actor],0.8,house.fade_groups)
		var body := await readability(name+"-fade",house,[actor])
		check(body.reference_pixels>20 and body.coverage>=0.90,name+"-body_readable",body)
	world.free()
	house.hide()

func _tree_checks() -> void:
	var pine := Scenery.new()
	scene.add_child(pine)
	preload("res://scripts/hd2d/tree.gd").build(pine, Vector2(512,384), 0)
	for tree_material: Material in pine.materials.values():
		if tree_material is ShaderMaterial:
			tree_material.set_shader_parameter("sway",0.0)
	var helper := Occlusion.new()
	for orientation: Vector2 in [Vector2(38,30),Vector2(38,-60),Vector2(38,120),Vector2(38,-150),Vector2(30,30),Vector2(65,30)]:
		var back := Projector.ground_back(orientation.y)
		var behind := Vector2(512,384)-Vector2(back.x,back.z)*(55.0/tan(deg_to_rad(orientation.x)))
		actor.set_pitch(orientation.x)
		actor.set_yaw(orientation.y)
		place(actor,behind)
		configure(2,behind,orientation.x,orientation.y)
		await pause()
		helper.reset(pine.fade_groups)
		pine.hide()
		var label := "pine-p%d-y%d" % [int(orientation.x),int(orientation.y)]
		var reference := red(await shot(label+"-reference"))
		pine.show()
		var solid := red(await shot(label+"-solid"))
		check(reference>20 and solid<reference*0.80,label+"-real_volume_blocks_body",{"reference":reference,"visible":solid})
		settle(helper,[actor],0.8,pine.fade_groups)
		var body := await readability(label+"-fade",pine,[actor],true)
		check(helper.strengths().has(&"prop/tree/0") and body.coverage>=0.90,label+"-local_fade_readable",body)
	var back := Projector.ground_back(30)
	var front := Vector2(512,384)+Vector2(back.x,back.z)*70
	place(actor,front)
	configure(2,front,38,30)
	actor.set_pitch(38)
	actor.set_yaw(30)
	await pause()
	settle(helper,[actor],0.9,pine.fade_groups)
	check(helper.strengths().is_empty(),"pine_in_background_restores_both_meshes")
	pine.hide()
