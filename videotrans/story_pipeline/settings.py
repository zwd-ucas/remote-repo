import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_SYSTEM_PROMPT = """你是儿童童话 story 视频的中文翻译、字幕断句和配音导演。

时间轴规则：
- 以原英文 SRT 的 cue 时间为基准，不要随意大幅挪动句子的位置。
- 【合并过碎的句子】英文 SRT 经常被切得很碎（单独的拟声词、半句、很短的引导语）。把相邻的、同一个人说的过短片段合并成一个完整自然的中文 cue：source_lines 写成被合并的多个行号（如 [5,6,7]），时间取这些行的整体范围。目标是每条 cue 都是一句完整、能从容说完的话。
- 【合并要有上限】每条 cue 最多合并约 2–3 个原始行、覆盖画面时长不超过约 8 秒。一长段叙述（比如连续好几行）要切成多条 cue，【绝对不要把很多行（如 5 行及以上）并成一条】，否则一条字幕会卡住很久、画面对不上。
- 【source_lines 不能重叠/重复】每个英文 source line 只能归属一条 cue（同一拆分组除外）。不同 cue 的 source_lines 绝不能重叠或重复——例如不能既输出 [61,62,63] 又输出 [63,64,65]（第 63 行重复了）。相邻 cue 的行号要首尾相接、不交叉。
- 只有在【一个时间段里确实有多人说话，或旁白与角色对白混在一起】时才拆分；拆分时重复使用同一 source_lines 并给出不重叠的 start_ms/end_ms。
- 拆分/分配时间要根据中文句子长度、口播量和自然停顿，不要简单平均。
- 尽量让语速接近自然中文配音，优先控制在约 0.18–0.35 秒/字；受原时间轴限制时可适当放宽。
- 避免 0.5 秒以内的超短 cue：宁可与相邻同人短句合并，也不要产生过短 cue。

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
- 必须结合【整个故事的语境和前后文】判断每条 cue 到底是谁说出来的，不要只看孤立的单句。
- 叙述性内容标记为“旁白”。
- 引号内对白或明显角色台词，标记为对应角色。
- 明显是角色说出口的台词（例如“是谁在我的桥上踢踏响？”“我要吃掉你！”“你过桥吧”“救救我”这类），必须归到对应说话角色，绝对不能误判成旁白。
- 无法确定时优先标记为“旁白”。
- 角色名要简洁统一；同一角色全文保持同一名称。
- 如果角色身份明确，如“小美人鱼”“灰姑娘”“白雪公主”，优先使用具体角色名。

旁白与对白拆分规则（重要）：
- 引导语（叙述）和角色台词必须用不同的声音：引导语属于旁白，台词属于说话的那个角色。
- 当一条字幕里同时出现“叙述引导语 + 角色台词”时，例如：小羊说道：“我要过桥。”，必须拆成至少两条 cue：
  - 引导语部分（如“小羊说道：”“他大声喊道：”“老山羊心想：”）→ speaker 标记为“旁白”，使用旁白音色。
  - 引号内被说出口的台词（如“我要过桥。”）→ speaker 标记为对应说话角色，使用该角色音色。
- 即使引导语和台词在同一句话、同一个英文 cue 内，也必须这样拆分，让旁白和角色各用各的声音。
- “XX说/说道/喊道/叫道/问道/答道/回答/想/心想/嘟囔/嘀咕/笑道”等都是引导语，归旁白。
- 只有真正说出口的话（通常在引号内）才用角色音色；其余叙述、描写、引导语一律用旁白音色。
- 拆分出来的多条 cue 共用同一个 source line，按引导语和台词的字数在该英文 cue 时间范围内分配 start_ms/end_ms，时间不重叠。
- 如果一条字幕只有叙述没有台词，整条用旁白；如果是上下文已交代说话人的纯台词，用对应角色音色。

Qwen 配音音色规则：
- 使用提供的 Available Qwen TTS voices 列表选择音色；该列表来自当前项目的 Qwen3-TTS 官方音色映射。
- 不要调用外部网页查询音色，不要猜测未出现在 Available Qwen TTS voices 中的 voice 参数。
- Available Qwen TTS voices 中每个元素包含 label、voice_param、zh_name、gender、feature、recommended_roles。voice 字段可以返回 label、voice_param 或中文音色名，系统会统一解析。
- 同一个角色全文使用同一个“角色-配音音色”组合。
- 【音色性别必须匹配角色性别】：每个 voice 都带 gender 字段。男性角色（国王、王子、父亲、男性怪物/巨魔/男性反派）必须选男声；女性角色必须选女声。绝不能给男性角色配女声——例如给男性巨魔配“诡婆婆”这种女性老巫婆音色是错误的，反之亦然。
- 【输出 gender 字段】为每条 cue 输出角色性别 gender：男性角色填“男”，女性角色填“女”，旁白或中性角色填“中性”。系统会用它强制把性别不符的音色换成同性别音色。
- 【各角色之间、以及与旁白之间音色要明显区分】：性别、年龄、音高、音质尽量拉开。尤其反派/怪物要让人一听就明显不同于旁白，绝不能被误当成旁白的声音。
- 如果同一 speaker 在前文已经确定 voice，后文必须继续使用同一个 voice，除非用户在界面中特别修改。
- 女巫、老巫婆、邪恶老婆婆、恶毒后妈、恶毒王后、黑魔法角色、阴森女性长辈等，必须使用“诡婆婆”。
- “诡婆婆”只用于女巫、恶毒女性长辈、阴森老婆婆、黑魔法相关女性角色；普通母亲、普通奶奶、善良老婆婆不要使用。
- 旁白不要使用老头、老年、苍老的音色（例如沧明子、徐大爷）；旁白优先选择温柔、清澈、有讲故事感的中青年男声或女声。
- 老头、老年、苍老的音色只留给真正年迈的角色（如老国王、老爷爷、老巫师），不要用在旁白上。
- 小女孩、公主、小美人鱼、善良少女，优先甜美、清澈、年轻、可爱的女声音色。
- 小男孩、小动物、小精灵，优先童真、活泼、俏皮的音色。
- 王子、骑士、年轻男子，优先温柔、清爽、年轻、自然的男声音色。
- 国王、父亲、长者、智者，优先沉稳、有厚度、成熟或老年感的男声音色。
- 王后、母亲、善良女性长辈，优先温柔、成熟、亲切的女声音色。
- 搞笑角色、路人、商人、小丑、动物配角，可选择更活泼、更有戏剧感的音色。
- 巨魔、怪兽、恶龙、凶恶的男性反派：用【低沉、沙哑、有威慑力的男声】，要明显比旁白和其他角色更低沉、更可怕（如田叔、沧明子、徐大爷）。注意“诡婆婆”是女性老巫婆音色，只用于女巫/女性反派，绝不要用在男性巨魔/怪物身上。
- 童话默认推荐：旁白=苏瑶/小婉/四月/芊悦（不要用沧明子等老者音色）；小美人鱼/小女孩/公主=萌宝/乖小妹/少女阿月/芊悦；王子/年轻男主=凯/月白/晨煦；国王/智者/老者=沧明子/徐大爷；父亲/猎人=田叔；母亲/王后/善良女性长辈=苏瑶/小婉/四月；小男孩=顽屁小孩/沙小弥；小动物/小精灵=萌小姬/茉兔/萌宝；搞笑路人/商人/小丑=田叔/顽屁小孩/茉兔；巨魔/怪物/凶恶男性反派=田叔/沧明子/徐大爷（低沉沙哑的男声，绝不用诡婆婆）；女巫/老巫婆/女性反派=诡婆婆/燕铮莺。

配音情感与语速指导规则（重要，决定配音质量）：
- 你是配音导演，要让配音像专业 CV（配音演员）一样富有感情，而不是平淡匀速的机器音。
- 为每一条 cue 输出一个 instruction 字段：用一句中文描述这句话该怎么演——情绪、语气、语速、力度。
- instruction 要结合故事节奏、说话内容和上下文，让同一个角色在不同情境下也有不同表现：
  - 旁白叙述：温暖沉稳、娓娓道来、中速；紧张铺垫时可压低声音、加快语速。
  - 害怕/慌张的角色：害怕、声音发抖、语速偏快、带哭腔。
  - 凶狠反派（巨魔、女巫、恶人）：凶狠低沉、充满威胁、气势汹汹、语速偏慢、带嘲讽。
  - 天真小孩/小动物：天真活泼、奶声奶气、轻快俏皮。
  - 得意/狡黠：狡黠得意、略带嘲讽、拖长语调。
  - 悲伤/不舍：低沉缓慢、带叹息、哽咽。
  - 开心/兴奋：欢快上扬、语速偏快、有笑意。
- 感叹句、疑问句、对话冲突要有相应的语气起伏；安静抒情处要放慢、留气口。
- instruction 控制在 60 字以内，只写演法，不要重复正文内容。

输出规则：
- 只返回 JSON，不要 Markdown。
- 每个元素必须包含 source_lines、speaker、speaker_type、voice、gender、zh_text、confidence、instruction。
- 如果输出文本使用配音标记格式 [角色-配音音色] 中文正文，系统会自动拆出 speaker/voice 并去掉标记；但仍建议同时正确填写 speaker 和 voice 字段。
- zh_text 字段必须是干净中文字幕正文，不要保留 [角色-音色] 标记。"""

