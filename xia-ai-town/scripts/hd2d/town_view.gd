extends Node3D
const Projector = preload("res://scripts/hd2d/projection.gd")
const Actor = preload("res://scripts/hd2d/actors.gd")
const ActorScene = preload("res://scenes/hd2d/actor.tscn")
const Occlusion = preload("res://scripts/hd2d/occlusion.gd")
var full_map := false
var population: Dictionary = {}
var actor_entries: Array[Dictionary] = []
var current_target := ""
var camera: Camera3D
var scenery: Node3D
var lighting: Node3D
var hero: Node3D
var target: Node3D
var occlusion = Occlusion.new()
var protect_target := false
var target_position := Vector2(540, 244)
var zoom := 2
var quality := "standard"
var applied_quality := ""
var focus := Vector2.ZERO
var initialized := false
var pitch := Projector.fixture_pitch()
var yaw := Projector.fixture_yaw()
var fov := Projector.fixture_fov()
var orthogonal := Projector.fixture_orthogonal()

func setup(world: Node2D, use_full_map: bool = false) -> void:
	full_map = use_full_map
	camera = $Camera
	lighting = $Lighting
	camera.attributes = lighting.attributes
	scenery = $Scenery
	hero = $Hero
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
	set_pitch(Projector.PITCH)
	set_yaw(Projector.YAW)
	set_fov(Projector.FOV)

func update(world: Node2D, pixels: Vector2, game_seconds: float, delta: float, target_key: String = "") -> void:
	hero.sync(world.actor_pos, delta)
	if full_map:
		_sync_population(world, delta)
		current_target = target_key
		target = population.get(target_key)
		protect_target = is_instance_valid(target)
	else:
		target.sync(target_position, delta)
	var back := Projector.ground_back(yaw)
	var wanted: Vector2 = world.actor_pos - Vector2(back.x, back.z) * 68.0
	focus = focus.lerp(wanted, minf(delta*8, 1)) if initialized else wanted
	initialized = true
	Projector.configure(camera, pixels, zoom, focus, pitch, yaw, orthogonal, fov, world.actor_pos)
	if applied_quality != quality:
		RenderingServer.directional_shadow_atlas_set_size(2048 if quality == "standard" else 1024, true)
		applied_quality = quality
	lighting.quality = quality
	lighting.update(game_seconds, scenery, -camera.to_local(hero.global_position).z, hero.global_position)
	var actors := [hero]
	if protect_target:
		actors.append(target)
	occlusion.update(self, camera, actors, scenery.fade_groups, delta)

func label_visible(actor: Node3D, protected: bool) -> bool:
	return (occlusion.enabled and protected) or not occlusion.actor_blocked(self, camera, actor)

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
	if Actor.has_p3_asset(asset_key):
		actor.configure_p3(asset_key)
	else:
		actor.configure_legacy(load(legacy_path))
	$Population.add_child(actor)
	actor.set_pitch(pitch)
	actor.set_yaw(yaw)
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
