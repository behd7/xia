extends RefCounted
## Default bindings by intent. No rebinding UI/persistence or gamepad claims.
const KEYS := {
	"move_left": [KEY_A, KEY_LEFT], "move_right": [KEY_D, KEY_RIGHT],
	"move_up": [KEY_W, KEY_UP], "move_down": [KEY_S, KEY_DOWN],
	"open_character": [KEY_I], "open_inventory": [KEY_B],
	"open_skills": [KEY_K], "open_map": [KEY_M],
	"interact": [KEY_E], "close_panel": [KEY_ESCAPE],
}
const MOUSE := {"zoom_in": MOUSE_BUTTON_WHEEL_UP, "zoom_out": MOUSE_BUTTON_WHEEL_DOWN}

static func install() -> void:
	for action: String in KEYS:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
		if not InputMap.action_get_events(action).is_empty():
			continue # Preserve an existing/custom binding; repeated setup is idempotent.
		for key: int in KEYS[action]:
			var event := InputEventKey.new()
			event.physical_keycode = key
			InputMap.action_add_event(action, event)
	for action: String in MOUSE:
		if not InputMap.has_action(action):
			InputMap.add_action(action)
		if not InputMap.action_get_events(action).is_empty():
			continue
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE[action]
		InputMap.action_add_event(action, event)

static func hint(action: String) -> String:
	for event: InputEvent in InputMap.action_get_events(action):
		if event is InputEventKey:
			var key: int = event.physical_keycode if event.physical_keycode else event.keycode
			if event.physical_keycode and DisplayServer.get_name() != "headless":
				var localized: int = DisplayServer.keyboard_get_label_from_physical(event.physical_keycode)
				if localized != 0:
					key = localized
			return "Esc" if key == KEY_ESCAPE else OS.get_keycode_string(key)
	return "—"

static func hints() -> Dictionary:
	var result := {}
	for action: String in KEYS:
		result[action] = hint(action)
	return result
