extends Node

var base_url: String = "http://127.0.0.1:8080"


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


# POST + text/event-stream. HTTPRequest buffers the whole body, so the dialogue
# stream polls an HTTPClient by hand and forwards each SSE event as it arrives.
# `on_event` is called with (event_name, data: Dictionary); the returned dict is
# the final `done` payload, or an error if the stream ended without one.
func stream_request(path: String, body: Variant, on_event: Callable, timeout: float = 60.0) -> Dictionary:
	var started := Time.get_ticks_msec()
	var url := base_url + path
	var target := _parse_url(url)
	if target.is_empty():
		return {"ok": false, "network": true, "elapsed_ms": 0,
			"error": "服务器地址无效：%s" % base_url}
	_log("-> POST %s (sse)" % url)
	var http := HTTPClient.new()
	var tls_options: TLSOptions = TLSOptions.client() if bool(target.tls) else null
	var err := http.connect_to_host(str(target.host), int(target.port), tls_options)
	if err != OK:
		return _stream_error(started, HTTPRequest.RESULT_CANT_CONNECT, timeout)
	while http.get_status() in [HTTPClient.STATUS_RESOLVING, HTTPClient.STATUS_CONNECTING]:
		if _expired(started, timeout):
			http.close()
			return _stream_error(started, HTTPRequest.RESULT_TIMEOUT, timeout)
		http.poll()
		await get_tree().process_frame
	if http.get_status() != HTTPClient.STATUS_CONNECTED:
		var connect_status := http.get_status()
		http.close()
		return _stream_error(started, _status_result(connect_status), timeout)
	var headers := PackedStringArray([
		"Content-Type: application/json",
		"Accept: text/event-stream",
		"Cache-Control: no-cache",
	])
	err = http.request(HTTPClient.METHOD_POST, str(target.path), headers, JSON.stringify(body))
	if err != OK:
		http.close()
		return _stream_error(started, HTTPRequest.RESULT_CANT_CONNECT, timeout)
	while http.get_status() == HTTPClient.STATUS_REQUESTING:
		if _expired(started, timeout):
			http.close()
			return _stream_error(started, HTTPRequest.RESULT_TIMEOUT, timeout)
		http.poll()
		await get_tree().process_frame
	if not http.has_response():
		var wait_status := http.get_status()
		http.close()
		return _stream_error(started, _status_result(wait_status), timeout)
	var code := http.get_response_code()
	var raw := PackedByteArray()
	var state := {"done": {}, "error": ""}
	while http.get_status() == HTTPClient.STATUS_BODY:
		if _expired(started, timeout):
			http.close()
			return _stream_error(started, HTTPRequest.RESULT_TIMEOUT, timeout)
		http.poll()
		var chunk := http.read_response_body_chunk()
		if chunk.size() > 0:
			raw.append_array(chunk)
			if code >= 200 and code < 300:
				var drained := _drain_sse(raw)
				raw = drained.rest
				for item in drained.events:
					_consume_sse_event(item, state, on_event)
		await get_tree().process_frame
	var elapsed := Time.get_ticks_msec() - started
	http.close()
	if code < 200 or code >= 300:
		var detail := "请求失败（%d）" % code
		var parsed: Variant = JSON.parse_string(raw.get_string_from_utf8())
		if parsed is Dictionary and parsed.has("detail"):
			detail = str(parsed.detail)
		_log("!! POST %s http=%d detail=%s elapsed=%dms" % [url, code, detail, elapsed])
		return {"ok": false, "network": false, "status": code, "elapsed_ms": elapsed,
			"error": detail}
	if not raw.is_empty():
		var tail := raw
		tail.append_array(PackedByteArray([10, 10]))
		for item in _drain_sse(tail).events:
			_consume_sse_event(item, state, on_event)
	if not str(state.error).is_empty():
		_log("!! POST %s sse error=%s elapsed=%dms" % [url, str(state.error), elapsed])
		return {"ok": false, "network": false, "status": code, "elapsed_ms": elapsed,
			"error": str(state.error)}
	if (state.done as Dictionary).is_empty():
		_log("!! POST %s sse ended without done elapsed=%dms" % [url, elapsed])
		return {"ok": false, "network": true, "status": code, "elapsed_ms": elapsed,
			"error": "话说到一半就断了，请再试一次。"}
	_log("<- POST %s http=%d (sse) elapsed=%dms" % [url, code, elapsed])
	return {"ok": true, "data": state.done, "status": code, "elapsed_ms": elapsed}


