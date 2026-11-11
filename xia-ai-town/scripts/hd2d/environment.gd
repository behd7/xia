extends Node3D
var sun: DirectionalLight3D
var environment: Environment
@export var attributes: CameraAttributesPractical
var quality := "standard"
var effects := true
var distance_fog := true

func _ready() -> void:
	sun = $Sun
	environment = $WorldEnvironment.environment

func update(game_seconds: float, scenery: Node3D, reference_depth: float = 100.0, player_position: Vector3 = Vector3.ZERO) -> void:
	# Same clock contract as Main: caller supplies extrapolated game seconds.
	var hour := fposmod(game_seconds / 3600.0, 24.0)
	var daylight := smoothstep(5.0, 8.0, hour) * (1.0 - smoothstep(17.0, 20.0, hour))
	var dusk := smoothstep(15.0, 18.0, hour) * (1.0 - smoothstep(18.0, 20.0, hour))
	sun.light_color = Color("b4c9e9").lerp(Color("ffe8c9"), daylight).lerp(Color("ffbc8c"), dusk*0.6)
	# A raking warm key separates roof slopes and walls. The old .7 fill
	# flattened the recesses; retain a stronger cool floor at night for paths.
	sun.light_energy = lerpf(0.18, 1.55, daylight)
	environment.ambient_light_energy = lerpf(0.48, 0.34, daylight)
	environment.ambient_light_color = Color("8da9d3").lerp(Color("b4c5cf"), daylight)
	environment.fog_enabled = effects and distance_fog and quality == "standard"
	# Perspective zoom moves the camera. Keep the same mild 15-unit far
	# margin beyond the player, instead of leaving the old fixed 115 distance.
	attributes.dof_blur_far_distance = reference_depth + 15.0
	attributes.dof_blur_far_enabled = effects and quality == "standard"
	var nearby_lights: Array = []
	if quality == "light" and scenery.world_assets != null:
		nearby_lights.assign(scenery.lanterns)
		nearby_lights.sort_custom(func(a: OmniLight3D, b: OmniLight3D): return a.global_position.distance_squared_to(player_position) < b.global_position.distance_squared_to(player_position))
		nearby_lights.resize(mini(3, nearby_lights.size()))
	for i: int in range(scenery.lanterns.size()):
		var light: OmniLight3D = scenery.lanterns[i]
		light.light_energy = (1.0-daylight)*0.8
		light.visible = quality == "standard" or (nearby_lights.has(light) if scenery.world_assets != null else i == 0)
	if scenery.lantern_material:
		scenery.lantern_material.set_shader_parameter("glow", (1.0-daylight)*0.55)