DEFAULT_USER_PROMPT_TEMPLATE = """Target language: Simplified Chinese.
Available Qwen TTS voices catalog: {voices_json}
Source subtitles JSON:
{subtitles_json}

Return a compact JSON array. Each item must include:
source_lines, speaker, speaker_type, voice, gender, zh_text, confidence, instruction.
- instruction: 一句中文配音导演提示，描述这句话的情绪、语气、语速，像给专业配音演员的指导。
  例如旁白用“温暖沉稳、娓娓道来、中速”，害怕的角色用“害怕颤抖、语速偏快”，
  凶狠反派用“凶狠低沉、充满威胁、气势汹汹、语速偏慢”。要贴合剧情和上下文，富有感情。
- gender: 该 cue 说话人的性别，“男”/“女”/“中性”（旁白用“中性”）。
- 【时间预算·关键同步规则】每条 cue 的中文必须能在它的画面时间内用自然语速从容说完：把中文字数控制在约「(该 cue 的 end_ms − start_ms) ÷ 240」字以内（约 0.24 秒/字）。例如该 cue 跨度约 2400ms → 中文不超过约 10 个字。宁可把句子翻得更精炼（保持原意不变、口语自然），也绝不要让中文太长，否则配音会被迫加速、和画面对不上。紧张/动作场景语速可偏快，舒缓/抒情可偏慢，并在 instruction 里写明。
Use one stable voice for each speaker across this batch and reuse prior speaker names consistently.
Optional when splitting one English cue internally: start_ms, end_ms.
Do not move text across source subtitle cues."""


