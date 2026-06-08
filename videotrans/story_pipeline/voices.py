import re
from typing import Any


QWEN_VOICE_DETAILS: dict[str, dict[str, Any]] = {
    "Cherry": {
        "zh_name": "芊悦",
        "gender": "女",
        "feature": "阳光积极、亲切自然",
        "recommended_roles": ["善良少女", "活泼旁白", "公主"],
    },
    "Serena": {
        "zh_name": "苏瑶",
        "gender": "女",
        "feature": "温柔小姐姐",
        "recommended_roles": ["母亲", "王后", "温柔旁白"],
    },
    "Ethan": {
        "zh_name": "晨煦",
        "gender": "男",
        "feature": "标准普通话、北方口音、阳光温暖",
        "recommended_roles": ["骑士", "少年英雄"],
    },
    "Chelsie": {
        "zh_name": "千雪",
        "gender": "女",
        "feature": "二次元虚拟女友",
        "recommended_roles": ["梦幻少女", "魔法少女"],
    },
    "Momo": {
        "zh_name": "茉兔",
        "gender": "女",
        "feature": "撒娇搞怪、逗人开心",
        "recommended_roles": ["小精灵", "搞笑配角"],
    },
    "Vivian": {
        "zh_name": "十三",
        "gender": "女",
        "feature": "拽拽的、可爱小暴躁",
        "recommended_roles": ["任性公主", "调皮女孩"],
    },
    "Moon": {
        "zh_name": "月白",
        "gender": "男",
        "feature": "率性帅气",
        "recommended_roles": ["年轻王子", "少年骑士"],
    },
    "Maia": {
        "zh_name": "四月",
        "gender": "女",
        "feature": "知性与温柔结合",
        "recommended_roles": ["成熟公主", "温柔姐姐"],
    },
    "Kai": {
        "zh_name": "凯",
        "gender": "男",
        "feature": "舒服、沉浸式",
        "recommended_roles": ["王子", "温柔男主"],
    },
    "Nofish": {
        "zh_name": "不吃鱼",
        "gender": "男",
        "feature": "不会翘舌音的设计师",
        "recommended_roles": ["轻松搞笑男配"],
    },
    "Bella": {
        "zh_name": "萌宝",
        "gender": "女",
        "feature": "小萝莉感",
        "recommended_roles": ["小女孩", "小美人鱼", "可爱动物"],
    },
    "Jennifer": {
        "zh_name": "詹妮弗",
        "gender": "女",
        "feature": "品牌级、电影质感美语女声",
        "recommended_roles": ["英文旁白", "国际感角色"],
    },
    "Ryan": {
        "zh_name": "甜茶",
        "gender": "男",
        "feature": "节奏感强、戏感和张力强",
        "recommended_roles": ["热血王子", "青年男主"],
    },
    "Katerina": {
        "zh_name": "卡捷琳娜",
        "gender": "女",
        "feature": "御姐音色、韵律感强",
        "recommended_roles": ["女王", "反派女性", "女巫替代"],
    },
    "Aiden": {
        "zh_name": "艾登",
        "gender": "男",
        "feature": "美语大男孩",
        "recommended_roles": ["英文年轻男主"],
    },
    "Eldric Sage": {
        "zh_name": "沧明子",
        "gender": "男",
        "feature": "沉稳睿智老者",
        "recommended_roles": ["旁白", "国王", "智者", "老巫师"],
    },
    "Mia": {
        "zh_name": "乖小妹",
        "gender": "女",
        "feature": "温顺乖巧柔和",
        "recommended_roles": ["善良女孩", "小公主"],
    },
    "Mochi": {
        "zh_name": "沙小弥",
        "gender": "男",
        "feature": "聪明伶俐的小大人",
        "recommended_roles": ["聪明小男孩"],
    },
    "Bellona": {
        "zh_name": "燕铮莺",
        "gender": "女",
        "feature": "洪亮清晰、人物鲜活、热血江湖感",
        "recommended_roles": ["女将军", "强势王后", "反派女王"],
    },
    "Vincent": {
        "zh_name": "田叔",
        "gender": "男",
        "feature": "沙哑烟嗓、江湖豪情",
        "recommended_roles": ["父亲", "猎人", "粗犷男子"],
    },
    "Bunny": {
        "zh_name": "萌小姬",
        "gender": "女",
        "feature": "萌属性很强的小萝莉",
        "recommended_roles": ["小精灵", "萌系动物"],
    },
    "Neil": {
        "zh_name": "阿闻",
        "gender": "男",
        "feature": "新闻主持人风、字正腔圆",
        "recommended_roles": ["正式旁白"],
    },
    "Elias": {
        "zh_name": "墨讲师",
        "gender": "女",
        "feature": "严谨讲解型",
        "recommended_roles": ["科普旁白", "老师"],
    },
    "Arthur": {
        "zh_name": "徐大爷",
        "gender": "男",
        "feature": "质朴老年嗓音、不疾不徐",
        "recommended_roles": ["老爷爷", "村民", "老人旁白"],
    },
    "Nini": {
        "zh_name": "邻家妹妹",
        "gender": "女",
        "feature": "软糯黏甜、撒娇感",
        "recommended_roles": ["妹妹", "小女孩"],
    },
    "Ebona": {
        "zh_name": "诡婆婆",
        "gender": "女",
        "feature": "阴森老巫婆感",
        "recommended_roles": ["女巫", "恶毒王后", "恶毒后妈", "阴森老婆婆"],
    },
    "Seren": {
        "zh_name": "小婉",
        "gender": "女",
        "feature": "温和舒缓、助眠风",
        "recommended_roles": ["睡前故事旁白", "温柔母亲"],
    },
    "Pip": {
        "zh_name": "顽屁小孩",
        "gender": "男",
        "feature": "调皮捣蛋、童真",
        "recommended_roles": ["小男孩", "淘气动物"],
    },
    "Stella": {
        "zh_name": "少女阿月",
        "gender": "女",
        "feature": "甜腻迷糊少女音，也可表现正义感",
        "recommended_roles": ["少女", "公主", "魔法少女"],
    },
    "Bodega": {
        "zh_name": "博德加",
        "gender": "男",
        "feature": "热情西班牙大叔",
        "recommended_roles": ["异域大叔", "商人"],
    },
    "Sonrisa": {
        "zh_name": "索尼莎",
        "gender": "女",
        "feature": "热情开朗拉美大姐",
        "recommended_roles": ["热情女性配角"],
    },
    "Alek": {
        "zh_name": "阿列克",
        "gender": "男",
        "feature": "冷中带暖、战斗民族气质",
        "recommended_roles": ["冷峻骑士", "异域男子"],
    },
    "Dolce": {
        "zh_name": "多尔切",
        "gender": "男",
        "feature": "慵懒意大利大叔",
        "recommended_roles": ["悠闲大叔", "厨师"],
    },
    "Sohee": {
        "zh_name": "素熙",
        "gender": "女",
        "feature": "温柔开朗、情绪丰富",
        "recommended_roles": ["温柔姐姐", "女性朋友"],
    },
    "Ono Anna": {
        "zh_name": "小野杏",
        "gender": "女",
        "feature": "鬼灵精怪的青梅竹马",
        "recommended_roles": ["机灵女孩", "小精灵"],
    },
    "Lenn": {
        "zh_name": "莱恩",
        "gender": "男",
        "feature": "理性中带叛逆的德国青年",
        "recommended_roles": ["冷静青年", "学者"],
    },
    "Emilien": {
        "zh_name": "埃米尔安",
        "gender": "男",
        "feature": "浪漫法国大哥哥",
        "recommended_roles": ["浪漫王子", "温柔男主"],
    },
    "Andre": {
        "zh_name": "安德雷",
        "gender": "男",
        "feature": "磁性、自然舒服、沉稳男声",
        "recommended_roles": ["成熟王子", "成年男主"],
    },
    "Radio Gol": {
        "zh_name": "拉迪奥·戈尔",
        "gender": "男",
        "feature": "足球解说风、激情",
        "recommended_roles": ["比赛解说", "夸张旁白"],
    },
    "Jada": {
        "zh_name": "上海-阿珍",
        "gender": "女",
        "feature": "风风火火的沪上阿姐",
        "recommended_roles": ["市井女性", "阿姨角色"],
    },
    "Dylan": {
        "zh_name": "北京-晓东",
        "gender": "男",
        "feature": "北京胡同少年",
        "recommended_roles": ["北京少年", "调皮男孩"],
    },
    "Li": {
        "zh_name": "南京-老李",
        "gender": "男",
        "feature": "耐心瑜伽老师",
        "recommended_roles": ["温和老师", "平静旁白"],
    },
    "Marcus": {
        "zh_name": "陕西-秦川",
        "gender": "男",
        "feature": "声沉、话短、质朴",
        "recommended_roles": ["朴实父亲", "村民"],
    },
    "Roy": {
        "zh_name": "闽南-阿杰",
        "gender": "男",
        "feature": "诙谐直爽、市井活泼",
        "recommended_roles": ["搞笑男配"],
    },
    "Peter": {
        "zh_name": "天津-李彼得",
        "gender": "男",
        "feature": "天津相声、捧哏风",
        "recommended_roles": ["搞笑旁白", "滑稽角色"],
    },
    "Sunny": {
        "zh_name": "四川-晴儿",
        "gender": "女",
        "feature": "甜美川妹子",
        "recommended_roles": ["活泼女孩"],
    },
    "Eric": {
        "zh_name": "四川-程川",
        "gender": "男",
        "feature": "跳脱市井成都男子",
        "recommended_roles": ["活泼男配"],
    },
    "Rocky": {
        "zh_name": "粤语-阿强",
        "gender": "男",
        "feature": "幽默风趣，粤语陪聊感",
        "recommended_roles": ["粤语男配"],
    },
    "Kiki": {
        "zh_name": "粤语-阿清",
        "gender": "女",
        "feature": "甜美港妹闺蜜",
        "recommended_roles": ["粤语女孩", "闺蜜角色"],
    },
}


