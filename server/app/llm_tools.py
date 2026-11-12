"""Provider-neutral native tool calls and incremental JSON string decoding.

Only tool *arguments* are JSON. Assistant content is ordinary text; nothing in
this module executes a game action while a completion is still in flight.
"""

import json
import re
from dataclasses import dataclass


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = ""


@dataclass
class ToolCallDelta:
    index: int
    name: str = ""
    arguments: str = ""
    id: str = ""


class ToolCallBuffer:
    """OpenAI index-addressed fragments; also accepts complete Ollama calls."""

    def __init__(self) -> None:
        self.pending: dict[int, ToolCallDelta] = {}

    def feed(self, delta: ToolCallDelta) -> None:
        if isinstance(delta.index, bool) or not isinstance(delta.index, int) or delta.index < 0:
            raise ValueError("invalid tool call index")
        call = self.pending.setdefault(delta.index, ToolCallDelta(delta.index))
        call.name += delta.name
        call.arguments += delta.arguments
        if delta.id:
            if call.id and call.id != delta.id:
                raise ValueError("tool call id changed")
            call.id = delta.id

    def finish(self) -> list[ToolCall]:
        calls = []
        for index in sorted(self.pending):
            call = self.pending[index]
            if not call.name:
                raise ValueError("missing tool name")
            args = json.loads(call.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
            calls.append(ToolCall(call.name, args, call.id))
        return calls


def complete_tool_calls(raw: list[dict]) -> list[ToolCall]:
    """Normalize non-streaming OpenAI/Ollama calls without dropping bad calls."""
    buffer = ToolCallBuffer()
    for index, call in enumerate(raw):
        fn = call["function"]
        args = fn.get("arguments", {})
        buffer.feed(ToolCallDelta(
            index, fn["name"], args if isinstance(args, str) else json.dumps(args),
            call.get("id", "")))
    return buffer.finish()


_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
            "n": "\n", "r": "\r", "t": "\t"}


def string_prefix(text: str, key: str) -> str | None:
    """Decode only the complete prefix of a streamed tool's string argument.

    Holds incomplete escapes and surrogate pairs until the next fragment.
    Full JSON validation still happens at finish(), before any side effect.
    """
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', text)
    if match is None:
        return None
    out: list[str] = []
    i = match.end()
    while i < len(text):
        c = text[i]
        if c == '"':
            return "".join(out)
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(text):
            break
        esc = text[i + 1]
        if esc == "u":
            if i + 6 > len(text) or not _HEX4.fullmatch(text[i + 2:i + 6]):
                break
            code = int(text[i + 2:i + 6], 16)
            i += 6
            if 0xD800 <= code <= 0xDBFF:
                if not (text.startswith("\\u", i) and i + 6 <= len(text)):
                    break
                low_hex = text[i + 2:i + 6]
                if not _HEX4.fullmatch(low_hex):
                    break
                low = int(low_hex, 16)
                if not 0xDC00 <= low <= 0xDFFF:
                    break
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00))
                i += 6
                continue
            if 0xDC00 <= code <= 0xDFFF:
                break
            out.append(chr(code))
            continue
        if esc not in _ESCAPES:
            break
        out.append(_ESCAPES[esc])
        i += 2
    return "".join(out)
