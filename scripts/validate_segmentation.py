"""Cheap validation of the wave-1 improvements (time-budget translation + gender + 断句).

Reuses the cached ASR (en.srt) + draft translations, re-runs segment+review+normalize with
the current prompt, and compares time-budget adherence / over-merge / overlap / voice-gender
against the previous run's cues (story_cues.json). No TTS/compose, no re-ASR.
"""
import json
import re
import statistics
import sys
from pathlib import Path

from videotrans.story_pipeline import pipeline
from videotrans.story_pipeline.pipeline import load_qwen_voice_names
from videotrans.story_pipeline.settings import default_settings_path, load_settings
from videotrans.story_pipeline.story_segments import normalize_story_cues
from videotrans.story_pipeline.voices import voice_gender
from videotrans.story_pipeline.youtube import parse_srt_file

D = Path("output/story/e2e-test")
MS_PER_CHAR = 240  # the budget the prompt targets


def measure(cues, label):
    over, ratios, maxlines = 0, [], 0
    line_tuples = {}  # line -> set of distinct source_lines tuples it appears in
    speaker_voice = {}
    for c in cues:
        get = (lambda k: c[k]) if isinstance(c, dict) else (lambda k: getattr(c, k))
        sl, zh, a, b = get("source_lines"), get("zh_text"), get("start_ms"), get("end_ms")
        chars = len(re.sub(r"\s", "", zh or ""))
        ratio = chars * MS_PER_CHAR / max(1, b - a)
        ratios.append(ratio)
        over += ratio > 1.0
        maxlines = max(maxlines, len(sl))
        for ln in sl:
            line_tuples.setdefault(ln, set()).add(tuple(sl))
        speaker_voice.setdefault(get("speaker"), get("voice"))
    # real overlap = a line claimed by two DIFFERENT cue groups (split-groups share a tuple)
    real_overlap = sum(1 for tups in line_tuples.values() if len(tups) > 1)
    print(f"\n=== {label} ===")
    print(f"cues={len(cues)}  over-budget(>1.0x)={over} ({100*over//max(1,len(cues))}%)  "
          f"median={statistics.median(ratios):.2f}x  p90={sorted(ratios)[int(len(ratios)*0.9)]:.2f}x  max={max(ratios):.2f}x")
    print(f"max source_lines/cue={maxlines}  REAL cross-group line overlaps={real_overlap}")
    print("speakers→voice→gender:", {s: f"{v}/{voice_gender(v)}" for s, v in speaker_voice.items()})


# OLD cues (previous run, pre-wave-1)
old = json.loads((D / "story_cues.json").read_text())
old = old["cues"] if isinstance(old, dict) else old
measure(old, "OLD cues (previous run)")

# NEW cues: re-run segment+review+normalize with the current prompt
subs = parse_srt_file(D / "en.srt")
settings = load_settings(default_settings_path(Path.cwd() / "output" / "story"))
drafts_raw = json.loads((D / "drafts.json").read_text())
drafts = [d if isinstance(d, str) else d.get("text", "") for d in drafts_raw] if isinstance(drafts_raw, list) else [""] * len(subs)
print(f"\nsubs={len(subs)} drafts={len(drafts)} — calling segment+review (LLM)...", file=sys.stderr)
raw = pipeline.default_segment(subs, drafts, settings, lambda *_: None)
raw = pipeline.default_review(raw, settings, lambda *_: None)
cues, issues = normalize_story_cues(raw, subs, valid_voices=load_qwen_voice_names(), default_voice=settings.qwen_default_voice)
measure(cues, "NEW cues (wave-1 prompt, before budget-fit)")
cues = pipeline.fit_translations_to_budget(cues, settings, lambda *_: None)
measure(cues, "NEW cues (after deterministic budget-fit)")
print(f"\nnormalize issues: {len(issues)}")
