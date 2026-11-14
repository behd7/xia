extends RefCounted
## Offline scenic town assembly. Layout owns terrain, footprints and placement.
const Projector = preload("res://scripts/hd2d/projection.gd")
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
const PineTree = preload("res://tools/hd2d/tree_builder.gd")
const PINE_SHADER = preload("res://scripts/hd2d/shaders/pine.gdshader")
const ROOT := "res://assets/hd2d/"
var scenery: Node3D
var data: Dictionary
var scenes: Dictionary = {}
var placed: Dictionary = {}
var _serial := 0
var _pine_serial := 0

func build(owner_node: Node3D, world: Node2D) -> void:
	scenery = owner_node
	data = JSON.parse_string(FileAccess.get_file_as_string(ROOT + "environment.json"))
	assert(data.schema == 1 and is_equal_approx(float(data.logical_scale), Projector.SCALE))
	for building: Dictionary in Layout.BUILDINGS:
		var key := "building/" + ("inn_town" if building.id == "inn" else str(building.id))
		var rect: Rect2 = building.rect
		var spec: Dictionary = data.assets[key]
		assert(rect.size == Vector2(float(spec.footprint[2]), float(spec.footprint[3])), "P3 building must match TownLayout")
		var node := place(key, rect.get_center())
		node.name = str(building.name) + "_Blender"
		_building_sign(building)
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
	place("prop/well", Layout.WELL_AT)
	place("town/gate", Layout.GATE_AT)
	_walls()
	_pond(world)
	_yard()
	_wilderness(world)
	_backdrop()
	for x: int in [40, 66, 100, 432, 458, 488, 824, 850, 874, 1380, 1410, 1444]:
		place("prop/bramble" if x % 3 == 0 else "prop/shrub", Vector2(x + 8, 56), 0.0, 0.65)

