extends Node3D
const Projector = preload("res://scripts/hd2d/projection.gd")
const PineTree = preload("res://tools/hd2d/tree_builder.gd")
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

func surface(key: String) -> ShaderMaterial:
	if materials.has(key):
		return materials[key]
	var material := ShaderMaterial.new()
	material.shader = FADE_SHADER
	material.set_shader_parameter("albedo_texture", load(ROOT + key + ".png"))
	materials[key] = material
	return material

func build(world: Node2D, full_map: bool = false) -> void:
	_ground(world)
	# P1: the inn is the single sample house, with its EXACT existing footprint.
	var r: Rect2 = world.BUILDINGS[5].rect
	_load_house(r)
	# Only the two visible P1 tree cells block walking. Both trees keep fixed
	# world geometry: a bent rooted trunk and closed modular bough volumes.
	var trees: Array[Vector2] = []
	for y: int in range(60):
		for x: int in range(100):
			if world._grid[y*100+x] == world.TREE:
				trees.append(Vector2(x*16+8,y*16+8))
	trees.sort_custom(func(a: Vector2,b: Vector2): return a.distance_squared_to(Vector2(512,410)) < b.distance_squared_to(Vector2(512,410)))
	if full_map:
		world_assets = preload("res://tools/hd2d/world_builder.gd").new()
		world_assets.build(self, world)
	else:
		for i: int in range(mini(2,trees.size())):
			PineTree.build(self, trees[i], i)
	for lamp: Dictionary in house_data.lanterns:
		var local: Array = lamp.local
		var at := r.get_center() + Vector2(float(local[0]), float(local[1]))
		var dimensions := Vector2(float(lamp.size[0]), float(lamp.size[1]))
		upright("灯笼", at, "lantern", dimensions, float(local[2]), 0.004, StringName(lamp.group))
		var light := OmniLight3D.new()
		light.name = "LanternLight_%d" % lanterns.size()
		light.position = Projector.point(at + Vector2(0, 6), float(local[2]) + 4)
		light.light_color = Color("ffc37c")
		light.omni_range = 8.0
		light.shadow_enabled = false
		add_child(light)
		lanterns.append(light)

func _load_house(footprint: Rect2) -> void:
	house_data = JSON.parse_string(FileAccess.get_file_as_string(ROOT + "buildings/inn.json"))
	assert(house_data.schema == 2 and is_equal_approx(float(house_data.logical_scale), Projector.SCALE))
	var expected: Array = house_data.footprint
	assert(footprint.size == Vector2(float(expected[2]), float(expected[3])), "Blender model must match the frozen inn footprint")
	var packed: PackedScene = load(ROOT + "buildings/inn.glb")
	house = packed.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	house.name = "客栈_Blender"
	house.position = Projector.point(footprint.get_center())
	add_child(house)
	var meshes := house.find_children("*", "MeshInstance3D", true, false)
	assert(meshes.size() == int(house_data.mesh_count), "Unexpected imported mesh count")
	# Static walls, roof and joinery share one mesh/ray shape. The shader's
	# per-fragment mask and body depth plane still reveal only foreground pixels.
	for mesh: MeshInstance3D in meshes:
		var component: Dictionary = house_data.components[String(mesh.name)]
		assert(mesh.mesh.get_surface_count() == component.materials.size(), "Imported material slots differ from the model")
		mesh.material_override = null
		for index: int in range(mesh.mesh.get_surface_count()):
			# Resolve by exported NAME, not primitive order (glTF can reorder slots).
			var imported: Material = mesh.mesh.surface_get_material(index)
			assert(imported != null and imported.resource_name.begins_with("XIA_P1_"), "Missing named GLB material")
			var key := imported.resource_name.trim_prefix("XIA_P1_")
			assert(component.materials.has(key), "Material is not part of its component")
			mesh.set_surface_override_material(index, _house_material(key))
		register_occluder(mesh, StringName(component.group))

func _house_material(key: String) -> ShaderMaterial:
	var cache_key := "house/" + key
	if materials.has(cache_key):
		return materials[cache_key]
	var spec: Dictionary = house_data.materials[key]
	var material := surface(String(spec.texture)).duplicate() as ShaderMaterial
	var rgb: Array = spec.tint
	material.set_shader_parameter("tint", Color(float(rgb[0]), float(rgb[1]), float(rgb[2])))
	if spec.has("color_mix"):
		material.set_shader_parameter("color_mix", float(spec.color_mix))
		material.set_shader_parameter("flat_color", Color(String(spec.flat_color)))
	materials[cache_key] = material
	return material

