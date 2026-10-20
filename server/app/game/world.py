"""世界公设（docs/DESIGN.md §6.11）：公开事实、NPC 立场与知识分层。

纯静态数据 + 纯函数，不 import DB / agent，避免循环依赖。
"""

STAGE_KEY = "world.stage"
ARTIFACT_ACQUIRED_KEY = "world.artifact_acquired"
ANNOUNCED_AT_KEY = "world.announced_at_game"
ARTIFACT_ITEM_KEY = "duanyue_dao"

PREMISE_KEYWORDS = (
    "后山", "山贼", "黑旋风", "寨子", "神器", "断岳刀", "宝刀", "那把刀",
    "张馒头", "破庙", "山外", "外面", "拜师", "武馆", "三派",
    "剑阳", "玄铁", "烟雨", "门派",
)

TOPIC_KEYWORDS = {
    "bandit": ("后山", "山贼", "黑旋风", "寨子"),
    "artifact": ("神器", "断岳刀", "宝刀", "那把刀"),
    "zhang": ("张馒头", "破庙", "山外", "外面"),
    "sect": ("拜师", "武馆", "三派", "剑阳", "玄铁", "烟雨", "门派"),
}

TOPIC_FACTS = {
    "bandit": ("bandits", "artifact"),
    "artifact": ("artifact", "bandits", "zhang"),
    "zhang": ("zhang", "artifact"),
    "sect": ("sects", "zhang"),
}

PUBLIC_FACTS = {
    "bandits": "半月前，黑旋风带一伙山贼占了后山崖旧寨，镇上货郎、猎户都不敢往那边走。",
    "artifact": "传闻黑旋风从外头劫来一把神器宝刀“断岳刀”，藏在寨子里；没人亲眼见过。",
    "zhang": "破庙的张馒头是镇上唯一出过远门的人，疯话里总念“山外有山”；传说取回神器的人才配听他说真话。",
    "sects": "剑阳道馆、玄铁堂、烟雨小筑都盯着后山；镇上流传神器现世之日，三派会重开山门收徒。",
}

STAGE2_FACT_TEMPLATE = "{player}杀了黑旋风，清了后山那伙山贼，取回了断岳刀；这件事已经传遍清溪镇。"
AMBIENT_LINE = "近日镇上都在议论后山的事。"

NPC_STANCE = {
    "gu_changfeng": {
        "stance": "你认为断岳刀该归剑阳，但嘴上不屑外物；你厌恶山贼，也不愿向张馒头低头。",
        "avoid": "不愿承认自己在意那把刀，也不愿提剑阳这些年被另外两派压过一头。",
        "knowledge": [
            {"text": "本派打算借神器压过玄铁堂与烟雨楼，但你不肯明说。",
             "min_stage": 0, "min_affinity": 60},
        ],
        "stage2": "你仍不承认自己看走眼，只说刀是死物、取刀的人才活着。",
    },
    "tie_wushuang": {
        "stance": "你想带人上山硬抢，把这事当成喝酒前的热身；你觉得张馒头是装疯。",
        "avoid": "不愿承认自己没先上山，也不愿提玄铁堡先前折了几个弟子。",
        "knowledge": [
            {"text": "猎户说过寨子后崖有条窄路，黑旋风每晚都从那里巡一遍；你没亲自去过。",
             "min_stage": 0, "min_affinity": 50},
        ],
        "stage2": "你拍桌大笑，要玩家请你喝酒，嘴上说刀归他、山贼归你。",
    },
    "su_wanqing": {
        "stance": "你怀疑“神器”是有人做的局，按兵不动，爱套别人的话。",
        "avoid": "不会说出自己查到了什么，也不愿让人看出烟雨楼在等谁先出手。",
        "knowledge": [
            {"text": "你隐约知道另外两派都派了人上后山，但不确定是谁。",
             "min_stage": 0, "min_affinity": 60},
        ],
        "stage2": "你先问玩家信不信那是神器，再问张馒头说了什么；仍留三分。",
    },
    "wang_dazhu": {
        "stance": "你不关心神器归谁，只关心消息能卖几个钱。",
        "avoid": "不会白送消息；也不愿承认自己手里的布防图有一半是猜的。",
        "knowledge": [
            {"text": "你手里有一份寨子布防，真假参半：后崖窄路、夜里换岗、黑旋风喝酒的习惯。",
             "min_stage": 0, "min_affinity": 0},
        ],
        "stage2": "你立刻想打听细节、转卖消息，劝玩家别把刀露出来。",
    },
    "chen_bansong": {
        "stance": "你只担心上山的人送命，避谈宝物。",
        "avoid": "不愿提当年为一件宝物丢命的那个人，也不愿说那人是谁。",
        "knowledge": [
            {"text": "你亲眼见过有人为宝物死在山上；你记得那人的伤，却不愿再提名字。",
             "min_stage": 0, "min_affinity": 60},
        ],
        "stage2": "你先看玩家伤势，念叨宝物害人，确认人没事才松口气。",
    },
    "liu_xiaodie": {
        "stance": "你把这事当全镇最热闹的谈资，说法最夸张。",
        "avoid": "不愿承认自己多半只是听来的，没亲眼见过。",
        "knowledge": [
            {"text": "你听来的说法比谁都多：有人说刀会发光，有人说黑旋风三头六臂；你都没亲眼见过。",
             "min_stage": 0, "min_affinity": 0},
        ],
        "stage2": "你把玩家当英雄，追问一路细节，说法越传越夸张。",
    },
    "zhang_mantou": {
        "stance": "你知道断岳刀的真正来历和山外的世界，但在等一个取回刀的人；在此之前你只用疯话点人。",
        "avoid": "阶段 2 之前不说破；之后也只说七分。",
        "knowledge": [],
        "stage2": "你认了玩家，开始用疯话提“山外”，但只说七分。",
        "stage2_truth": "断岳刀是山外旧朝的信物；持刀的人才能听懂你疯话里的地名；山外还有更大的江湖。",
    },
}

