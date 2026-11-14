extends RefCounted
## One modal scope. Save recursive policies, never disable/re-enable business buttons.
var open_count := 0
var background: Array[Dictionary] = []
var previous_focus: WeakRef
var controls: Array[Control] = []

func open(root: Control, modal: Control) -> void:
	open_count += 1
	var focused := root.get_viewport().gui_get_focus_owner()
	previous_focus = weakref(focused) if focused != null else null
	background.clear()
	for child: Node in root.get_children():
		if child is Control and child != modal:
			background.append({"node": weakref(child), "focus": child.focus_behavior_recursive, "mouse": child.mouse_behavior_recursive})
			child.focus_behavior_recursive = Control.FOCUS_BEHAVIOR_DISABLED
			child.mouse_behavior_recursive = Control.MOUSE_BEHAVIOR_DISABLED

func close(fallback: Control = null) -> void:
	for state: Dictionary in background:
		var node = state.node.get_ref()
		if is_instance_valid(node):
			node.focus_behavior_recursive = state.focus
			node.mouse_behavior_recursive = state.mouse
	background.clear()
	controls.clear()
	var focused = previous_focus.get_ref() if previous_focus != null else null
	previous_focus = null
	if is_instance_valid(focused) and focused.is_visible_in_tree() and focused.focus_mode != Control.FOCUS_NONE:
		focused.grab_focus()
	elif is_instance_valid(fallback) and fallback.is_visible_in_tree() and (not fallback is BaseButton or not fallback.disabled):
		fallback.grab_focus()

func _collect(node: Node, result: Array[Control]) -> void:
	if node.is_queued_for_deletion():
		return
	if node is Control and node.is_visible_in_tree() and node.focus_mode == Control.FOCUS_ALL and not node is ScrollBar:
		if not node is BaseButton or not node.disabled:
			result.append(node)
	for child: Node in node.get_children():
		_collect(child, result)

func configure(modal: Control, body: Control) -> void:
	controls.clear()
	_collect(modal, controls)
	if controls.is_empty():
		return
	for i: int in controls.size():
		var node := controls[i]
		node.focus_next = node.get_path_to(controls[(i + 1) % controls.size()])
		node.focus_previous = node.get_path_to(controls[posmod(i - 1, controls.size())])
		for side: int in [SIDE_LEFT, SIDE_TOP, SIDE_RIGHT, SIDE_BOTTOM]:
			var neighbor := node
			var best := INF
			var axis: Vector2 = [Vector2.LEFT, Vector2.UP, Vector2.RIGHT, Vector2.DOWN][side]
			for candidate: Control in controls:
				var offset := candidate.get_global_rect().get_center() - node.get_global_rect().get_center()
				var along: float = offset.dot(axis)
				if along > 1.0:
					var across: float = absf(offset.cross(axis))
					var score: float = along + across * 3.0
					if score < best:
						best = score
						neighbor = candidate
			node.set_focus_neighbor(side, node.get_path_to(neighbor))
	var focused := modal.get_viewport().gui_get_focus_owner()
	if focused in controls:
		return # Preserve a dialogue LineEdit's focus, caret and active IME.
	var preferred: Array[Control] = []
	_collect(body, preferred)
	(preferred[0] if not preferred.is_empty() else controls[0]).grab_focus()
