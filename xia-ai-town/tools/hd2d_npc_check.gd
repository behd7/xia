extends SceneTree
## The real World/Main entry points; no network or model calls in this contract.
const World = preload("res://scripts/town_world.gd")
const Layout = preload("res://scripts/hd2d/scenic_layout.gd")
var failures := 0
var checks := 0

class Client extends "res://scripts/main.gd":
	var opened := ""
	func _ready() -> void: pass
	func _process(_delta: float) -> void: pass
	func notify(_text: String) -> void: pass
	func _new_modal(_title: String, _subtitle: String, kind: String, _width: float = 780) -> VBoxContainer:
		opened = kind
		var body := VBoxContainer.new()
		add_child(body)
		return body
	func _append_talk(_who: String, _text: String) -> void: pass
	func _render_dialogue_footer(_mode: String) -> void: pass

func check(ok: bool, message: String) -> void:
	checks += 1
	if not ok:
		failures += 1
		printerr("NPC_FAIL: ", message)

func _initialize() -> void:
	_run.call_deferred()

func _run() -> void:
	var world := World.new()
	world.render_2d = false
	world.scenic_layout = true
	world.initialize()
	world.schedule = {"phases": [
		{"kind": "work", "start": 25200, "end": 39600},
		{"kind": "walk_work_to_gather", "start": 39600, "end": 40200},
		{"kind": "gather", "start": 40200, "end": 49800}]}
	var app := Client.new()
	app.world = world
	app.player = {"name": "隔离测试"}
	root.add_child(app)
	var id := 0
	for npc: Dictionary in world.npcs:
		id += 1
		var route: Dictionary = Layout.NPC_ROUTES[npc.slug]
		npc["npc_id"] = id
		npc["home_to_work_pts"] = route.home_to_work
		npc["work_to_gather_pts"] = route.work_to_gather
		app.people_npcs.append({"id": id, "slug": npc.slug, "name": npc.name, "role": "master"})
	for seconds: float in [36000.0, 39900.0, 43200.0]:
		world.tick_schedule(seconds)
		for npc: Dictionary in world.npcs:
			# Place at the exact visible NPC: building-priority used to fail here.
			world.actor_pos = npc.pos
			app.opened = ""
			app.interact()
			check(app.opened == "dialogue" and app.dialogue_slug == npc.slug,
				"near %s at %.0f" % [npc.slug, seconds])
			var before: Vector2 = npc.pos
			world.tick_schedule(seconds + 60, app.dialogue_slug)
			check(npc.pos == before, "dialogue freeze %s" % npc.slug)
			app.dialogue_slug = ""
			world.tick_schedule(seconds + 60)
			check(npc.pos == world.schedule_position(npc, seconds + 60), "resume %s" % npc.slug)
			world.tick_schedule(seconds)
			world.actor_pos = Vector2(8, 8)
			app.opened = ""
			app.open_dialogue(npc.slug)
			check(app.opened.is_empty(), "far %s at %.0f" % [npc.slug, seconds])
	world.actor_pos = Vector2(240, 502)
	check(world.nearby().get("id") == "clinic", "clinic door outside NPC circle")
	app.free()
	world.free()
	print("HD2D_NPC_%s: %d checks (42 near/far + 42 freeze/resume + shop)" % ["PASS" if failures == 0 else "FAIL", checks])
	quit(1 if failures else 0)
