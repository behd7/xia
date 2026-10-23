"""固定窗口对话批总结（docs/DESIGN.md §6.6）。

MemoryStore.add_conversation_turn 在 conversation 超过窗口容量时，把最早一批
原始对话交给这里归纳成一条 event；落库与 Chroma 向量化由 MemoryStore.add_memory
统一完成。失败链路：重试一次(0.3) -> 取最后一轮玩家原话兜底，绝不阻塞对话。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def _parse_event(result_text: str) -> tuple[str, int]:
    text = result_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.S)
        text = re.sub(r"\n?```$", "", text, flags=re.S).strip()
    data = json.loads(text)
    content = str(data.get("content", "")).strip() if isinstance(data, dict) else ""
    if not content:
        raise ValueError("missing content")
    try:
        importance = int(data.get("importance", 3))
    except (TypeError, ValueError):
        importance = 3
    return content[:200], max(1, min(10, importance))


def _messages(player_name: str, place: str, turns: list[dict]) -> list[dict]:
    lines = "\n".join(
        f"{player_name}：{t['user']}\n你：{t['npc']}" for t in turns
    ) or "（无有效对话）"
    system = (
        "你是武侠NPC。下面是玩家与你的最近一段对话，"
        f"发生在{place}。\n"
        f"{lines}\n"
        "请以你的视角，把这段交流归纳成 1 条简短的第三人称事件摘要"
        "（一句话，古风白话，不超过80字），只概括对你有意义、日后值得记起的事，"
        "不要虚构对话中没提到的内容；并标注记忆重要度（1~10整数）。\n"
        '仅输出JSON：{"content":"...","importance":5}'
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "请开始归纳。"}]


async def summarize_dialogue_window(store, npc_id: int, player_name: str, place: str,
                                    turns: list[dict]) -> dict:
    """把一批窗口对话归纳成单条 event；失败时用最后一句玩家原话兜底。"""
    msgs = _messages(player_name, place, turns)
    last_error: Exception | None = None
    for temperature in (0.7, 0.3):
        try:
            cr = await store.llm.chat(
                "dialogue_summary", msgs, json_format=True, temperature=temperature,
                metadata={"npc_id": npc_id, "window": len(turns)},
            )
            content, importance = _parse_event(cr.text)
            logger.info("dialogue_summary npc=%d window=%d", npc_id, len(turns))
            return {"content": content, "importance": importance}
        except Exception as exc:
            last_error = exc
            continue
    logger.warning("dialogue_summary npc=%d fallback after retry: %s: %s",
                   npc_id, type(last_error).__name__ if last_error else "unknown",
                   last_error)
    content = str(turns[-1]["user"]).strip() if turns else "与玩家交谈了几句。"
    return {"content": (content or "与玩家交谈了几句。")[:200], "importance": 3}
