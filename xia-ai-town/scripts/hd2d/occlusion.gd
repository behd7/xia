extends RefCounted
const Projector = preload("res://scripts/hd2d/projection.gd")
## Rays identify foreground surfaces; only their pixels near the protected body
## become translucent. Each target has its own mask, depth plane and hysteresis.
const QUERY_INTERVAL := 1.0 / 30.0
const RESTORE_HOLD := 0.25
const FADE_IN_SECONDS := 0.18
const FADE_OUT_SECONDS := 0.35
const MAX_RAY_HITS := 128
const MAX_TARGETS := 2
var enabled := true
var _query_elapsed := QUERY_INTERVAL
var _wanted: Dictionary = {}
var _states: Dictionary = {}

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
		var hit := view.get_world_3d().direct_space_state.intersect_ray(ray)
		if hit.is_empty():
			break
		var mesh: Node3D = hit.collider.get_parent()
		if mesh.is_visible_in_tree() and mesh.has_meta("fade_group") and _opaque_hit(mesh, hit.position):
			found[mesh.get_meta("fade_group")] = true
			if first_only:
				break
		# Selecting a merged mesh enables the mask on all its foreground faces.
		# Continue through other meshes/alpha holes; no need to re-hit this RID.
		var excluded := ray.exclude
		excluded.append(hit.rid)
		ray.exclude = excluded
	return found

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

func _slot() -> Dictionary:
	return {"strength": 0.0, "clear_time": RESTORE_HOLD, "mask": Vector4.ZERO, "plane": Vector4.ZERO}

func _mask(camera: Camera3D, actor: Node3D) -> Vector4:
	var rect: Rect2 = actor.occlusion_rect(camera)
	var padding := maxf(3.0, rect.size.y * 0.10)
	var half_size := rect.size * 0.5 + Vector2.ONE * padding
	return Vector4(rect.get_center().x, rect.get_center().y, half_size.x, half_size.y)

func _plane(actor: Node3D) -> Vector4:
	# The body is a vertical, fixed-Y plane. Test each scenery fragment against
	# that plane, rather than fading background faces of a selected mesh too.
	var normal := Projector.ground_back(float(actor.yaw))
	return Vector4(normal.x, normal.y, normal.z, normal.dot(actor.global_position))

func update(view: Node3D, camera: Camera3D, actors: Array, groups: Dictionary, delta: float) -> void:
	_query_elapsed += delta
	if not enabled:
		_wanted.clear()
		_query_elapsed = QUERY_INTERVAL
	elif _query_elapsed >= QUERY_INTERVAL:
		_query_elapsed = 0.0
		_wanted.clear()
		for index: int in range(mini(MAX_TARGETS, actors.size())):
			var actor: Node3D = actors[index]
			for key: StringName in blocking_groups(view, camera, actor):
				_wanted[key] = int(_wanted.get(key, 0)) | (1 << index)
		for key: StringName in _wanted:
			if groups.has(key) and not _states.has(key):
				_states[key] = [_slot(), _slot()]
	for key: StringName in _states.keys():
		var slots: Array = _states[key]
		var maximum := 0.0
		for index: int in range(MAX_TARGETS):
			var state: Dictionary = slots[index]
			var selected := enabled and (int(_wanted.get(key, 0)) & (1 << index)) != 0
			state.clear_time = 0.0 if selected else float(state.clear_time) + delta
			var hold := enabled and float(state.clear_time) < RESTORE_HOLD
			var goal := 1.0 if selected or hold else 0.0
			var seconds := FADE_IN_SECONDS if goal > 0.0 else FADE_OUT_SECONDS
			state.strength = move_toward(float(state.strength), goal, delta / seconds)
			maximum = maxf(maximum, float(state.strength))
			# Camera/actor motion updates every rendered frame, independently of
			# the 30Hz ray queries. A removed target restores at its last location.
			if index < actors.size():
				var actor: Node3D = actors[index]
				var depth := -camera.to_local(actor.global_position).z
				if depth > camera.near and depth < camera.far:
					state.mask = _mask(camera, actor)
					state.plane = _plane(actor)
		for mesh: MeshInstance3D in groups.get(key, []):
			mesh.set_instance_shader_parameter("occlusion_fade", maximum)
			mesh.set_instance_shader_parameter("occlusion_weights", Vector2(slots[0].strength, slots[1].strength))
			for index: int in range(MAX_TARGETS):
				mesh.set_instance_shader_parameter("occlusion_mask%d" % index, slots[index].mask)
				mesh.set_instance_shader_parameter("occlusion_plane%d" % index, slots[index].plane)
		if maximum == 0.0 and not _wanted.has(key):
			_states.erase(key)

func strengths() -> Dictionary:
	var result: Dictionary = {}
	for key: StringName in _states:
		var slots: Array = _states[key]
		result[key] = maxf(float(slots[0].strength), float(slots[1].strength))
	return result

func reset(groups: Dictionary) -> void:
	for meshes: Array in groups.values():
		for mesh: MeshInstance3D in meshes:
			mesh.set_instance_shader_parameter("occlusion_fade", 0.0)
			mesh.set_instance_shader_parameter("occlusion_weights", Vector2.ZERO)
			for index: int in range(MAX_TARGETS):
				mesh.set_instance_shader_parameter("occlusion_mask%d" % index, Vector4.ZERO)
				mesh.set_instance_shader_parameter("occlusion_plane%d" % index, Vector4.ZERO)
	_states.clear()
	_wanted.clear()
	_query_elapsed = QUERY_INTERVAL
