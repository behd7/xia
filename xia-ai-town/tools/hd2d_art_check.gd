extends SceneTree
## R09 local art guards: gameplay grid unchanged, main-lane furniture clearance,
## real roof opening and its closed intact counterpart in the exported GLB.
## --baseline-layout=/absolute/scenic_layout_before.gd compares all 6,000 cells.
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
const Projector = preload("res://scripts/hd2d/projection.gd")
var failures := 0
var checks := 0

func check(ok: bool, message: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		printerr("ART_FAIL " + message)

func _initialize() -> void:
	var grid := Layout.create_grid()
	print("ART_GRID_SHA256 ", grid.hex_encode().sha256_text())
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--baseline-layout="):
			var baseline: GDScript = load(argument.trim_prefix("--baseline-layout="))
			check(baseline != null, "baseline layout must load")
			if baseline != null:
				check(grid == baseline.create_grid(), "all 6,000 gameplay cells must remain unchanged")
	# These three movable props used to cover the central north/south lane.
	var count := 0
	for furniture: Dictionary in Layout.furniture():
		var at: Vector2 = furniture.at + Vector2(16, 30)
		if at.x < 700 or at.x > 810 or at.y < 520 or at.y > 580:
			continue
		if furniture.index not in [Layout.P_TABLE, Layout.P_BARREL]:
			continue
		var radius := 17.0 if furniture.index == Layout.P_TABLE else 10.0
		var road: Dictionary = Layout.ROAD_PATHS[4]
		var clearance := INF
		for index: int in range(road.points.size() - 1):
			var nearest := Geometry2D.get_closest_point_to_segment(at, road.points[index], road.points[index+1])
			clearance = minf(clearance, at.distance_to(nearest) - float(road.width)*0.5 - radius)
		check(clearance >= 2.0, "main-lane furniture envelope overlaps road: %s, clearance=%s" % [at, clearance])
		count += 1
	check(count == 3, "all three shifted props must be tested")
	var temple: Node3D = load("res://assets/hd2d/buildings/temple.glb").instantiate()
	var instance: MeshInstance3D = temple.find_child("temple_structure", true, false)
	check(instance != null, "exported temple mesh missing")
	if instance != null:
		var faces := PackedVector3Array()
		for surface: int in instance.mesh.get_surface_count():
			var material: Material = instance.mesh.surface_get_material(surface)
			if material.resource_name != "XIA_P3_roof": continue
			var arrays: Array = instance.mesh.surface_get_arrays(surface)
			var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
			var indices: PackedInt32Array = arrays[Mesh.ARRAY_INDEX]
			for index: int in indices: faces.append(vertices[index])
		check(not faces.is_empty(), "exported roof surface must exist")
		for x: float in [-42.0, 36.0]:
			var start := Projector.point(Vector2(x, 43), 220)
			var end := Projector.point(Vector2(x, 43), 0)
			var hit := false
			for index: int in range(0, faces.size(), 3):
				if Geometry3D.segment_intersects_triangle(start, end, faces[index], faces[index+1], faces[index+2]) != null:
					hit = true
					break
			check(hit == (x > 0), "real roof gap / opposite roof at x=%s" % x)
	temple.free()
	if failures:
		printerr("HD2D_ART_FAIL %d/%d" % [failures, checks])
		quit(1)
	else:
		print("HD2D_ART_PASS %d checks; local geometry only, NOT user readability approval" % checks)
		quit(0)
