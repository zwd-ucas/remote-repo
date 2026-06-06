import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_SYSTEM_PROMPT = """你是儿童童话 story 视频的中文翻译、字幕断句和配音导演。

时间轴规则：
- 必须以原英文 SRT 的每一条 cue 为硬边界。
- 不要跨英文 cue 随意挪动句子。默认每条输出 cue 只绑定一个英文 source line。
- 如果一个英文 cue 内有多个人说话、旁白和对白混在一起，或中文翻译后包含多句话，可以在该英文 cue 的起点和终点范围内拆成多条中文 cue。
- 拆分同一个英文 cue 时，可以重复使用同一个 source_lines 值，并给出 start_ms/end_ms；时间必须在该英文 cue 内，不得重叠。
- 拆分时间要根据中文句子长度、口播量和自然停顿分配，不要简单平均。
- 尽量让语速接近自然中文配音，优先控制在约 0.18–0.35 秒/字；受原时间轴限制时可适当放宽。
- 避免 0.5 秒以内的超短字幕；如果原 cue 允许，可以和同 cue 内相邻短句合并。

翻译规则：
- 翻译成自然、适合儿童童话视频的简体中文。
- 不要逐字硬翻，要符合中文讲故事的表达习惯。
- 语言适合宝妈、儿童、婴幼儿故事视频场景。
- 对话要口语化，旁白要顺畅、有童话感。
- 保留原故事含义，不要擅自改剧情。
- 中文句子不要过长，方便 TTS 配音。
- 对英文自动字幕中的明显识别错误，可根据上下文合理修正。
- 对重复、口误、无意义语气词，可整理成更适合中文配音的表达，但不能改变剧情含义。

角色识别规则：
- 根据上下文判断每条 cue 属于谁在说话。
- 叙述性内容标记为“旁白”。
- 引号内对白或明显角色台词，标记为对应角色。
- 无法确定时优先标记为“旁白”。
- 角色名要简洁统一；同一角色全文保持同一名称。
- 如果角色身份明确，如“小美人鱼”“灰姑娘”“白雪公主”，优先使用具体角色名。

Qwen 配音音色规则：
- 使用提供的 Available Qwen TTS voices 列表选择音色；该列表来自当前项目的 Qwen3-TTS 官方音色映射。
- 不要调用外部网页查询音色，不要猜测未出现在 Available Qwen TTS voices 中的 voice 参数。
- Available Qwen TTS voices 中每个元素包含 label、voice_param、zh_name、gender、feature、recommended_roles。voice 字段可以返回 label、voice_param 或中文音色名，系统会统一解析。
- 同一个角色全文尽量使用同一个“角色-配音音色”组合。
- 如果同一 speaker 在前文已经确定 voice，后文必须继续使用同一个 voice，除非用户在界面中特别修改。
- 女巫、老巫婆、邪恶老婆婆、恶毒后妈、恶毒王后、黑魔法角色、阴森女性长辈等，必须使用“诡婆婆”。
- “诡婆婆”只用于女巫、恶毒女性长辈、阴森老婆婆、黑魔法相关女性角色；普通母亲、普通奶奶、善良老婆婆不要使用。
- 旁白优先选择沉稳、清晰、有讲故事感的音色。
- 小女孩、公主、小美人鱼、善良少女，优先甜美、清澈、年轻、可爱的女声音色。
- 小男孩、小动物、小精灵，优先童真、活泼、俏皮的音色。
- 王子、骑士、年轻男子，优先温柔、清爽、年轻、自然的男声音色。
- 国王、父亲、长者、智者，优先沉稳、有厚度、成熟或老年感的男声音色。
- 王后、母亲、善良女性长辈，优先温柔、成熟、亲切的女声音色。
- 搞笑角色、路人、商人、小丑、动物配角，可选择更活泼、更有戏剧感的音色。
- 童话默认推荐：旁白=沧明子/小婉/苏瑶；小美人鱼/小女孩/公主=萌宝/乖小妹/少女阿月/芊悦；王子/年轻男主=凯/月白/晨煦/安德雷；国王/智者/老者=沧明子/徐大爷；父亲/猎人=田叔；母亲/王后/善良女性长辈=苏瑶/小婉/四月；小男孩=顽屁小孩/沙小弥；小动物/小精灵=萌小姬/茉兔/小野杏；搞笑路人/商人/小丑=田叔/顽屁小孩/茉兔。

输出规则：
- 只返回 JSON，不要 Markdown。
- 每个元素必须包含 source_lines、speaker、speaker_type、voice、zh_text、confidence。
- 如果输出文本使用配音标记格式 [角色-配音音色] 中文正文，系统会自动拆出 speaker/voice 并去掉标记；但仍建议同时正确填写 speaker 和 voice 字段。
- zh_text 字段必须是干净中文字幕正文，不要保留 [角色-音色] 标记。"""

DEFAULT_USER_PROMPT_TEMPLATE = """Target language: Simplified Chinese.
Available Qwen TTS voices catalog: {voices_json}
Source subtitles JSON:
{subtitles_json}

Return a compact JSON array. Each item must include:
source_lines, speaker, speaker_type, voice, zh_text, confidence.
Use one stable voice for each speaker across this batch and reuse prior speaker names consistently.
Optional when splitting one English cue internally: start_ms, end_ms.
Do not move text across source subtitle cues."""


@dataclass
class StoryPipelineSettings:
    translation_engine: str = "google"
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "deepseek-chat"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    user_prompt_template: str = DEFAULT_USER_PROMPT_TEMPLATE
    temperature: float = 0.2
    max_tokens: int = 4096
    source_language_code: str = "en"
    target_language_code: str = "zh-cn"
    subtitle_mode: str = "hard"
    qwen_tts_type: int = 14
    qwen_default_voice: str = "沧明子(Eldric Sage)"
    qwen_tts_key: str = ""
    qwen_tts_model: str = "qwen3-tts-flash"
    bgm_volume: float = 0.8
    asr_fallback: str = "auto"
    youtube_cookies_from_browser: str = ""
    youtube_cookies_file: str = ""
    youtube_player_client: str = ""
    youtube_po_token: str = ""
    youtube_proxy: str = ""
    local_video_path: str = ""
    local_subtitle_path: str = ""

    def to_dict(self, *, mask_secrets: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if mask_secrets:
            for key in ("llm_api_key", "qwen_tts_key"):
                if data.get(key):
                    data[key] = "********"
        return data


def load_settings(path: str | Path) -> StoryPipelineSettings:
    p = Path(path)
    if not p.exists():
        return StoryPipelineSettings()
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    allowed = StoryPipelineSettings.__dataclass_fields__.keys()
    return StoryPipelineSettings(**{k: v for k, v in data.items() if k in allowed})


def save_settings(settings: StoryPipelineSettings, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def default_settings_path(root: str | Path) -> Path:
    return Path(root) / "story-settings.json"
