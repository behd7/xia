extends MeshInstance3D
## A shared low-poly body casts real light/shadow-map silhouettes in every view.
## Never rendered as a character, never used for gameplay or occlusion ray tests.
const Projector = preload("res://scripts/hd2d/projection.gd")
const poses = [
	preload("res://assets/hd2d/static/shadows/pose_0.res"),
	preload("res://assets/hd2d/static/shadows/pose_1.res"),
	preload("res://assets/hd2d/static/shadows/pose_2.res"),
	preload("res://assets/hd2d/static/shadows/pose_3.res"),
	preload("res://assets/hd2d/static/shadows/pose_4.res"),
	preload("res://assets/hd2d/static/shadows/pose_5.res"),
	preload("res://assets/hd2d/static/shadows/pose_6.res"),
	preload("res://assets/hd2d/static/shadows/pose_7.res"),
	preload("res://assets/hd2d/static/shadows/pose_8.res"),
]

func set_pose(walk_frame: int, world_direction: Vector2) -> void:
	mesh = poses[0 if walk_frame < 0 else 1 + posmod(walk_frame, 8)]
	if not world_direction.is_zero_approx():
		rotation.y = atan2(world_direction.x, world_direction.y)
