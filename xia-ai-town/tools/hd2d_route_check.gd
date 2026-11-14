extends SceneTree
## Headless check: every authored scenic NPC route, pacing box, mob seat and
## remapped spawn must be walkable on the scenic grid.
## Run: godot --headless --path xia-ai-town --script res://tools/hd2d_route_check.gd
const World = preload("res://scripts/town_world.gd")
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")

func _initialize() -> void:
	var world := World.new()
	world.render_2d = false
	world.scenic_layout = true
	world.initialize()
	var failures := 0
	for slug: String in Layout.NPC_ROUTES:
		var route: Dictionary = Layout.NPC_ROUTES[slug]
		if route.home_to_work[-1] != route.work_to_gather[0]:
			printerr("ROUTE_FAIL %s: work anchor differs between the two polylines" % slug)
			failures += 1
		for key: String in ["home_to_work", "work_to_gather"]:
			var points: Array = route[key]
			for index: int in range(points.size() - 1):
				var length: float = points[index].distance_to(points[index + 1])
				for step: int in range(int(length / 4.0) + 1):
					var at: Vector2 = points[index].lerp(points[index + 1], minf(1.0, step * 4.0 / maxf(length, 1.0)))
					if not world.can_walk(at):
						printerr("ROUTE_FAIL %s %s segment %d blocked at %s" % [slug, key, index, at])
						failures += 1
						break
		# Idle pacing wanders +-8/+-6 around every rest anchor.
		for anchor: Vector2 in [route.home_to_work[0], route.home_to_work[-1], route.work_to_gather[-1]]:
			for offset: Vector2 in [Vector2(-8, -6), Vector2(8, -6), Vector2(-8, 6), Vector2(8, 6)]:
				if not world.can_walk(anchor + offset):
					printerr("ROUTE_FAIL %s pacing box blocked at %s" % [slug, anchor + offset])
					failures += 1
	var gathered: Array = []
	for slug: String in Layout.NPC_ROUTES:
		var spot: Vector2 = Layout.NPC_ROUTES[slug].work_to_gather[-1]
		for other: Vector2 in gathered:
			if spot.distance_to(other) < 24.0:
				printerr("ROUTE_FAIL %s gather spot overlaps another at %s" % [slug, spot])
				failures += 1
		gathered.append(spot)
		if spot.distance_to(Layout.GATHER_ANCHOR) > 64.0:
			printerr("ROUTE_FAIL %s gathers too far from the anchor" % slug)
			failures += 1
	for id: String in Layout.MOB_POSITIONS:
		var at: Vector2 = Layout.MOB_POSITIONS[id]
		for offset: Vector2 in [Vector2.ZERO, Vector2(-17, 0), Vector2(17, 0), Vector2(0, -17), Vector2(0, 17)]:
			if not world.can_walk(at + offset):
				printerr("ROUTE_FAIL mob %s unreachable around %s" % [id, at + offset])
				failures += 1
	for source: Vector2 in Layout.SPAWN_REMAP:
		if not world.can_walk(Layout.SPAWN_REMAP[source]):
			printerr("ROUTE_FAIL spawn %s maps to a blocked cell" % source)
			failures += 1
	for npc: Dictionary in world.npcs:
		if not world.can_walk(npc.pos):
			printerr("ROUTE_FAIL default %s stands in a blocked cell %s" % [npc.slug, npc.pos])
			failures += 1
	failures += _connectivity(world)
	world.free()
	if failures > 0:
		printerr("HD2D_ROUTE_FAIL %d" % failures)
		quit(1)
		return
	print("HD2D_ROUTE_PASS: %d NPC routes, %d mob seats, %d spawns" % [Layout.NPC_ROUTES.size(), Layout.MOB_POSITIONS.size(), Layout.SPAWN_REMAP.size()])
	quit()

func _connectivity(world: Node2D) -> int:
	const WIDTH := 200
	const HEIGHT := 120
	const CELL := 8.0
	var clear := PackedByteArray()
	clear.resize(WIDTH * HEIGHT)
	for y: int in HEIGHT:
		for x: int in WIDTH:
			clear[y * WIDTH + x] = int(world.can_walk(Vector2(x, y) * CELL + Vector2(4, 4)))
	var targets: Dictionary = {"gather": Layout.GATHER_ANCHOR}
	for building: Dictionary in Layout.BUILDINGS:
		var rect: Rect2 = building.rect
		targets["door/" + str(building.id)] = Vector2(rect.get_center().x, rect.end.y + 28)
	for source: Vector2 in Layout.SPAWN_REMAP:
		targets["spawn/" + str(source)] = Layout.SPAWN_REMAP[source]
	for id: String in Layout.MOB_POSITIONS:
		targets["mob/" + id] = Layout.MOB_POSITIONS[id]
	if "--fault-route" in OS.get_cmdline_user_args():
		targets["fault/isolated-door"] = Layout.BUILDINGS[0].rect.get_center()
	var visited := PackedByteArray()
	visited.resize(WIDTH * HEIGHT)
	var spawn: Vector2 = Layout.SPAWN_REMAP[Vector2(792, 440)]
	var first := int(spawn.y / CELL) * WIDTH + int(spawn.x / CELL)
	var queue := PackedInt32Array([first])
	visited[first] = 1
	var head := 0
	while head < queue.size():
		var at := queue[head]
		head += 1
		var x := at % WIDTH
		var y := at / WIDTH
		for offset: Vector2i in [Vector2i(-1, 0), Vector2i(1, 0), Vector2i(0, -1), Vector2i(0, 1)]:
			var nx := x + offset.x
			var ny := y + offset.y
			if nx < 0 or ny < 0 or nx >= WIDTH or ny >= HEIGHT: continue
			var next := ny * WIDTH + nx
			if visited[next] or not clear[next]: continue
			# Every 8px graph edge checks its 4px midpoint with the real foot box.
			var midpoint := (Vector2(x, y) + Vector2(nx, ny)) * CELL * 0.5 + Vector2(4, 4)
			if not world.can_walk(midpoint): continue
			visited[next] = 1
			queue.append(next)
	var failures := 0
	for key: String in targets:
		var at: Vector2 = targets[key]
		var cell := int(at.y / CELL) * WIDTH + int(at.x / CELL)
		if not world.can_walk(at) or not visited[cell]:
			printerr("ROUTE_FAIL disconnected %s at %s" % [key, at])
			failures += 1
	print("HD2D_CONNECTIVITY: %d destinations, %d reachable 8px cells (4px edge checks)" % [targets.size(), queue.size()])
	return failures
