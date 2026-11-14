extends RefCounted
const Projector = preload("res://scripts/hd2d/projection.gd")
## Scenery is never made translucent. Rays against the baked visual shapes find
## the actors a building or tree (partly) hides; those enable a depth-inverted
## silhouette pass (actor_outline.gdshader), which draws only the covered pixels
## as a contour on top of the cover. Each actor fades and restores on its own.
const QUERY_INTERVAL := 1.0 / 30.0
## The player and current target are tested every query, the rest in turn.
const BACKGROUND_QUERIES := 4
const RESTORE_HOLD := 0.25
const FADE_IN_SECONDS := 0.12
const FADE_OUT_SECONDS := 0.30
const MAX_RAY_HITS := 128
var enabled := true
var _query_elapsed := QUERY_INTERVAL
var _states: Dictionary = {}
var _cursor := 0
## Opt-in counters: runtime rays and diagnostic sign/geometry rays stay separate.
var profile_enabled := false
var profile := {"batches": 0, "batch_usec": 0, "max_batch_usec": 0, "rays": 0, "diagnostic_rays": 0, "outside_physics": 0}
var _runtime_query := false

func _opaque_hit(mesh: Node3D, at: Vector3) -> bool:
	if not mesh.has_meta("alpha_image"):
		return true
	var image: Image = mesh.get_meta("alpha_image")
	var local := mesh.to_local(at)
	var uv: Vector2
	if mesh.has_meta("alpha_quad"):
		var dimensions: Vector2 = mesh.get_meta("alpha_quad")
		uv = Vector2(local.x / dimensions.x + 0.5, 0.5 - local.y / dimensions.y)
	else:
		var rect: Rect2 = mesh.get_meta("alpha_rect")
		uv = Vector2((local.x - rect.position.x) / rect.size.x,
			1.0 - (local.y - rect.position.y) / rect.size.y)
	var pixel := Vector2i(clampi(int(uv.x * image.get_width()), 0, image.get_width() - 1),
		clampi(int(uv.y * image.get_height()), 0, image.get_height() - 1))
	return image.get_pixelv(pixel).a >= 0.5

func _ray_groups(view: Node3D, camera: Camera3D, at: Vector3, first_only: bool = false) -> Dictionary:
	var found: Dictionary = {}
	var depth := -camera.to_local(at).z
	if depth <= camera.near or depth >= camera.far:
		return found
	var ray := PhysicsRayQueryParameters3D.create(Projector.ray_origin(camera, at), at, 2)
	ray.hit_back_faces = true
	for _layer: int in range(MAX_RAY_HITS):
		if profile_enabled:
			var counter := "rays" if _runtime_query else "diagnostic_rays"
			profile[counter] += 1
		var hit := view.get_world_3d().direct_space_state.intersect_ray(ray)
		if hit.is_empty():
			break
		var mesh: Node3D = hit.collider.get_parent()
		if mesh.is_visible_in_tree() and mesh.has_meta("fade_group") and _opaque_hit(mesh, hit.position):
			found[mesh.get_meta("fade_group")] = true
			if first_only:
				break
		# Continue through other meshes/alpha holes; no need to re-hit this RID.
		var excluded := ray.exclude
		excluded.append(hit.rid)
		ray.exclude = excluded
	return found

## Every scenery component in front of the actor. Diagnostics only.
func blocking_groups(view: Node3D, camera: Camera3D, actor: Node3D) -> Dictionary:
	var found: Dictionary = {}
	for at: Vector3 in actor.occlusion_points():
		found.merge(_ray_groups(view, camera, at))
	return found

func actor_blocked(view: Node3D, camera: Camera3D, actor: Node3D) -> bool:
	for at: Vector3 in actor.occlusion_points():
		if not _ray_groups(view, camera, at, true).is_empty():
			return true
	return false

func _visible(camera: Camera3D, actor: Node3D) -> bool:
	if camera.is_position_behind(actor.global_position):
		return false
	var size := Vector2(camera.get_viewport().get_visible_rect().size)
	return Rect2(Vector2.ZERO, size).grow(96.0).has_point(camera.unproject_position(actor.global_position))

## `actors[0 .. priority)` are tested every query (player, current target).
## Runtime callers must enter from _physics_process; the last result is cached
## between 30Hz queries. Visual strength still advances at the render rate.
func query(view: Node3D, camera: Camera3D, actors: Array, priority: int, delta: float) -> void:
	_query_elapsed += delta
	if not enabled:
		_query_elapsed = QUERY_INTERVAL
		for state: Dictionary in _states.values():
			state.blocked = false
	elif _query_elapsed >= QUERY_INTERVAL:
		var query_start := Time.get_ticks_usec() if profile_enabled else 0
		_runtime_query = true
		_query_elapsed = 0.0
		var pending: Array = actors.slice(0, priority)
		var rest := actors.size() - priority
		for _step: int in range(mini(BACKGROUND_QUERIES, rest)):
			_cursor = (_cursor + 1) % rest
			pending.append(actors[priority + _cursor])
		for actor in pending:
			if not is_instance_valid(actor) or actor.is_queued_for_deletion():
				continue
			var blocked := _visible(camera, actor) and actor_blocked(view, camera, actor)
			var id: int = actor.get_instance_id()
			if blocked and not _states.has(id):
				_states[id] = {"actor": actor, "strength": 0.0, "clear_time": RESTORE_HOLD, "blocked": true}
			elif _states.has(id):
				_states[id].blocked = blocked
		_runtime_query = false
		if profile_enabled:
			var elapsed := Time.get_ticks_usec() - query_start
			profile.batches += 1
			profile.batch_usec += elapsed
			profile.max_batch_usec = maxi(profile.max_batch_usec, elapsed)
			profile.outside_physics += int(not Engine.is_in_physics_frame())

func advance(actors: Array, delta: float) -> void:
	if not enabled:
		for state: Dictionary in _states.values():
			state.blocked = false
	for id: int in _states.keys():
		var state: Dictionary = _states[id]
		if not is_instance_valid(state.actor) or not actors.has(state.actor):
			if is_instance_valid(state.actor):
				state.actor.set_outline(0.0)
			_states.erase(id)
			continue
		state.clear_time = 0.0 if state.blocked else float(state.clear_time) + delta
		var goal := 1.0 if state.blocked or (enabled and float(state.clear_time) < RESTORE_HOLD) else 0.0
		state.strength = move_toward(float(state.strength), goal, delta / (FADE_IN_SECONDS if goal > 0.0 else FADE_OUT_SECONDS))
		state.actor.set_outline(float(state.strength))
		if float(state.strength) == 0.0 and not state.blocked:
			_states.erase(id)

## Outline strength per outlined actor node name.
func strengths() -> Dictionary:
	var result: Dictionary = {}
	for state: Dictionary in _states.values():
		if is_instance_valid(state.actor):
			result[str(state.actor.name)] = float(state.strength)
	return result

func reset() -> void:
	for state: Dictionary in _states.values():
		if is_instance_valid(state.actor):
			state.actor.set_outline(0.0)
	_states.clear()
	_query_elapsed = QUERY_INTERVAL
