extends Node3D
## Static nodes/resources are authored in *_scenery.tscn; only binding is runtime.
## `fade_group` metadata names a ray component for occlusion.gd; scenery itself
## is never faded.
const Projector = preload("res://scripts/hd2d/projection.gd")
const FADE_SHADER = preload("res://scripts/hd2d/shaders/scenery.gdshader")
const ROOT := "res://assets/hd2d/"
var materials: Dictionary = {}
var fade_groups: Dictionary = {}
var lanterns: Array[OmniLight3D] = []
var lantern_material: ShaderMaterial
var occluders: Array[MeshInstance3D] = []
var uprights: Array[MeshInstance3D] = []
var pitch := Projector.PITCH
var house_data: Dictionary = {}
var house: Node3D
var world_assets: RefCounted
var _material_copies: Dictionary = {}

func _ready() -> void:
	if has_meta("full_map"):
		_bind_scene()

func _bind_scene() -> void:
	bind_branch(self)
	house = find_child("客栈_Blender", true, false)
	if house != null:
		house_data = JSON.parse_string(FileAccess.get_file_as_string(ROOT + "buildings/inn.json"))
	if get_meta("full_map", false) == true:
		world_assets = preload("res://scripts/hd2d/world_assets.gd").new()
		world_assets.setup(self, get_meta("inventory", {}))

# Diagnostic fixtures can still request the static P1/full-map layout.
func build(_world: Node2D, full_map: bool = false) -> void:
	assert(get_child_count() == 0, "Scenery already contains an authored layout")
	var path := "res://scenes/hd2d/%s_scenery.tscn" % ("town" if full_map else "prototype")
	var branch: Node3D = load(path).instantiate()
	branch.set_script(null)
	add_child(branch)
	set_meta("full_map", full_map)
	set_meta("inventory", branch.get_meta("inventory", {}))
	_bind_scene()
	var requested_pitch := pitch
	pitch = Projector.PITCH
	set_pitch(requested_pitch)

func _instance_material(source: Material) -> Material:
	if not source is ShaderMaterial:
		return source
	var key := source.get_instance_id()
	if not _material_copies.has(key):
		# Share within this world, isolate glow/probe edits from other viewports.
		var copy := source.duplicate() as ShaderMaterial
		_material_copies[key] = copy
		materials[copy.resource_name] = copy
		if copy.resource_name.begins_with("p3_"):
			materials["p3/" + copy.resource_name.trim_prefix("p3_")] = copy
		if copy.get_shader_parameter("hanging") == true:
			lantern_material = copy
	return _material_copies[key]

func bind_branch(branch: Node3D, group_override: StringName = &"") -> void:
	for mesh: MeshInstance3D in branch.find_children("*", "MeshInstance3D", true, false):
		mesh.material_override = _instance_material(mesh.material_override)
		for index: int in range(mesh.mesh.get_surface_count()):
			mesh.set_surface_override_material(index, _instance_material(mesh.get_surface_override_material(index)))
		if mesh.has_meta("upright_origin"):
			# Camera-pitch compensation must not mutate the saved/shared quad.
			mesh.mesh = mesh.mesh.duplicate()
			var texture: Texture2D = mesh.get_meta("alpha_texture")
			mesh.set_meta("alpha_image", texture.get_image())
			uprights.append(mesh)
		if mesh.has_meta("fade_group"):
			register_occluder(mesh, group_override if not group_override.is_empty() else StringName(mesh.get_meta("fade_group")))
	for light: OmniLight3D in branch.find_children("*", "OmniLight3D", true, false):
		lanterns.append(light)

func set_pitch(value: float) -> void:
	if is_equal_approx(pitch, value):
		return # The saved quads/ray shapes already match the default pitch.
	pitch = value
	for node: MeshInstance3D in uprights:
		var quad: QuadMesh = node.mesh
		quad.size.y = Projector.upright_height(float(node.get_meta("upright_height")), pitch)
		node.position = node.get_meta("upright_origin") + Vector3.UP * Projector.upright_height(float(node.get_meta("upright_center")), pitch)
		node.set_meta("alpha_quad", quad.size)
		var shape: CollisionShape3D = node.get_node("VisualOccluder/RayShape")
		# Only upright quads change geometry when the camera pitch changes.
		shape.shape = quad.create_trimesh_shape()
		shape.shape.backface_collision = true

func set_yaw(value: float) -> void:
	for node: MeshInstance3D in uprights:
		node.rotation.y = deg_to_rad(value)

func register_occluder(mesh: MeshInstance3D, group: StringName) -> void:
	assert(not group.is_empty(), "Every occluder needs an explicit ray group")
	mesh.set_meta("fade_group", group)
	if not fade_groups.has(group):
		fade_groups[group] = []
	fade_groups[group].append(mesh)
	occluders.append(mesh)
	if mesh.has_node("VisualOccluder"):
		return
	# Procedural diagnostic walls only. The playable town has baked ray shapes.
	var body := StaticBody3D.new()
	body.name = "VisualOccluder"
	body.collision_layer = 2
	body.collision_mask = 0
	var shape := CollisionShape3D.new()
	shape.name = "RayShape"
	shape.shape = mesh.mesh.create_trimesh_shape()
	shape.shape.backface_collision = true
	body.add_child(shape)
	mesh.add_child(body)
