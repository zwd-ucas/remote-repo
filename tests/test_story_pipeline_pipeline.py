import json

from videotrans.story_pipeline.pipeline import PipelineDependencies, StoryPipeline
from videotrans.story_pipeline.settings import StoryPipelineSettings
from videotrans.story_pipeline.types import SrtItem


def test_mock_pipeline_run_writes_story_manifest(tmp_path):
    source_subs = [
        SrtItem(line=1, start_time=0, end_time=1000, startraw="00:00:00,000", endraw="00:00:01,000", text="Hello."),
    ]

    def fake_download(url, work_dir, progress, settings):
        return {
            "video_id": "abc123",
            "title": "Story",
            "video_path": str(work_dir / "video.mp4"),
            "subtitle_path": str(work_dir / "en.srt"),
            "source_subtitles": source_subs,
        }

    def fake_translate(subs, settings, progress):
        return ["你好。"]

    def fake_segment(subs, drafts, settings, progress):
        return [
            {
                "source_lines": [1],
                "speaker": "Narrator",
                "speaker_type": "narrator",
                "voice": "沧明子(Eldric Sage)",
                "zh_text": "你好。",
                "confidence": 0.99,
            }
        ]

    def fake_synthesize(cues, settings, work_dir, progress):
        return {cues[0].id: str(work_dir / "cue-1.wav")}

    def fake_compose(download, cues, audio_files, settings, work_dir, progress):
        return str(work_dir / "final.mp4")

    pipeline = StoryPipeline(
        tmp_path,
        PipelineDependencies(
            download=fake_download,
            translate=fake_translate,
            segment=fake_segment,
            synthesize=fake_synthesize,
            compose=fake_compose,
        ),
    )

    manifest = pipeline.run("https://youtube.test/watch?v=abc123", StoryPipelineSettings())

    manifest_path = tmp_path / "manifest.json"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.status == "ready"
    assert saved["status"] == "ready"
    assert saved["video_id"] == "abc123"
    assert saved["source_subtitles"][0]["text"] == "Hello."
    assert saved["audio_files"][saved["cues"][0]["id"]].endswith("cue-1.wav")
    assert saved["cues"][0]["start_ms"] == 0
    assert saved["cues"][0]["end_ms"] == 1000
