extends Node3D
const Projector = preload("res://scripts/hd2d/projection.gd")
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
const CameraLimits = preload("res://scripts/hd2d/camera_limits.gd")
const Actor = preload("res://scripts/hd2d/actors.gd")
const ActorScene = preload("res://scenes/hd2d/actor.tscn")
const Occlusion = preload("res://scripts/hd2d/occlusion.gd")
var full_map := false
var profile_enabled := false
var profile_frames := 0
var profile_usec: Dictionary = {}
var _occlusion_actors: Array = []
var _occlusion_priority := 0

func _profile_stage(stage: String, since: int) -> int:
	if not profile_enabled:
		return 0
	var now := Time.get_ticks_usec()
	profile_usec[stage] = int(profile_usec.get(stage, 0)) + now - since
	return now

var population: Dictionary = {}
var actor_entries: Array[Dictionary] = []
var current_target := ""
var camera: Camera3D
var scenery: Node3D
var lighting: Node3D
var hero: Node3D
var target: Node3D
var occlusion = Occlusion.new()
var target_position := Vector2(540, 244)
var zoom := 2
var quality := "standard"
var applied_quality := ""
var focus := Vector2.ZERO
var initialized := false
var focus_height := 0.0
var last_actor_position := Vector2.ZERO
var foreground_clip_depth := -1.0
var pitch := Projector.fixture_pitch()
var yaw := Projector.fixture_yaw()
var fov := Projector.fixture_fov()
var orthogonal := Projector.fixture_orthogonal()

func setup(world: Node2D, use_full_map: bool = false) -> void:
	full_map = use_full_map
	if full_map:
		pitch = Projector.GAME_PITCH
		yaw = Projector.GAME_YAW
		fov = Projector.FOV
		orthogonal = false
	camera = $Camera
	lighting = $Lighting
	lighting.distance_fog = not full_map
	camera.attributes = lighting.attributes
	scenery = $Scenery
	hero = $Hero
	hero.scenic_terrain = full_map
	hero.set_outline_kind("hero")
	if not full_map:
		target = $Target
	else:
		_sync_population(world)
	set_pitch(pitch)
	set_yaw(yaw)

func set_pitch(value: float) -> void:
	pitch = clampf(value, Projector.MIN_PITCH, Projector.MAX_PITCH)
	for actor: Node3D in _all_actors():
		actor.set_pitch(pitch)
	scenery.set_pitch(pitch)

func set_yaw(value: float) -> void:
	yaw = wrapf(value, -180.0, 180.0)
	for actor: Node3D in _all_actors():
		actor.set_yaw(yaw)
	scenery.set_yaw(yaw)

func set_fov(value: float) -> void:
	fov = clampf(value, Projector.MIN_FOV, Projector.MAX_FOV)

func reset_camera() -> void:
	set_pitch(Projector.GAME_PITCH if full_map else Projector.PITCH)
	set_yaw(Projector.GAME_YAW if full_map else Projector.YAW)
	set_fov(Projector.FOV)

func update(world: Node2D, pixels: Vector2, game_seconds: float, delta: float, target_key: String = "") -> void:
	var profile_at := Time.get_ticks_usec() if profile_enabled else 0
	hero.sync(world.actor_pos, delta)
	if full_map:
		_sync_population(world, delta)
		current_target = target_key
		target = population.get(target_key)
	else:
		target.sync(target_position, delta)
	profile_at = _profile_stage("actors", profile_at)
	var back := Projector.ground_back(yaw)
	var wanted: Vector2 = world.actor_pos - Vector2(back.x, back.z) * 68.0
	if full_map:
		wanted = wanted.clamp(Layout.CAMERA_FOCUS_BOUNDS.position, Layout.CAMERA_FOCUS_BOUNDS.end)
	var wanted_height: float = hero.position.y / Projector.SCALE
	if initialized and world.actor_pos.distance_to(last_actor_position) > 128.0:
		reset_follow()
	_smooth_follow(wanted, wanted_height, delta)
	last_actor_position = world.actor_pos
	if full_map:
		focus = focus.clamp(Layout.CAMERA_FOCUS_BOUNDS.position, Layout.CAMERA_FOCUS_BOUNDS.end)
	if full_map:
		focus = CameraLimits.configure(camera, pixels, zoom, focus, pitch, yaw, orthogonal, fov, world.actor_pos, focus_height, wanted_height, hero.occlusion_bounds)
	else:
		Projector.configure(camera, pixels, zoom, focus, pitch, yaw, orthogonal, fov, world.actor_pos, focus_height, wanted_height)
	profile_at = _profile_stage("camera", profile_at)
	if full_map:
		# Clip only very close foreground surfaces; preserve the player and nearby cover.
		# A fraction of eye distance is bounded in world units. A hero-relative
		# 24-unit band wrongly cut peripheral roofs when zooming out to 1×.
		var clip_depth := minf(8.0, maxf(0.0, -camera.to_local(hero.global_position).z * 0.24))
		if absf(clip_depth - foreground_clip_depth) > 0.02:
			foreground_clip_depth = clip_depth
			for material: Material in scenery.materials.values():
				if material is ShaderMaterial:
					material.set_shader_parameter("foreground_clip_depth", clip_depth)
	if applied_quality != quality:
		RenderingServer.directional_shadow_atlas_set_size(2048 if quality == "standard" else 1024, true)
		applied_quality = quality
	profile_at = _profile_stage("clip_quality", profile_at)
	lighting.quality = quality
	lighting.update(game_seconds, scenery, -camera.to_local(hero.global_position).z, hero.global_position)
	if full_map:
		_update_sky(game_seconds)
	profile_at = _profile_stage("lighting", profile_at)
	# Player and current target first: they are ray-tested on every query.
	var actors: Array = [hero]
	if is_instance_valid(target):
		actors.append(target)
	var priority := actors.size()
	for actor: Node3D in population.values():
		if actor != target:
			actors.append(actor)
	_occlusion_actors = actors
	_occlusion_priority = priority
	occlusion.advance(actors, delta)
	_profile_stage("occlusion", profile_at)
	if profile_enabled:
		profile_frames += 1