ROLE_VOICE_RECOMMENDATIONS: dict[str, list[str]] = {
    "旁白": ["苏瑶(Serena)", "小婉(Seren)", "四月(Maia)"],
    "小美人鱼/小女孩/公主": ["萌宝(Bella)", "乖小妹(Mia)", "少女阿月(Stella)", "芊悦(Cherry)"],
    "王子/年轻男主": ["凯(Kai)", "月白(Moon)", "晨煦(Ethan)"],
    "国王/智者/老者": ["沧明子(Eldric Sage)", "徐大爷(Arthur)"],
    "父亲/猎人/粗犷男子": ["田叔(Vincent)"],
    "母亲/王后/善良女性长辈": ["苏瑶(Serena)", "小婉(Seren)", "四月(Maia)"],
    "小男孩": ["顽屁小孩(Pip)", "沙小弥(Mochi)"],
    "小动物/小精灵": ["萌小姬(Bunny)", "茉兔(Momo)"],
    "女巫/恶毒王后/恶毒后妈": ["诡婆婆(Ebona)", "燕铮莺(Bellona)"],
    "巨魔/怪物/凶恶男性反派": ["田叔(Vincent)", "沧明子(Eldric Sage)", "徐大爷(Arthur)"],
    "老巫师/黑暗智者": ["沧明子(Eldric Sage)"],
    "搞笑路人/商人/小丑": ["田叔(Vincent)", "顽屁小孩(Pip)", "茉兔(Momo)"],
}