func _building_sign(building: Dictionary) -> void:
	var spec: Dictionary = Layout.BUILDING_SIGNS[building.id]
	var offset: Vector3 = spec.offset
	var rect: Rect2 = building.rect
	var at := rect.get_center() + Vector2(offset.x, offset.z)
	var center := Projector.point(at, Layout.height_at(rect.get_center()) + offset.y)
	var board := place("prop/building_sign", at)
	board.name = "牌匾_" + str(building.id)
	board.position = center
	board.scale.x = float(spec.width) / 64.0
	board.set_meta("building_id", building.id)
	# Native text, physical depth test, no billboard and no per-frame rebuild.
	# Font selection matches the existing UI; shaping/fallback handles CJK.
	var font := SystemFont.new()
	font.font_names = PackedStringArray(["Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "Microsoft YaHei"])
	font.allow_system_fallback = true
	var label := Label3D.new()
	label.name = "牌匾文字_" + str(building.id)
	label.text = str(building.name)
	label.font = font
	label.font_size = 64
	label.pixel_size = Projector.SCALE * 18.0 / 64.0
	label.outline_size = 3
	label.modulate = Color("f6dfa2")
	label.outline_modulate = Color("302319")
	label.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
	label.billboard = BaseMaterial3D.BILLBOARD_DISABLED
	label.no_depth_test = false
	# Scenery has a near-camera alpha pass. Write glyph depth with cutout so
	# transparent mesh sorting cannot paint the wooden board over its letters.
	label.alpha_cut = Label3D.ALPHA_CUT_DISCARD
	label.alpha_scissor_threshold = 0.4
	label.shaded = false
	label.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	label.position = center + Vector3(0, 0, 2.8 * Projector.SCALE)
	label.set_meta("building_id", building.id)
	label.set_meta("logical_height", 18.0)
	label.set_meta("logical_width", spec.width)
	scenery.add_child(label)

func material(key: String) -> ShaderMaterial:
	var cache_key := "p3/" + key
	if scenery.materials.has(cache_key):
		return scenery.materials[cache_key]
	var spec: Dictionary = data.materials[key]
	var mat: ShaderMaterial = scenery.surface(str(spec.texture)).duplicate()
	var rgb: Array = spec.tint
	mat.set_shader_parameter("tint", Color(float(rgb[0]), float(rgb[1]), float(rgb[2])))
	mat.set_shader_parameter("glow", float(spec.get("glow", 0.0)))
	if spec.has("color_mix"):
		mat.set_shader_parameter("color_mix", float(spec.color_mix))
		mat.set_shader_parameter("flat_color", Color(str(spec.flat_color)))
	if bool(spec.get("foliage", false)) or key == "bark":
		mat.shader = PINE_SHADER
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
	node.position = Projector.point(at, Layout.height_at(at))
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
	base += Layout.height_at(at)
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
	var center_at := rect.get_center()
	var base_height := Layout.height_at(center_at) if Layout.VIEW_BOUNDS.has_point(center_at) else Layout.scenery_height_at(center_at)
	var center := Projector.point(center_at, base_height + (low + high) * 0.5)
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
	var wall := _tool()
	var coping := _tool()
	var piers := _tool()
	var west := Layout.WALL_INSET
	var east := Layout.WORLD_SIZE.x - Layout.WALL_INSET
	var north := Layout.WALL_INSET
	var south := Layout.WORLD_SIZE.y - Layout.WALL_INSET
	var gate_left := Layout.GATE_AT.x - Layout.WALL_GATE_WIDTH * 0.5
	var gate_right := Layout.GATE_AT.x + Layout.WALL_GATE_WIDTH * 0.5
	for rect: Rect2 in Layout.boundary_walls():
		var horizontal := rect.size.x > Layout.WALL_WIDTH
		_wall_run(wall, coping, rect.position.x if horizontal else rect.position.y,
			rect.end.x if horizontal else rect.end.y,
			rect.get_center().y if horizontal else rect.get_center().x, horizontal)
	var posts: Array[Vector2] = []
	for x: int in range(int(west), int(east) + 1, 128):
		posts.append(Vector2(x, north))
		if x + Layout.WALL_WIDTH * 0.5 <= gate_left or x - Layout.WALL_WIDTH * 0.5 >= gate_right:
			posts.append(Vector2(x, south))
	for y: int in range(int(north) + 128, int(south), 128):
		posts.append(Vector2(west, y))
		posts.append(Vector2(east, y))
	for at: Vector2 in [Vector2(east, north), Vector2(east, south),
		Vector2(gate_left - Layout.WALL_WIDTH * 0.5, south), Vector2(gate_right + Layout.WALL_WIDTH * 0.5, south)]:
		if not posts.has(at):
			posts.append(at)
	for at: Vector2 in posts:
		_wall_pier(piers, at)
	_commit(wall, "镇界城墙", material("stone"))
	_commit(coping, "镇界瓦压顶", material("roof"))
	_commit(piers, "镇界垛柱", material("stone"))

func _wall_run(wall: SurfaceTool, coping: SurfaceTool, start: float, finish: float, fixed: float, horizontal: bool) -> void:
	var cursor := start
	while cursor < finish:
		var end := minf(cursor + 32.0, finish)
		var rect := Rect2(cursor, fixed - Layout.WALL_WIDTH * 0.5, end - cursor, Layout.WALL_WIDTH) if horizontal else Rect2(fixed - Layout.WALL_WIDTH * 0.5, cursor, Layout.WALL_WIDTH, end - cursor)
		_box(wall, rect, -12.0, Layout.WALL_HEIGHT)
		_box(coping, rect.grow(2.0), Layout.WALL_HEIGHT, Layout.WALL_HEIGHT + 5.0)
		cursor = end

func _wall_pier(tool: SurfaceTool, at: Vector2) -> void:
	var size := Layout.WALL_WIDTH
	_box(tool, Rect2(at - Vector2.ONE * size * 0.5, Vector2.ONE * size), -12.0, Layout.WALL_HEIGHT + 14.0)

func _pond(world: Node2D) -> void:
	var water := _tool()
	var edge := _tool()
	for y: int in range(9, 27):
		for x: int in range(82, 97):
			if world._grid[y * 100 + x] != Layout.WATER:
				continue
			_box(water, Rect2(x*16, y*16, 16, 16), -1, -0.6)
			for d: Vector2i in [Vector2i.LEFT, Vector2i.RIGHT, Vector2i.UP, Vector2i.DOWN]:
				if world._grid[(y + d.y) * 100 + x + d.x] == Layout.WATER:
					continue
				var at := Vector2(x*16+8+d.x*6, y*16+8+d.y*6)
				place("prop/mossy_boulder" if (x+y)%3 == 0 else "prop/slab", at, x*19+y*37, 0.62)
	_commit(water, "听雨池水面", material("water"), false)
	# Low wooden landing stays on shore and is open to the main road.
	for x: int in range(1304, 1345, 8):
		_box(edge, Rect2(x, 288, 6.8, 48), 0, 2.2)
	_commit(edge, "池边木栈", material("wood_light"), false)

func _yard() -> void:
	var boundary := _tool()
	var r: Rect2 = Layout.YARD
	for rect: Rect2 in [Rect2(r.position, Vector2(r.size.x, 3)), Rect2(r.position.x, r.end.y-3, r.size.x, 3), Rect2(r.position, Vector2(3, r.size.y)), Rect2(r.end.x-3, r.position.y, 3, r.size.y)]:
		_box(boundary, rect, 0.08, 0.8)
	_commit(boundary, "演武坪石界", material("stone"), false)

func _wilderness(world: Node2D) -> void:
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
						PineTree.build(scenery, at, _pine_serial, Layout.height_at(at))
						_pine_serial += 1
					1: place("prop/cypress" if variant == 0 else "prop/bamboo", at, noise % 360)
					2: place("prop/bramble" if variant == 0 else "prop/thorn_bush", at, noise % 360)
					3: place("prop/flower_shrub" if variant == 0 else "prop/dry_grass", at, noise % 360)
					4: place("prop/log" if variant == 0 else "prop/stump", at, noise % 360)
					5: place("prop/bare_tree" if y > 46 else "prop/shrub", at, noise % 360)
			elif tag == Layout.CLIFF:
				var rocks := ["boulder", "slab", "lichen_rock", "mossy_boulder", "outcrop"]
				place("prop/" + str(rocks[noise % rocks.size()]), at, noise % 360, 0.5 + float(noise % 4) * 0.12)
				cliff_heights[Vector2i(x, y)] = true
	placed["shared/pine"] = _pine_serial
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

func _backdrop() -> void:
	var bounds: Rect2 = Layout.RIDGE_BOUNDS
	var ridge := _ridge_mesh(bounds, 48, Callable(self, "_eastern_ridge_height"))
	_commit(ridge, "东山岩脊", material("rock"))
	_eastern_forest()

	var near_material := material("rock").duplicate() as ShaderMaterial
	near_material.set_shader_parameter("tint", Color("87938b"))
	near_material.set_shader_parameter("color_mix", 0.28)
	near_material.set_shader_parameter("flat_color", Color("87938b"))
	scenery.materials["p3/distant_ridge_near"] = near_material
	var near_ridge := _ridge_mesh(Layout.DISTANT_RIDGE_NEAR, 192, Callable(self, "_distant_near_height"))
	var near_node := _commit(near_ridge, "远山前岭", near_material, false)
	near_node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF

	var far_material := material("rock").duplicate() as ShaderMaterial
	far_material.set_shader_parameter("tint", Color("6d7976"))
	far_material.set_shader_parameter("color_mix", 0.6)
	far_material.set_shader_parameter("flat_color", Color("6d7976"))
	scenery.materials["p3/distant_ridge_far"] = far_material
	var far_ridge := _ridge_mesh(Layout.DISTANT_RIDGE_FAR, 192, Callable(self, "_distant_far_height"))
	var far_node := _commit(far_ridge, "远山后岭", far_material, false)
	far_node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF

	# Small rock and flowering-shrub groups break up the older open margins.
	for group: Vector2 in [Vector2(420,168),Vector2(952,440),Vector2(360,896),Vector2(80,608)]:
		for offset: Vector2 in [Vector2(-22,-7),Vector2(16,12),Vector2(32,-20)]:
			var at := group + offset
			place("prop/mossy_boulder", at, at.x * 3.0, 0.8)
		for offset: Vector2 in [Vector2(-40,-25),Vector2(38,24)]:
			place("prop/flower_shrub", group + offset, offset.x * 5.0, 1.1)

func _ridge_mesh(bounds: Rect2, step: int, height_at: Callable) -> SurfaceTool:
	var ridge := _tool()
	for z: int in range(int(bounds.position.y), int(bounds.end.y), step):
		var next_z := mini(z + step, int(bounds.end.y))
		for x: int in range(int(bounds.position.x), int(bounds.end.x), step):
			var next_x := mini(x + step, int(bounds.end.x))
			var points: Array = []
			for at: Vector2 in [Vector2(x,z),Vector2(x,next_z),Vector2(next_x,next_z),Vector2(next_x,z)]:
				points.append(Vector3(at.x, float(height_at.call(at)), at.y))
			_face(ridge, points)
	return ridge

func _eastern_ridge_height(at: Vector2) -> float:
	return Layout.eastern_ridge_height_at(at)

func _distant_near_height(at: Vector2) -> float:
	return _distant_height(at, 2112.0, 2880.0, 4240.0, 5200.0, 252.0, 42.0, 0.0)

func _distant_far_height(at: Vector2) -> float:
	return _distant_height(at, 3936.0, 5100.0, 9280.0, 11500.0, 548.0, 86.0, 1.7)

func _distant_height(at: Vector2, start: float, crest_start: float, crest_end: float, finish: float, peak: float, amplitude: float, phase: float) -> float:
	var approach := smoothstep(start, crest_start, at.x)
	var retreat := 1.0 - smoothstep(crest_end, finish, at.x)
	var roughness := sin(at.y * 0.0031 + phase) * amplitude + cos(at.y * 0.0087 - phase) * amplitude * 0.42 + sin(at.x * 0.0043 + at.y * 0.0012) * amplitude * 0.3
	return Layout.scenery_height_at(at) + approach * retreat * maxf(96.0, peak + roughness)

func _eastern_forest() -> void:
	for row: int in range(7):
		for column: int in range(7):
			var noise := Layout.noise(column + 19, row + 31, 8)
			if noise % 7 == 6:
				continue
			var at := Vector2(1664 + column * 96 + noise % 33 - 16,
				128 + row * 112 + Layout.noise(column + 43, row + 57, 11) % 33 - 16)
			var elevation := Layout.eastern_ridge_height_at(at)
			match noise % 7:
				0, 1, 2:
					PineTree.build(scenery, at, _pine_serial, elevation)
					_pine_serial += 1
				3: _place_on_ridge("prop/cypress", at, noise % 360)
				4: _place_on_ridge("prop/bamboo", at, noise % 360)
				5: _place_on_ridge("prop/flower_shrub" if row % 2 == 0 else "prop/thorn_bush", at, noise % 360, 0.9)
				6: _place_on_ridge("prop/mossy_boulder" if column % 2 == 0 else "prop/outcrop", at, noise % 360, 0.8)
	placed["shared/pine"] = _pine_serial

func _place_on_ridge(key: String, at: Vector2, angle: float, size: float = 1.0) -> Node3D:
	var node := place(key, at, angle, size)
	node.position = Projector.point(at, Layout.eastern_ridge_height_at(at))
	return node
