extends Node3D
const Projector = preload("res://scripts/hd2d/projection.gd")
const SHEET = preload("res://assets/hd2d/characters/hero.png")
static var body_material: ShaderMaterial = preload("res://assets/hd2d/static/materials/actor_body.tres")
const OUTLINE_SHADER = preload("res://scripts/hd2d/shaders/actor_outline.gdshader")
## Contour colours by role, readable on roofs, walls and pine crowns alike.
const OUTLINE_COLORS := {"hero": Color("fff0b8"), "npc": Color("8fe6d0"), "mob": Color("ff8a70")}
static var outline_materials: Dictionary = {}
static var legacy_data: Dictionary = {}
static var legacy_materials: Dictionary = {}
static var p3_manifest: Dictionary = {}
var p3_key := ""
var sheet: Texture2D = SHEET
var pixel_ratio := 0.5
var direction_rows := [0, 1, 2, 3, 4, 5, 6, 7]

const DIRECTIONS := ["s", "sw", "w", "nw", "n", "ne", "e", "se"]
var sprite: Sprite3D
var body_shadow: MeshInstance3D
var contact_shadow: MeshInstance3D
var metadata: Dictionary
var stride_distance := 0.0
var facing := 0 # Camera-relative animation row; camera rotation alone preserves it.
var elapsed := 0.0
var frame := -1
var last_position := Vector2.ZERO
var initialized := false
var logical_position := Vector2.ZERO
var scenic_terrain := false
var moving := false
var pitch := Projector.PITCH
var yaw := 0.0
var occlusion_bounds := Rect2()
var outline := 0.0
var outline_material: ShaderMaterial

# P2 fallback remains available until every P3 actor is present.
func configure_legacy(texture: Texture2D) -> void:
	p3_key = ""
	sheet = texture
	pixel_ratio = 1.0
	direction_rows = [0, 1, 1, 3, 3, 3, 2, 0]

func configure_p3(asset_key: String) -> void:
	if p3_manifest.is_empty():
		p3_manifest = JSON.parse_string(FileAccess.get_file_as_string("res://assets/hd2d/manifest.json"))
	assert(p3_manifest.has("actors") and p3_manifest.actors.has(asset_key), "Missing P3 actor asset: " + asset_key)
	p3_key = asset_key
	var data: Dictionary = p3_manifest.actors[asset_key]
	sheet = load("res://assets/hd2d/" + str(data.sheet))
	pixel_ratio = float(data.pixel_ratio)
	direction_rows = [0, 1, 2, 3, 4, 5, 6, 7]

static func has_p3_asset(asset_key: String) -> bool:
	if p3_manifest.is_empty():
		p3_manifest = JSON.parse_string(FileAccess.get_file_as_string("res://assets/hd2d/manifest.json"))
	return p3_manifest.has("actors") and p3_manifest.actors.has(asset_key)

func animation_data() -> Dictionary:
	if not p3_key.is_empty():
		return p3_manifest.actors[p3_key]
	if sheet == SHEET:
		return JSON.parse_string(FileAccess.get_file_as_string("res://assets/hd2d/manifest.json"))
	var key := sheet.resource_path
	if not legacy_data.has(key):
		var frames: Array[Dictionary] = []
		var image := sheet.get_image()
		for row: int in range(4):
			for column: int in range(4):
				var bounds := image.get_region(Rect2i(column * 32, row * 32, 32, 32)).get_used_rect()
				frames.append({"bounds": [bounds.position.x, bounds.position.y, bounds.size.x, bounds.size.y], "foot": [16, 30]})
		legacy_data[key] = {"columns": 4, "directions": ["s", "w", "e", "n"],
			"frame_size": [32, 32], "idle_columns": [0], "walk_columns": [0, 1, 2, 3],
			"walk_cycle_distance": 64.0, "frames": frames}
	return legacy_data[key]

