#!/usr/bin/env python3
"""中期召回阈值标定（docs/MEMORY-V2-PLAN.md §12）。

用**真实的对话后端 + 真实的嵌入后端**跑一遍中期召回，测量
「最近 12 条消息拼接 query」与「中期线索」的余弦分布，回答两个问题：

1. 相关对话能不能超过 `MIDTERM_MIN_SCORE`（现默认 0.8）——漏召回；
2. 无关对话会不会也超过阈值——误召回。

同时打印「玩家单句 query」的对照组，用来验证 §12 的结论：0.8 只在长拼接
query 下成立，换成单句会几乎全灭。

用法（server 目录下）：
    NO_PROXY=localhost,127.0.0.1 .venv/bin/python tools/calibrate_midterm.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from app.agent import dialogue as dialogue_mod  # noqa: E402
from app.agent import summary as summary_mod  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.llm import OllamaClient  # noqa: E402

NPC_NAME = "铁无双"
PLAYER_NAME = "秦侠客"

# 每块 10 轮，模拟一次中期归档。主题取材于游戏内真实内容，避免分布失真。
BLOCKS: list[dict] = [
    {
        "place": "铁匠铺",
        "turns": [
            ("听说后山来了伙黑风寨的人？", "可不是，夜里总能听见崖那边吆喝。"),
            ("他们可曾动过镇子？", "只敢在崖口收买路钱，镇里还没闹过。"),
            ("山上那条道难走么？", "陡得很，你若去，带口刀再带两包药。"),
            ("黑旋风是什么人？", "山贼头子，一条横练功夫，寻常刀砍不进去。"),
            ("镇里没人管？", "镖局嫌远，衙门更嫌远，就这么耗着。"),
            ("你若肯去，我给你打把刀。", "当真？那我明日一早上山。"),
            ("刀要几日？", "三日，精铁三尺，包你砍得动。"),
            ("我身子还没练开，怕是不济。", "火候不到就别硬闯，命只有一条。"),
            ("那伙人可认得断岳刀？", "闭嘴。这话在镇里传出去要出人命的。"),
            ("我记下了，不多说。", "去吧，三日后来取刀。"),
        ],
    },
    {
        "place": "铁匠铺",
        "turns": [
            ("上回那把精钢剑还有么？", "有一把，二百两，不还价。"),
            ("能不能便宜些？", "你摸过这剑口子再说便宜。"),
            ("银子我这就取来。", "一手交钱一手交货，老规矩。"),
            ("剑上的纹路是什么？", "千锤百炼出来的，不是刻的。"),
            ("拿剑的手该怎样？", "腕子松，指头紧，别死死攥着。"),
            ("我总使不上力。", "那是你心浮，跟剑没干系。"),
            ("你打了多少年铁？", "二十三年，从十三岁起。"),
            ("难怪炉子跟你亲。", "炉子不认人，只认火候。"),
            ("改日来讨教。", "讨教不敢当，来喝酒倒是真的。"),
        ],
    },
    {
        "place": "武馆",
        "turns": [
            ("我想学一门功法。", "你先前可练过别的？"),
            ("练过几日拳脚，没入门。", "那还好，怕的是半瓶子晃荡的。"),
            ("能否教我烈阳剑诀？", "练这门要吃得苦，你若半途而废，我丢脸。"),
            ("我不怕吃苦。", "先交学费，再看你三个月后还在不在。"),
            ("学费几何？", "三十两，学不会退你十两。"),
            ("功法属性有什么讲究？", "同源可换，异源就要散功重来。"),
            ("散功会怎样？", "修为归零，四维还在，图鉴也不丢。"),
            ("那我先学同源的。", "懂事。别急着跳到高上限的门里去。"),
            ("多谢指点。", "去后院扎马步，扎到日落。"),
        ],
    },
    {
        "place": "药铺",
        "turns": [
            ("我这刀伤总不好。", "伤口可洗过了？拿酒洗，别拿井水。"),
            ("洗过了，还是热。", "热就是里头没清干净。"),
            ("要买什么药？", "小回春散，一日两服，连服三日。"),
            ("多少钱一包？", "十五两，童叟无欺。"),
            ("能不能多买几包？", "药不是米，吃多了伤底子。"),
            ("那大回春散呢？", "那是救急的，六十两一包，你现在用不上。"),
            ("我记住了。", "记性好的病人活得久。"),
            ("这几日我不上山了。", "早该如此，刀伤没好就去拼命。"),
            ("过几日再来。", "来之前先睡足，别顶着黑眼圈来。"),
        ],
    },
    {
        "place": "镇口",
        "turns": [
            ("你在这儿等谁？", "等王二麻子，说好带酒来。"),
            ("镇口这几日热闹么？", "热闹，赌坊开了新桌。"),
            ("我不赌。", "不赌好，赌到头的没一个有好下场。"),
            ("听说镖局换了趟头？", "换了个姓赵的，横得很。"),
            ("黑风寨那边呢？", "消停了些，许是转到别处去了。"),
            ("镇上可有人去报官？", "报了两回，衙门回了两封空文。"),
            ("那你们怎么办？", "能怎么办，天黑少出门。"),
            ("我这几日会常在镇口。", "那正好，替我看着点那桌酒。"),
            ("成。", "够义气。"),
        ],
    },
    {
        "place": "后山",
        "turns": [
            ("崖口那堆石头是做什么的？", "挡路的，绕过就得走泥道。"),
            ("泥道上可有埋伏？", "有，我上回就被绊了一跤。"),
            ("山贼用什么兵器？", "多是柴刀，也有拿铁棍的。"),
            ("铁棍难对付么？", "破空声沉，躲开第一下就有机会。"),
            ("你可伤过人？", "伤过，也被人伤过，扯平了。"),
            ("山上还有别的路么？", "有条小路通崖后，只有猎户知道。"),
            ("能带我去么？", "等我哪天闲了，再说。"),
            ("我想早日了结这事。", "急不得，急了就是给山贼送头。"),
            ("我听你的。", "回去吧，天黑前下山。"),
        ],
    },
]

# 每块对应的「后来的对话」：相关 3 段 + 无关 3 段。
PROBES: list[dict] = [
    {
        "related": [
            [("后山黑风寨那伙人可还在？", "还在崖口，昨夜又听见吆喝。"),
             ("我这几日练了些身手。", "练了也先别上山，黑旋风不是野狗。")],
            [("崖那边的路你还记得么？", "记得，绕过石堆走泥道，别踩空。"),
             ("黑旋风横练功夫当真刀枪不入？", "寻常刀不行，得找缝隙下手。")],
            [("我明日就上山了。", "刀带上，药也带上，别逞强。")],
        ],
        "unrelated": [
            [("这药几钱一包？", "小回春散十五两，别贪多。"),
             ("我这刀伤还热。", "热就再洗一回，别拿井水。")],
            [("烈阳剑诀第三式怎么使？", "腕子松，别死死攥着。"),
             ("我要不要散功换个上限高的？", "异源要归零，先想清楚。")],
            [("镇口那桌酒还在么？", "在，王二麻子输了半坛。")],
        ],
    },
    {
        "related": [
            [("上回那把精钢剑你还留着么？", "留着一把，二百两。"),
             ("银子我带来了。", "一手交钱，老规矩。")],
            [("这剑纹路当真不是刻的？", "千锤百炼，刻不出来。"),
             ("拿剑的手该怎么使力？", "腕松指紧，别用蛮力。")],
            [("你打了多少年铁了？", "二十三年，从十三岁起。")],
        ],
        "unrelated": [
            [("后山崖口可还太平？", "消停些了，别一个人去。"),
             ("黑旋风还在么？", "在，只是不再下山。")],
            [("我想学一门功法。", "先说你练过什么。"),
             ("练过几日拳脚。", "那还能调，就怕半瓶子的。")],
            [("小回春散还有么？", "有，一日两服，别贪多。")],
        ],
    },
    {
        "related": [
            [("烈阳剑诀我练到第二式了。", "不错，比我想的快。"),
             ("能不能再教我一套？", "同源的可以，异源先散功。")],
            [("学费我补交了。", "交了就扎马步，扎到日落。"),
             ("散功真的会归零？", "修为归零，四维和图鉴都不丢。")],
            [("我三个月后还在。", "那我这脸面就保住了。")],
        ],
        "unrelated": [
            [("后山那边最近如何？", "安静，偶尔还有吆喝。"),
             ("黑风寨不动了？", "多半是转到别处去了。")],
            [("这把精钢剑多少钱？", "二百两，不还价。"),
             ("能便宜点么？", "摸了剑口子再说。")],
            [("镇口的赌坊还开么？", "开着，新桌子还有人输。")],
        ],
    },
    {
        "related": [
            [("我这刀伤又犯了。", "还拿酒洗么？别拿井水。"),
             ("洗过了，还是热。", "热就是里头没清干净。")],
            [("再给我三包小回春散。", "一日两服，连服三日，别再逞强。"),
             ("大回春散要不要一起买？", "你现在用不上，别乱花银子。")],
            [("我这几日不上山了。", "早该如此，刀伤没好别拼命。")],
        ],
        "unrelated": [
            [("后山崖口还有人么？", "有，只是不下山了。"),
             ("黑旋风可还认得你？", "他认不认得我不知道，我认得他。")],
            [("精钢剑可有现货？", "一把，二百两。"),
             ("我看看剑纹。", "看吧，刻不出来这个。")],
            [("烈阳剑诀能传我么？", "交学费，练三个月。")],
        ],
    },
    {
        "related": [
            [("镇口今天有人么？", "王二麻子又输了半坛。"),
             ("镖局那趟头换了？", "换了个姓赵的，横得很。")],
            [("我去报官如何？", "报了两回都是空文，别白跑。"),
             ("那夜里还能出门么？", "天黑少出门，镇上的人都这样。")],
            [("我替你看着那桌酒。", "够义气，有好事记你一份。")],
        ],
        "unrelated": [
            [("后山黑风寨还在崖口么？", "在，夜里还有吆喝。"),
             ("上山走哪条道？", "绕石堆走泥道，别踩空。")],
            [("我要不要散功重修？", "异源归零，你先想清楚。"),
             ("同源换功呢？", "同源保留等级，只抬上限。")],
            [("刀伤用什么药？", "小回春散，一日两服。")],
        ],
    },
    {
        "related": [
            [("崖口那堆石头还在么？", "在，路还是得绕。"),
             ("泥道上还有埋伏么？", "有，走的时候看着脚下。")],
            [("山贼使的什么兵器？", "多是柴刀，也有铁棍。"),
             ("铁棍怎么破？", "躲开第一下，破空声沉。")],
            [("带我去崖后那条小路。", "等我闲了，别急。")],
        ],
        "unrelated": [
            [("这剑多重？", "三尺，你举得动。"),
             ("纹路怎么这么深？", "锤出来的。")],
            [("烈阳剑诀我练不动。", "练不动就扎马步。"),
             ("学费能退么？", "学不会退你十两。")],
            [("镇口的酒还有么？", "有，别喝太多。")],
        ],
    },
]


ALL: dict[str, list[float]] = {}


def _sweep(all_rows: dict[str, list[float]]) -> str:
    lines = ["τ 扫描："]
    for mode in ("long", "single"):
        rel = all_rows.get(f"related/{mode}", [])
        unrel = all_rows.get(f"unrelated/{mode}", [])
        if not rel or not unrel:
            continue
        lines.append(f"  [{mode}] 相关 n={len(rel)} 无关 n={len(unrel)}")
        for i in range(9):
            tau = 0.40 + i * 0.05
            tp = sum(1 for s in rel if s > tau)
            fp = sum(1 for s in unrel if s > tau)
            recall = tp / len(rel)
            fpr = fp / len(unrel)
            mark = " ←最大 J" if abs((recall - fpr) - _best_j(all_rows, mode)) < 1e-9 else ""
            lines.append(f"    τ={tau:.2f}  recall={recall:.2f}  FPR={fpr:.2f}  "
                         f"J={recall - fpr:+.2f}{mark}")
    return "\n".join(lines)


def _best_j(all_rows: dict[str, list[float]], mode: str) -> float:
    rel = all_rows.get(f"related/{mode}", [])
    unrel = all_rows.get(f"unrelated/{mode}", [])
    best = -1.0
    for i in range(9):
        tau = 0.40 + i * 0.05
        j = (sum(1 for s in rel if s > tau) / len(rel)
             - sum(1 for s in unrel if s > tau) / len(unrel))
        best = max(best, j)
    return best


class _Store:
    """summarize_midterm / embed 只用到 store.llm。"""

    def __init__(self, llm) -> None:
        self.llm = llm
        self.clock = None


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _query(turns: list[tuple[str, str]], message: str) -> str:
    window = [{"user": u, "npc": n} for u, n in turns]
    return dialogue_mod._midterm_query(window, message)


def _single(message: str) -> str:
    return message


def _stats(values: list[float]) -> str:
    if not values:
        return "-"
    return (f"n={len(values)} min={min(values):.3f} "
            f"median={statistics.median(values):.3f} max={max(values):.3f}")


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="xia_calib_")
    db = Database(Path(tmp) / "calib.db")
    await db.connect()
    llm = OllamaClient(settings.ollama_host, settings.llm_timeout, db)
    store = _Store(llm)
    threshold = settings.midterm_min_score
    print(f"chat={settings.llm_api_model or settings.chat_model} "
          f"embed={settings.embed_model} 阈值={threshold} "
          f"query上限={settings.midterm_query_max_chars} 字\n")

    try:
        for index, block in enumerate(BLOCKS):
            turns = [{"user": u, "npc": n, "place": block["place"]}
                     for u, n in block["turns"]]
            result = await summary_mod.summarize_midterm(
                store, index + 1, NPC_NAME, PLAYER_NAME, block["place"], turns)
            clues = result["clues"]
            if not clues:
                print(f"[块{index + 1}] 线索为空（摘要兜底），跳过：{result['summary']}")
                continue
            cue_vecs = await llm.embed(clues)
            probes = PROBES[index]
            rows: list[tuple[str, str, float]] = []
            cases: list[tuple[str, str]] = []
            for kind in ("related", "unrelated"):
                for probe in probes[kind]:
                    last_user = probe[-1][0]
                    cases.append((f"{kind}/long", _query(probe, last_user)))
                    cases.append((f"{kind}/single", _single(last_user)))
            vecs = await llm.embed([text for _, text in cases])
            for (kind, text), vec in zip(cases, vecs):
                score = max(_dot(vec, cue) for cue in cue_vecs)
                rows.append((kind, text, score))
            print(f"[块{index + 1}] {block['place']} 线索={clues}")
            print(f"    summary={result['summary']}")
            for kind, text, score in rows:
                flag = "命中" if score > threshold else "    "
                print(f"    {kind:<9} {flag} {score:.3f} "
                      f"({len(text):>4}字) {text[:28].replace(chr(10), ' ')}…")
            for kind in ("related/long", "unrelated/long",
                         "related/single", "unrelated/single"):
                vals = [s for k, _, s in rows if k == kind]
                print(f"    {kind:<15} {_stats(vals)}")
            for kind, _, score in rows:
                ALL.setdefault(kind, []).append(score)
            print()

        print("=" * 76)
        print(_sweep(ALL))
        print("说明：*\/long = 12 条消息拼接 query（现行实现）；*\/single = 只取"
              "最后一句（对照）。recall=相关命中率，FPR=无关误报率，"
              "J=Youden 指数（越大越好）。")
    finally:
        await llm.close()
        await db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
