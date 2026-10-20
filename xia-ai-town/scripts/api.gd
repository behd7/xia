extends Node

var base_url: String = "http://127.0.0.1:8000"


func _ready() -> void:
	var source := "default"
	var config := ConfigFile.new()
	if config.load("user://connection.cfg") == OK:
		base_url = str(config.get_value("server", "url", base_url))
		source = "user://connection.cfg"
	var override := OS.get_environment("XIA_API_URL")
	if not override.is_empty():
		base_url = override.trim_suffix("/")
		source = "XIA_API_URL"
	_log("base_url=%s source=%s" % [base_url, source])


func configure(url: String) -> void:
	base_url = url.strip_edges().trim_suffix("/")
	var config := ConfigFile.new()
	config.set_value("server", "url", base_url)
	var err := config.save("user://connection.cfg")
	if err == OK:
		_log("configured base_url=%s" % base_url)
	else:
		_log("!! configure save failed base_url=%s err=%d" % [base_url, err])


func request(path: String, body: Variant = null, timeout: float = 12.0) -> Dictionary:
	var http := HTTPRequest.new()
	http.timeout = timeout
	add_child(http)
	var method := HTTPClient.METHOD_GET if body == null else HTTPClient.METHOD_POST
	var method_name := "GET" if body == null else "POST"
	var headers := PackedStringArray(["Content-Type: application/json"])
	var payload := "" if body == null else JSON.stringify(body)
	var url := base_url + path
	var started := Time.get_ticks_msec()
	_log("-> %s %s" % [method_name, url])
	var code := http.request(url, headers, method, payload)
	if code != OK:
		var elapsed := Time.get_ticks_msec() - started
		http.queue_free()
		_log("!! %s %s request() failed code=%d elapsed=%dms" % [method_name, url, code, elapsed])
		return {"ok": false, "network": true, "elapsed_ms": elapsed,
			"error": "连接未能发出（错误码 %d），请检查服务器地址。" % code}
	var response: Array = await http.request_completed
	var elapsed := Time.get_ticks_msec() - started
	http.queue_free()
	var result := int(response[0])
	if result != HTTPRequest.RESULT_SUCCESS:
		var reason := _result_text(result)
		_log("!! %s %s transport result=%d (%s) elapsed=%dms timeout=%.0fs" % [
			method_name, url, result, reason, elapsed, timeout])
		var hint := "（%.0f 秒）" % timeout if result == HTTPRequest.RESULT_TIMEOUT else ""
		return {"ok": false, "network": true, "elapsed_ms": elapsed, "result": result,
			"error": "%s%s，请确认后端已启动、地址正确。" % [reason, hint]}
	var status := int(response[1])
	var body_text: String = response[3].get_string_from_utf8()
	var parsed: Variant = JSON.parse_string(body_text)
	if not parsed is Dictionary:
		_log("!! %s %s http=%d invalid JSON elapsed=%dms body=%.200s" % [
			method_name, url, status, elapsed, body_text])
		return {"ok": false, "network": false, "status": status, "elapsed_ms": elapsed,
			"error": "服务器返回的数据无法读取（HTTP %d）。" % status}
	if status < 200 or status >= 300:
		var detail := str(parsed.get("detail", "请求失败（%d）" % status))
		_log("!! %s %s http=%d detail=%s elapsed=%dms" % [
			method_name, url, status, detail, elapsed])
		return {"ok": false, "network": false, "status": status, "elapsed_ms": elapsed,
			"error": detail}
	_log("<- %s %s http=%d elapsed=%dms" % [method_name, url, status, elapsed])
	return {"ok": true, "data": parsed, "status": status, "elapsed_ms": elapsed}


func _result_text(result: int) -> String:
	match result:
		HTTPRequest.RESULT_CANT_CONNECT:
			return "连接被拒绝"
		HTTPRequest.RESULT_CANT_RESOLVE:
			return "无法解析服务器地址"
		HTTPRequest.RESULT_CONNECTION_ERROR:
			return "连接中断"
		HTTPRequest.RESULT_NO_RESPONSE:
			return "服务器无响应"
		HTTPRequest.RESULT_TIMEOUT:
			return "等待服务器响应超时"
		_:
			return "网络错误（%d）" % result


func _log(message: String) -> void:
	print("[xia/api %s] %s" % [Time.get_datetime_string_from_system(), message])