func _ready() -> void:
	metadata = animation_data()
	# A stable full-animation envelope avoids selecting/releasing building parts
	# on every idle/walk frame. Values are logical pixels relative to the foot.
	for info: Dictionary in metadata.frames:
		var b: Array = info.bounds
		var foot: Array = info.foot
		var rect := Rect2((float(b[0]) - float(foot[0])) * pixel_ratio,
			(float(foot[1]) - float(b[1]) - float(b[3])) * pixel_ratio, float(b[2]) * pixel_ratio, float(b[3]) * pixel_ratio)
		occlusion_bounds = rect if occlusion_bounds.size == Vector2.ZERO else occlusion_bounds.merge(rect)
	sprite = $Sprite
	body_shadow = $BodyShadow
	contact_shadow = $ContactShadow
	sprite.texture = sheet
	sprite.hframes = int(metadata.columns)
	sprite.vframes = metadata.directions.size()
	sprite.pixel_size = pixel_ratio * Projector.SCALE
	sprite.scale.y = 1.0 / cos(deg_to_rad(pitch))
	if sheet == SHEET:
		sprite.material_override = body_material
	else:
		if not legacy_materials.has(sheet.resource_path):
			var material := body_material.duplicate() as ShaderMaterial
			material.set_shader_parameter("albedo_texture", sheet)
			legacy_materials[sheet.resource_path] = material
		sprite.material_override = legacy_materials[sheet.resource_path]
	var outline_key := sheet.resource_path
	if not outline_materials.has(outline_key):
		var contour := ShaderMaterial.new()
		contour.shader = OUTLINE_SHADER
		# Scenery sorts in the alpha pass; the contour must be drawn after it.
		contour.render_priority = 16
		contour.set_shader_parameter("albedo_texture", sheet)
		contour.set_shader_parameter("frames", Vector2(sprite.hframes, sprite.vframes))
		contour.set_shader_parameter("edge_texels", maxf(1.0, roundf(1.0 / pixel_ratio)))
		outline_materials[outline_key] = contour
	outline_material = outline_materials[outline_key]
	_set_frame(0)

func set_outline_kind(kind: String) -> void:
	sprite.set_instance_shader_parameter("outline_color", OUTLINE_COLORS.get(kind, OUTLINE_COLORS.npc))

## 0 removes the extra pass; occlusion.gd drives it while cover hides the body.
func set_outline(strength: float) -> void:
	if is_equal_approx(outline, strength):
		return
	outline = strength
	sprite.material_overlay = outline_material if strength > 0.0 else null
	sprite.set_instance_shader_parameter("outline_strength", strength)

func set_pitch(value: float) -> void:
	pitch = value
	sprite.scale.y = 1.0 / cos(deg_to_rad(pitch))
	var current := frame
	frame = -1
	_set_frame(current)

func set_yaw(value: float) -> void:
	yaw = value
	# CPU transform matches fixed-Y billboard for foot offsets and ray envelopes.
	sprite.rotation.y = deg_to_rad(yaw)
	if not initialized:
		body_shadow.rotation.y = deg_to_rad(yaw)
	# Rotating the camera turns the upright, not the remembered animation row.
	var current := frame
	frame = -1
	_set_frame(current)

