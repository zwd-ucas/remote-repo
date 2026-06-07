from pathlib import Path
import json
import re
import shutil
import subprocess
from typing import Callable, Sequence

from pydub import AudioSegment, silence

from .audio_slots import plan_voice_timeline
from .story_segments import StoryCue, cues_to_srt

ProgressFn = Callable[[str], None]


def compose_final_video(
    downloaded: dict,
    cues: Sequence[StoryCue],
    audio_files: dict[str, str],
    settings,
    work_dir: Path,
    progress: ProgressFn,
) -> str:
    AudioSegment.converter = ffmpeg_exe()
    video_path = Path(downloaded["video_path"])
    video_duration = _video_duration_ms(video_path)
    novoice = work_dir / "novoice.mp4"
    source_audio = work_dir / "source_audio.wav"
    tts_audio = work_dir / "zh_voice.wav"
    target_subtitle = work_dir / "zh.srt"

    progress("prepare video")
    run_ffmpeg(["-y", "-i", video_path.as_posix(), "-an", "-c:v", "copy", novoice.as_posix()])
    run_ffmpeg(
        ["-y", "-i", video_path.as_posix(), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", source_audio.as_posix()]
    )

    progress("assemble voice track")
    # "fit" mode caps per-cue compression at dub_max_speed; "extend" never compresses.
    max_speed = getattr(settings, "dub_max_speed", 1.5) if getattr(settings, "dub_fit_mode", "fit") == "fit" else 1.0
    voice, subtitle_timings = _build_voice_track(cues, audio_files, video_duration, progress, max_speed=max_speed)
    voice.export(tts_audio.as_posix(), format="wav")
    # Re-time the burned-in subtitles to match the de-overlapped voice track.
    target_subtitle.write_text(cues_to_srt(_retime_cues_for_subtitles(cues, subtitle_timings)), encoding="utf-8")

    # Natural-speed voice can run longer than the source video; extend the picture
    # (hold the last frame) to fit so nothing is sped up or cut.
    final_duration = max(video_duration, len(voice))

    instrument = work_dir / "instrument.wav"
    mixed = work_dir / "mixed.wav"
    if not instrument.exists():
        # Extract the real BGM by AI vocal separation (the center-cut cancels centered
        # music along with the narration, so the music drops out). Cache as instrument.wav.
        progress("separate background music")
        separated = _separate_instrument(source_audio, work_dir, progress)
        if separated and separated.exists():
            separated.replace(instrument)
        else:
            progress("separation unavailable; center-cut fallback")
            cut = _create_center_cut_bgm(source_audio, work_dir / "instrument.center-cut.wav", progress)
            if cut.exists():
                cut.replace(instrument)
    if instrument.exists():
        bgm = _load_leveled_bgm(instrument, work_dir)
    else:
        bgm = AudioSegment.silent(duration=final_duration, frame_rate=48000).set_channels(2)
    # Hold the music bed at a steady level clearly under the voice (voice ~-20 dBFS).
    bgm = _fit_duration(bgm, final_duration)
    bgm = _normalize_loudness(bgm, target_dbfs=-26.0, peak_ceiling_dbfs=-3.0)
    bgm = bgm.apply_gain(_volume_to_gain(settings.bgm_volume))
    mixed_audio = bgm.overlay(_fit_duration(voice, final_duration))
    mixed_audio.export(mixed.as_posix(), format="wav")

    progress("mux video")
    final_path = work_dir / "final.zh-dub.mp4"
    extra_seconds = max(0, final_duration - video_duration) / 1000.0
    # Freeze the last frame for the extra audio. tpad must run BEFORE subtitles so
    # the tail narration's subtitles burn onto the held frames too.
    pad = f"tpad=stop_mode=clone:stop_duration={extra_seconds:.3f}" if extra_seconds > 0.001 else ""
    if settings.subtitle_mode == "soft":
        video_args = ["-vf", pad, "-c:v", "libx264"] if pad else ["-c:v", "copy"]
        run_ffmpeg(
            [
                "-y", "-i", novoice.as_posix(), "-i", mixed.as_posix(), "-i", target_subtitle.as_posix(),
                "-map", "0:v", "-map", "1:a", "-map", "2:s", *video_args, "-af", _MASTER_LOUDNORM,
                "-c:a", "aac", "-c:s", "mov_text", final_path.as_posix(),
            ]
        )
    else:
        subtitle = f"subtitles={_ffmpeg_filter_path(target_subtitle)}:force_style='{_SUBTITLE_STYLE}'"
        vf = ",".join(filter(None, [pad, subtitle]))
        run_ffmpeg(
            [
                "-y", "-i", novoice.as_posix(), "-i", mixed.as_posix(),
                "-vf", vf, "-af", _MASTER_LOUDNORM,
                "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac",
                final_path.as_posix(),
            ]
        )
    return final_path.as_posix()


# Professional subtitle look (clear black outline + soft shadow) and broadcast-style
# loudness master (-16 LUFS, true-peak limited so the mix never clips).
_SUBTITLE_STYLE = "FontSize=18,Outline=2,Shadow=1,BorderStyle=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,MarginV=28"
_MASTER_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


def _separate_instrument(source_audio: Path, work_dir: Path, progress: ProgressFn) -> Path | None:
    """Extract the instrumental (BGM) stem with AI vocal separation.

    Removes the original narration cleanly regardless of panning — unlike the center-cut,
    which cancels centered music too. Returns the instrumental wav path, or None if the
    separator (audio-separator) is unavailable or fails (caller falls back to center-cut).
    """
    try:
        import logging
        import os

        from audio_separator.separator import Separator
    except Exception:
        return None
    try:
        sep_kwargs = dict(output_dir=work_dir.as_posix(), output_format="WAV", log_level=logging.WARNING)
        home = os.environ.get("STORY_DUBBING_HOME")
        if home:  # packaged app: keep the downloaded separation model in the writable data dir
            sep_kwargs["model_file_dir"] = (Path(home).expanduser() / "models" / "audio-separator").as_posix()
        # onnxruntime-gpu (the Windows CUDA build) makes audio-separator auto-offload to the GPU.
        separator = Separator(**sep_kwargs)
        # Fast MDX-NET instrumental model (onnx) — the default bs_roformer is far too slow
        # on CPU (~20 min for a 6-min clip). MDX is ~2 min and gives a clean instrumental.
        separator.load_model(model_filename="UVR-MDX-NET-Inst_HQ_3.onnx")
        outputs = separator.separate(source_audio.as_posix())
    except Exception as exc:  # pragma: no cover - external model/runtime
        progress(f"BGM separation failed: {exc}")
        return None
    for name in outputs or []:
        if "Instrumental" in str(name):
            candidate = Path(name)
            return candidate if candidate.is_absolute() else work_dir / str(name)
    return None


def _load_leveled_bgm(instrument: Path, work_dir: Path) -> AudioSegment:
    """Even out the separated music bed so it's consistently audible.

    The original music is loud in intro/breaks but very faint behind the narrator, so a
    flat mix makes it feel like the BGM drops out. dynaudnorm raises the quiet sections
    toward the louder ones for a steady bed.
    """
    leveled = work_dir / "instrument.leveled.wav"
    try:
        run_ffmpeg(
            [
                "-y", "-i", instrument.as_posix(),
                "-af", "dynaudnorm=f=200:g=15:p=0.85:m=12",
                "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", leveled.as_posix(),
            ]
        )
        return AudioSegment.from_file(leveled.as_posix()).set_channels(2).set_frame_rate(48000)
    except Exception:
        return AudioSegment.from_file(instrument.as_posix()).set_channels(2).set_frame_rate(48000)


def _create_center_cut_bgm(source_audio: Path, output: Path, progress: ProgressFn) -> Path:
    # This is a conservative fallback when a true UVR/instrument.wav is absent:
    # it cancels center-panned vocals instead of ever reusing the original voice track.
    try:
        run_ffmpeg(
            [
                "-y",
                "-i",
                source_audio.as_posix(),
                "-af",
                "pan=stereo|c0=c0-c1|c1=c1-c0,volume=0.7",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                output.as_posix(),
            ]
        )
    except Exception as exc:
        progress(f"center-cut BGM fallback failed: {exc}")
    return output


def _volume_to_gain(volume: float) -> float:
    volume = max(0.0, min(1.5, float(volume)))
    if volume == 0:
        return -120.0
    import math

    return 20 * math.log10(volume)


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run([ffmpeg_exe(), *args], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg failed").strip())


def _video_duration_ms(path: Path) -> int:
    result = subprocess.run(
        [
            ffmpeg_exe(),
            "-i",
            path.as_posix(),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    output = result.stderr or result.stdout
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        raise RuntimeError((output or "Unable to read video duration with ffmpeg").strip())
    hours, minutes, seconds = match.groups()
    return int((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg is required. Install imageio-ffmpeg or add ffmpeg to PATH.") from exc


def _ffmpeg_filter_path(path: Path) -> str:
    escaped = path.as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"filename='{escaped}'"


def _build_voice_track(
    cues: Sequence[StoryCue],
    audio_files: dict[str, str],
    video_duration: int,
    progress: ProgressFn | None = None,
    *,
    max_speed: float = 1.0,
) -> tuple[AudioSegment, dict[str, tuple[int, int]]]:
    """Assemble a non-overlapping, natural-paced Chinese voice track.

    Each clip is silence-trimmed and placed so voices never overlap. With
    ``max_speed == 1.0`` (default) the voice keeps its natural speed and the track
    may be longer than the video (the caller extends the picture to match). With
    ``max_speed > 1.0`` a single pitch-preserving tempo change scales the track
    toward the video length. Returns the track (at its natural length) plus per-cue
    output timings so the burned subtitles can be re-synced.
    """
    segments: dict[str, AudioSegment] = {}
    items: list[tuple[str, int, int]] = []
    for cue in cues:
        path = audio_files.get(cue.id)
        if not path or not Path(path).exists() or not cue.zh_text.strip():
            continue
        segment = AudioSegment.from_file(path).set_channels(2).set_frame_rate(48000)
        segment = _trim_silence(segment)
        if len(segment) <= 0:
            continue
        segment = _normalize_loudness(segment)
        segments[cue.id] = segment
        items.append((cue.id, int(cue.start_ms), len(segment)))

    timeline = plan_voice_timeline(items, video_duration_ms=max(video_duration, 1), max_speed=max_speed)

    track = AudioSegment.silent(duration=max(timeline.out_total_ms, 1), frame_rate=48000).set_channels(2)
    sped = 0
    for placement in timeline.placements:
        segment = segments.get(placement.cue_id)
        if segment is None:
            continue
        if placement.speed > 1.0:
            # Only the lines that overrun their on-screen window are compressed, each on
            # its own, pitch-preserved — so they stay anchored to the picture without a
            # global uniform speed-up.
            segment = _atempo(segment, placement.speed)
            sped += 1
        track = track.overlay(segment, position=placement.out_start_ms)
    if progress and sped:
        progress(f"compress {sped} over-long lines to hold picture sync")

    timings = {p.cue_id: (p.out_start_ms, p.out_end_ms) for p in timeline.placements}
    return track, timings


def _retime_cues_for_subtitles(
    cues: Sequence[StoryCue], timings: dict[str, tuple[int, int]]
) -> list[StoryCue]:
    """Return spoken cues re-timed to the assembled voice track, in order.

    Cues without audio (e.g. music/empty lines) carry no timing and are dropped so
    the burned-in subtitles only show actual narration, in sync with the voice.
    """
    synced: list[StoryCue] = []
    for cue in cues:
        bounds = timings.get(cue.id)
        if not bounds:
            continue
        retimed = StoryCue(**cue.to_dict())
        retimed.start_ms, retimed.end_ms = bounds
        synced.append(retimed)
    synced.sort(key=lambda cue: cue.start_ms)
    return synced


# Level every voice clip to a consistent loudness so the dub isn't loud-then-soft across
# cues (different voices + emotion instructions synthesize at different levels). RMS-
# normalize each clip toward the target, but never boost peaks past the ceiling (no clip).
_VOICE_TARGET_DBFS = -20.0
_VOICE_PEAK_CEILING_DBFS = -1.0


def _normalize_loudness(
    segment: AudioSegment,
    *,
    target_dbfs: float = _VOICE_TARGET_DBFS,
    peak_ceiling_dbfs: float = _VOICE_PEAK_CEILING_DBFS,
) -> AudioSegment:
    if segment.dBFS == float("-inf"):  # silent clip — nothing to normalize
        return segment
    gain = target_dbfs - segment.dBFS
    headroom = peak_ceiling_dbfs - segment.max_dBFS
    return segment.apply_gain(min(gain, headroom))


def _trim_silence(segment: AudioSegment, *, threshold: float = -40.0, keep_ms: int = 25) -> AudioSegment:
    lead = silence.detect_leading_silence(segment, silence_threshold=threshold)
    trail = silence.detect_leading_silence(segment.reverse(), silence_threshold=threshold)
    lead = max(0, lead - keep_ms)
    trail = max(0, trail - keep_ms)
    if lead + trail >= len(segment):
        return segment
    return segment[lead : len(segment) - trail]


def _fit_duration(segment: AudioSegment, target_ms: int) -> AudioSegment:
    if len(segment) > target_ms:
        return segment[:target_ms]
    if len(segment) < target_ms:
        return segment + AudioSegment.silent(duration=target_ms - len(segment), frame_rate=segment.frame_rate)
    return segment


def _atempo(segment: AudioSegment, factor: float) -> AudioSegment:
    """Pitch-preserving tempo change via ffmpeg's atempo (chained for large factors)."""
    if factor <= 1.0:
        return segment
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.wav"
        dst = Path(tmp) / "out.wav"
        segment.export(src.as_posix(), format="wav")
        run_ffmpeg(
            ["-y", "-i", src.as_posix(), "-filter:a", _atempo_filter(factor),
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", dst.as_posix()]
        )
        return AudioSegment.from_file(dst.as_posix()).set_channels(2).set_frame_rate(48000)


def _atempo_filter(factor: float) -> str:
    # atempo is reliable within [0.5, 2.0]; chain stages for larger factors.
    stages = []
    remaining = float(factor)
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    stages.append(f"atempo={remaining:.6f}")
    return ",".join(stages)
