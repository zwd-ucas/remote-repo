from videotrans.story_pipeline.audio_slots import plan_audio_slots
from videotrans.story_pipeline.story_segments import StoryCue


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