_KNOWLEDGE_TAG_ORDER = ("bandit", "artifact", "zhang", "sect")
_CUT_LINE = "只依据以上事实与你的见闻说话；不知道的岔开、装糊涂；不要编造新人物、新宝物、新门派；不要复述设定。"


def stance_entry(slug: str) -> dict:
    return NPC_STANCE.get(slug, {})


def topic_tags(text: str) -> list[str]:
    if not text:
        return []
    return [
        tag for tag in _KNOWLEDGE_TAG_ORDER
        if any(word in text for word in TOPIC_KEYWORDS[tag])
    ]


def knowledge_for(slug: str, stage: int, affinity: int, tags: list[str]) -> list[str]:
    entry = stance_entry(slug)
    out = [
        item["text"] for item in entry.get("knowledge", [])
        if stage >= int(item.get("min_stage", 0)) and affinity >= int(item.get("min_affinity", 0))
    ]
    if entry.get("stage2_truth") and stage >= 2 and (
        "artifact" in tags or "zhang" in tags
    ):
        out.append(entry["stage2_truth"])
    return out


def _public_fact_keys(tags: list[str]) -> list[str]:
    keys: list[str] = []
    for tag in tags:
        for key in TOPIC_FACTS.get(tag, ()):
            if key not in keys:
                keys.append(key)
    return [key for key in PUBLIC_FACTS if key in keys]


def brief_for(npc: dict, text: str, stage: int, affinity: int, player_name: str) -> str:
    slug = str(npc.get("slug", ""))
    entry = stance_entry(slug)
    tags = topic_tags(text)
    if not tags and stage < 2:
        return AMBIENT_LINE
    if not entry:
        return AMBIENT_LINE

    fact_keys = _public_fact_keys(tags)[:3]
    announced = stage >= 2

    def compose(keys: list[str]) -> str:
        fact_lines = []
        if announced:
            fact_lines.append(STAGE2_FACT_TEMPLATE.format(player=player_name))
        fact_lines.extend(f"- {PUBLIC_FACTS[key]}" for key in keys)
        lines = ["【清溪镇人人都知道】", *fact_lines, "【你的立场】", entry.get("stance", "")]
        if announced and entry.get("stage2"):
            lines.append(entry["stage2"])
        details = knowledge_for(slug, stage, affinity, tags)
        if details:
            lines.append("【你知道的细节】")
            lines.extend(f"- {item}" for item in details)
            if slug == "zhang_mantou" and announced:
                lines.append("你只说七分，不把山外一次说尽。")
        lines.extend(["【分寸】", _CUT_LINE])
        return "\n".join(lines)

    while True:
        brief = compose(fact_keys)
        if len(brief) <= 260 or not fact_keys:
            return brief
        fact_keys.pop()


def public_facts_for_client() -> list[str]:
    return list(PUBLIC_FACTS.values())
