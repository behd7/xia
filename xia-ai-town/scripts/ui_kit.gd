extends RefCounted

const INK := Color("293b37")
const MUTED := Color("788078")
const PAPER := Color("f3f0e5")
const LINE := Color("d9d9c9")
const JADE := Color("526b57")
const RED := Color("a75342")
const GOLD := Color("a48148")
const PANEL_TEXTURE_PATH := "res://assets/ui/panel.png"

static func box(color: Color = PAPER, border: Color = LINE, radius: int = 4, padding: int = 16) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = color
	style.border_color = border
	style.set_border_width_all(1)
	style.set_corner_radius_all(radius)
	style.content_margin_left = padding
	style.content_margin_right = padding
	style.content_margin_top = padding
	style.content_margin_bottom = padding
	return style

static func panel_box(padding: int = 16) -> StyleBox:
	if ResourceLoader.exists(PANEL_TEXTURE_PATH):
		var style := StyleBoxTexture.new()
		style.texture = load(PANEL_TEXTURE_PATH)
		style.texture_margin_left = 32
		style.texture_margin_top = 32
		style.texture_margin_right = 32
		style.texture_margin_bottom = 32
		style.content_margin_left = padding
		style.content_margin_top = padding
		style.content_margin_right = padding
		style.content_margin_bottom = padding
		return style
	return box(PAPER, LINE, 4, padding)

static func make_theme() -> Theme:
	var t := Theme.new()
	var font := SystemFont.new()
	font.font_names = PackedStringArray(["Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "Microsoft YaHei"])
	font.allow_system_fallback = true
	t.default_font = font
	t.default_font_size = 16
	t.set_color("font_color", "Label", INK)
	t.set_color("font_color", "Button", INK)
	t.set_color("font_hover_color", "Button", INK)
	t.set_color("font_disabled_color", "Button", Color("969c91"))
	t.set_stylebox("normal", "Button", box(Color("e9e9dc"), LINE, 3, 12))
	t.set_stylebox("hover", "Button", box(Color("dbe1cf"), JADE, 3, 12))
	t.set_stylebox("pressed", "Button", box(Color("cbd5c1"), JADE, 3, 12))
	t.set_stylebox("disabled", "Button", box(Color("e9e8df"), Color("deded3"), 3, 12))
	t.set_stylebox("focus", "Button", box(Color(0, 0, 0, 0), GOLD, 3, 12))
	t.set_stylebox("normal", "LineEdit", box(Color("fcfaf2"), LINE, 3, 12))
	t.set_stylebox("focus", "LineEdit", box(Color("fcfaf2"), JADE, 3, 12))
	t.set_color("font_color", "LineEdit", INK)
	t.set_color("font_placeholder_color", "LineEdit", MUTED)
	t.set_color("caret_color", "LineEdit", INK)
	t.set_stylebox("panel", "PanelContainer", panel_box(16))
	t.set_stylebox("background", "ProgressBar", box(Color("d8ddd0"), Color.TRANSPARENT, 1, 0))
	t.set_stylebox("fill", "ProgressBar", box(JADE, Color.TRANSPARENT, 1, 0))
	t.set_constant("separation", "VBoxContainer", 12)
	t.set_constant("separation", "HBoxContainer", 12)
	return t

static func label(text: String, size: int = 16, color: Color = INK) -> Label:
	var node := Label.new()
	node.text = text
	node.add_theme_font_size_override("font_size", size)
	node.add_theme_color_override("font_color", color)
	return node

static func paragraph(text: String, size: int = 15, color: Color = MUTED) -> Label:
	var node := label(text, size, color)
	node.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	node.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return node

static func button(text: String, action: Callable, primary: bool = false) -> Button:
	var node := Button.new()
	node.text = text
	node.custom_minimum_size.y = 42
	node.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	node.pressed.connect(action)
	if primary:
		node.add_theme_stylebox_override("normal", box(JADE, JADE, 3, 12))
		node.add_theme_stylebox_override("hover", box(Color("637f66"), JADE, 3, 12))
		node.add_theme_stylebox_override("pressed", box(Color("3c5444"), JADE, 3, 12))
		node.add_theme_color_override("font_color", PAPER)
		node.add_theme_color_override("font_hover_color", PAPER)
		node.add_theme_color_override("font_pressed_color", PAPER)
	return node

static func rule() -> HSeparator:
	var node := HSeparator.new()
	var style := StyleBoxLine.new()
	style.color = LINE
	style.thickness = 1
	node.add_theme_stylebox_override("separator", style)
	return node

static func bar(value: float, maximum: float, color: Color) -> ProgressBar:
	var node := ProgressBar.new()
	node.custom_minimum_size = Vector2(0, 5)
	node.max_value = maxf(maximum, 1)
	node.value = value
	node.show_percentage = false
	node.add_theme_stylebox_override("fill", box(color, Color.TRANSPARENT, 1, 0))
	return node

static func row(parent: Node, left: String, right: String, color: Color = INK) -> HBoxContainer:
	var line := HBoxContainer.new()
	parent.add_child(line)
	var text := label(left, 15, MUTED)
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	line.add_child(text)
	line.add_child(label(right, 16, color))
	return line
