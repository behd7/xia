extends RefCounted
## Static tree prefab adapter used by the isolated occlusion probe.
const Projector = preload("res://scripts/hd2d/projection.gd")

static func build(scenery: Node3D, at: Vector2, index: int) -> void:
	var tree: Node3D = load("res://scenes/hd2d/pine.tscn").instantiate()
	tree.name = "松树%d_叶簇体积" % index
	tree.position = Projector.point(at)
	tree.rotation.y = deg_to_rad(index * 43.0)
	scenery.add_child(tree)
	scenery.bind_branch(tree, StringName("prop/tree/%d" % index))
