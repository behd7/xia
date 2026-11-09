extends RefCounted
## A shared low-poly body casts real light/shadow-map silhouettes in every view.
## Never rendered as a character, never used for gameplay or occlusion ray tests.
static func _capsule(tool: SurfaceTool, a: Vector3, b: Vector3, radius: float) -> void:
	var capsule := CapsuleMesh.new()
	capsule.radius = radius
	capsule.height = a.distance_to(b) + radius * 2.0
	capsule.radial_segments = 8
	capsule.rings = 2
	var basis := Basis(Quaternion(Vector3.UP, (b-a).normalized()))
	tool.append_from(capsule, 0, Transform3D(basis, (a+b)*0.5))

static func _build_pose(walk_frame: int) -> ArrayMesh:
	var tool := SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	var walking := walk_frame >= 0
	var phase := float(walk_frame) / 8.0
	# Physical body height is independent of billboard's camera-pitch compensation.
	var bob := cos(phase * TAU * 2.0) * 0.025 if walking else 0.0
	_capsule(tool, Vector3(0, 1.93+bob, 0), Vector3(0, 2.07+bob, 0), 0.19)
	_capsule(tool, Vector3(0, 1.04+bob, 0), Vector3(0, 1.57+bob, 0), 0.24)
	# Robe volume and a single back scabbard make the silhouette less tubular.
	_capsule(tool, Vector3(0, 0.80+bob, 0), Vector3(0, 1.09+bob, 0), 0.28)
	_capsule(tool, Vector3(-0.23, 1.85+bob, -0.17), Vector3(0.25, 0.93+bob, -0.17), 0.035)
	for side: float in [-1.0, 1.0]:
		var t := fposmod(phase + (0.5 if side > 0.0 else 0.0), 1.0)
		var stride := cos(t*TAU)*0.24 if walking else 0.0
		var lift := sin((t-0.5)*TAU)*0.10 if walking and t > 0.5 else 0.0
		var foot := Vector3(side*0.14, 0.09+lift, stride)
		var hip := Vector3(side*0.14, 1.01+bob, 0)
		var knee := (hip+foot)*0.5 + Vector3(0, 0, 0.10+lift*0.4)
		_capsule(tool, hip, knee, 0.115)
		_capsule(tool, knee, foot, 0.085)
		_capsule(tool, foot+Vector3(0, 0, -0.035), foot+Vector3(0, 0, 0.09), 0.085)
		var shoulder := Vector3(side*0.27, 1.63+bob, 0)
		var elbow := Vector3(side*0.34, 1.32+bob, -stride*0.3)
		var hand := Vector3(side*0.33, 1.03+bob, -stride*0.75)
		_capsule(tool, shoulder, elbow, 0.10)
		_capsule(tool, elbow, hand, 0.075)
	# One mesh/surface per actor in the shadow pass, all nine poses shared.
	var material := StandardMaterial3D.new()
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	tool.set_material(material)
	return tool.commit()
