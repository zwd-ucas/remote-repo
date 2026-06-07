from videotrans.story_pipeline.audio_slots import plan_audio_slots, plan_voice_timeline
from videotrans.story_pipeline.story_segments import StoryCue, normalize_story_cues
from videotrans.story_pipeline.types import SrtItem


def test_voice_timeline_keeps_natural_speed_and_extends_when_audio_too_long():
    # Overlapping anchors (like YouTube rolling auto-captions) with TTS audio whose
    # natural total exceeds the video. By default (max_speed=1.0) the voice is NOT
    # sped up: no overlap, natural length kept, and the timeline runs past the video
    # so the caller can extend the picture.
    items = [("a", 0, 3000), ("b", 1000, 3000), ("c", 2000, 3000)]
    tl = plan_voice_timeline(items, video_duration_ms=4000, min_gap_ms=140)

    assert tl.speed == 1.0  # never sped up
    assert tl.out_total_ms > 4000  # natural length kept, extends past the video
    outs = sorted((p.out_start_ms, p.out_end_ms) for p in tl.placements)
    for (_s0, e0), (s1, _e1) in zip(outs, outs[1:]):
        assert e0 <= s1, f"voices overlap: {e0} > {s1}"
    assert all(p.out_end_ms > p.out_start_ms for p in tl.placements)


def test_voice_timeline_fit_mode_compresses_per_cue_to_fit_video():
    # With a cap (max_speed > 1) only the over-long cues are sped up, per-cue (not one
    # global factor), so the timeline fits the video without chipmunking everything.
    items = [("a", 0, 3000), ("b", 1000, 3000), ("c", 2000, 3000)]
    tl = plan_voice_timeline(items, video_duration_ms=4000, min_gap_ms=140, max_speed=10.0)

    assert tl.speed == 1.0  # the global field is unused now; tempo is per-cue
    assert any(p.speed > 1.0 for p in tl.placements)  # over-long cues compressed
    assert all(p.speed <= 10.0 + 1e-6 for p in tl.placements)  # cap respected
    assert tl.out_total_ms <= 4000 + 5  # fits the video
    outs = sorted((p.out_start_ms, p.out_end_ms) for p in tl.placements)
    for (_s0, e0), (s1, _e1) in zip(outs, outs[1:]):
        assert e0 <= s1, f"voices overlap: {e0} > {s1}"
    assert all(p.out_end_ms > p.out_start_ms for p in tl.placements)


def test_voice_timeline_recovers_drift_at_slack():
    # An over-long cue ('a') overruns and pushes the next ('b') past its anchor, but a
    # later cue ('c') with a big slot re-anchors to its picture time — drift recovers
    # instead of accumulating. (max_speed=1.0 so the overrun isn't compressed away.)
    items = [("a", 0, 3000), ("b", 600, 500), ("c", 9000, 500)]
    tl = plan_voice_timeline(items, video_duration_ms=12000, min_gap_ms=120, max_speed=1.0)
    by_id = {p.cue_id: p for p in tl.placements}
    assert by_id["b"].out_start_ms > 600  # 'b' pushed past its anchor (local drift)
    assert by_id["c"].out_start_ms == 9000  # 'c' snaps back to its anchor — drift recovered


def test_voice_timeline_coincident_anchors_do_not_explode():
    # Two cues on nearly the same anchor must not demand absurd compression; the window
    # look-ahead borrows forward to the next real anchor and the cap bounds the speed.
    items = [("a", 1000, 5000), ("b", 1000, 1000), ("c", 6000, 1000)]
    tl = plan_voice_timeline(items, video_duration_ms=8000, min_gap_ms=120, max_speed=1.5, min_slot_ms=400)
    assert all(p.speed <= 1.5 + 1e-6 for p in tl.placements)  # never 5000/0
    outs = sorted((p.out_start_ms, p.out_end_ms) for p in tl.placements)
    for (_s0, e0), (s1, _e1) in zip(outs, outs[1:]):
        assert e0 <= s1


def test_rebalance_splits_multi_line_shared_window():
    # A "xxx说道" attribution + its quoted line both tagged source_lines=[1,2] must be
    # spread across the shared window, not collapsed onto a zero-width slot.
    subs = [
        SrtItem(line=1, start_time=1000, end_time=3000, text="he said"),
        SrtItem(line=2, start_time=3000, end_time=5000, text="cross the bridge"),
    ]
    raw = [
        {"source_lines": [1, 2], "speaker": "旁白", "zh_text": "他说道：", "voice": "沧明子(Eldric Sage)"},
        {"source_lines": [1, 2], "speaker": "小羊", "zh_text": "我要过桥去吃草。", "voice": "萌宝(Bella)"},
    ]
    cues, _ = normalize_story_cues(raw, subs, valid_voices={"沧明子(Eldric Sage)", "萌宝(Bella)"}, default_voice="沧明子(Eldric Sage)")
    assert len(cues) == 2
    assert cues[0].start_ms < cues[0].end_ms <= cues[1].start_ms < cues[1].end_ms  # distinct, non-zero
    assert cues[1].start_ms > cues[0].start_ms  # the two no longer share one instant


