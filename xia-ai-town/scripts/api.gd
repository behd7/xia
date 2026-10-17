extends Node

var base_url: String = "http://127.0.0.1:8000"

func _ready() -> void:
	var config := ConfigFile.new()
	if config.load("user://connection.cfg") == OK:
		base_url = str(config.get_value("server", "url", base_url))
	var override := OS.get_environment("XIA_API_URL")
	if not override.is_empty():
		base_url = override.trim_suffix("/")

func configure(url: String) -> void:
	base_url = url.strip_edges().trim_suffix("/")
	var config := ConfigFile.new()
	config.set_value("server", "url", base_url)
	config.save("user://connection.cfg")

func request(path: String, body: Variant = null, timeout: float = 12.0) -> Dictionary:
	var http := HTTPRequest.new()
	http.timeout = timeout
	add_child(http)
	var method := HTTPClient.METHOD_GET if body == null else HTTPClient.METHOD_POST
	var headers := PackedStringArray(["Content-Type: application/json"])
	var payload := "" if body == null else JSON.stringify(body)
	var code := http.request(base_url + path, headers, method, payload)
	if code != OK:
		http.queue_free()
		return {"ok": false, "error": "连接未能发出，请检查服务器地址。"}
	var response: Array = await http.request_completed
	http.queue_free()
	if response[0] != HTTPRequest.RESULT_SUCCESS:
		return {"ok": false, "error": "暂时无法连接江湖，请检查服务器后重试。"}
	var parsed: Variant = JSON.parse_string(response[3].get_string_from_utf8())
	if not parsed is Dictionary:
		return {"ok": false, "error": "服务器返回的数据无法读取。"}
	if response[1] < 200 or response[1] >= 300:
		return {"ok": false, "error": str(parsed.get("detail", "请求失败（%s）" % response[1])), "status": response[1]}
	return {"ok": true, "data": parsed}
