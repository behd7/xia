extends RefCounted
## P4 authored town. Shared by HD-2D terrain, movement, door interactions and map.
## Coordinates remain 1600×960; NPC schedules are deliberately outside this revision.

const TILE: int = 16
const WORLD_SIZE: Vector2 = Vector2(1600, 960)
const VIEW_BOUNDS := Rect2(Vector2.ZERO, WORLD_SIZE)
# Scenic ground is render-only. Gameplay and uploaded coordinates remain 1600×960.
const SCENERY_BOUNDS := Rect2(-8192, -8192, 17984, 17344)
const SCENERY_FLOOR := -16.0
const CAMERA_FOCUS_BOUNDS := Rect2(112, 96, 1376, 768)
const WALL_INSET := 48.0
const WALL_WIDTH := 32.0
const WALL_HEIGHT := 60.0
const WALL_GATE_WIDTH := 80.0
const GATE_AT := Vector2(824, 920)
const RIDGE_BOUNDS := Rect2(1648, 64, 688, 832)
const DISTANT_RIDGE_NEAR := Rect2(2112, -2048, 3088, 5120)
const DISTANT_RIDGE_FAR := Rect2(3936, -4096, 5856, 8192)
const GRASS: int = 1
const ROAD: int = 2
const BUILDING: int = 3
const WATER: int = 4
const TREE: int = 5
const CLIFF: int = 6
const SAND: int = 7
const WALL: int = 8
const WELL: int = 9
const TOWN: int = 10
const BUILDINGS: Array[Dictionary] = [
	{"rect": Rect2(176, 64, 192, 112), "name": "剑阳道馆", "kind": "sect", "id": "jianyang"},
	{"rect": Rect2(592, 144, 176, 128), "name": "玄铁堂", "kind": "sect", "id": "xuantie"},
	{"rect": Rect2(1088, 64, 192, 112), "name": "烟雨小筑", "kind": "sect", "id": "yanyu"},
	{"rect": Rect2(112, 384, 144, 96), "name": "药铺", "kind": "shop", "id": "clinic"},
	{"rect": Rect2(1040, 368, 160, 112), "name": "铁匠铺", "kind": "shop", "id": "smith"},
	{"rect": Rect2(560, 432, 224, 128), "name": "客栈", "kind": "inn", "id": "inn"},
	{"rect": Rect2(1344, 624, 160, 112), "name": "破庙", "kind": "", "id": "temple"},
]
const ROAD_PATHS: Array[Dictionary] = [
	{"points": [Vector2(72,304),Vector2(400,272),Vector2(528,320),Vector2(848,352),Vector2(1032,272),Vector2(1296,288)], "width": 48.0},
	{"points": [Vector2(72,544),Vector2(352,560),Vector2(496,624),Vector2(832,624),Vector2(1008,576),Vector2(1256,560),Vector2(1304,448)], "width": 56.0},
	{"points": [Vector2(824,336),Vector2(832,624),Vector2(824,784),GATE_AT], "width": 64.0},
	{"points": [Vector2(128,848),Vector2(432,800),Vector2(824,784),Vector2(1104,816),Vector2(1456,784)], "width": 40.0},
	{"points": [Vector2(272,176),Vector2(272,288)], "width": 40.0},
	{"points": [Vector2(680,272),Vector2(680,336)], "width": 48.0},
	{"points": [Vector2(1184,176),Vector2(1184,288)], "width": 40.0},
	{"points": [Vector2(184,480),Vector2(184,552)], "width": 40.0},
	{"points": [Vector2(672,560),Vector2(672,624)], "width": 48.0},
	{"points": [Vector2(1120,480),Vector2(1120,568)], "width": 40.0},
	{"points": [Vector2(1424,736),Vector2(1424,784),Vector2(1304,768),Vector2(1288,576)], "width": 40.0},
]
const POND := Rect2(1328, 160, 208, 256)
const YARD := Rect2(880, 112, 144, 128)
const WELL_AT := Vector2(448, 576)

