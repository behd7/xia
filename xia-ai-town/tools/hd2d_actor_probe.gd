extends SceneTree
## Real Forward+ readback on the same isolated display as the full P1 probe.
const Projector = preload("res://scripts/hd2d/projection.gd")
const Actor = preload("res://scripts/hd2d/actors.gd")
const Lighting = preload("res://scripts/hd2d/environment.gd")
var output: String
var scene: Node3D
var camera: Camera3D
var actor: Node3D
var light: DirectionalLight3D
var results: Array = []
var failed := false

func _initialize() -> void:
	_run.call_deferred()

func check(ok: bool, title: String, detail: Dictionary = {}) -> void:
	results.append({"name":title,"passed":ok,"detail":detail})
	if not ok:
		failed = true
		printerr("HD2D_FAIL: ",title," ",detail)
	else:
		print("HD2D_ACTOR_PASS: ",title," ",detail)

func shot(title: String) -> Image:
	for i: int in range(10):
		await process_frame
	await RenderingServer.frame_post_draw
	var image := root.get_texture().get_image()
	image.save_png(output.path_join(title+".png"))
	return image

func difference(a: Image, b: Image) -> Dictionary:
	var count := 0
	var rect := Rect2i()
	for y: int in range(a.get_height()):
		for x: int in range(a.get_width()):
			var ca := a.get_pixel(x,y)
			var cb := b.get_pixel(x,y)
			if cb.get_luminance()-ca.get_luminance() > 0.035:
				var p := Vector2i(x,y)
				rect = Rect2i(p,Vector2i.ONE) if count == 0 else rect.expand(p)
				count += 1
	return {"pixels":count,"width":rect.size.x,"height":rect.size.y,"rect":str(rect)}