WITCH_ROLE_KEYWORDS = (
    "女巫",
    "老巫婆",
    "巫婆",
    "恶毒王后",
    "恶毒后妈",
    "恶毒女性",
    "邪恶老婆婆",
    "黑魔法",
    "阴森老婆婆",
)


def qwen_voice_catalog(voice_map: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, param in voice_map.items():
        details = QWEN_VOICE_DETAILS.get(param, {})
        zh_name = str(details.get("zh_name") or _label_zh_name(label))
        rows.append(
            {
                "label": label,
                "voice_param": param,
                "zh_name": zh_name,
                "gender": details.get("gender", ""),
                "feature": details.get("feature", ""),
                "recommended_roles": details.get("recommended_roles", []),
            }
        )
    return sorted(rows, key=lambda item: _voice_sort_key(str(item["voice_param"])))


def _voice_sort_key(param: str) -> tuple[int, str]:
    order = {param: idx for idx, param in enumerate(QWEN_VOICE_DETAILS)}
    return order.get(param, len(order)), param


def _label_zh_name(label: str) -> str:
    match = re.match(r"^(.*?)\(", label)
    return match.group(1) if match else label


def _voice_param(voice: str) -> str:
    """Get the Qwen voice_param from a label ('苏瑶(Serena)'), a param ('Serena'), or a zh_name."""
    match = re.search(r"\(([^)]+)\)\s*$", voice or "")
    if match:
        return match.group(1).strip()
    if voice in QWEN_VOICE_DETAILS:
        return voice
    for param, details in QWEN_VOICE_DETAILS.items():
        if details.get("zh_name") == voice:
            return param
    return voice or ""


def voice_gender(voice: str) -> str:
    """Return '男' / '女' / '' for a voice given by label, param, or zh_name."""
    return str(QWEN_VOICE_DETAILS.get(_voice_param(voice), {}).get("gender", ""))


def same_gender_voices(gender: str, valid_voices) -> list[str]:
    """Voices from valid_voices whose gender matches, in catalog order."""
    gender = (gender or "").strip()
    matches = [v for v in valid_voices if voice_gender(v) == gender]
    return sorted(matches, key=lambda v: _voice_sort_key(_voice_param(v)))
