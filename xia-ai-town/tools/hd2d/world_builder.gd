extends RefCounted
## P3 geometry library. TownLayout alone supplies footprints and placement.
const Projector = preload("res://scripts/hd2d/projection.gd")
const Layout = preload("res://scripts/town_layout.gd")
const PineTree = preload("res://tools/hd2d/tree_builder.gd")
const PINE_SHADER = preload("res://scripts/hd2d/shaders/pine.gdshader")
const ROOT := "res://assets/hd2d/"
var scenery: Node3D
var data: Dictionary
var scenes: Dictionary = {}
var placed: Dictionary = {}
var _serial := 0

func build(owner_node: Node3D, world: Node2D) -> void:
	scenery = owner_node
	data = JSON.parse_string(FileAccess.get_file_as_string(ROOT + "environment.json"))
	assert(data.schema == 1 and is_equal_approx(float(data.logical_scale), Projector.SCALE))
	for building: Dictionary in Layout.BUILDINGS:
		if building.id == "inn":
			continue
		var key := "building/" + str(building.id)
		var rect: Rect2 = building.rect
		var spec: Dictionary = data.assets[key]
		assert(rect.size == Vector2(float(spec.footprint[2]), float(spec.footprint[3])), "P3 building must match TownLayout")
		var node := place(key, rect.get_center())
		node.name = str(building.name) + "_Blender"
		for lamp: Array in spec.lanterns:
			lantern(rect.get_center() + Vector2(float(lamp[0]), float(lamp[1])), float(lamp[2]))
	for furniture: Dictionary in Layout.furniture():
		var key := str(data.prop_keys[int(furniture.index)])
		var at: Vector2 = furniture.at + Vector2(16, 30)
		if key == "lantern":
			place("prop/lantern_post", at)
			lantern(at + Vector2(10, 0), 53)
		else:
			place("prop/" + key, at)
	# The well is its frozen 2x2 blocked cells, not the old 32px picture bounds.
	place("prop/well", Vector2(720, 416))
	place("town/gate", Vector2(800, 496))
	_walls()
	_pond()
	_yard()
	_wilderness(world)
	for x: int in range(24, 1552, 26):
		place("prop/bramble" if x % 3 == 0 else "prop/shrub", Vector2(x + 8, 56), 0.0, 0.65)

func material(key: String) -> ShaderMaterial:
	var cache_key := "p3/" + key
	if scenery.materials.has(cache_key):
		return scenery.materials[cache_key]
	var spec: Dictionary = data.materials[key]
	var mat: ShaderMaterial = scenery.surface(str(spec.texture)).duplicate()
	var rgb: Array = spec.tint
	mat.set_shader_parameter("tint", Color(float(rgb[0]), float(rgb[1]), float(rgb[2])))
	# Closed multi-surface assemblies can stack more foreground skins than the
	# P1 fixture. Use the same 1% residue as closed foliage for robust contrast
	# at 3x/low pitch on both GPUs; the mask and body-depth plane are unchanged.
	mat.set_shader_parameter("fade_residue", 0.01)
	if spec.has("color_mix"):
		mat.set_shader_parameter("color_mix", float(spec.color_mix))
		mat.set_shader_parameter("flat_color", Color(str(spec.flat_color)))
	if bool(spec.get("foliage", false)) or key == "bark":
		mat.shader = PINE_SHADER
		mat.set_shader_parameter("fade_residue", 0.01)
	if bool(spec.get("foliage", false)):
		mat.set_shader_parameter("volume_foliage", true)
		mat.set_shader_parameter("sway", 0.012)
	if bool(spec.get("water", false)):
		mat.set_shader_parameter("water_motion", true)
	if key == "cloth":
		mat.set_shader_parameter("foliage_sway", true)
		mat.set_shader_parameter("sway", 0.006)
	scenery.materials[cache_key] = mat
	return mat

func place(key: String, at: Vector2, angle: float = 0.0, size: float = 1.0) -> Node3D:
	assert(data.assets.has(key), "Unproduced P3 asset: " + key)
	var spec: Dictionary = data.assets[key]
	if not scenes.has(key):
		scenes[key] = load(ROOT + str(spec.path))
	var node: Node3D = scenes[key].instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	node.name = key.replace("/", "_") + "_%d" % _serial
	node.position = Projector.point(at)
	node.rotation.y = deg_to_rad(angle)
	node.scale = Vector3.ONE * size
	node.set_meta("asset_key", key)
	scenery.add_child(node)
	var group := StringName("p3/%s/%d" % [key, _serial])
	_serial += 1
	var meshes := node.find_children("*", "MeshInstance3D", true, false)
	assert(meshes.size() == int(spec.mesh_count), "P3 mesh count differs from manifest")
	for mesh: MeshInstance3D in meshes:
		var component: Dictionary = spec.components[String(mesh.name)]
		for index: int in range(mesh.mesh.get_surface_count()):
			var imported: Material = mesh.mesh.surface_get_material(index)
			assert(imported != null and imported.resource_name.begins_with("XIA_P3_"))
			var name := imported.resource_name.trim_prefix("XIA_P3_")
			assert(component.materials.has(name))
			mesh.set_surface_override_material(index, material(name))
		scenery.register_occluder(mesh, group)
	placed[key] = int(placed.get(key, 0)) + 1
	return node