func _physics_process(delta: float) -> void:
	if not initialized or not is_instance_valid(camera) or _occlusion_actors.is_empty():
		return
	occlusion.query(self, camera, _occlusion_actors, _occlusion_priority, delta)

func reset_follow() -> void:
	initialized = false

func _smooth_follow(wanted: Vector2, wanted_height: float, delta: float) -> void:
	if not initialized or delta <= 0:
		focus = wanted
		focus_height = wanted_height
	else:
		var weight := 1.0 - exp(-8.0 * delta)
		focus = focus.lerp(wanted, weight)
		focus_height = lerpf(focus_height, wanted_height, weight)
	initialized = true

func _update_sky(game_seconds: float) -> void:
	var hour := fposmod(game_seconds / 3600.0, 24.0)
	var daylight := smoothstep(5.0, 8.0, hour) * (1.0 - smoothstep(17.0, 20.0, hour))
	var color := Color("202d3b").lerp(Color("9daaa6"), daylight)
	lighting.environment.background_color = color
	lighting.environment.fog_light_color = color

func _all_actors() -> Array:
	var actors: Array = [hero]
	if full_map:
		actors.append_array(population.values())
	elif is_instance_valid(target):
		actors.append(target)
	return actors

func _population_actor(key: String, asset_key: String, legacy_path: String = "") -> Node3D:
	if population.has(key):
		return population[key]
	var actor := ActorScene.instantiate()
	actor.name = key.validate_node_name()
	actor.scenic_terrain = full_map
	if Actor.has_p3_asset(asset_key):
		actor.configure_p3(asset_key)
	else:
		actor.configure_legacy(load(legacy_path))
	$Population.add_child(actor)
	actor.set_pitch(pitch)
	actor.set_yaw(yaw)
	actor.set_outline_kind("mob" if key.begins_with("mob/") else "npc")
	population[key] = actor
	return actor

func _sync_population(world: Node2D, delta: float = 0.0) -> void:
	var keep: Dictionary = {}
	actor_entries.assign([{"key": "hero", "actor": hero, "name": world.actor_name, "kind": "hero"}])
	for npc: Dictionary in world.npcs:
		var key := "npc/" + str(npc.slug)
		var asset_key := str(npc.slug)
		var actor := _population_actor(key, asset_key, "res://assets/characters/%s.png" % str(npc.name))
		actor.sync(npc.pos, delta)
		keep[key] = true
		actor_entries.append({"key": key, "actor": actor, "name": str(npc.name), "kind": "npc",
			"bubble": str(npc.get("bubble", "")), "bubble_until": float(npc.get("bubble_until", 0.0))})
	for mob: Dictionary in world.mobs:
		if not bool(mob.get("alive", false)):
			continue
		var key := "mob/" + str(mob.id)
		var asset_key := str(mob.get("type", "wolf"))
		var actor := _population_actor(key, asset_key, "res://assets/mobs/%s.png" % asset_key)
		actor.sync(Vector2(float(mob.x), float(mob.y)), delta)
		keep[key] = true
		actor_entries.append({"key": key, "actor": actor,
			"name": "%s  Lv.%d" % [str(mob.get("name", "野狼")), int(mob.get("level", 1))], "kind": "mob"})
	for key: String in population.keys():
		if not keep.has(key):
			population[key].queue_free()
			population.erase(key)