# Stable prop identities shared with the 2D atlas and P3 environment catalog.
const P_PINE := 0
const P_DEAD_TREE := 1
const P_SHRUB := 2
const P_BRAMBLE := 3
const P_BOULDER := 4
const P_OUTCROP := 5
const P_WELL := 6
const P_FENCE := 7
const P_TABLE := 8
const P_RACK := 9
const P_LANTERN := 10
const P_BALE := 11
const P_BARREL := 12
const P_HERBS := 13
const P_DUMMY := 14
const P_MAT := 15
const P_PINE2 := 16
const P_CYPRESS := 17
const P_BARE_TREE := 18
const P_DRY_GRASS := 19
const P_MOSSY_BOULDER := 20
const P_SLAB := 21
const P_LICHEN_ROCK := 22
const P_LOG := 23
const P_STUMP := 24
const P_BAMBOO := 25
const P_THORN_BUSH := 26
const P_FLOWER_SHRUB := 27
const P_PUDDLE := 28
const P_CAIRN := 29
const P_WHEEL := 30
const P_FIREWOOD := 31

var _grid := PackedByteArray()

static func create_grid() -> PackedByteArray:
	var layout = new()
	layout._build_grid()
	return layout._grid

func _build_grid() -> void:
	_grid.resize(6000)
	_grid.fill(GRASS)
	for y: int in range(60):
		for x: int in range(100):
			var at := Vector2(x*16+8,y*16+8)
			for road: Dictionary in ROAD_PATHS:
				for i: int in range(road.points.size()-1):
					if at.distance_to(Geometry2D.get_closest_point_to_segment(at, road.points[i], road.points[i+1])) < float(road.width)*0.5:
						_grid[y*100+x] = ROAD
	for building: Dictionary in BUILDINGS:
		var r: Rect2 = building.rect
		var apron := Rect2i(Vector2i(r.position / TILE) + Vector2i(-1, 0), Vector2i(r.size / TILE) + Vector2i(2, 3))
		_fill_tiles(apron, TOWN)
		_fill_tiles(Rect2i(Vector2i(r.position / TILE), Vector2i(r.size / TILE)), BUILDING)
	_fill_tiles(Rect2i(Vector2i(YARD.position / TILE), Vector2i(YARD.size / TILE)), SAND)
	# Chamfer the pond corners rather than presenting a perfect rectangle.
	for y: int in range(10, 26):
		for x: int in range(83, 96):
			if Vector2(x - 89.0, (y - 18.0) * 0.8).length() < 7.0:
				_grid[y * 100 + x] = WATER
	_fill_tiles(Rect2i(27, 35, 2, 2), WELL)
	for y: int in range(2, 59):
		for x: int in range(2, 98):
			if _grid[y * 100 + x] != GRASS:
				continue
			var n := noise(x, y)
			var woodland := (y > 50 and x < 43) or (x < 6 and y > 24)
			var grove := y < 14 and (x > 23 and x < 33 or x > 49 and x < 54)
			if (woodland and n % 19 == 0) or (grove and n % 37 == 0):
				_grid[y * 100 + x] = TREE
			elif y > 52 and x > 56 and n % 7 == 0:
				_grid[y * 100 + x] = CLIFF
	_boundary_wall_grid()

func _boundary_wall_grid() -> void:
	for rect: Rect2 in boundary_walls():
		_fill_wall_rect(rect)
	# Round the gate posts out to full collision cells while leaving the road open.
	for offset: int in [-48, 32]:
		var post := Rect2(GATE_AT + Vector2(offset, -16), Vector2(16, 32))
		_fill_wall_rect(post)

func _fill_wall_rect(rect: Rect2) -> void:
	var clipped := rect.intersection(VIEW_BOUNDS)
	if not clipped.has_area():
		return
	var first := Vector2i((clipped.position / TILE).floor())
	var end := Vector2i((clipped.end / TILE).ceil())
	_fill_tiles(Rect2i(first, end - first), WALL)

static func boundary_walls() -> Array[Rect2]:
	var west := WALL_INSET - WALL_WIDTH * 0.5
	var north := WALL_INSET - WALL_WIDTH * 0.5
	var east := WORLD_SIZE.x - west
	var south := WORLD_SIZE.y - north
	var gate_left := GATE_AT.x - WALL_GATE_WIDTH * 0.5
	var gate_right := GATE_AT.x + WALL_GATE_WIDTH * 0.5
	return [Rect2(west, north, east - west, WALL_WIDTH),
		Rect2(west, south - WALL_WIDTH, gate_left - west, WALL_WIDTH),
		Rect2(gate_right, south - WALL_WIDTH, east - gate_right, WALL_WIDTH),
		Rect2(west, north + WALL_WIDTH, WALL_WIDTH, south - north - WALL_WIDTH * 2),
		Rect2(east - WALL_WIDTH, north + WALL_WIDTH, WALL_WIDTH, south - north - WALL_WIDTH * 2)]

