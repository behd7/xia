extends Node2D
## Legacy 2D presentation only. TownWorld owns all gameplay state.
const Layout = preload("res://scripts/town_layout.gd")
const TILE := Layout.TILE
const WORLD_SIZE := Layout.WORLD_SIZE
const BUILDINGS := Layout.BUILDINGS
const GRASS := Layout.GRASS
const ROAD := Layout.ROAD
const BUILDING := Layout.BUILDING
const WATER := Layout.WATER
const TREE := Layout.TREE
const CLIFF := Layout.CLIFF
const SAND := Layout.SAND
const WALL := Layout.WALL
const WELL := Layout.WELL
const TOWN := Layout.TOWN

# Art assets: 1 art pixel == 1 world pixel, so integer zoom stays crisp.
const TERRAIN_ATLAS := preload("res://assets/atlas/terrain.png")
const PROPS_ATLAS := preload("res://assets/atlas/props.png")
const PARTS_ATLAS := preload("res://assets/atlas/parts.png")
const BUILDING_DIR: String = "res://assets/buildings/"
const CHARACTER_DIR: String = "res://assets/characters/"
const MOB_DIR: String = "res://assets/mobs/"
const HERO_SHEET := preload("res://assets/characters/玩家初始.png")
const CHAR_CELL: int = 32
const TERRAIN_CELL: int = 16
const PROP_CELL: int = 32
const PART_CELL: int = 64
const CHAR_ROWS: Array[String] = ["down", "left", "right", "up"]

# Terrain atlas cells, matching docs/ART-PROMPTS.md.
const T_DIRT := 0
const T_PAVING := 1
const T_GRASS := 2
const T_BRUSH := 3
const T_SAND := 4
const T_WATER := 5
const T_CLIFF := 6
const T_TRAIL := 7
const T_BRICK := 8
const T_PLASTER := 10
const T_PLINTH := 11
const T_YARD := 12
const T_PLANK := 13

# Props atlas cells.
const P_PINE := Layout.P_PINE
const P_DEAD_TREE := Layout.P_DEAD_TREE
const P_SHRUB := Layout.P_SHRUB
const P_BRAMBLE := Layout.P_BRAMBLE
const P_BOULDER := Layout.P_BOULDER
const P_OUTCROP := Layout.P_OUTCROP
const P_WELL := Layout.P_WELL
const P_FENCE := Layout.P_FENCE
const P_TABLE := Layout.P_TABLE
const P_RACK := Layout.P_RACK
const P_LANTERN := Layout.P_LANTERN
const P_BALE := Layout.P_BALE
const P_BARREL := Layout.P_BARREL
const P_HERBS := Layout.P_HERBS
const P_DUMMY := Layout.P_DUMMY
const P_MAT := Layout.P_MAT

# Additional wilderness & scenery props (草木山石2, indices 16..31).
const P_PINE2 := Layout.P_PINE2
const P_CYPRESS := Layout.P_CYPRESS
const P_BARE_TREE := Layout.P_BARE_TREE
const P_DRY_GRASS := Layout.P_DRY_GRASS
const P_MOSSY_BOULDER := Layout.P_MOSSY_BOULDER
const P_SLAB := Layout.P_SLAB
const P_LICHEN_ROCK := Layout.P_LICHEN_ROCK
const P_LOG := Layout.P_LOG
const P_STUMP := Layout.P_STUMP
const P_BAMBOO := Layout.P_BAMBOO
const P_THORN_BUSH := Layout.P_THORN_BUSH
const P_FLOWER_SHRUB := Layout.P_FLOWER_SHRUB
const P_PUDDLE := Layout.P_PUDDLE
const P_CAIRN := Layout.P_CAIRN
const P_WHEEL := Layout.P_WHEEL
const P_FIREWOOD := Layout.P_FIREWOOD

# Building parts atlas cells used standalone (gate arch, wall coping).
const RB_GATE := 12
const RB_FOUNDATION := 8


var world: Node2D
var _static_texture: ImageTexture
var _static_ready := false
var _sheets: Dictionary = {}
var _mob_sheets: Dictionary = {}
var _time := 0.0
var _hero_last := Vector2.ZERO
var _last_actor_pos := Vector2.ZERO
var _redraw_elapsed := 0.0

func _ready() -> void:
	texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_hero_last = world.actor_pos
	_bake_static()