func lantern(at: Vector2, base: float) -> void:
	scenery.upright("灯笼", at, "lantern", Vector2(13, 18), base, 0.004, StringName("p3/lantern/%d" % _serial))
	_serial += 1
	var light := OmniLight3D.new()
	light.name = "LanternLight_%d" % scenery.lanterns.size()
	light.position = Projector.point(at + Vector2(0, 4), base + 3)
	light.light_color = Color("ffc37c")
	light.omni_range = 5.0
	light.shadow_enabled = false
	scenery.add_child(light)
	scenery.lanterns.append(light)

func _tool() -> SurfaceTool:
	var result := SurfaceTool.new()
	result.begin(Mesh.PRIMITIVE_TRIANGLES)
	return result

func _box(tool: SurfaceTool, rect: Rect2, low: float, high: float) -> void:
	var source := BoxMesh.new()
	source.size = Vector3(rect.size.x, high - low, rect.size.y) * Projector.SCALE
	var arrays := source.surface_get_arrays(0)
	var points: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
	var indices: PackedInt32Array = arrays[Mesh.ARRAY_INDEX]
	var center := Projector.point(rect.get_center(), (low + high) * 0.5)
	for index: int in indices:
		var point := points[index] + center
		var normal := normals[index]
		var uv := Vector2(point.x, point.y)
		if absf(normal.y) > 0.5:
			uv = Vector2(point.x, point.z)
		elif absf(normal.x) > 0.5:
			uv = Vector2(point.z, point.y)
		tool.set_normal(normal)
		tool.set_uv(uv / (64.0 * Projector.SCALE))
		tool.add_vertex(point)

func _commit(tool: SurfaceTool, title: String, mat: ShaderMaterial, occluder: bool = true) -> MeshInstance3D:
	var node := MeshInstance3D.new()
	node.name = title
	node.mesh = tool.commit()
	node.material_override = mat
	scenery.add_child(node)
	if occluder:
		scenery.register_occluder(node, StringName("p3/terrain/" + title))
	return node

func _walls() -> void:
	for span: Vector2 in [Vector2(0, 752), Vector2(848, 1600)]:
		var start := span.x
		while start < span.y:
			var end := minf(start + 128, span.y)
			var wall := _tool()
			_box(wall, Rect2(start, 480, end - start, 32), 0, 38)
			_commit(wall, "城墙_%d" % int(start), material("stone"))
			var coping := _tool()
			_box(coping, Rect2(start, 479, end - start, 34), 38, 42)
			for x: int in range(int(start), int(end), 64):
				_box(coping, Rect2(x, 478, 6, 36), 42, 47)
			_commit(coping, "墙顶_%d" % int(start), material("stone"))
			start = end

func _pond() -> void:
	var stone := _tool()
	# All banks are inside the frozen water rectangle: nearby walking space is
	# never covered by a raised new border or a geometry-derived collision.
	for rect: Rect2 in [Rect2(1152, 384, 112, 3), Rect2(1152, 445, 112, 3),
		Rect2(1152, 387, 3, 58), Rect2(1261, 387, 3, 58)]:
		_box(stone, rect, 0, 5)
	_commit(stone, "池塘石岸", material("stone"))
	var water := _tool()
	_box(water, Rect2(1155, 387, 106, 58), 0.1, 0.4)
	_commit(water, "池塘细波", material("water"), false)

func _yard() -> void:
	var sand := _tool()
	_box(sand, Rect2(832, 80, 288, 192), 0.02, 0.08)
	var sand_material: ShaderMaterial = scenery.surface("materials/earth").duplicate()
	sand_material.set_shader_parameter("tint", Color("d6c292"))
	_commit(sand, "练武场夯土", sand_material, false)
	var boundary := _tool()
	for rect: Rect2 in [Rect2(832, 80, 288, 4), Rect2(832, 268, 288, 4),
		Rect2(832, 84, 4, 184), Rect2(1116, 84, 4, 184)]:
		_box(boundary, rect, 0.08, 0.3)
	_commit(boundary, "练武场石界", material("stone"), false)