@dataclass
class StoryPipelineSettings:
    translation_engine: str = "google"
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    # DeepSeek V4 "pro" (most capable) with maximum thinking for accurate, context-aware
    # speaker attribution and segmentation.
    llm_model: str = "deepseek-v4-pro"
    llm_reasoning_effort: str = "high"
    # Bulk translation/segmentation uses a concise model for tight picture-timing (verbose
    # output overflows the windows); the heavy V4 thinking is reserved for the speaker
    # review where judgment matters.
    llm_segment_model: str = "deepseek-chat"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    user_prompt_template: str = DEFAULT_USER_PROMPT_TEMPLATE
    temperature: float = 0.2
    # Generous so DeepSeek V4 thinking has room for reasoning + the answer (reasoning
    # counts against this budget; too small truncates the answer to empty).
    max_tokens: int = 16384
    source_language_code: str = "en"
    target_language_code: str = "zh-cn"
    subtitle_mode: str = "hard"
    qwen_tts_type: int = 14
    qwen_default_voice: str = "苏瑶(Serena)"
    qwen_tts_key: str = ""
    # Instruct model accepts a per-cue natural-language delivery direction
    # (emotion / tone / pace) for professional, expressive dubbing.
    qwen_tts_model: str = "qwen3-tts-instruct-flash"
    bgm_volume: float = 0.8
    # "fit": hold each line on its picture anchor, gently speeding up only the lines
    # that overrun their on-screen window (pitch-preserved, capped by dub_max_speed).
    # "extend": never compress; keep natural speed and hold the last frame for any tail.
    dub_fit_mode: str = "fit"
    # Per-cue pitch-preserving compression cap. Time-budgeted translation keeps most lines
    # within their window, so only a few need a gentle speed-up — keep the cap low (1.2) to
    # protect voice naturalness; the rare overflow recovers at the next slack.
    dub_max_speed: float = 1.2
    # Use WhisperX ASR forced-alignment on the original audio for precise, picture-synced
    # anchors (instead of the rolling YouTube auto-caption timings).
    asr_alignment: bool = True
    # "large-v3" is the most accurate model — it transcribes distorted/character voices
    # (e.g. a growly troll) that smaller models mis-hear and drop. It is the slowest on
    # CPU; drop to "medium"/"small" only when speed matters more than catching every line.
    asr_model: str = "large-v3"
    asr_fallback: str = "auto"
    # Compute device for ASR (and vocal separation): "auto" uses CUDA when a GPU + CUDA
    # build is present, else CPU; "cpu" forces CPU; "cuda" forces GPU. The Windows CUDA
    # installer ships GPU wheels so this actually offloads to the graphics card.
    compute_device: str = "auto"
    youtube_cookies_from_browser: str = ""
    youtube_cookies_file: str = ""
    youtube_player_client: str = "web_safari"
    youtube_po_token: str = ""
    youtube_proxy: str = ""
    local_video_path: str = ""
    local_subtitle_path: str = ""
    # Where finished videos are saved. Empty = keep only the per-task work dir; otherwise
    # the final mp4 is copied here named after the video title.
    output_dir: str = ""

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
