"""Full render proof of the wave-1 + budget-fit improvements.

Re-segments the cached billy-goats ASR, fits to budget, synthesizes TTS, measures real
voice-vs-picture drift + compression, and composes the final video. Reuses the cached
video + instrument.wav (no re-download, no re-separation).
"""
import json
import shutil
import statistics
import sys
from pathlib import Path

from pydub import AudioSegment

from videotrans.story_pipeline import pipeline
from videotrans.story_pipeline.audio_slots import plan_voice_timeline
from videotrans.story_pipeline.pipeline import load_qwen_voice_names
from videotrans.story_pipeline.render import _video_duration_ms, compose_final_video, ffmpeg_exe
from videotrans.story_pipeline.settings import default_settings_path, load_settings
from videotrans.story_pipeline.story_segments import normalize_story_cues
from videotrans.story_pipeline.youtube import parse_srt_file

AudioSegment.converter = ffmpeg_exe()
SRC = Path("output/story/e2e-test")
WORK = Path("output/story/e2e-fit")
VIDEO = SRC / "3QzT1sq6kCY.mp4"


WORK.mkdir(parents=True, exist_ok=True)
shutil.copy(SRC / "instrument.wav", WORK / "instrument.wav")  # reuse separated BGM -> skip separation
settings = load_settings(default_settings_path(Path.cwd() / "output" / "story"))
subs = parse_srt_file(SRC / "en.srt")
drafts_raw = json.loads((SRC / "drafts.json").read_text())
drafts = [d if isinstance(d, str) else d.get("text", "") for d in drafts_raw]

print("segment+review+fit ...", file=sys.stderr)
raw = pipeline.default_segment(subs, drafts, settings, lambda *_: None)
raw = pipeline.default_review(raw, settings, lambda *_: None)
cues, _ = normalize_story_cues(raw, subs, valid_voices=load_qwen_voice_names(), default_voice=settings.qwen_default_voice)
cues = pipeline.fit_translations_to_budget(cues, settings, lambda *m: print("  ", *m, file=sys.stderr))

print(f"TTS {len(cues)} cues ...", file=sys.stderr)
audio_files = pipeline.default_synthesize(cues, settings, WORK, lambda *m: print("  tts", *m, file=sys.stderr) if m and "/" in str(m[0]) else None)

# Drift + compression from the real synthesized clip durations
dur = {cid: len(AudioSegment.from_file(p)) for cid, p in audio_files.items()}
items = [(c.id, c.start_ms, dur.get(c.id, 0)) for c in cues if dur.get(c.id)]
vid_ms = _video_duration_ms(VIDEO)
tl = plan_voice_timeline(items, video_duration_ms=vid_ms, max_speed=settings.dub_max_speed)
drifts = [abs(p.out_start_ms - p.natural_start_ms) for p in tl.placements] if hasattr(tl, "placements") else []
# natural_start_ms == anchor only when not pushed; compare out_start to the cue anchor directly:
anchor = {c.id: c.start_ms for c in cues}
drifts = [abs(p.out_start_ms - anchor[p.cue_id]) for p in tl.placements]
sped = [p for p in tl.placements if p.speed > 1.01]
print("\n=== REAL placement (synthesized durations, cap %.2f) ===" % settings.dub_max_speed)
print(f"cues placed={len(tl.placements)}  drift mean={statistics.mean(drifts)/1000:.2f}s "
      f"median={statistics.median(drifts)/1000:.2f}s  p90={sorted(drifts)[int(len(drifts)*0.9)]/1000:.2f}s  max={max(drifts)/1000:.2f}s")
print(f"compressed clips={len(sped)} ({100*len(sped)//len(tl.placements)}%)  "
      f"median speed of those={statistics.median([p.speed for p in sped]):.2f}x" if sped else "compressed clips=0 (all natural speed)")

print("compose ...", file=sys.stderr)
final = compose_final_video({"video_path": str(VIDEO), "source_subtitles": subs}, cues, audio_files, settings, WORK, lambda *m: None)
print("\nFINAL VIDEO:", final)
