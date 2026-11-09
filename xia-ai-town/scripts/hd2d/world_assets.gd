extends RefCounted
## Runtime inventory/material lookup; placement lives in town_scenery.tscn.
const ROOT := "res://assets/hd2d/"
var scenery: Node3D
var data: Dictionary
var placed: Dictionary = {}

func setup(owner_node: Node3D, inventory: Dictionary) -> void:
	scenery = owner_node
	data = JSON.parse_string(FileAccess.get_file_as_string(ROOT + "environment.json"))
	placed = inventory.duplicate()

func material(key: String) -> ShaderMaterial:
	return scenery.materials["p3/" + key]
