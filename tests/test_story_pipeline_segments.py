import json

from videotrans.story_pipeline.story_segments import normalize_story_cues
from videotrans.story_pipeline.types import SrtItem


def test_review_reattributes_speaker_and_inherits_cast_voice(monkeypatch):
    # The speaker-review pass must fix a mis-attributed line and give it the speaker's
    # established (cast-majority) voice — so a character's line never stays in the wrong
    # voice. (The troll's quote had been left in the narrator's voice.)
    from videotrans.story_pipeline import pipeline
    from videotrans.story_pipeline.settings import StoryPipelineSettings

    raw = [
        {"source_lines": [1], "speaker": "巨魔", "voice": "诡婆婆(Ebona)", "zh_text": "是谁在我桥上？", "instruction": "凶"},
        {"source_lines": [2], "speaker": "旁白", "voice": "苏瑶(Serena)", "zh_text": "我要吃掉你！", "instruction": "凶"},
    ]
    # LLM judges line 2's quote actually belongs to the troll, not the narrator.
    monkeypatch.setattr(
        pipeline,
        "call_llm_chat",
        lambda *a, **k: json.dumps(
            [
                {"source_lines": [1], "speaker": "巨魔", "zh_text": "是谁在我桥上？", "instruction": "凶"},
                {"source_lines": [2], "speaker": "巨魔", "zh_text": "我要吃掉你！", "instruction": "凶"},
            ]
        ),
    )
    out = pipeline.default_review(raw, StoryPipelineSettings(), lambda _t: None)
    assert out[1]["speaker"] == "巨魔"
    assert out[1]["voice"] == "诡婆婆(Ebona)"  # inherits the troll's cast voice, not the narrator's
    assert out[1]["speaker_type"] == "character"


def _srt(line, start, end, text):
    return SrtItem(
        line=line,
        start_time=start,
        end_time=end,
        startraw=f"00:00:{start // 1000:02d},000",
        endraw=f"00:00:{end // 1000:02d},000",
        time=f"00:00:{start // 1000:02d},000 --> 00:00:{end // 1000:02d},000",
        text=text,
    )


def test_cue_timing_is_inherited_from_covered_english_lines():
    source = [
        _srt(1, 1000, 2500, "Once upon a time,"),
        _srt(2, 2500, 4000, "a girl opened the door."),
    ]
    payload = [
        {
            "source_lines": [1, 2],
            "start_ms": 999999,
            "end_ms": 999999,
            "speaker": "Narrator",
            "speaker_type": "narrator",
            "voice": "沧明子(Eldric Sage)",
            "zh_text": "很久以前，一个女孩打开了门。",
            "confidence": 0.92,
        }
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"沧明子(Eldric Sage)"},
        default_voice="沧明子(Eldric Sage)",
    )

    assert issues == []
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms == 4000
    assert cues[0].source_lines == [1, 2]


def test_invalid_qwen_voice_falls_back_to_default_and_records_issue():
    source = [_srt(1, 0, 1600, "She whispered, hello.")]
    payload = [
        {
            "source_lines": [1],
            "speaker": "Girl",
            "speaker_type": "character",
            "voice": "Imaginary Voice",
            "zh_text": "她轻声说，你好。",
            "confidence": 0.8,
        }
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"少女阿月(Stella)"},
        default_voice="少女阿月(Stella)",
    )

    assert cues[0].voice == "少女阿月(Stella)"
    assert issues[0].code == "invalid_voice"
    assert issues[0].line == 1


def test_non_contiguous_source_lines_are_rejected():
    source = [
        _srt(1, 0, 1000, "A"),
        _srt(2, 1000, 2000, "B"),
        _srt(3, 2000, 3000, "C"),
    ]
    payload = [
        {
            "source_lines": [1, 3],
            "speaker": "Narrator",
            "voice": "沧明子(Eldric Sage)",
            "zh_text": "不连续的字幕。",
        }
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"沧明子(Eldric Sage)"},
        default_voice="沧明子(Eldric Sage)",
    )

    assert cues == []
    assert issues[0].code == "non_contiguous_source_lines"