func _process(delta: float) -> void:
	_time += delta
	if world.actor_pos.distance_squared_to(_hero_last) > 0.25:
		world.hero_facing = world._facing_from(world.actor_pos - _hero_last)
		_hero_last = world.actor_pos
	_redraw_elapsed += delta
	if world.actor_pos != _last_actor_pos or world.moving or _redraw_elapsed >= 1.0 / 30.0:
		_last_actor_pos = world.actor_pos
		_redraw_elapsed = 0.0
		queue_redraw()

func _font() -> Font:
	return world.ui_font if world.ui_font != null else ThemeDB.fallback_font


func _label(canvas: CanvasItem, text: String, center: Vector2, size: int, color: Color, plate: bool = false) -> void:
	var font: Font = _font()
	var width: float = ceilf(font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)
	if plate:
		canvas.draw_rect(Rect2(center.x - width / 2 - 5, center.y - size + 1, width + 10, size + 6), Color("263e38e8"))
		canvas.draw_line(Vector2(center.x - width / 2 - 3, center.y + 5), Vector2(center.x + width / 2 + 3, center.y + 5), Color("9c9b6d"))
	canvas.draw_string(font, center + Vector2(-width / 2 + 1, 1), text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, Color("1c302caa"))
	canvas.draw_string(font, center + Vector2(-width / 2, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, color)


# --- static layer -----------------------------------------------------------
# The whole landscape is composited once into a single world-sized texture:
# terrain tiles from the atlas, then props and houses on top. Gameplay sprites
# (hero, NPCs, mobs) are drawn every frame in _draw() so they can depth-sort.


func _terrain_index(tile: int, y: int) -> int:
	match tile:
		TOWN:
			return T_DIRT
		ROAD:
			return T_PAVING if y <= 30 else T_TRAIL
		SAND:
			return T_SAND
		WATER:
			return T_WATER
		CLIFF:
			return T_CLIFF
		BUILDING:
			return T_PLANK
		WALL:
			return T_BRICK
		WELL:
			return T_PAVING
		_:
			return T_GRASS


func _bake_static() -> void:
	var atlas: Image = TERRAIN_ATLAS.get_image()
	var canvas: Image = Image.create(int(WORLD_SIZE.x), int(WORLD_SIZE.y), false, Image.FORMAT_RGBA8)
	canvas.fill(Color("718d72"))
	for y: int in range(60):
		for x: int in range(100):
			var index: int = _terrain_index(world._grid[y * 100 + x], y)
			var src: Rect2i = Rect2i((index % 4) * TERRAIN_CELL, (index / 4) * TERRAIN_CELL, TERRAIN_CELL, TERRAIN_CELL)
			canvas.blit_rect(atlas, src, Vector2i(x * TILE, y * TILE))
	# Training yard: packed earth with a single painted circle (the yard tile bakes
	# its own circle, so tiling it would repeat the ring every 16px).
	_fill_bake(canvas, Rect2i(832, 80, 288, 192), T_DIRT)
	_draw_yard_circle(canvas, Vector2(976, 176), 44)
	_fill_bake(canvas, Rect2i(832, 80, 288, 6), T_PLINTH)
	_fill_bake(canvas, Rect2i(832, 266, 288, 6), T_PLINTH)
	_fill_bake(canvas, Rect2i(832, 80, 6, 192), T_PLINTH)
	_fill_bake(canvas, Rect2i(1114, 80, 6, 192), T_PLINTH)
	for building: Dictionary in BUILDINGS:
		var rect: Rect2 = building["rect"]
		_blend_file(canvas, "%s%s.png" % [BUILDING_DIR, building["id"]], rect.position)
	# Pond rim (tiles 72..79 x 24..28) so the water reads as a contained body.
	_fill_bake(canvas, Rect2i(1152, 380, 112, 4), T_PLINTH)
	_fill_bake(canvas, Rect2i(1152, 448, 112, 4), T_PLINTH)
	_fill_bake(canvas, Rect2i(1148, 380, 4, 72), T_PLINTH)
	_fill_bake(canvas, Rect2i(1264, 380, 4, 72), T_PLINTH)
	# Town wall: brick courses with a pale stone coping along the top, and the
	# gate arch bridged over the road opening at x=768..832.
	_fill_bake(canvas, Rect2i(0, 470, 1600, 6), T_PLINTH)
	_fill_bake(canvas, Rect2i(768, 470, 64, 6), T_PAVING)
	_blend_part(canvas, RB_GATE, Vector2(768, 448))
	# Wilderness: terrain first, then clustered pines, cypresses, diverse rocks and forest undergrowth.
	for y: int in range(34, 59):
		for x: int in range(1, 99):
			var tile: int = world._grid[y * 100 + x]
			var n: int = Layout.noise(x, y)
			if tile == TREE:
				match n % 6:
					0:
						_blend_prop(canvas, P_PINE if n % 2 == 0 else P_PINE2, Vector2(x * TILE - 8, y * TILE - 16))
					1:
						_blend_prop(canvas, P_CYPRESS if n % 2 == 0 else P_BAMBOO, Vector2(x * TILE - 8, y * TILE - 16))
					2:
						_blend_prop(canvas, P_BRAMBLE if n % 2 == 0 else P_THORN_BUSH, Vector2(x * TILE - 8, y * TILE - 14))
					3:
						_blend_prop(canvas, P_FLOWER_SHRUB if n % 2 == 0 else P_DRY_GRASS, Vector2(x * TILE - 8, y * TILE - 14))
					4:
						_blend_prop(canvas, P_LOG if n % 2 == 0 else P_STUMP, Vector2(x * TILE - 8, y * TILE - 14))
					5:
						if y > 46:
							_blend_prop(canvas, P_BARE_TREE, Vector2(x * TILE - 8, y * TILE - 16))
			elif tile == CLIFF:
				# Diverse boulders, lichen rocks, flat slabs and stone cairns keep the ridge rich.
				if n % 3 == 0:
					var rock_prop: int = P_BOULDER
					match n % 5:
						0: rock_prop = P_OUTCROP if y >= 52 else P_BOULDER
						1: rock_prop = P_MOSSY_BOULDER
						2: rock_prop = P_SLAB
						3: rock_prop = P_LICHEN_ROCK
						4: rock_prop = P_CAIRN
					_blend_prop(canvas, rock_prop, Vector2(x * TILE - 8, y * TILE - 16))
	# Planted border along the north edge of town.
	for x: int in range(24, 1552, 26):
		_blend_prop(canvas, P_SHRUB if x % 3 else P_BRAMBLE, Vector2(x - 8, 26))
	_blend_prop(canvas, P_WELL, Vector2(704, 400))
	_draw_town_furniture(canvas)
	_static_texture = ImageTexture.create_from_image(canvas)
	_static_ready = true
	queue_redraw()


func _fill_bake(canvas: Image, area: Rect2i, index: int) -> void:
	var atlas: Image = TERRAIN_ATLAS.get_image()
	var src: Rect2i = Rect2i((index % 4) * TERRAIN_CELL, (index / 4) * TERRAIN_CELL, TERRAIN_CELL, TERRAIN_CELL)
	for y: int in range(area.position.y, area.end.y, TERRAIN_CELL):
		for x: int in range(area.position.x, area.end.x, TERRAIN_CELL):
			var w: int = mini(TERRAIN_CELL, area.end.x - x)
			var h: int = mini(TERRAIN_CELL, area.end.y - y)
			canvas.blit_rect(atlas, Rect2i(src.position, Vector2i(w, h)), Vector2i(x, y))


func _draw_yard_circle(canvas: Image, center: Vector2, radius: int) -> void:
	# Two painted rings blended into the packed earth (Image.set_pixel overwrites,
	# so the mix is done by hand).
	var paint: Color = Color(0.47, 0.44, 0.33)
	for step: int in range(2):
		var r: float = float(radius - step * 5)
		for deg: int in range(0, 360):
			var rad: float = deg_to_rad(float(deg))
			var at: Vector2 = center + Vector2(cos(rad) * r, sin(rad) * r * 0.62)
			var px: int = clampi(int(at.x), 0, canvas.get_width() - 1)
			var py: int = clampi(int(at.y), 0, canvas.get_height() - 1)
			var alpha: float = 0.5 - step * 0.18
			canvas.set_pixel(px, py, canvas.get_pixel(px, py).lerp(paint, alpha))


func _blend_prop(canvas: Image, index: int, at: Vector2) -> void:
	var atlas: Image = PROPS_ATLAS.get_image()
	var src: Rect2i = Rect2i((index % 4) * PROP_CELL, (index / 4) * PROP_CELL, PROP_CELL, PROP_CELL)
	canvas.blend_rect(atlas, src, Vector2i(at))


func _blend_part(canvas: Image, index: int, at: Vector2) -> void:
	var atlas: Image = PARTS_ATLAS.get_image()
	var src: Rect2i = Rect2i((index % 4) * PART_CELL, (index / 4) * PART_CELL, PART_CELL, PART_CELL)
	canvas.blend_rect(atlas, src, Vector2i(at))


func _blend_file(canvas: Image, path: String, at: Vector2) -> void:
	if not ResourceLoader.exists(path):
		return
	var texture: Texture2D = load(path)
	var image: Image = texture.get_image()
	canvas.blend_rect(image, Rect2i(Vector2i.ZERO, image.get_size()), Vector2i(at))


func _draw_town_furniture(canvas: Image) -> void:
	for prop: Dictionary in Layout.furniture():
		_blend_prop(canvas, int(prop.index), prop.at)


# --- frame drawing ----------------------------------------------------------


func _draw() -> void:
	if not _static_ready:
		return
	draw_texture(_static_texture, Vector2.ZERO)
	for building: Dictionary in BUILDINGS:
		var rect: Rect2 = building["rect"]
		_label(self, building["name"], Vector2(rect.get_center().x, rect.position.y + rect.size.y * 0.60), 12, Color("eddfad"), true)
	_label(self, "清 溪 镇", Vector2(800, 470), 13, Color("eddfb5"), true)
	_label(self, "练 武 场", Vector2(976, 74), 13, Color("3c4a3d"), true)
	_label(self, "东 山 林 地", Vector2(416, 563), 15, Color("e6ecc9"), true)
	_label(self, "后 山 崖", Vector2(1232, 563), 15, Color("e2ddc0"), true)
	var actors: Array[Dictionary] = []
	for npc: Dictionary in world.npcs:
		actors.append({"pos": npc["pos"], "kind": "npc", "data": npc})
	for value: Variant in world.mobs:
		if not value is Dictionary:
			continue
		var mob: Dictionary = value
		if not bool(mob.get("alive", true)):
			continue
		actors.append({"pos": Vector2(float(mob.get("x", 0)), float(mob.get("y", 0))), "kind": "mob", "data": mob})
	actors.append({"pos": world.actor_pos, "kind": "hero", "data": {}})
	actors.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return Vector2(a["pos"]).y < Vector2(b["pos"]).y)
	for entry: Dictionary in actors:
		var at: Vector2 = entry["pos"]
		var data: Dictionary = entry["data"]
		match entry["kind"]:
			"hero":
				_draw_character(HERO_SHEET, at, world.hero_facing, int(world.walk_time * 9.0) % 4 if world.moving else 0, true)
				_label(self, world.actor_name, at + Vector2(0, -34), 12, Color("faf0cb"), true)
			"npc":
				var sheet: Texture2D = _character_sheet(str(data.get("name", "")))
				if sheet != null:
					_draw_character(sheet, at, str(data.get("facing", "down")), int(_time * 8.0) % 4 if bool(data.get("world.moving", false)) else 0, false)
				_label(self, data["name"], at + Vector2(0, -34), 11, Color("e8e4c7"), true)
				if float(data.get("bubble_until", 0.0)) > Time.get_ticks_msec() / 1000.0:
					_draw_bubble(at, str(data.get("bubble", "")))
			"mob":
				_draw_mob(at, data)


