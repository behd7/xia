@tool
extends MultiMeshInstance3D
## Rebind authored data: the headless dummy renderer cannot serialize GPU buffers.
## Mesh, transforms and bounds are all authored offline; no layout is generated here.
func _ready() -> void:
	if multimesh == null or not multimesh.has_meta("authored_buffer"):
		return
	if DisplayServer.get_name() == "headless":
		# The dummy renderer has no GPU buffer to bind; the saved metadata is intact.
		return
	multimesh = multimesh.duplicate()
	multimesh.buffer = multimesh.get_meta("authored_buffer")
	multimesh.custom_aabb = multimesh.get_meta("authored_bounds")
