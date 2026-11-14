extends SceneTree
const UI = preload("res://scripts/ui_kit.gd")
var failures := 0
var checks := 0

func check(ok: bool, label: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		push_error(label)

func _initialize() -> void:
	var font: SystemFont = UI.BODY_FONT
	check(ProjectSettings.get_setting("gui/theme/custom_font", "") == font.resource_path, "bootstrap font uses the existing UI font")
	check(ThemeDB.get_default_theme().default_font == font, "new native controls reuse the configured font before tree inheritance")
	check(font.font_names == PackedStringArray(["Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "Microsoft YaHei"]), "font families and cross-platform fallback order unchanged")
	check(font.allow_system_fallback, "system fallback is preserved, not disabled to hide matching cost")
	var theme := UI.make_theme()
	check(theme.default_font == font and theme.default_font_size == 16, "UI theme and size unchanged")
	for size: int in [13, 14, 16, 17, 28, 32]:
		var label := UI.label("清溪镇 · 侠客", size)
		check(label.get_theme_font_size("font_size") == size, "native per-label size unchanged: %d" % size)
		label.free()
	var corpus := "清溪镇剑阳道馆玄铁堂烟雨小筑药铺铁匠铺客栈破庙王九陆铁无双苏婉清陈半松刘小蝶张馒头侠客你好气血内力行囊武学买入卖出银两回春散凝神丸清溪路远江湖相逢请多保重◆◇❤·，。！？0123456789WASDTabEsc"
	var primary_missing := ""
	var characters: Dictionary = {}
	for character: String in corpus:
		characters[character] = true
		if not font.has_char(character.unicode_at(0)):
			primary_missing += character
	# has_char() excludes automatic system fallback. Validate the actual shaped
	# run instead of reporting a supported fallback symbol as missing.
	var line := TextLine.new()
	line.add_string(corpus, font, 16)
	var text_server := TextServerManager.get_primary_interface()
	var missing := ""
	var shaped_fonts: Dictionary = {}
	for glyph: Dictionary in text_server.shaped_text_get_glyphs(line.get_rid()):
		if not glyph.font_rid.is_valid() or int(glyph.index) == 0:
			missing += corpus.substr(int(glyph.start), int(glyph.end) - int(glyph.start))
		else:
			shaped_fonts[text_server.font_get_name(glyph.font_rid)] = text_server.font_get_face_index(glyph.font_rid)
	check(missing.is_empty() and not shaped_fonts.is_empty(), "selected shaped UI/sign/name/symbol glyph coverage; missing=" + missing)
	var report := {"os": OS.get_name(), "primary_family": font.get_font_name(), "shaped_fonts_and_faces": shaped_fonts, "primary_missing": primary_missing, "requested_families": font.font_names, "system_fallback": font.allow_system_fallback, "selected_unique_characters": characters.size(), "missing": missing, "passed": failures == 0, "scope": "selected shaped glyphs on this OS only; not a regional-form guarantee, all dialogue Unicode or other platforms"}
	var report_path := OS.get_environment("XIA_HD2D_FONT_REPORT")
	if not report_path.is_empty():
		var file := FileAccess.open(report_path, FileAccess.WRITE)
		if file == null:
			failures += 1
			printerr("HD2D_FAIL: cannot open font report: ", report_path)
		else:
			file.store_string(JSON.stringify(report))
			file.close()
	print("HD2D_FONT_PLATFORM: ", JSON.stringify(report))
	print("HD2D_FONT_CHECKS: %d failures=%d" % [checks, failures])
	quit(1 if failures else 0)
