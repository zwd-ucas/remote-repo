import json
import threading
import time

from videotrans.story_pipeline.pipeline import StoryManifest
from videotrans.story_pipeline.server import TaskState, _cue_from_dict, _make_checkpoint, app


def test_story_pipeline_web_app_exposes_core_routes():
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/api/settings" in paths
    assert "/api/settings/test-llm" in paths
    assert "/api/settings/test-qwen" in paths
    assert "/api/run" in paths
    assert "/api/tasks" in paths
    assert "/api/tasks/{task_id}" in paths
    assert "/api/tasks/{task_id}/pause" in paths
    assert "/api/tasks/{task_id}/resume" in paths
    assert "/api/tasks/{task_id}/confirm" in paths
    assert "/api/tasks/{task_id}/cancel" in paths
    assert "/api/tasks/{task_id}/cues" in paths
    assert "/api/tasks/{task_id}/tts" in paths
    assert "/api/tasks/{task_id}/tts/{cue_id}" in paths


def test_task_state_supports_pause_and_resume(tmp_path):
    state = TaskState("task-1", tmp_path)
    state.status = "running"

    state.request_pause()

    assert state.pause_requested is True
    assert state.status == "pausing"

    state.resume()

    assert state.pause_requested is False
    assert state.status == "running"


def test_cue_from_dict_ignores_unknown_fields():
    # The frontend round-trips cues with UI-only fields (needs_review); building a StoryCue
    # must drop anything that is not a real dataclass field instead of crashing.
    cue = _cue_from_dict(
        {
            "id": "c1",
            "source_lines": [1],
            "start_ms": 0,
            "end_ms": 1000,
            "speaker": "旁白",
            "speaker_type": "narrator",
            "voice": "苏瑶(Serena)",
            "zh_text": "你好。",
            "needs_review": True,
            "_junk": 1,
        }
    )
    assert cue.id == "c1" and cue.zh_text == "你好。" and cue.voice == "苏瑶(Serena)"


def test_task_state_cancel_marks_cancelled():
    state = TaskState("task-c", "/tmp", mode="manual")
    state.cancel()
    assert state.status == "cancelled" and state.cancelled is True


def test_progress_pct_is_monotonic_and_uses_tts_subprogress():
    from videotrans.story_pipeline.server import _progress_pct as pct

    assert pct("queued", "queued") == 0
    assert pct("ready", "ready") == 100
    # TTS, the long stage, interpolates per-cue between 55 and 85
    assert pct("tts:0/40", "running") == 55
    assert pct("tts:40/40", "running") == 85
    assert 55 < pct("tts:20/40", "running") < 85
    # never goes backwards across a typical run
    seq = ["download", "transcribe", "translate:1-96", "segment:1-96", "review", "tts:1/40", "tts:40/40", "mux video", "saved to /x"]
    vals = [pct(s, "running") for s in seq]
    assert all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))


def test_manual_checkpoint_blocks_then_returns_edited_cues(tmp_path):
    # The manual checkpoint must expose a review manifest, block until confirm, and return the
    # user's edited cues from story_cues.json (not the originals).
    state = TaskState("task-m", tmp_path, mode="manual")
    original = [
        _cue_from_dict(
            {"id": "c1", "source_lines": [1], "start_ms": 0, "end_ms": 1000, "speaker": "旁白", "speaker_type": "narrator", "voice": "苏瑶(Serena)", "zh_text": "原文"}
        )
    ]
    (tmp_path / "story_cues.json").write_text(
        json.dumps([dict(original[0].to_dict(), zh_text="编辑后", voice="田叔(Vincent)")], ensure_ascii=False),
        encoding="utf-8",
    )
    review = StoryManifest("awaiting_review", "", "", str(tmp_path), "", "", "", [], [c.to_dict() for c in original], {})
    checkpoint = _make_checkpoint(state)
    out: dict = {}
    worker = threading.Thread(target=lambda: out.__setitem__("cues", checkpoint(original, review)))
    worker.start()
    time.sleep(0.3)
    assert state.status == "awaiting_review" and state.awaiting == "review" and state.manifest is not None
    state.confirm()
    worker.join(timeout=3)
    assert state.status == "running"
    assert out["cues"][0].zh_text == "编辑后" and out["cues"][0].voice == "田叔(Vincent)"
