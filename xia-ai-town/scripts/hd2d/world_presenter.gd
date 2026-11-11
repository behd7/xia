extends Control
## Main is the only input/state owner. This node only presents its world.
var viewport: SubViewport
var view: Node3D
var overlay: Control
var camera_panel: PanelContainer
var sliders: Dictionary = {}
var readout: Label
var camera_dragging := false

func setup(world: Node2D) -> void:
	viewport = $WorldContainer/WorldViewport
	view = $WorldContainer/WorldViewport/TownView3D
	overlay = $WorldOverlay
	view.setup(world, true)
	var quality := OS.get_environment("XIA_HD2D_QUALITY")
	view.quality = quality if quality in ["standard", "light"] else "standard"
	_bind_camera_panel()

func update(world: Node2D, zoom: int, game_seconds: float, delta: float, target: String, locked: bool) -> void:
	if locked:
		camera_dragging = false
	var pixels := Vector2i(maxi(1, roundi(size.x)), maxi(1, roundi(size.y)))
	if viewport.size != pixels:
		viewport.size = pixels
	view.zoom = zoom
	view.update(world, Vector2(pixels), game_seconds, delta, target)
	overlay.update(view, delta)

func _bind_camera_panel() -> void:
	camera_panel = $CameraPanel
	readout = $CameraPanel/Content/Readout
	sliders = {"fov": $CameraPanel/Content/FovSlider,
		"pitch": $CameraPanel/Content/PitchSlider, "yaw": $CameraPanel/Content/YawSlider}
	for property: String in sliders:
		var slider: HSlider = sliders[property]
		slider.editable = property != "fov" or not view.orthogonal
		slider.value_changed.connect(func(value: float):
			view.call("set_" + property, value)
			_sync_controls())
	$CameraPanel/Content/Reset.pressed.connect(reset_camera)
	_sync_controls()

func _sync_controls() -> void:
	for key: String in sliders:
		sliders[key].set_value_no_signal(float(view.get(key)))
	readout.text = "视野 %.1f°  ·  俯角 %.1f°  ·  方位 %.1f°" % [view.fov, view.pitch, view.yaw]

func toggle_camera_panel() -> void:
	camera_panel.visible = not camera_panel.visible

func reset_camera() -> void:
	view.reset_camera()
	_sync_controls()

func handle_event(event: InputEvent, locked: bool) -> bool:
	if locked:
		camera_dragging = false
		return false
	if event is InputEventMouseMotion and camera_dragging:
		if not Input.is_mouse_button_pressed(MOUSE_BUTTON_MIDDLE):
			camera_dragging = false
			return false
		view.set_yaw(view.yaw - event.relative.x * 0.3)
		view.set_pitch(view.pitch + event.relative.y * 0.15)
		_sync_controls()
		return true
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_MIDDLE and get_global_rect().has_point(event.position):
		camera_dragging = true
		return true
	return false