static func eastern_ridge_height_at(at: Vector2) -> float:
	var rise := smoothstep(RIDGE_BOUNDS.position.x - 48.0, RIDGE_BOUNDS.position.x + 192.0, at.x)
	var fall := 1.0 - smoothstep(RIDGE_BOUNDS.end.x - 192.0, RIDGE_BOUNDS.end.x, at.x)
	var crest := 138.0 + sin(at.y * 0.012) * 22.0 + cos(at.y * 0.027) * 13.0 + sin(at.y * 0.004) * 28.0
	return scenery_height_at(at) + rise * fall * crest

static func scenery_height_at(at: Vector2) -> float:
	var edge := at.clamp(Vector2.ZERO, WORLD_SIZE)
	# Match the town rim exactly, then settle into a quiet, low hillside.
	return lerpf(height_at(edge), 6.0, smoothstep(0.0, 768.0, at.distance_to(edge))) - 0.2

static func raw_height(at: Vector2) -> float:
	# Broad ramps keep foot placement continuous. Building pads are flattened below.
	return 44.0 * (1.0 - smoothstep(160.0, 300.0, at.y)) + 64.0 * smoothstep(1150.0, 1450.0, at.x) * smoothstep(500.0, 690.0, at.y) + 5.0 * sin(at.x / 145.0) * sin(at.y / 170.0)

static func height_at(at: Vector2) -> float:
	var height := raw_height(at)
	for building: Dictionary in BUILDINGS:
		var r: Rect2 = building.rect
		var outside := Vector2(maxf(r.position.x - at.x, maxf(at.x - r.end.x, 0)), maxf(r.position.y - at.y, maxf(at.y - r.end.y, 0)))
		var weight := 1.0 - smoothstep(12.0, 56.0, outside.length())
		height = lerpf(height, raw_height(r.get_center()), weight)
	# Level water rim; the inner basin is lower, and never walkable.
	var pond_distance := maxf(absf(at.x - 1424.0) - 112.0, absf(at.y - 288.0) - 136.0)
	height = lerpf(height, 1.0, 1.0 - smoothstep(0.0, 40.0, pond_distance))
	return height


func _fill_tiles(r: Rect2i, value: int) -> void:
	for y: int in range(r.position.y, r.end.y):
		for x: int in range(r.position.x, r.end.x):
			_grid[y * 100 + x] = value


static func noise(x: int, y: int, salt: int = 0) -> int:
	return absi((x * 374761393 + y * 668265263 + salt * 69069) ^ ((x + salt) * 1274126177)) % 997



static func furniture() -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	# Positions are feet. The common renderer's legacy +16/+30 is removed here.
	for entry: Array in [
		[P_TABLE, 510, 578], [P_TABLE, 810, 536], [P_BARREL, 798, 564],
		[P_BARREL, 798, 534], [P_FIREWOOD, 568, 578],
		[P_HERBS, 128, 504], [P_HERBS, 248, 502], [P_FLOWER_SHRUB, 278, 450],
		[P_BAMBOO, 78, 416], [P_FIREWOOD, 1222, 428], [P_WHEEL, 1226, 482],
		[P_RACK, 910, 140], [P_DUMMY, 966, 146], [P_DUMMY, 1000, 202], [P_BALE, 902, 212],
		[P_RACK, 186, 214], [P_DUMMY, 356, 214],
		[P_BAMBOO, 1066, 110], [P_BAMBOO, 1304, 148], [P_FLOWER_SHRUB, 1302, 196],
		[P_MOSSY_BOULDER, 1320, 416], [P_BOULDER, 1516, 426], [P_BRAMBLE, 1500, 144],
		[P_WHEEL, 1320, 736], [P_CAIRN, 1520, 744], [P_DRY_GRASS, 1320, 658],
		[P_LANTERN, 424, 548], [P_LANTERN, 872, 628], [P_LANTERN, 1288, 520],
		[P_SHRUB, 360, 222], [P_SHRUB, 542, 160], [P_FLOWER_SHRUB, 800, 212],
		[P_LOG, 312, 840], [P_STUMP, 352, 846], [P_PUDDLE, 726, 668], [P_MAT, 1130, 250]
	]:
		result.append({"index": int(entry[0]), "at": Vector2(entry[1], entry[2]) - Vector2(16, 30)})
	for x: int in range(880, 1024, 44):
		result.append({"index": P_FENCE, "at": Vector2(x, 90) - Vector2(16, 30)})
	return result
