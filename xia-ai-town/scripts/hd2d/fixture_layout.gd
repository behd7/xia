extends RefCounted
## Offline P1 layout only. Never apply this grid to the game or server state.
## Keep the original 1600x960 bounds/foot collision, but only block visible props.
const INN_INDEX := 5

static func prepare(world: Node2D) -> void:
	world._ensure_grid()
	var trees: Array[Vector2i] = []
	for y: int in range(60):
		for x: int in range(100):
			if world._grid[y * 100 + x] == world.TREE:
				trees.append(Vector2i(x, y))
	trees.sort_custom(func(a: Vector2i, b: Vector2i):
		return (Vector2(a) * 16 + Vector2(8, 8)).distance_squared_to(Vector2(512, 410)) < (Vector2(b) * 16 + Vector2(8, 8)).distance_squared_to(Vector2(512, 410)))
	# The old wall, well, other buildings, pond and forest have no P1 geometry.
	# Their invisible collision used to cut the sample into disconnected areas.
	for y: int in range(60):
		for x: int in range(100):
			var index := y * 100 + x
			if world._grid[index] in [world.BUILDING, world.WATER, world.TREE, world.CLIFF, world.WALL, world.WELL]:
				world._grid[index] = world.TOWN if x >= 1 and x < 99 and y >= 2 and y < 30 else world.GRASS
	# A continuous circuit around the inn, connected to the southern road.
	for road: Rect2i in [Rect2i(23, 13, 18, 2), Rect2i(23, 25, 18, 2), Rect2i(23, 13, 2, 14), Rect2i(39, 13, 2, 14), Rect2i(31, 27, 2, 11)]:
		world._fill_tiles(road, world.ROAD)
	var inn: Rect2 = world.BUILDINGS[INN_INDEX].rect
	world._fill_tiles(Rect2i(Vector2i(inn.position / 16), Vector2i(inn.size / 16)), world.BUILDING)
	var restored := 0
	for cell: Vector2i in trees:
		if world._grid[cell.y * 100 + cell.x] == world.ROAD:
			continue
		world._grid[cell.y * 100 + cell.x] = world.TREE
		restored += 1
		if restored == 2:
			break

static func location(at: Vector2, world: Node2D) -> String:
	var inn: Rect2 = world.BUILDINGS[INN_INDEX].rect
	return "客栈" if inn.grow(32).has_point(at) else "样板空地"
