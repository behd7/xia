"""记忆注入的总预算（docs/MEMORY-V2-PLAN.md §5.3）。

各召回模块不许自己硬编码字数上限：中期、画像、（将来的）长期全部交给这里，
按优先级从高到低装填，超出就从低优先级整块丢弃，只有最高优先级那一块允许截断。
"""

from __future__ import annotations


def fit_blocks(blocks: list[tuple[str, str]], limit: int) -> str:
    """按优先级装填记忆板块，返回拼好的文本（不含尾部换行）。

    blocks: [(名字, 文本)]，**按优先级从高到低**；空文本会被跳过。
    """
    limit = max(0, int(limit))
    kept: list[str] = []
    used = 0
    for _, text in blocks:
        text = (text or "").strip()
        if not text:
            continue
        room = limit - used
        if room <= 0:
            break
        if len(text) <= room:
            kept.append(text)
            used += len(text) + 1
            continue
        if not kept:  # 最高优先级那一块放不下才截断
            kept.append(text[:room].rstrip())
        break
    return "\n".join(kept)
