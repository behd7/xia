"""Native NPC tools -> the existing dialogue settlement contract.

Control tools do not count against the two in-world acts and never write state
here. The dialogue layer retains its original clamp, ordering and stale guard.
"""

from ..llm_tools import ToolCall, ToolCallBuffer, ToolCallDelta, string_prefix


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }}


REPLY_TOOL = _tool("reply", "以 NPC 的口吻回复对方，不超过80字。只行动时不必调用。",
                   {"text": {"type": "string", "description": "实际说出口的话"}}, ["text"])
DIALOGUE_TOOLS = [
    REPLY_TOOL,
    _tool("change_affinity", "本轮关系变化，只调用一次；无变化可不调用。建议 -2 至 +3，服务器限幅。",
          {"delta": {"type": "integer", "description": "关系变化量，不是最终好感"}}, ["delta"]),
    _tool("end_dialogue", "想就此收住话头时调用；继续交谈时不要调用。", {}, []),
    _tool("give_item", "把自己身上已有的东西给对方；最多两个原地动作。", {
        "item": {"type": "string", "description": "物品名"},
        "count": {"type": "integer", "minimum": 1, "maximum": 3}}, ["item"]),
    _tool("take_item", "向对方要过东西，须交情够且对方有足量物品。", {
        "item": {"type": "string", "description": "物品名"},
        "count": {"type": "integer", "minimum": 1, "maximum": 3}}, ["item"]),
    _tool("teach_art", "把当前功法教给对方，须同源、交情够，不替对方确认散功或压级。",
          {"art": {"type": "string", "description": "功法名"}}, ["art"]),
    _tool("refuse", "明确回绝对方。",
          {"reason": {"type": "string", "description": "不超过20字"}}, []),
]

PROMPT_SPECS = (
    "正常应答，不要输出 JSON 对象、代码块或工具说明。\n"
    "用 reply 工具说出你的回复（≤80字）；普通文本也会作为说出口的话。不要两处重复回复。\n"
    "用 change_affinity 给出本轮关系变化（-2~+3整数，无变化可省略）；"
    "想收住话头才调用 end_dialogue。上述工具每轮至多各一次。\n"
    "也可调用 give_item、take_item、teach_art、refuse，最多两个原地动作；"
    "只行动时可不作声。工具只表示意图，能否做成由实际条件决定。"
)


def parse_turn(text: str, calls: list[ToolCall]) -> dict:
    """Map wire form only: all effects remain in their pre-migration services."""
    reply = text.strip()
    delta, end = 0, False
    acts: list[dict] = []
    seen = set()
    for call in calls:
        args = call.arguments
        if call.name in ("reply", "change_affinity", "end_dialogue"):
            if call.name in seen:
                raise ValueError(f"duplicate control tool: {call.name}")
            seen.add(call.name)
            if call.name == "reply":
                if set(args) != {"text"} or not isinstance(args["text"], str):
                    raise ValueError("reply requires text")
                reply = args["text"].strip()
            elif call.name == "change_affinity":
                if set(args) != {"delta"} or type(args["delta"]) is not int:
                    raise ValueError("change_affinity requires integer delta")
                delta = args["delta"]
            else:
                if args:
                    raise ValueError("end_dialogue takes no arguments")
                end = True
        else:
            # Unknown names follow the existing action rejection/correction path.
            acts.append({"tool": call.name, "args": args})
    if not reply and not acts:
        raise ValueError("empty reply and actions")
    return {"reply": reply, "affinity_delta": delta, "end": end, "acts": acts}


class TurnStream:
    """Stream ordinary content / reply arguments, buffer all intents until EOF."""

    def __init__(self) -> None:
        self.buffer = ToolCallBuffer()
        self.text = ""
        self.sent = ""

    def feed(self, chunk: str | ToolCallDelta) -> tuple[bool, str]:
        if isinstance(chunk, str):
            self.text += chunk
        else:
            self.buffer.feed(chunk)
        replies = [c for c in self.buffer.pending.values() if c.name == "reply"]
        value = string_prefix(replies[0].arguments, "text") if replies else self.text
        if value is None:
            return False, ""
        reset = not value.startswith(self.sent)
        delta = value if reset else value[len(self.sent):]
        self.sent = value
        return reset, delta

    def finish(self) -> dict:
        return parse_turn(self.text, self.buffer.finish())
