extends RefCounted
## Only this module knows the world-pixel -> 3D conversion. Never upload 3D values.
const SCALE := 1.0 / 16.0
const PITCH := 38.0
const YAW := 30.0
const FOV := 35.0
const MIN_FOV := 30.0
const MAX_FOV := 40.0
const MIN_PITCH := 30.0
const MAX_PITCH := 65.0
const PITCH_PRESETS := [38.0, 45.0, 40.0, 55.0]
const BODY_PIXELS := 36.0

static func fixture_pitch() -> float:
	var value := OS.get_environment("XIA_HD2D_PITCH")
	return clampf(float(value), MIN_PITCH, MAX_PITCH) if value.is_valid_float() else PITCH

static func fixture_yaw() -> float:
	var value := OS.get_environment("XIA_HD2D_YAW")
	return wrapf(float(value), -180.0, 180.0) if value.is_valid_float() else YAW

static func fixture_fov() -> float:
	var value := OS.get_environment("XIA_HD2D_FOV")
	return clampf(float(value), MIN_FOV, MAX_FOV) if value.is_valid_float() else FOV

static func fixture_orthogonal() -> bool:
	return OS.get_environment("XIA_HD2D_PROJECTION") == "orthogonal"

static func point(logical: Vector2, height: float = 0.0) -> Vector3:
	return Vector3(logical.x, height, logical.y) * SCALE

static func upright_height(pixels: float, pitch: float = PITCH) -> float:
	# Compensate the vertical plane's foreshortening. Perspective still changes
	# its apparent size with depth; never scale every actor to a fixed screen size.
	return pixels * SCALE / cos(deg_to_rad(pitch))

static func right(yaw: float) -> Vector3:
	return Vector3(cos(deg_to_rad(yaw)), 0, -sin(deg_to_rad(yaw)))

static func ground_back(yaw: float) -> Vector3:
	return Vector3(sin(deg_to_rad(yaw)), 0, cos(deg_to_rad(yaw)))

static func camera_relative_direction(direction: Vector2, yaw: float) -> Vector2:
	var side := right(yaw)
	var back := ground_back(yaw)
	return Vector2(side.x, side.z) * direction.x + Vector2(back.x, back.z) * direction.y

static func screen_relative_direction(direction: Vector2, yaw: float) -> Vector2:
	var side := right(yaw)
	var back := ground_back(yaw)
	return Vector2(direction.dot(Vector2(side.x, side.z)), direction.dot(Vector2(back.x, back.z)))

static func configure(camera: Camera3D, pixels: Vector2, zoom: int, focus: Vector2, pitch: float = PITCH, yaw: float = YAW, orthogonal: bool = false, fov: float = FOV, reference: Vector2 = Vector2.INF) -> void:
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL if orthogonal else Camera3D.PROJECTION_PERSPECTIVE
	camera.keep_aspect = Camera3D.KEEP_HEIGHT
	camera.size = maxf(pixels.y, 1.0) * SCALE / zoom
	camera.fov = clampf(fov, MIN_FOV, MAX_FOV)
	camera.near = 0.1
	camera.far = 240.0
	var center := point(focus)
	var unit := SCALE / zoom
	var side := right(yaw)
	var back := ground_back(yaw)
	var elevation := deg_to_rad(pitch)
	# Snap only the displayed camera, at the reference-depth pixel density.
	# Perspective cannot put every depth on the same integer pixel grid.
	center = side * snappedf(center.dot(side), unit) + back * (snappedf(center.dot(back) * sin(elevation), unit) / sin(elevation))
	var toward_camera := Vector3.UP * sin(elevation) + back * cos(elevation)
	var distance := 100.0
	if not orthogonal:
		var reference_point := point(focus if reference == Vector2.INF else reference)
		# KEEP_HEIGHT makes fov vertical. At the player's foot depth, one world
		# logical pixel spans zoom screen pixels horizontally, independent of FOV.
		var reference_depth := camera.size / (2.0 * tan(deg_to_rad(camera.fov) * 0.5))
		distance = reference_depth + (reference_point - center).dot(toward_camera)
	camera.position = center + toward_camera * distance
	camera.rotation_degrees = Vector3(-pitch, yaw, 0)

static func ray_origin(camera: Camera3D, at: Vector3) -> Vector3:
	if camera.projection == Camera3D.PROJECTION_ORTHOGONAL:
		return at + camera.global_basis.z * camera.far
	# Start at the near plane on the SAME perspective ray as the visible pixel.
	# Geometry behind the camera/inside its near clip cannot hide a drawn actor.
	return camera.project_position(camera.unproject_position(at), camera.near)