def test_role_recommendations_only_use_supported_catalog_voices():
    # The instruct TTS supports only a subset of voices; the catalog is pruned to those.
    # Every recommended voice must exist in the catalog, or the LLM/UI could pick a voice
    # the TTS rejects (which previously crashed the run).
    from videotrans.story_pipeline.pipeline import load_qwen_voice_names
    from videotrans.story_pipeline.voices import ROLE_VOICE_RECOMMENDATIONS

    catalog = load_qwen_voice_names()
    for role, voices in ROLE_VOICE_RECOMMENDATIONS.items():
        for voice in voices:
            assert voice in catalog, f"{role} recommends {voice!r} not in the supported catalog"


def test_normalize_loudness_levels_loud_and_soft_clips_without_clipping():
    from pydub.generators import Sine

    from videotrans.story_pipeline.render import _normalize_loudness

    loud = Sine(440).to_audio_segment(duration=400).apply_gain(-3)
    soft = Sine(440).to_audio_segment(duration=400).apply_gain(-30)
    leveled_loud = _normalize_loudness(loud)
    leveled_soft = _normalize_loudness(soft)
    assert abs(leveled_loud.dBFS - leveled_soft.dBFS) < 2.0  # loudness converged
    assert leveled_loud.max_dBFS <= 0.0 and leveled_soft.max_dBFS <= 0.0  # no clipping


def test_same_speaker_keeps_one_consistent_voice():
    # The LLM may pick a different voice for the troll in different chunks; the result must
    # collapse to ONE voice per character (majority wins) so a character never switches.
    subs = [SrtItem(line=i, start_time=i * 1000, end_time=i * 1000 + 900, text=f"line{i}") for i in range(1, 5)]
    raw = [
        {"source_lines": [1], "speaker": "巨魔", "zh_text": "是谁在我桥上？", "voice": "诡婆婆(Ebona)"},
        {"source_lines": [2], "speaker": "巨魔", "zh_text": "我要吃掉你！", "voice": "卡捷琳娜(Katerina)"},  # drifted
        {"source_lines": [3], "speaker": "巨魔", "zh_text": "哦不，你不行。", "voice": "诡婆婆(Ebona)"},
        {"source_lines": [4], "speaker": "小羊", "zh_text": "别吃我。", "voice": "萌宝(Bella)"},
    ]
    valid = {"诡婆婆(Ebona)", "卡捷琳娜(Katerina)", "萌宝(Bella)", "沧明子(Eldric Sage)"}
    cues, _ = normalize_story_cues(raw, subs, valid_voices=valid, default_voice="沧明子(Eldric Sage)")
    troll_voices = {c.voice for c in cues if c.speaker == "巨魔"}
    assert troll_voices == {"诡婆婆(Ebona)"}  # one voice, majority kept
    assert next(c.voice for c in cues if c.speaker == "小羊") == "萌宝(Bella)"


def test_voice_timeline_keeps_natural_speed_and_anchor_when_audio_fits():
    # Short audio that fits the video: no speed-up, anchored to start times, no overlap.
    items = [("a", 0, 500), ("b", 2000, 500)]
    tl = plan_voice_timeline(items, video_duration_ms=5000, min_gap_ms=140)

    assert tl.speed == 1.0
    by_id = {p.cue_id: p for p in tl.placements}
    assert by_id["b"].out_start_ms == 2000  # kept its natural anchor (gap preserved)
    assert by_id["a"].out_end_ms <= by_id["b"].out_start_ms


def test_audio_slot_plan_never_crosses_cue_boundaries():
    cues = [
        StoryCue(
            id="cue-1",
            source_lines=[1],
            start_ms=0,
            end_ms=1000,
            speaker="Narrator",
            speaker_type="narrator",
            voice="沧明子(Eldric Sage)",
            zh_text="第一句。",
            confidence=1.0,
        ),
        StoryCue(
            id="cue-2",
            source_lines=[2],
            start_ms=1000,
            end_ms=2500,
            speaker="Girl",
            speaker_type="character",
            voice="少女阿月(Stella)",
            zh_text="第二句。",
            confidence=1.0,
        ),
    ]

    plan = plan_audio_slots(cues, {"cue-1": 700, "cue-2": 2200}, video_duration_ms=2600)

    assert plan.final_duration_ms == 2600
    assert plan.slots[0].target_duration_ms == 1000
    assert plan.slots[0].tail_silence_ms == 300
    assert plan.slots[0].speed_target_ms is None
    assert plan.slots[1].target_duration_ms == 1500
    assert plan.slots[1].tail_silence_ms == 0
    assert plan.slots[1].speed_target_ms == 1500