func sync(at: Vector2, delta: float, forced_direction: int = -1) -> void:
	var step := at - last_position if initialized else Vector2.ZERO
	var was_moving := moving
	moving = step.length_squared() > 0.0001
	if forced_direction >= 0:
		facing = forced_direction
	elif moving:
		var direction := Projector.screen_relative_direction(step, yaw)
		facing = posmod(roundi(atan2(-direction.x, direction.y) / (PI / 4.0)), 8)
	if moving != was_moving:
		elapsed = 0.0
		stride_distance = 0.0
	elapsed += delta
	var walk_index := -1
	var col := int(metadata.idle_columns[int(elapsed / 0.5) % metadata.idle_columns.size()])
	if moving:
		# Advance from actual travel, so stalls/wall slides do not skate in place.
		# Fixture teleports start a new cycle rather than jumping arbitrary poses.
		stride_distance = fposmod(stride_distance + step.length(), float(metadata.walk_cycle_distance)) if step.length() < 32.0 else 0.0
		walk_index = int(stride_distance / float(metadata.walk_cycle_distance) * metadata.walk_columns.size()) % metadata.walk_columns.size()
		col = int(metadata.walk_columns[walk_index])
	_set_frame(int(direction_rows[facing]) * sprite.hframes + col)
	var shadow_direction := step
	if forced_direction >= 0:
		shadow_direction = Projector.camera_relative_direction(Vector2(-sin(facing*PI/4.0), cos(facing*PI/4.0)), yaw)
	body_shadow.set_pose(walk_index, shadow_direction)
	logical_position = at
	position = Projector.point(at, preload("res://scripts/hd2d/scenic_layout.gd").height_at(at) if scenic_terrain else 0.0)
	last_position = at
	initialized = true

func _set_frame(value: int) -> void:
	if frame == value:
		return
	frame = value
	sprite.frame = value
	var info: Dictionary = metadata.frames[value]
	# Sprite center is raised along WORLD UP. No camera.up offset or full billboard.
	sprite.position = Projector.right(yaw) * ((float(metadata.frame_size[0]) * 0.5 - float(info.foot[0])) * sprite.pixel_size) + Vector3.UP * ((float(info.foot[1]) - float(metadata.frame_size[1]) * 0.5) * sprite.pixel_size * sprite.scale.y)

func screen_rect(camera: Camera3D, margin: float = 0.0) -> Rect2:
	var info: Dictionary = metadata.frames[frame]
	var bounds: Array = info.bounds
	var foot: Array = info.foot
	var side := Projector.right(yaw)
	var rect := Rect2()
	var first := true
	# A perspective upright is a trapezoid: the top corners are nearer/larger.
	# Two opposite corners can miss a shoulder and place the name off center.
	for x: float in [float(bounds[0]), float(bounds[0])+float(bounds[2])]:
		for y: float in [float(bounds[1]), float(bounds[1])+float(bounds[3])]:
			var at := global_position + side * ((x-float(foot[0]))*sprite.pixel_size) + Vector3.UP * Projector.upright_height((float(foot[1])-y)*pixel_ratio, pitch)
			var screen := camera.unproject_position(at)
			if first:
				rect = Rect2(screen, Vector2.ZERO)
				first = false
			else:
				rect = rect.expand(screen)
	return rect.grow(margin)

func occlusion_rect(camera: Camera3D) -> Rect2:
	# Use every frame's union, so the local reveal never pulses with the gait.
	var rect := Rect2()
	var first := true
	for x: float in [occlusion_bounds.position.x, occlusion_bounds.end.x]:
		for height: float in [occlusion_bounds.position.y, occlusion_bounds.end.y]:
			var at := global_position + Projector.right(yaw) * x * Projector.SCALE + Vector3.UP * Projector.upright_height(height, pitch)
			var screen := camera.unproject_position(at)
			if first:
				rect = Rect2(screen, Vector2.ZERO)
				first = false
			else:
				rect = rect.expand(screen)
	return rect

func occlusion_points() -> Array[Vector3]:
	var points: Array[Vector3] = []
	# Include shoulders/feet on both sides, not just three points down the spine.
	for x_fraction: float in [0.08, 0.5, 0.92]:
		var x := lerpf(occlusion_bounds.position.x, occlusion_bounds.end.x, x_fraction) * Projector.SCALE
		for y_fraction: float in [0.04, 0.36, 0.68, 0.96]:
			var height := lerpf(occlusion_bounds.position.y, occlusion_bounds.end.y, y_fraction)
			points.append(global_position + Projector.right(yaw) * x + Vector3.UP * Projector.upright_height(height, pitch))
	return points
