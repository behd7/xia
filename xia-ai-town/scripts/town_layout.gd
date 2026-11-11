extends RefCounted
## Frozen 1600×960 gameplay layout, shared by both renderers and the atlas.
## This module contains no textures, scene nodes or server state.

const TILE: int = 16
const WORLD_SIZE: Vector2 = Vector2(1600, 960)
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
	{"rect": Rect2(64, 64, 160, 96), "name": "剑阳道馆", "kind": "sect", "id": "jianyang"},
	{"rect": Rect2(288, 64, 160, 96), "name": "玄铁堂", "kind": "sect", "id": "xuantie"},
	{"rect": Rect2(512, 64, 160, 96), "name": "烟雨小筑", "kind": "sect", "id": "yanyu"},
	{"rect": Rect2(64, 256, 128, 96), "name": "药铺", "kind": "shop", "id": "clinic"},
	{"rect": Rect2(240, 256, 128, 96), "name": "铁匠铺", "kind": "shop", "id": "smith"},
	{"rect": Rect2(416, 256, 192, 128), "name": "客栈", "kind": "inn", "id": "inn"},
	{"rect": Rect2(1280, 288, 160, 112), "name": "破庙", "kind": "", "id": "temple"},
]
# Static presentation defaults; runtime positions come from the server schedule.
const NPC_DEFAULTS := {
	"gu_changfeng": {"name": "顾长风", "robe": Color("647e83"), "pos": Vector2(144, 183)},
	"tie_wushuang": {"name": "铁无双", "robe": Color("8b7061"), "pos": Vector2(368, 183)},
	"su_wanqing": {"name": "苏婉清", "robe": Color("7b8c83"), "pos": Vector2(592, 183)},
	"chen_bansong": {"name": "陈半松", "robe": Color("91a68b"), "pos": Vector2(128, 375)},
	"wang_dazhu": {"name": "王大柱", "robe": Color("93725a"), "pos": Vector2(304, 375)},
	"liu_xiaodie": {"name": "柳小蝶", "robe": Color("b68b8b"), "pos": Vector2(566, 407)},
	"zhang_mantou": {"name": "张馒头", "robe": Color("858578"), "pos": Vector2(752, 427)},
}


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
var _random_state := 0

static func create_grid() -> PackedByteArray:
	var layout = new()
	layout._build_grid()
	return layout._grid

func _build_grid() -> void:
	if not _grid.is_empty():
		return
	_grid.resize(6000)
	_grid.fill(GRASS)
	_fill_tiles(Rect2i(1, 2, 98, 28), TOWN)
	_fill_tiles(Rect2i(2, 12, 96, 2), ROAD)
	_fill_tiles(Rect2i(48, 12, 4, 27), ROAD)
	_fill_tiles(Rect2i(8, 38, 83, 2), ROAD)
	for building: Dictionary in BUILDINGS:
		var r: Rect2 = building["rect"]
		_fill_tiles(Rect2i(int(r.position.x / 16), int(r.position.y / 16), int(r.size.x / 16), int(r.size.y / 16)), BUILDING)
	_fill_tiles(Rect2i(52, 5, 18, 12), SAND)
	_fill_tiles(Rect2i(72, 24, 7, 4), WATER)
	_fill_tiles(Rect2i(44, 25, 2, 2), WELL)
	_fill_tiles(Rect2i(0, 30, 100, 2), WALL)
	_fill_tiles(Rect2i(48, 30, 4, 2), ROAD)
	_random_state = 20261016
	for y: int in range(34, 59):
		for x: int in range(1, 46):
			if _grid[y * 100 + x] == GRASS and _mulberry32() < 0.13:
				_grid[y * 100 + x] = TREE
	_random_state = 4396
	for y: int in range(34, 59):
		for x: int in range(54, 99):
			if _grid[y * 100 + x] == GRASS and (y >= 51 or _mulberry32() < 0.09):
				_grid[y * 100 + x] = CLIFF


func _fill_tiles(r: Rect2i, value: int) -> void:
	for y: int in range(r.position.y, r.end.y):
		for x: int in range(r.position.x, r.end.x):
			_grid[y * 100 + x] = value


func _imul32(a: int, b: int) -> int:
	# Low 32 bits without overflowing signed int64 on unsigned JS operands.
	return ((a & 65535) * b + (((a >> 16) * b & 65535) << 16)) & 0xffffffff


func _mulberry32() -> float:
	_random_state = (_random_state + 0x6d2b79f5) & 0xffffffff
	var t: int = _imul32(_random_state ^ (_random_state >> 15), _random_state | 1)
	t = t ^ ((t + _imul32(t ^ (t >> 7), t | 61)) & 0xffffffff)
	return float((t ^ (t >> 14)) & 0xffffffff) / 4294967296.0


static func noise(x: int, y: int, salt: int = 0) -> int:
	return absi((x * 374761393 + y * 668265263 + salt * 69069) ^ ((x + salt) * 1274126177)) % 997



static func furniture() -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	result.append({"index": P_TABLE, "at": Vector2(420, 386)})
	result.append({"index": P_RACK, "at": Vector2(836, 90)})
	result.append({"index": P_DUMMY, "at": Vector2(1082, 92)})
	result.append({"index": P_BALE, "at": Vector2(1096, 136)})
	result.append({"index": P_BARREL, "at": Vector2(56, 372)})
	result.append({"index": P_HERBS, "at": Vector2(72, 352)})
	result.append({"index": P_MAT, "at": Vector2(456, 272)})
	result.append({"index": P_LANTERN, "at": Vector2(1264, 300)})
	# Fence segments frame the south road and the training yard.
	for x: int in range(880, 1120, 48):
		result.append({"index": P_FENCE, "at": Vector2(x, 268)})
	result.append({"index": P_SHRUB, "at": Vector2(60, 226)})
	result.append({"index": P_BRAMBLE, "at": Vector2(208, 226)})
	result.append({"index": P_SHRUB, "at": Vector2(393, 228)})
	result.append({"index": P_BRAMBLE, "at": Vector2(640, 225)})
	result.append({"index": P_SHRUB, "at": Vector2(1270, 263)})
	result.append({"index": P_BRAMBLE, "at": Vector2(1444, 263)})
	# Town scenery details: bamboo & flowers at Yanyu, firewood at Smith/Inn, broken wheel & cairn at ruins.
	result.append({"index": P_BAMBOO, "at": Vector2(504, 116)})
	result.append({"index": P_FLOWER_SHRUB, "at": Vector2(674, 122)})
	result.append({"index": P_FLOWER_SHRUB, "at": Vector2(194, 318)})
	result.append({"index": P_FIREWOOD, "at": Vector2(232, 332)})
	result.append({"index": P_FIREWOOD, "at": Vector2(406, 338)})
	result.append({"index": P_STUMP, "at": Vector2(1116, 236)})
	result.append({"index": P_WHEEL, "at": Vector2(1254, 370)})
	result.append({"index": P_CAIRN, "at": Vector2(852, 606)})
	result.append({"index": P_PUDDLE, "at": Vector2(744, 608)})
	result.append({"index": P_WHEEL, "at": Vector2(688, 620)})
	result.append({"index": P_DRY_GRASS, "at": Vector2(648, 622)})
	return result