def test_role_voice_prefix_is_removed_from_subtitle_text():
    source = [_srt(1, 0, 2000, "The witch laughed.")]
    payload = [
        {
            "source_lines": [1],
            "speaker": "旁白",
            "speaker_type": "character",
            "voice": "沧明子(Eldric Sage)",
            "zh_text": "[女巫-诡婆婆] 哈哈哈，我的魔法就要成功了！",
            "confidence": 0.9,
        }
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"沧明子(Eldric Sage)", "诡婆婆(Ebona)"},
        default_voice="沧明子(Eldric Sage)",
    )

    assert issues == []
    assert cues[0].speaker == "女巫"
    assert cues[0].voice == "诡婆婆(Ebona)"
    assert cues[0].zh_text == "哈哈哈，我的魔法就要成功了！"


def test_voice_parameter_name_resolves_to_web_voice_label():
    source = [_srt(1, 0, 2000, "The prince smiled.")]
    payload = [
        {
            "source_lines": [1],
            "speaker": "王子",
            "speaker_type": "character",
            "voice": "Kai",
            "zh_text": "王子轻轻笑了。",
        }
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"凯(Kai)", "月白(Moon)"},
        default_voice="凯(Kai)",
    )

    assert issues == []
    assert cues[0].voice == "凯(Kai)"


def test_same_speaker_valid_voice_is_not_overridden_by_role_recommendation():
    source = [
        _srt(1, 0, 1800, "The prince opened the gate."),
        _srt(2, 1800, 3600, "The prince whispered hello."),
    ]
    payload = [
        {
            "source_lines": [1],
            "speaker": "王子",
            "speaker_type": "character",
            "voice": "月白(Moon)",
            "zh_text": "王子打开了城门。",
        },
        {
            "source_lines": [2],
            "speaker": "王子",
            "speaker_type": "character",
            "voice": "月白(Moon)",
            "zh_text": "王子轻声说，你好。",
        },
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"凯(Kai)", "月白(Moon)"},
        default_voice="凯(Kai)",
    )

    assert issues == []
    # The LLM's valid choice (月白) is kept for the whole character — not replaced by the
    # default/role-recommended 凯 — and stays consistent across the character's lines.
    assert [cue.voice for cue in cues] == ["月白(Moon)", "月白(Moon)"]


def test_witch_role_valid_voice_is_prompt_guidance_not_hard_override():
    source = [_srt(1, 0, 2000, "The witch raised her wand.")]
    payload = [
        {
            "source_lines": [1],
            "speaker": "女巫",
            "speaker_type": "character",
            "voice": "卡捷琳娜(Katerina)",
            "zh_text": "女巫举起了魔杖。",
        }
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"卡捷琳娜(Katerina)", "诡婆婆(Ebona)"},
        default_voice="卡捷琳娜(Katerina)",
    )

    assert issues == []
    assert cues[0].voice == "卡捷琳娜(Katerina)"


def test_multiple_outputs_inside_one_source_cue_are_retimed_without_overlap():
    source = [_srt(1, 1000, 4000, "Hello there. Come with me.")]
    payload = [
        {
            "source_lines": [1],
            "speaker": "旁白",
            "speaker_type": "narrator",
            "voice": "沧明子(Eldric Sage)",
            "zh_text": "你好呀。",
        },
        {
            "source_lines": [1],
            "speaker": "旁白",
            "speaker_type": "narrator",
            "voice": "沧明子(Eldric Sage)",
            "zh_text": "跟我一起来吧。",
        },
    ]

    cues, issues = normalize_story_cues(
        payload,
        source,
        valid_voices={"沧明子(Eldric Sage)"},
        default_voice="沧明子(Eldric Sage)",
    )

    assert issues == []
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms <= cues[1].start_ms
    assert cues[1].end_ms == 4000