func _character_sheet(name: String) -> Texture2D:
	if _sheets.has(name):
		return _sheets[name]
	var path: String = "%s%s.png" % [CHARACTER_DIR, name]
	var sheet: Texture2D = load(path) if ResourceLoader.exists(path) else null
	_sheets[name] = sheet
	return sheet


func _draw_character(sheet: Texture2D, at: Vector2, facing: String, frame: int, hero: bool) -> void:
	var row: int = CHAR_ROWS.find(facing)
	if row < 0:
		row = 0
	draw_rect(Rect2(at + Vector2(-9, -4), Vector2(18, 5)), Color("203d3640"))
	if hero:
		draw_arc(at + Vector2(0, 1), 11, 0, TAU, 20, Color("e2d4a077"), 1, false)
	var origin: Vector2 = Vector2(at.x - CHAR_CELL * 0.5, at.y - CHAR_CELL + 2)
	draw_texture_rect_region(sheet, Rect2(origin, Vector2(CHAR_CELL, CHAR_CELL)),
		Rect2(frame * CHAR_CELL, row * CHAR_CELL, CHAR_CELL, CHAR_CELL))


func _draw_bubble(at: Vector2, text: String) -> void:
	var clean: String = text.replace("\n", " ").strip_edges()
	if clean.is_empty():
		return
	var limit: int = 13
	var lines: Array[String] = []
	var cursor: int = 0
	while cursor < clean.length() and lines.size() < 2:
		var remain: int = clean.length() - cursor
		if remain <= limit or lines.size() == 1:
			var tail: String = clean.substr(cursor, limit)
			if remain > limit:
				tail = tail.substr(0, tail.length() - 1) + "…"
			lines.append(tail)
			break
		lines.append(clean.substr(cursor, limit))
		cursor += limit
	var font: Font = _font()
	var width: float = 0.0
	for line: String in lines:
		width = maxf(width, font.get_string_size(line, HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x)
	width = ceilf(width) + 16.0
	var line_height: float = 17.0
	var height: float = lines.size() * line_height + 9.0
	var origin: Vector2 = Vector2(roundf(at.x - width / 2.0), roundf(at.y - 50.0 - height))
	draw_rect(Rect2(origin, Vector2(width, height)), Color("f3f0e7f2"))
	draw_rect(Rect2(origin, Vector2(width, height)), Color("8f9b86"), false, 1.0)
	var tip: Vector2 = Vector2(at.x, origin.y + height + 5.0)
	draw_colored_polygon(PackedVector2Array([
		Vector2(at.x - 5, origin.y + height),
		Vector2(at.x + 5, origin.y + height), tip]), Color("f3f0e7f2"))
	draw_line(Vector2(at.x - 5, origin.y + height), tip, Color("8f9b86"), 1.0)
	draw_line(Vector2(at.x + 5, origin.y + height), tip, Color("8f9b86"), 1.0)
	for i: int in range(lines.size()):
		var line_width: float = font.get_string_size(lines[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x
		draw_string(font, origin + Vector2(roundf((width - line_width) / 2.0), 17.0 + i * line_height),
			lines[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color("2b3a34"))


func _mob_sheet(type: String) -> Texture2D:
	if _mob_sheets.has(type):
		return _mob_sheets[type]
	var path: String = "%s%s.png" % [MOB_DIR, type]
	var sheet: Texture2D = load(path) if ResourceLoader.exists(path) else null
	_mob_sheets[type] = sheet
	return sheet


func _draw_mob(at: Vector2, mob: Dictionary) -> void:
	var type: String = str(mob.get("type", "wolf"))
	var sheet: Texture2D = _mob_sheet(type)
	if sheet != null:
		var frame: int = int(_time * 4.0) % 4
		var row: int = 0
		var scale: float = 1.25 if type == "boss" else 1.0
		var shadow_w: float = 18.0 * scale
		draw_rect(Rect2(at + Vector2(-shadow_w * 0.5, -4), Vector2(shadow_w, 5)), Color("203d3640"))
		var origin: Vector2 = Vector2(at.x - CHAR_CELL * 0.5, at.y - CHAR_CELL + 2)
		draw_texture_rect_region(sheet, Rect2(origin, Vector2(CHAR_CELL, CHAR_CELL)),
			Rect2(frame * CHAR_CELL, row * CHAR_CELL, CHAR_CELL, CHAR_CELL))
	else:
		var body: Color = Color("8c5f53") if type == "boss" else Color("7b7561")
		var scale: float = 1.25 if type == "boss" else 1.0
		draw_rect(Rect2(at + Vector2(-8 * scale, -3), Vector2(16 * scale, 5)), Color("203d3640"))
		draw_rect(Rect2(at + Vector2(-5 * scale, -15 * scale), Vector2(10 * scale, 13 * scale)), body.darkened(0.2))
		draw_rect(Rect2(at + Vector2(-4 * scale, -14 * scale), Vector2(8 * scale, 11 * scale)), body)
		draw_rect(Rect2(at + Vector2(-3 * scale, -21 * scale), Vector2(6 * scale, 6 * scale)), Color("d4b690"))
		draw_rect(Rect2(at + Vector2(-4 * scale, -23 * scale), Vector2(8 * scale, 4 * scale)), Color("354039"))
		draw_line(at + Vector2(5 * scale, -18 * scale), at + Vector2(10 * scale, -4 * scale), Color("c2bea5"), 2)
		if type == "boss":
			draw_rect(Rect2(at + Vector2(-5, -25), Vector2(11, 3)), Color("a25845"))
	var title: String = "%s  Lv.%s" % [str(mob.get("name", "野狼")), str(mob.get("level", 1))]
	_label(self, title, at + Vector2(0, -34), 10, Color("e2c0a1"), true)
