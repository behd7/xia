extends SceneTree
## Offline authoring only: preserve the frozen layout as editor-visible scenes.
## Run: godot --headless --path xia-ai-town --script res://tools/build_hd2d_scenes.gd
const Builder = preload("res://tools/hd2d/scenery_builder.gd")
const World = preload("res://scripts/town_world.gd")
const Fixture = preload("res://scripts/hd2d/fixture_layout.gd")
const ShadowBuilder = preload("res://tools/hd2d/shadow_builder.gd")
const SCENES := "res://scenes/hd2d/"
const RESOURCES := "res://assets/hd2d/static/"
var saved: Dictionary = {}
var shape_cache: Dictionary = {}

func _initialize() -> void:
	call_deferred("_build")

func _save(resource: Resource, path: String) -> void:
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	assert(ResourceSaver.save(resource, path) == OK, "Could not save " + path)
	resource.take_over_path(path)
	if path.get_extension() in ["tscn", "tres"]:
		_normalize_text(path)

func _normalize_text(path: String) -> void:
	# ResourceSaver uses random editor IDs. Keep offline rebuild diffs readable.
	var content := FileAccess.get_file_as_string(path)
	var ids := RegEx.create_from_string("\\[(ext_resource|sub_resource)[^\\n]* id=\"([^\"]+)\"")
	var counts := {"ext_resource": 0, "sub_resource": 0}
	for match_id: RegExMatch in ids.search_all(content):
		var kind := match_id.get_string(1)
		counts[kind] += 1
		var stable := str(counts[kind]) if kind == "ext_resource" else "Resource_%d" % counts[kind]
		content = content.replace('"' + match_id.get_string(2) + '"', '"' + stable + '"')
	content = RegEx.create_from_string(" unique_id=[0-9]+").sub(content, "", true)
	content = content.replace("format=4", "format=3")
	var file := FileAccess.open(path, FileAccess.WRITE)
	file.store_string(content)
	file.close()

func _own(node: Node, owner_node: Node) -> void:
	for child: Node in node.get_children():
		# Preserve GLB ownership so pack stores overrides, not copied geometry.
		if child.owner == null:
			child.owner = owner_node
		_own(child, owner_node)
	if node != owner_node and not node.scene_file_path.is_empty():
		owner_node.set_editable_instance(node, true)

func _material(material: Material) -> void:
	if material == null or saved.has(material.get_instance_id()):
		return
	var key := material.resource_name
	if key.is_empty():
		key = "surface_%d" % saved.size()
	var path := RESOURCES + "materials/" + key.validate_filename() + ".tres"
	saved[material.get_instance_id()] = path
	_save(material, path)

func _resources(scenery: Node3D, label: String) -> void:
	for key: String in scenery.materials:
		var mat: Material = scenery.materials[key]
		mat.resource_name = key.replace("/", "_")
		_material(mat)
	var serial := 0
	for mesh: MeshInstance3D in scenery.find_children("*", "MeshInstance3D", true, false):
		_material(mesh.material_override)
		for i: int in range(mesh.mesh.get_surface_count()):
			_material(mesh.get_surface_override_material(i))
		# Imported meshes stay inside their GLB instance, not copied cache paths.
		if mesh.mesh.resource_path.is_empty():
			var path := RESOURCES + "meshes/%s_%s_%d.res" % [label, mesh.name.validate_filename(), serial]
			_save(mesh.mesh, path)
		serial += 1
		if mesh.has_meta("fade_group"):
			var shape: CollisionShape3D = mesh.get_node("VisualOccluder/RayShape")
			var key := mesh.mesh.get_faces().to_byte_array().hex_encode().sha256_text()
			if not shape_cache.has(key):
				var path := RESOURCES + "rays/" + key + ".res"
				_save(shape.shape, path)
				shape_cache[key] = shape.shape
			else:
				shape.shape = shape_cache[key]

func _build_map(full_map: bool) -> void:
	var world := World.new()
	world.render_2d = false
	world._ensure_grid()
	if not full_map:
		Fixture.prepare(world)
	var scenery := Builder.new()
	scenery.name = "Scenery"
	scenery.build(world, full_map)
	var label := "town" if full_map else "prototype"
	scenery.set_meta("full_map", full_map)
	if full_map:
		scenery.set_meta("inventory", scenery.world_assets.placed)
	_resources(scenery, label)
	# Binding and animation stay in the runtime adapter; construction does not.
	scenery.set_script(load("res://scripts/hd2d/scenery.gd"))
	_own(scenery, scenery)
	var packed := PackedScene.new()
	assert(packed.pack(scenery) == OK)
	_save(packed, SCENES + label + "_scenery.tscn")
	print("HD2D_STATIC_MAP ", label, " nodes=", scenery.find_children("*", "", true, false).size())
	scenery.free()
	world.free()

func _build_tree() -> void:
	var scenery := Builder.new()
	scenery.name = "Pine"
	preload("res://tools/hd2d/tree_builder.gd").build(scenery, Vector2.ZERO, 0)
	_resources(scenery, "pine")
	scenery.set_script(null)
	_own(scenery, scenery)
	var packed := PackedScene.new()
	assert(packed.pack(scenery) == OK)
	_save(packed, SCENES + "pine.tscn")
	scenery.free()

func _build() -> void:
	_build_map(true)
	_build_map(false)
	_build_tree()
	for phase: int in range(9):
		var pose := ShadowBuilder._build_pose(phase - 1)
		var material := pose.surface_get_material(0)
		material.resource_name = "body_shadow"
		_material(material)
		_save(pose, RESOURCES + "shadows/pose_%d.res" % phase)
	_save(preload("res://scripts/ui_kit.gd").make_theme(), RESOURCES + "ui_theme.tres")
	_save(preload("res://scripts/ui_kit.gd").panel_box(20), RESOURCES + "panel_20.tres")
	print("HD2D_STATIC_BUILD_PASS")
	quit()