func _ground(world: Node2D) -> void:
	var plane := PlaneMesh.new()
	plane.size = Vector2(240, 200)
	var node := MeshInstance3D.new()
	node.name = "GrassBackdrop"
	node.mesh = plane
	node.position = Projector.point(Vector2(800, 480), -0.05)
	var mat := StandardMaterial3D.new()
	mat.resource_name = "grass_backdrop"
	mat.albedo_texture = load(ROOT+"materials/grass.png")
	mat.uv1_scale = Vector3(60, 50, 1)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS
	mat.roughness = 1.0
	node.material_override = mat
	add_child(node)
	# Merge all ground tiles into just two meshes, not 6000 scene nodes.
	for road: bool in [false, true]:
		var tool := SurfaceTool.new()
		tool.begin(Mesh.PRIMITIVE_TRIANGLES)
		for y: int in range(60):
			for x: int in range(100):
				var tag: int = world._grid[y*100+x]
				if (tag == world.ROAD) != road or (not road and tag != world.TOWN):
					continue
				var corners := [Vector3(x*16, 0, y*16), Vector3((x+1)*16, 0, y*16), Vector3((x+1)*16, 0, (y+1)*16), Vector3(x*16, 0, (y+1)*16)]
				for i: int in [0, 1, 2, 0, 2, 3]:
					tool.set_normal(Vector3.UP)
					tool.set_uv(Vector2(corners[i].x, corners[i].z) / 64.0)
					tool.add_vertex(corners[i] * Projector.SCALE)
		var ground := MeshInstance3D.new()
		ground.name = "Roads" if road else "TownGround"
		ground.mesh = tool.commit()
		var m := StandardMaterial3D.new()
		m.resource_name = "road" if road else "town_ground"
		m.albedo_texture = load(ROOT+"materials/road.png")
		m.albedo_color = Color.WHITE if road else Color("c7bda4")
		m.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS
		m.roughness = 1.0
		ground.material_override = m
		add_child(ground)

func upright(title: String, at: Vector2, asset: String, dimensions: Vector2, base: float, motion: float, group: StringName) -> void:
	var data: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(ROOT+"manifest.json"))
	var info: Dictionary = data.props[asset]
	var texture: Texture2D = load(ROOT+"props/"+asset+".png")
	var ratio := dimensions.y / float(info.bounds[3])
	var quad := QuadMesh.new()
	quad.size = Vector2(dimensions.x * Projector.SCALE, Projector.upright_height(texture.get_height()*ratio, pitch))
	var node := MeshInstance3D.new()
	node.name = title + "_" + String(group).replace("/", "_")
	node.mesh = quad
	var center_height := (float(info.foot[1])-texture.get_height()/2.0)*ratio
	node.position = Projector.point(at, base) + Vector3.UP * Projector.upright_height(center_height, pitch)
	var material := surface("props/"+asset)
	material.set_shader_parameter("upright", true)
	material.set_shader_parameter("sway", motion)
	material.set_shader_parameter("hanging", asset == "lantern")
	node.material_override = material
	node.set_meta("alpha_texture", texture)
	node.set_meta("alpha_quad", quad.size)
	node.set_meta("upright_origin", Projector.point(at, base))
	node.set_meta("upright_height", texture.get_height()*ratio)
	node.set_meta("upright_center", center_height)
	add_child(node)
	register_occluder(node, group)
	uprights.append(node)
	if asset == "lantern":
		lantern_material = material

func set_pitch(value: float) -> void:
	pitch = value
	for node: MeshInstance3D in uprights:
		var quad: QuadMesh = node.mesh
		quad.size.y = Projector.upright_height(float(node.get_meta("upright_height")), pitch)
		node.position = node.get_meta("upright_origin") + Vector3.UP * Projector.upright_height(float(node.get_meta("upright_center")), pitch)
		node.set_meta("alpha_quad", quad.size)
		var shape: CollisionShape3D = node.get_child(0).get_child(0)
		shape.shape = quad.create_trimesh_shape()
		shape.shape.backface_collision = true

func set_yaw(value: float) -> void:
	# The shader rotates the visible quad; rotate its CPU ray shape/alpha space
	# too. Buildings and all authoritative 2D obstacles stay unchanged.
	for node: MeshInstance3D in uprights:
		node.rotation.y = deg_to_rad(value)

func register_occluder(mesh: MeshInstance3D, group: StringName) -> void:
	assert(not group.is_empty(), "Every occluder needs an explicit ray group")
	mesh.set_meta("fade_group", group)
	if not fade_groups.has(group):
		fade_groups[group] = []
	fade_groups[group].append(mesh)
	mesh.set_instance_shader_parameter("occlusion_fade", 0.0)
	occluders.append(mesh)
	# Visual ray tests only, separate layer; never used for player movement.
	var body := StaticBody3D.new()
	body.name = "VisualOccluder"
	body.collision_layer = 2
	body.collision_mask = 0
	var shape := CollisionShape3D.new()
	shape.name = "RayShape"
	shape.shape = mesh.mesh.create_trimesh_shape()
	# Scenery is rendered double-sided. Jolt also needs the SHAPE flag;
	# ray.hit_back_faces alone does not expose the reverse side of a trimesh.
	shape.shape.backface_collision = true
	body.add_child(shape)
	mesh.add_child(body)

