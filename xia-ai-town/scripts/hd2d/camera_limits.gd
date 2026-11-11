extends RefCounted
## Full-town camera limits use the entire ground footprint, including rotation.
const Projector = preload("res://scripts/hd2d/projection.gd")
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")

static func ground_corners(camera: Camera3D, pixels: Vector2) -> PackedVector3Array:
	var result := PackedVector3Array()
	var size := pixels.max(Vector2.ONE)
	var aspect := size.x / size.y
	var half_height := camera.size * 0.5 if camera.projection == Camera3D.PROJECTION_ORTHOGONAL else tan(deg_to_rad(camera.fov) * 0.5)
	# Include a two-pixel guard outside the raster, avoiding an exposed seam
	# from subpixel snapping, edge filtering or the last screen pixel.
	var guard := Vector2.ONE + Vector2(4.0 / size.x, 4.0 / size.y)
	for sign_x: float in [-1.0, 1.0]:
		for sign_y: float in [-1.0, 1.0]:
			var offset := camera.global_basis.x * sign_x * guard.x * half_height * aspect + camera.global_basis.y * sign_y * guard.y * half_height
			var origin := camera.global_position
			var direction := -camera.global_basis.z
			if camera.projection == Camera3D.PROJECTION_ORTHOGONAL:
				origin += offset
			else:
				direction += offset
			if direction.y >= -0.00001:
				return PackedVector3Array()
			result.append(origin + direction * ((Layout.SCENERY_FLOOR * Projector.SCALE - origin.y) / direction.y))
	return result

static func ground_rect(camera: Camera3D, pixels: Vector2) -> Rect2:
	var corners := ground_corners(camera, pixels)
	if corners.is_empty():
		return Rect2()
	var first := Vector2(corners[0].x, corners[0].z) / Projector.SCALE
	var rect := Rect2(first, Vector2.ZERO)
	for at: Vector3 in corners:
		rect = rect.expand(Vector2(at.x, at.z) / Projector.SCALE)
	return rect

static func _reference_rect(camera: Camera3D, pixels: Vector2, reference: Vector2, reference_height: float, pitch: float, yaw: float, body: Rect2) -> Rect2:
	var rect := Rect2()
	var first := true
	for x: float in [body.position.x, body.end.x]:
		for height: float in [body.position.y, body.end.y]:
			var point := Projector.point(reference, reference_height) + Projector.right(yaw) * x * Projector.SCALE + Vector3.UP * Projector.upright_height(height, pitch)
			var local := camera.to_local(point)
			var density := pixels.y / camera.size if camera.projection == Camera3D.PROJECTION_ORTHOGONAL else pixels.y / (2.0 * tan(deg_to_rad(camera.fov) * 0.5) * -local.z)
			var screen := pixels * 0.5 + Vector2(local.x, -local.y) * density
			rect = Rect2(screen, Vector2.ZERO) if first else rect.expand(screen)
			first = false
	return rect

static func configure(camera: Camera3D, pixels: Vector2, zoom: int, focus: Vector2, pitch: float, yaw: float, orthogonal: bool, fov: float, reference: Vector2, focus_height: float, reference_height: float, body: Rect2 = Rect2(-16, -3, 32, 44)) -> Vector2:
	var fitted := focus
	var bounds := Layout.SCENERY_BOUNDS.grow(-32.0)
	for attempt: int in range(32):
		# Reconfigure rather than moving the eye alone: keep 1/2/3× calibrated
		# at the player's foot depth even when a limit changes the follow point.
		Projector.configure(camera, pixels, zoom, fitted, pitch, yaw, orthogonal, fov, reference, focus_height, reference_height)
		# A fixed follow-center clamp can crop the head at 3×/65° in a corner.
		# Keep the full animation envelope inside the native world viewport.
		var frame := _reference_rect(camera, pixels, reference, reference_height, pitch, yaw, body)
		var safe_frame := Rect2(Vector2(24, 72), pixels - Vector2(48, 136))
		var screen_shift := Vector2.ZERO
		if frame.position.x < safe_frame.position.x:
			screen_shift.x = safe_frame.position.x - frame.position.x
		elif frame.end.x > safe_frame.end.x:
			screen_shift.x = safe_frame.end.x - frame.end.x
		if frame.position.y < safe_frame.position.y:
			screen_shift.y = safe_frame.position.y - frame.position.y
		elif frame.end.y > safe_frame.end.y:
			screen_shift.y = safe_frame.end.y - frame.end.y
		if screen_shift.length_squared() > 0.0625 and attempt < 31:
			var side := Projector.right(yaw)
			var back := Projector.ground_back(yaw)
			fitted -= Vector2(side.x, side.z) * screen_shift.x / zoom + Vector2(back.x, back.z) * screen_shift.y / (zoom * sin(deg_to_rad(pitch)))
			continue
		var rect := ground_rect(camera, pixels)
		var shift := Vector2.ZERO
		if rect.position.x < bounds.position.x:
			shift.x = bounds.position.x - rect.position.x
		elif rect.end.x > bounds.end.x:
			shift.x = bounds.end.x - rect.end.x
		if rect.position.y < bounds.position.y:
			shift.y = bounds.position.y - rect.position.y
		elif rect.end.y > bounds.end.y:
			shift.y = bounds.end.y - rect.end.y
		if shift.length_squared() <= 0.0625 or attempt == 31:
			break
		fitted += shift * 0.5
	# A low-pitch, wide or large native window can see farther than the old
	# fixed 240-unit clip. Include the complete terrain footprint in the draw.
	for corner: Vector3 in ground_corners(camera, pixels):
		camera.far = maxf(camera.far, -camera.to_local(corner).z + 8.0)
	return fitted
