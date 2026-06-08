"""ASR forced-alignment of the original audio for picture-synced anchors.

Transcribing the video's own English speech gives clean, non-overlapping segments
timed to when each line is actually spoken — far more precise anchors than rolling
YouTube auto-captions. Prefers WhisperX (adds wav2vec2 word-level alignment) when it
is installed; otherwise uses faster-whisper, WhisperX's own transcription backend,
which already yields precise segment + word timestamps and is far lighter (no torch).
"""
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .settings import StoryPipelineSettings
from .story_segments import ms_to_srt_time
from .types import SrtItem

ProgressFn = Callable[[str], None]


def _ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError("ffmpeg is required for ASR audio extraction.") from exc


def _extract_audio(video_path: Path, out_wav: Path) -> None:
    result = subprocess.run(
        [
            _ffmpeg_exe(), "-y", "-i", video_path.as_posix(),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_wav.as_posix(),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffmpeg audio extraction failed").strip())


def transcribe_audio(
    video_path: str | Path, settings: StoryPipelineSettings, progress: ProgressFn | None = None
) -> list[SrtItem]:
    """Transcribe the video's original audio into precisely-timed English segments."""
    video_path = Path(video_path)
    wav = video_path.parent / "asr_audio.16k.wav"
    if progress:
        progress("transcribe: extract audio")
    _extract_audio(video_path, wav)

    model_name = (settings.asr_model or "small").strip()
    device, compute_type = _resolve_device(settings, progress)
    if progress:
        progress(f"transcribe: ASR ({model_name}, {device})")
    segments = _refine_segments(_run_asr(wav, model_name, device, compute_type))

    items: list[SrtItem] = []
    line = 1
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start_ms = max(0, int(round(float(seg["start"]) * 1000)))
        end_ms = max(start_ms + 1, int(round(float(seg["end"]) * 1000)))
        items.append(
            SrtItem(
                line=line,
                start_time=start_ms,
                end_time=end_ms,
                startraw=ms_to_srt_time(start_ms),
                endraw=ms_to_srt_time(end_ms),
                text=text,
            )
        )
        line += 1
    return items


# Soften voice-activity detection. The default VAD clips quiet/growly/character voices
# (e.g. a fairy-tale troll): it garbled the troll's "you may cross the bridge" into "they
# crossed the bridge" and the line was lost. A lower threshold keeps low-confidence speech
# and extra padding stops onsets/offsets being chopped. Do not tighten without re-checking
# that distorted character lines survive. See tests/test_story_pipeline_asr.py.
VAD_PARAMETERS = {"threshold": 0.35, "speech_pad_ms": 600}


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _resolve_device(settings: StoryPipelineSettings, progress: ProgressFn | None = None) -> tuple[str, str]:
    """Pick (device, compute_type) from settings.compute_device, falling back to CPU."""
    pref = (getattr(settings, "compute_device", "auto") or "auto").strip().lower()
    if pref == "cpu":
        return "cpu", "int8"
    if (pref in {"cuda", "auto"}) and _cuda_available():
        return "cuda", "float16"
    if pref == "cuda" and progress:
        progress("CUDA requested but no GPU/CUDA build found; using CPU")
    return "cpu", "int8"


def _run_asr(wav: Path, model_name: str, device: str = "cpu", compute_type: str = "int8") -> list[dict]:
    try:
        return _run_whisperx(wav, model_name, device, compute_type)
    except ImportError:
        return _run_faster_whisper(wav, model_name, device, compute_type)


def _run_faster_whisper(wav: Path, model_name: str, device: str = "cpu", compute_type: str = "int8") -> list[dict]:
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception:
        if device == "cpu":
            raise
        # GPU/CUDA init failed (missing driver/cuDNN) — degrade gracefully to CPU.
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    # Word timestamps let us split coarse segments into precisely-timed sub-units, so the
    # Chinese anchors to where each phrase is actually spoken (finer picture sync).
    seg_iter, _info = model.transcribe(
        wav.as_posix(), language="en", word_timestamps=True,
        vad_filter=True, vad_parameters=VAD_PARAMETERS,
    )
    out: list[dict] = []
    for s in seg_iter:
        out.append(
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])],
            }
        )
    return out


def _refine_segments(segments: list[dict], *, max_gap_s: float = 0.55, max_dur_s: float = 7.0) -> list[dict]:
    """Split coarse ASR segments into finer, word-accurate units.

    faster-whisper sometimes merges several sentences into one long segment (seen up to
    23s). Anchoring whole paragraphs is the main source of picture/voice drift. Using the
    word timestamps, break each segment at sentence boundaries, long pauses, or a max
    duration, timing every unit from its own words — so each Chinese line lands on the
    moment its English is actually spoken.
    """
    refined: list[dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            refined.append(seg)
            continue
        current: list[dict] = []
        for i, word in enumerate(words):
            current.append(word)
            text = str(word.get("word") or "").strip()
            ends_sentence = text.endswith((".", "!", "?", "。", "！", "？", "…"))
            # Also break at a clause boundary (comma/semicolon/dash), but only once the unit
            # is already substantial — so run-on clauses split cleanly without chopping short
            # phrases into fragments.
            ends_clause = text.endswith((",", "，", ";", "；", ":", "：", "—"))
            unit_dur = word["end"] - current[0]["start"]
            unit_substantial = len(current) >= 6 or unit_dur >= 2.5
            gap_to_next = (words[i + 1]["start"] - word["end"]) if i + 1 < len(words) else 0.0
            too_long = unit_dur >= max_dur_s
            if ends_sentence or (ends_clause and unit_substantial) or gap_to_next >= max_gap_s or too_long:
                refined.append(_unit_from_words(current))
                current = []
        if current:
            refined.append(_unit_from_words(current))
    return refined


def _unit_from_words(words: list[dict]) -> dict:
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": "".join(str(w.get("word") or "") for w in words).strip(),
        "words": words,
    }


def _run_whisperx(wav: Path, model_name: str, device: str = "cpu", compute_type: str = "int8") -> list[dict]:
    import whisperx  # ImportError -> caller falls back to faster-whisper

    model = whisperx.load_model(model_name, device, compute_type=compute_type)
    audio = whisperx.load_audio(wav.as_posix())
    result = model.transcribe(audio, language="en")
    align_model, meta = whisperx.load_align_model(language_code="en", device=device)
    aligned = whisperx.align(result["segments"], align_model, meta, audio, device, return_char_alignments=False)
    return [
        {"start": s["start"], "end": s["end"], "text": s["text"], "words": s.get("words", [])}
        for s in aligned["segments"]
    ]