func _consume_sse_event(item: Dictionary, state: Dictionary, on_event: Callable) -> void:
	var event_name := str(item.get("event", "message"))
	var data: Variant = item.get("data", {})
	if not data is Dictionary:
		data = {}
	if event_name == "done":
		state.done = data
	elif event_name == "error":
		state.error = str(data.get("detail", "对话中断。"))
	if on_event.is_valid():
		on_event.call(event_name, data)


# Split complete SSE blocks out of a byte buffer. Works on bytes so a UTF-8
# character split across network chunks is decoded only once its block is whole.
func _drain_sse(buffer: PackedByteArray) -> Dictionary:
	var events: Array = []
	var cursor := 0
	var size := buffer.size()
	while cursor < size:
		var end := -1
		var skip := 0
		var i := cursor
		while i < size - 1:
			if buffer[i] == 10 and buffer[i + 1] == 10:
				end = i
				skip = 2
				break
			if (buffer[i] == 13 and i + 3 < size and buffer[i + 1] == 10
					and buffer[i + 2] == 13 and buffer[i + 3] == 10):
				end = i
				skip = 4
				break
			i += 1
		if end < 0:
			break
		events.append(_parse_sse_block(buffer.slice(cursor, end).get_string_from_utf8()))
		cursor = end + skip
	return {"events": events, "rest": buffer.slice(cursor) if cursor < size else PackedByteArray()}


func _parse_sse_block(block: String) -> Dictionary:
	var event_name := "message"
	var data_lines: Array[String] = []
	for raw_line in block.split("\n"):
		var line := raw_line.trim_suffix("\r")
		if line.is_empty() or line.begins_with(":"):
			continue
		var colon := line.find(":")
		var field := line if colon < 0 else line.substr(0, colon)
		var value := "" if colon < 0 else line.substr(colon + 1)
		if value.begins_with(" "):
			value = value.substr(1)
		if field == "event":
			event_name = value
		elif field == "data":
			data_lines.append(value)
	var parsed: Variant = null
	if not data_lines.is_empty():
		parsed = JSON.parse_string("\n".join(data_lines))
	return {"event": event_name, "data": parsed if parsed is Dictionary else {}}


func _parse_url(url: String) -> Dictionary:
	var rest := url
	var tls := false
	if url.begins_with("https://"):
		tls = true
		rest = url.substr(8)
	elif url.begins_with("http://"):
		rest = url.substr(7)
	else:
		return {}
	var slash := rest.find("/")
	var hostport := rest if slash < 0 else rest.substr(0, slash)
	var path := "/" if slash < 0 else rest.substr(slash)
	var host := hostport
	var port := 443 if tls else 80
	var colon := hostport.rfind(":")
	if colon > 0 and not hostport.begins_with("["):
		host = hostport.substr(0, colon)
		port = int(hostport.substr(colon + 1))
	if host.is_empty() or port <= 0:
		return {}
	return {"host": host, "port": port, "path": path, "tls": tls}


func _status_result(status: int) -> int:
	match status:
		HTTPClient.STATUS_CANT_CONNECT:
			return HTTPRequest.RESULT_CANT_CONNECT
		HTTPClient.STATUS_CANT_RESOLVE:
			return HTTPRequest.RESULT_CANT_RESOLVE
		HTTPClient.STATUS_CONNECTION_ERROR, HTTPClient.STATUS_TLS_HANDSHAKE_ERROR:
			return HTTPRequest.RESULT_CONNECTION_ERROR
		_:
			return HTTPRequest.RESULT_CONNECTION_ERROR


func _stream_error(started: int, result: int, timeout: float) -> Dictionary:
	var elapsed := Time.get_ticks_msec() - started
	var reason := _result_text(result)
	_log("!! stream result=%d (%s) elapsed=%dms timeout=%.0fs" % [result, reason, elapsed, timeout])
	var hint := "（%.0f 秒）" % timeout if result == HTTPRequest.RESULT_TIMEOUT else ""
	return {"ok": false, "network": true, "elapsed_ms": elapsed, "result": result,
		"error": "%s%s，请确认后端已启动、地址正确。" % [reason, hint]}


func _expired(started: int, timeout: float) -> bool:
	return Time.get_ticks_msec() - started > int(timeout * 1000.0)


func _log(message: String) -> void:
	print("[xia/api %s] %s" % [Time.get_datetime_string_from_system(), message])
