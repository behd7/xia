extends Control
## Main is the only input/state owner. This node only presents its world.
## The camera is fixed (Projector.GAME_PITCH/GAME_YAW): no panel, drag or reset.
var viewport: SubViewport
var view: Node3D
var overlay: Control

func setup(world: Node2D) -> void:
	viewport = $WorldContainer/WorldViewport
	view = $WorldContainer/WorldViewport/TownView3D
	overlay = $WorldOverlay
	view.setup(world, true)
	var quality := OS.get_environment("XIA_HD2D_QUALITY")
	view.quality = quality if quality in ["standard", "light"] else "standard"

func update(world: Node2D, zoom: int, game_seconds: float, delta: float, target: String) -> void:
	var pixels := Vector2i(maxi(1, roundi(size.x)), maxi(1, roundi(size.y)))
	if viewport.size != pixels:
		viewport.size = pixels
	view.zoom = zoom
	view.update(world, Vector2(pixels), game_seconds, delta, target)
	overlay.update(view, delta)