func _wilderness(world: Node2D) -> void:
	var tree_count := 0
	var cliff_heights: Dictionary = {}
	for y: int in range(60):
		for x: int in range(100):
			var tag: int = world._grid[y * 100 + x]
			var noise := Layout.noise(x, y)
			var variant := int(noise / 6) % 2
			var at := Vector2(x * 16 + 8, y * 16 + 8)
			if tag == Layout.TREE:
				match noise % 6:
					0:
						PineTree.build(scenery, at, tree_count)
						tree_count += 1
					1: place("prop/cypress" if variant == 0 else "prop/bamboo", at, noise % 360)
					2: place("prop/bramble" if variant == 0 else "prop/thorn_bush", at, noise % 360)
					3: place("prop/flower_shrub" if variant == 0 else "prop/dry_grass", at, noise % 360)
					4: place("prop/log" if variant == 0 else "prop/stump", at, noise % 360)
					5: place("prop/bare_tree" if y > 46 else "prop/shrub", at, noise % 360)
			elif tag == Layout.CLIFF:
				# Tops stay over blocked cells. Neighbor sides are emitted only
				# above the neighbor height, avoiding stacked internal box faces.
				cliff_heights[Vector2i(x, y)] = 24.0 + maxf(0, y - 50) * 9.0 + float(noise % 3) * 4.0
	placed["shared/pine"] = tree_count
	var tools: Dictionary = {}
	for cell: Vector2i in cliff_heights:
		var x := float(cell.x * 16)
		var z := float(cell.y * 16)
		if cell.y < 51:
			# Scattered original rock obstacles are rounded masses contained in
			# their tile, not square columns. The continuous southern cliff uses
			# shared corner heights so its surface has no internal step walls.
			var rocks := ["boulder", "slab", "lichen_rock", "mossy_boulder", "outcrop"]
			place("prop/" + str(rocks[Layout.noise(cell.x, cell.y) % rocks.size()]), Vector2(x + 8, z + 8), cell.x * 23.0, 0.5)
			continue
		var chunk := Vector2i(cell.x / 8, cell.y / 8)
		if not tools.has(chunk):
			tools[chunk] = _tool()
		var tool: SurfaceTool = tools[chunk]
		var corners := [Vector3(x, _cliff_height(cell.x, cell.y), z),
			Vector3(x, _cliff_height(cell.x, cell.y + 1), z + 16),
			Vector3(x + 16, _cliff_height(cell.x + 1, cell.y + 1), z + 16),
			Vector3(x + 16, _cliff_height(cell.x + 1, cell.y), z)]
		_face(tool, corners)
		for direction: Vector2i in [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]:
			var neighbor := cell + direction
			if neighbor.y >= 51 and cliff_heights.has(neighbor):
				continue
			var a: Vector3
			var b: Vector3
			match direction:
				Vector2i.LEFT: a = corners[0]; b = corners[1]
				Vector2i.RIGHT: a = corners[2]; b = corners[3]
				Vector2i.UP: a = corners[3]; b = corners[0]
				Vector2i.DOWN: a = corners[1]; b = corners[2]
			_face(tool, [Vector3(a.x, 0, a.z), Vector3(b.x, 0, b.z), b, a])
	for chunk: Vector2i in tools:
		_commit(tools[chunk], "山崖_%d_%d" % [chunk.x, chunk.y], material("rock"))
	placed["terrain/cliff_cells"] = cliff_heights.size()

func _cliff_height(x: int, y: int) -> float:
	return 24.0 + maxf(0, y - 50) * 9.0 + sin(float(x) * 0.47) * 6.0 + sin(float(y) * 0.65 + float(x) * 0.3) * 4.0

func _face(tool: SurfaceTool, points: Array) -> void:
	# Godot's front faces wind clockwise, opposite Blender/glTF. Preserve the
	# outward normal and calculate each sloping cliff triangle independently.
	for triangle: Array in [[0, 2, 1], [0, 3, 2]]:
		var normal: Vector3 = (points[triangle[2]] - points[triangle[0]]).cross(points[triangle[1]] - points[triangle[0]]).normalized()
		for index: int in triangle:
			var point: Vector3 = points[index]
			var uv := Vector2(point.x, point.y)
			if absf(normal.y) > 0.5:
				uv = Vector2(point.x, point.z)
			elif absf(normal.x) > 0.5:
				uv = Vector2(point.z, point.y)
			tool.set_normal(normal)
			tool.set_uv(uv / 64.0)
			tool.add_vertex(point * Projector.SCALE)