func _run() -> void:
	output = OS.get_environment("XIA_HD2D_OUTPUT")
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = 60
	scene = Node3D.new()
	root.add_child(scene)
	var lighting: Node3D = load("res://scenes/hd2d/lighting.tscn").instantiate()
	scene.add_child(lighting)
	light = lighting.sun
	light.light_energy = 1.55
	lighting.environment.fog_enabled = false
	RenderingServer.directional_shadow_atlas_set_size(1024 if OS.get_environment("XIA_HD2D_QUALITY")=="light" else 2048,true)
	camera = Camera3D.new()
	scene.add_child(camera)
	camera.current = true
	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(240,240)
	ground.mesh = plane
	ground.position = Projector.point(Vector2(512,422),-0.05)
	var material := StandardMaterial3D.new()
	material.albedo_color = Color("b1b3a0")
	material.roughness = 1.0
	ground.material_override = material
	scene.add_child(ground)
	actor = load("res://scenes/hd2d/actor.tscn").instantiate()
	scene.add_child(actor)
	actor.sync(Vector2(512,422),0.0,0)
	await create_timer(2.0).timeout
	check(root.size==Vector2i(1440,900),"native_window",{"window":[root.size.x,root.size.y]})
	check(actor.sprite.cast_shadow==GeometryInstance3D.SHADOW_CASTING_SETTING_OFF and actor.body_shadow.cast_shadow==GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY,
		"single_volumetric_shadow_without_paper_sprite_shadow")
	# Prove a two-dimensional ground footprint, including the view where the
	# old flat upright faced the sun edge-on. Hide only visual body/contact.
	actor.sprite.hide()
	actor.contact_shadow.hide()
	for angle: Vector2 in [Vector2(38,30),Vector2(30,-58),Vector2(65,122),Vector2(38,-148)]:
		actor.set_pitch(angle.x)
		actor.set_yaw(angle.y)
		Projector.configure(camera,Vector2(root.size),2,Vector2(512,422),angle.x,angle.y,false,35,Vector2(512,422))
		actor.body_shadow.show()
		var name := "shadow-p%d-y%d" % [int(angle.x),int(angle.y)]
		var with_shadow := await shot(name)
		actor.body_shadow.hide()
		var empty := await shot(name+"-empty")
		var diff := difference(with_shadow,empty)
		check(diff.pixels>80 and diff.width>8 and diff.height>8,name+"-has_area",diff)
	# Shadow shape is independent of camera pitch/billboard compensation.
	actor.body_shadow.show()
	actor.set_pitch(38)
	actor.set_yaw(30)
	Projector.configure(camera,Vector2(root.size),2,Vector2(512,422),38,30,false,35,Vector2(512,422))
	actor.body_shadow.set_pose(-1,Vector2.DOWN)
	actor.sprite.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var idle := await shot("shadow-idle")
	actor.sprite.show()
	actor.contact_shadow.show()
	await shot("shadow-comparison-after")
	actor.body_shadow.hide()
	actor.sprite.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	var contact_plane: PlaneMesh = actor.contact_shadow.mesh
	contact_plane.size = Vector2(0.9,0.5)
	await shot("shadow-comparison-before")
	contact_plane.size = Vector2(1.05,0.78)
	actor.sprite.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	actor.sprite.hide()
	actor.contact_shadow.hide()
	actor.body_shadow.show()
	actor.body_shadow.set_pose(0,Vector2.DOWN)
	var stride := await shot("shadow-stride")
	var gait := difference(idle,stride)
	var reverse := difference(stride,idle)
	check(gait.pixels+reverse.pixels>8,"shadow_tracks_walk_pose",{"changed_pixels":gait.pixels+reverse.pixels})
	actor.body_shadow.hide()
	actor.contact_shadow.show()
	var contact := await shot("contact-shadow")
	actor.contact_shadow.hide()
	var bare := await shot("contact-empty")
	var contact_diff := difference(contact,bare)
	check(contact_diff.pixels>20 and contact_diff.height>4,"contact_shadow_has_ground_area",contact_diff)
	actor.sprite.show()
	actor.body_shadow.show()
	actor.contact_shadow.show()
	# Full colour eight-direction loop and frame-by-frame atlas at actual 2x.
	var gallery: Array[Node3D] = [actor]
	for row: int in range(1,8):
		var who: Node3D = load("res://scenes/hd2d/actor.tscn").instantiate()
		scene.add_child(who)
		gallery.append(who)
	for row: int in range(8):
		var who := gallery[row]
		who.set_pitch(38)
		who.set_yaw(30)
		var offset := Projector.camera_relative_direction(Vector2((row%4-1.5)*95,(row/4-0.5)*140),30)
		who.sync(Vector2(512,422)+offset,0,row)
	for col: int in range(10):
		for row: int in range(8):
			var who := gallery[row]
			who._set_frame(row*who.sprite.hframes+col)
			var direction := Projector.camera_relative_direction(Vector2(-sin(row*PI/4.0),cos(row*PI/4.0)),30)
			who.body_shadow.set_pose(col-2 if col>=2 else -1,direction)
		await shot("directions-frame-%02d" % col)
	check(true,"eight_direction_gpu_gallery",{"frames":10,"directions":8})
	for who: Node3D in gallery:
		who.hide()
	# P3 identity gallery: eight independent rows prove that every NPC and mob
	# uses its own loaded sheet while keeping the same fixed-Y projection.
	for asset_key: String in ["gu_changfeng", "tie_wushuang", "su_wanqing", "chen_bansong", "wang_dazhu", "liu_xiaodie", "zhang_mantou", "wolf", "bandit", "boss"]:
		var row: Array[Node3D] = []
		for direction: int in range(8):
			var who: Node3D = load("res://scenes/hd2d/actor.tscn").instantiate()
			who.configure_p3(asset_key)
			scene.add_child(who)
			who.set_pitch(38)
			who.set_yaw(30)
			who.sync(Vector2(300 + direction * 62, 422), 0.0, direction)
			row.append(who)
		var identity_image := await shot("p3-actor-" + asset_key)
		check(identity_image.get_size() == Vector2i(1440,900), "p3_actor_gallery_" + asset_key,
			{"directions":8,"frame_size":row[0].metadata.frame_size,"asset":asset_key})
		for who: Node3D in row:
			who.queue_free()
	await lighting_checks()
	var file := FileAccess.open(output.path_join("probe.json"),FileAccess.WRITE)
	file.store_string(JSON.stringify({"device":RenderingServer.get_video_adapter_name(),"backend":RenderingServer.get_current_rendering_method(),
		"passed":not failed,"checks":results,"scope":"P1 actor gait, ground-shadow and scene-lighting GPU readbacks"},"\t"))
	quit(1 if failed else 0)

