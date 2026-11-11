extends RefCounted
## Rooted bent trunk + closed needle-cluster volumes; no foliage planes.
const Projector = preload("res://scripts/hd2d/projection.gd")
const ROOT := "res://assets/hd2d/"
const PINE_SHADER = preload("res://scripts/hd2d/shaders/pine.gdshader")

static func _material(scenery: Node3D, key: String, spec: Dictionary) -> ShaderMaterial:
	var cache_key := "pine/" + key
	if scenery.materials.has(cache_key):
		return scenery.materials[cache_key]
	var material: ShaderMaterial = scenery.surface(String(spec.texture)).duplicate()
	material.shader = PINE_SHADER
	var rgb: Array = spec.tint
	material.set_shader_parameter("tint", Color(float(rgb[0]), float(rgb[1]), float(rgb[2])))
	material.set_shader_parameter("vertex_color", true)
	material.set_shader_parameter("fade_residue", 0.01)
	if key == "needles":
		material.set_shader_parameter("volume_foliage", true)
		material.set_shader_parameter("sway", 0.018)
	scenery.materials[cache_key] = material
	return material

static func build(scenery: Node3D, at: Vector2, index: int, elevation: float = 0.0) -> void:
	var group := StringName("prop/tree/%d" % index)
	var data: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(ROOT+"props/pine.json"))
	assert(data.schema == 1 and is_equal_approx(float(data.logical_scale), Projector.SCALE))
	var source: PackedScene = load(ROOT+"props/pine.glb")
	var tree := source.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE) as Node3D
	tree.name = "松树%d_叶簇体积" % index
	tree.position = Projector.point(at, elevation)
	tree.rotation.y = deg_to_rad(index*43.0)
	scenery.add_child(tree)
	var parts := tree.find_children("*", "MeshInstance3D", true, false)
	assert(parts.size() == int(data.mesh_count), "Pine mesh count differs from its builder")
	for mesh: MeshInstance3D in parts:
		var component: Dictionary = data.components[String(mesh.name)]
		var key := String(component.material)
		var extent := mesh.mesh.get_aabb().size
		assert(mesh.mesh.get_surface_count() == 1 and minf(extent.x, minf(extent.y, extent.z)) > 0.0)
		mesh.material_override = _material(scenery, key, data.materials[key])
		mesh.set_meta("volume_foliage", bool(component.volume_foliage))
		scenery.register_occluder(mesh, group)
