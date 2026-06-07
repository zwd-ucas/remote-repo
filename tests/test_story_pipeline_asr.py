from videotrans.story_pipeline import asr
from videotrans.story_pipeline.settings import StoryPipelineSettings


def test_asr_uses_softened_vad_so_character_voices_survive():
    # Regression guard: the default (tight) VAD clipped the troll's growly lines and the
    # content was lost. Keep the VAD soft (low threshold + extra padding) so quiet/
    # distorted character speech is not dropped or garbled.
    assert asr.VAD_PARAMETERS["threshold"] <= 0.4
    assert asr.VAD_PARAMETERS["speech_pad_ms"] >= 400


def test_default_asr_model_is_robust_for_character_voices():
    # 'tiny'/'base' mis-hear distorted character voices (the troll), so the default must be
    # at least 'small'. (The troll bug was reproduced on 'small' + tight VAD.)
    assert StoryPipelineSettings().asr_model not in {"tiny", "base"}


def test_refine_segments_splits_coarse_segments_at_sentences_and_pauses():
    # A coarse multi-sentence segment must split into finer, word-accurately-timed units
    # so each Chinese line anchors to where its English is actually spoken.
    words = [
        {"start": 0.0, "end": 0.4, "word": " This"},
        {"start": 0.4, "end": 1.2, "word": " story."},
        {"start": 3.0, "end": 3.3, "word": " Once"},
        {"start": 3.3, "end": 4.6, "word": " time."},
    ]
    out = asr._refine_segments([{"start": 0.0, "end": 4.6, "text": "This story. Once time.", "words": words}])
    assert [u["text"] for u in out] == ["This story.", "Once time."]
    assert out[1]["start"] == 3.0  # second unit times from its own words, not the segment start


def test_transcribe_audio_maps_segments_to_ms_srt_items(monkeypatch, tmp_path):
    monkeypatch.setattr(asr, "_extract_audio", lambda video, wav: None)
    monkeypatch.setattr(
        asr,
        "_run_asr",
        lambda wav, model: [
            {"start": 1.5, "end": 3.25, "text": " Who's that on my bridge? "},
            {"start": 3.25, "end": 4.0, "text": ""},  # empty -> dropped
            {"start": 4.0, "end": 5.5, "text": "You may cross."},
        ],
    )
    items = asr.transcribe_audio(tmp_path / "v.mp4", StoryPipelineSettings(asr_model="medium"))

    assert [it["text"] for it in items] == ["Who's that on my bridge?", "You may cross."]
    assert items[0]["start_time"] == 1500 and items[0]["end_time"] == 3250  # seconds -> ms
    assert items[0]["line"] == 1 and items[1]["line"] == 2  # sequential, empty dropped
    assert items[0]["startraw"] == "00:00:01,500"
