extends RefCounted
## Offline terrain authoring: height field, shallow paving relief and grass chunks.
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
const Projector = preload("res://scripts/hd2d/projection.gd")
const ROOT := "res://assets/hd2d/"

static func _material(texture: String, color: Color, name: String) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.resource_name = name
	mat.albedo_texture = load(ROOT + "materials/" + texture + ".png")
	mat.albedo_color = color
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST_WITH_MIPMAPS
	mat.roughness = 0.92
	mat.vertex_color_use_as_albedo = true
	return mat

static func _tool() -> SurfaceTool:
	var tool := SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	return tool

static func _quad(tool: SurfaceTool, points: Array, color: Color = Color.WHITE) -> void:
	for tri: Array in [[0, 1, 2], [0, 2, 3]]:
		var normal: Vector3 = (points[tri[2]] - points[tri[0]]).cross(points[tri[1]] - points[tri[0]]).normalized()
		for index: int in tri:
			var point: Vector3 = points[index]
			tool.set_normal(normal)
			tool.set_color(color)
			tool.set_uv(Vector2(point.x, point.z) / 64.0)
			tool.add_vertex(point * Projector.SCALE)

static func _tile(tool: SurfaceTool, rect: Rect2, lift: float, color: Color = Color.WHITE) -> void:
	var points: Array = []
	for at: Vector2 in [rect.position, rect.position + Vector2(rect.size.x, 0), rect.end, rect.position + Vector2(0, rect.size.y)]:
		points.append(Vector3(at.x, Layout.height_at(at) + lift, at.y))
	_quad(tool, points, color)

static func _commit(parent: Node3D, tool: SurfaceTool, name: String, material: Material) -> void:
	var node := MeshInstance3D.new()
	node.name = name
	node.mesh = tool.commit()
	node.material_override = material
	parent.add_child(node)

static func build(parent: Node3D, world: Node2D) -> void:
	var grass := _tool()
	var earth := _tool()
	var road_bed := _tool()
	var stone := _tool()
	# The town keeps its original logical extent. Render-only ground outside
	# its walls prevents a perspective or rotated view from exposing a cut edge.
	_scenic_apron(grass)
	for y: int in range(60):
		for x: int in range(100):
			var rect := Rect2(x * 16, y * 16, 16, 16)
			var tag: int = world._grid[y * 100 + x]
			if tag == Layout.WATER:
				continue
			var tone := 0.96 + 0.025 * sin(x * 0.16) * sin(y * 0.23)
			_tile(grass, rect, -0.2, Color(tone, tone, tone))
			if tag in [Layout.BUILDING, Layout.TOWN, Layout.SAND]:
				_tile(earth, rect, 0.0)
			elif tag == Layout.ROAD:
				_tile(road_bed, rect, 0.0)
			if tag == Layout.ROAD:
				# Running bond, small gaps and raised bevels catch light at grazing angles.
				for row: int in range(2):
					for column: int in range(2 if row == 1 else 1):
						var cell := Rect2(rect.position + Vector2(column * 8, row * 8), Vector2(8 if row == 1 else 16, 8))
						var noise := Layout.noise(x * 2 + column, y * 2 + row)
						var lift := 0.6 + float(noise % 5) * 0.07
						var tile := cell.grow(-0.22)
						var inner := tile.grow(-0.35)
						var shade := 0.88 + float(noise % 7) * 0.022
						_tile(stone, inner, lift, Color(shade, shade, shade))
						var outside: Array = []
						var inside: Array = []
						for r: Rect2 in [tile, inner]:
							var list: Array = outside if r == tile else inside
							for at: Vector2 in [r.position, r.position + Vector2(r.size.x, 0), r.end, r.position + Vector2(0, r.size.y)]:
								list.append(Vector3(at.x, Layout.height_at(at) + (0.1 if r == tile else lift), at.y))
						for side: int in range(4):
							var next := (side + 1) % 4
							_quad(stone, [outside[side], outside[next], inside[next], inside[side]], Color(shade, shade, shade))
	_commit(parent, grass, "缓坡草地", _material("grass", Color("a3b69a"), "scenic_grass"))
	_commit(parent, earth, "街巷夯土", _material("earth", Color("b6b097"), "scenic_earth"))
	_commit(parent, road_bed, "石路缝底", _material("stone", Color("647168"), "scenic_road_bed"))
	_commit(parent, stone, "青石板路", _material("stone", Color("a7b0aa"), "scenic_paving"))
	_grasses(parent, world)

static func _apron_axis(length: int) -> PackedInt32Array:
	var axis := PackedInt32Array([-8192, -4096, -2048, -1024, -512, -256, -128, -64])
	for value: int in range(0, length + 1, 16):
		axis.append(value)
	for margin: int in [64, 128, 256, 512, 1024, 2048, 4096, 8192]:
		axis.append(length + margin)
	return axis

