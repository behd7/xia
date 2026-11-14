extends SceneTree
## Parse all production scripts AND GPU-only probes before selecting a runner.
## Shader syntax errors are emitted in the engine log. The Python gate requires
## that log to be clean in addition to this exit code and final marker.
var scripts := 0
var shaders := 0
var failures := 0

func _initialize() -> void:
	_scan("res://scripts")
	for file: String in DirAccess.get_files_at("res://tools"):
		if file.begins_with("hd2d_") and file.ends_with(".gd"):
			_check("res://tools/" + file)
	print("HD2D_SCRIPT_CHECKS: scripts=%d shaders=%d failures=%d" % [scripts, shaders, failures])
	quit(1 if failures else 0)

func _scan(directory: String) -> void:
	for file: String in DirAccess.get_files_at(directory):
		if file.ends_with(".gd") or file.ends_with(".gdshader"):
			_check(directory.path_join(file))
	for folder: String in DirAccess.get_directories_at(directory):
		if not folder.begins_with("."):
			_scan(directory.path_join(folder))

func _check(path: String) -> void:
	var resource := load(path)
	if resource is GDScript:
		scripts += 1
		if not resource.can_instantiate():
			failures += 1
			printerr("SCRIPT_CHECK_FAIL: ", path)
	elif resource is Shader:
		shaders += 1
		# load() is lazy. This invokes the engine parser, not a GPU driver.
		resource.get_shader_uniform_list()
	else:
		failures += 1
		printerr("SCRIPT_CHECK_FAIL: could not load ", path)