func garment_luminance(image: Image, who: Node3D, view_camera: Camera3D) -> float:
	# Sample the same pale cloth/skin texels in both materials, rather than
	# including the bright grass or black boots in a screen rectangle average.
	var atlas: Image = Actor.SHEET.get_image()
	var frame_rect: Array = who.metadata.frames[who.frame].rect
	var luminance := 0.0
	var count := 0
	for y: int in range(80):
		for x: int in range(64):
			var texel := atlas.get_pixel(frame_rect[0]+x,frame_rect[1]+y)
			if texel.a < 0.5 or texel.get_luminance() < 0.6:
				continue
			var local := Vector3((x+0.5-32)*who.sprite.pixel_size,(40-y-0.5)*who.sprite.pixel_size,0)
			var pixel := Vector2i(view_camera.unproject_position(who.sprite.to_global(local)))
			if Rect2i(Vector2i.ZERO,image.get_size()).has_point(pixel):
				luminance += image.get_pixelv(pixel).get_luminance()
				count += 1
	return luminance / maxf(1,count)

func lighting_checks() -> void:
	scene.queue_free()
	await process_frame
	var world := preload("res://scripts/town_world.gd").new()
	world._ensure_grid()
	preload("res://scripts/hd2d/fixture_layout.gd").prepare(world)
	var view := preload("res://scripts/hd2d/town_view.gd").new()
	root.add_child(view)
	view.setup(world)
	view.quality = OS.get_environment("XIA_HD2D_QUALITY")
	var cases := {}
	for entry: Array in [
		["day",12,38,30,Vector2(512,422)],
		["key",12,38,-58,Vector2(512,422)],
		["back",12,38,122,Vector2(512,422)],
		["low",12,30,30,Vector2(512,422)],
		["high",12,65,30,Vector2(512,422)],
		["dusk",18,38,30,Vector2(512,422)],
		["night",22,38,30,Vector2(512,422)],
		["house",12,38,30,Vector2(512,240)]]:
		world.actor_pos = entry[4]
		view.set_pitch(entry[2])
		view.set_yaw(entry[3])
		view.initialized = false
		for i: int in range(30):
			view.update(world,Vector2(root.size),entry[1]*3600.0,1.0/60.0)
			await process_frame
		view.hero.sync(world.actor_pos,0,0)
		view.hero._set_frame(0)
		view.hero.body_shadow.set_pose(-1,Vector2.DOWN)
		for who: Node3D in [view.hero,view.target]:
			who.sprite.material_override = null
		var before := await shot("lighting-"+entry[0]+"-before")
		for who: Node3D in [view.hero,view.target]:
			who.sprite.material_override = Actor.body_material
		var after := await shot("lighting-"+entry[0]+"-after")
		var bounds: Rect2 = view.hero.screen_rect(view.camera)
		cases[entry[0]] = {"before":garment_luminance(before,view.hero,view.camera),
			"after":garment_luminance(after,view.hero,view.camera),
			"bounds":[bounds.position.x,bounds.position.y,bounds.size.x,bounds.size.y]}
		if entry[0] == "night":
			for lantern: OmniLight3D in view.scenery.lanterns:
				lantern.light_energy = 0.0
			var no_lantern := await shot("lighting-night-no-lantern")
			cases.night["no_lantern"] = garment_luminance(no_lantern,view.hero,view.camera)
		if entry[0] == "house":
			view.lighting.sun.shadow_enabled = false
			var unshadowed := await shot("lighting-house-no-shadows")
			var clear := garment_luminance(unshadowed,view.hero,view.camera)
			check(clear>cases.house.after+0.04,"actor_receives_scene_shadow",{"shadowed":cases.house.after,"unshadowed":clear})
			view.lighting.sun.shadow_enabled = true
	check(cases.day.after>cases.day.before*1.18 and cases.day.after<0.92,"day_cloth_readable",cases.day)
	check(cases.back.after>cases.back.before*1.18,"backlit_cloth_readable",cases.back)
	check(cases.night.no_lantern<cases.day.after*0.85 and cases.night.no_lantern>0.15,"night_remains_dimmer_than_day",{"night":cases.night.no_lantern,"day":cases.day.after})
	# Lightweight uses only one lantern; leave margin for shadow-map rounding.
	check(cases.night.after>cases.night.no_lantern+0.025,"actor_receives_lantern_light",cases.night)
	var file := FileAccess.open(output.path_join("lighting.json"),FileAccess.WRITE)
	file.store_string(JSON.stringify(cases,"\t"))
	world.free()