static func _scenic_apron(tool: SurfaceTool) -> void:
	var xs := _apron_axis(int(Layout.WORLD_SIZE.x))
	var ys := _apron_axis(int(Layout.WORLD_SIZE.y))
	for y: int in range(ys.size() - 1):
		for x: int in range(xs.size() - 1):
			if xs[x] >= 0 and xs[x + 1] <= Layout.WORLD_SIZE.x and ys[y] >= 0 and ys[y + 1] <= Layout.WORLD_SIZE.y:
				continue
			var points: Array = []
			for at: Vector2 in [Vector2(xs[x], ys[y]), Vector2(xs[x + 1], ys[y]),
				Vector2(xs[x + 1], ys[y + 1]), Vector2(xs[x], ys[y + 1])]:
				points.append(Vector3(at.x, Layout.scenery_height_at(at), at.y))
			_quad(tool, points, Color(0.96, 0.96, 0.96))

static func _grass_mesh() -> ArrayMesh:
	var tool := _tool()
	# Five tapered, bent blades. No alpha texture or camera-facing cross cards.
	for blade: int in range(5):
		var angle := float(blade) * 2.4
		var side := Vector3(cos(angle), 0, sin(angle)) * 0.9
		var root := Vector3(sin(angle) * 2.4, 0, cos(angle) * 2.4)
		var middle := root + Vector3(sin(angle) * 1.1, 3.8 + blade * 0.4, cos(angle) * 1.1)
		var tip := middle + Vector3(sin(angle) * 1.6, 3.4, cos(angle) * 1.6)
		for tri: Array in [[root-side, root+side, middle+side*.65], [root-side, middle+side*.65, middle-side*.65], [middle-side*.65, middle+side*.65, tip]]:
			for point: Vector3 in tri:
				tool.set_normal(Vector3.UP)
				tool.set_color(Color("a4ae6f") if point.y > 6 else Color("657d4c") if point.y > 2 else Color("3c583c"))
				tool.add_vertex(point * Projector.SCALE)
	return tool.commit()

static func _grasses(parent: Node3D, world: Node2D) -> void:
	var mesh := _grass_mesh()
	var material := ShaderMaterial.new()
	material.resource_name = "scenic_grass_blades"
	material.shader = preload("res://scripts/hd2d/shaders/grass.gdshader")
	mesh.surface_set_material(0, material)
	var chunks: Dictionary = {}
	for y: int in range(60):
		for x: int in range(100):
			if world._grid[y * 100 + x] != Layout.GRASS:
				continue
			var noise := Layout.noise(x, y)
			# Leave broad quiet gaps; small dense patches outline paths and gardens.
			if sin(x * 0.36) + cos(y * 0.31) < -0.45 or noise % 5 == 0:
				continue
			for j: int in range(3):
				var at := Vector2(x * 16 + 2 + (noise + j * 7) % 12, y * 16 + 2 + (noise / 13 + j * 5) % 12)
				var chunk := Vector2i(x / 10, y / 10)
				if not chunks.has(chunk): chunks[chunk] = []
				chunks[chunk].append(Transform3D(Basis(Vector3.UP, deg_to_rad(float(noise % 360))).scaled(Vector3.ONE * (0.65 + float(noise % 7) * 0.09)), Projector.point(at, Layout.height_at(at))))
	for chunk: Vector2i in chunks:
		var node := MultiMeshInstance3D.new()
		node.set_script(preload("res://scripts/hd2d/grass_patch.gd"))
		node.name = "草簇_%d_%d" % [chunk.x, chunk.y]
		node.position = Projector.point(Vector2(chunk.x * 160, chunk.y * 160))
		var instances := MultiMesh.new()
		instances.transform_format = MultiMesh.TRANSFORM_3D
		instances.mesh = mesh
		instances.instance_count = chunks[chunk].size()
		var buffer := PackedFloat32Array()
		var bounds := AABB()
		for i: int in range(instances.instance_count):
			var t: Transform3D = chunks[chunk][i]
			t.origin -= node.position
			buffer.append_array(PackedFloat32Array([t.basis.x.x, t.basis.y.x, t.basis.z.x, t.origin.x, t.basis.x.y, t.basis.y.y, t.basis.z.y, t.origin.y, t.basis.x.z, t.basis.y.z, t.basis.z.z, t.origin.z]))
			var box: AABB = t * mesh.get_aabb()
			bounds = box if i == 0 else bounds.merge(box)
		instances.set_meta("authored_buffer", buffer)
		instances.set_meta("authored_bounds", bounds.grow(0.15))
		node.multimesh = instances
		node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		node.visibility_range_end = 110.0
		node.extra_cull_margin = 0.3
		parent.add_child(node)
