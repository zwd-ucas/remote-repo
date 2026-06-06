from pathlib import Path
import json
import re
import shutil
import subprocess
from typing import Callable, Sequence

from pydub import AudioSegment

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
    target_subtitle.write_text(cues_to_srt(cues), encoding="utf-8")

    progress("prepare video")
    run_ffmpeg(["-y", "-i", video_path.as_posix(), "-an", "-c:v", "copy", novoice.as_posix()])
    run_ffmpeg(
        ["-y", "-i", video_path.as_posix(), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", source_audio.as_posix()]
    )

    progress("assemble voice track")
    voice = _build_voice_track(cues, audio_files, video_duration)
    voice.export(tts_audio.as_posix(), format="wav")

    instrument = work_dir / "instrument.wav"
    mixed = work_dir / "mixed.wav"
    if instrument.exists():
        bgm = AudioSegment.from_file(instrument.as_posix()).set_channels(2).set_frame_rate(48000)
    else:
        progress("remove original vocal")
        instrument = _create_center_cut_bgm(source_audio, work_dir / "instrument.center-cut.wav", progress)
        if instrument.exists():
            bgm = AudioSegment.from_file(instrument.as_posix()).set_channels(2).set_frame_rate(48000)
        else:
            bgm = AudioSegment.silent(duration=video_duration, frame_rate=48000).set_channels(2)
    bgm = _fit_duration(bgm, video_duration).apply_gain(_volume_to_gain(settings.bgm_volume))
    mixed_audio = bgm.overlay(voice)
    mixed_audio.export(mixed.as_posix(), format="wav")

    progress("mux video")
    final_path = work_dir / "final.zh-dub.mp4"
    if settings.subtitle_mode == "soft":
        run_ffmpeg(
            [
                "-y",
                "-i",
                novoice.as_posix(),
                "-i",
                mixed.as_posix(),
                "-i",
                target_subtitle.as_posix(),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:s",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-c:s",
                "mov_text",
                final_path.as_posix(),
            ]
        )
    else:
        run_ffmpeg(
            [
                "-y",
                "-i",
                novoice.as_posix(),
                "-i",
                mixed.as_posix(),
                "-vf",
                f"subtitles={_ffmpeg_filter_path(target_subtitle)}",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                final_path.as_posix(),
            ]
        )
    return final_path.as_posix()


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


def _build_voice_track(cues: Sequence[StoryCue], audio_files: dict[str, str], video_duration: int) -> AudioSegment:
    track = AudioSegment.silent(duration=max(video_duration, 1), frame_rate=48000).set_channels(2)
    for cue in cues:
        path = audio_files.get(cue.id)
        if not path or not Path(path).exists():
            continue
        slot_ms = max(1, cue.end_ms - cue.start_ms)
        segment = AudioSegment.from_file(path).set_channels(2).set_frame_rate(48000)
        if len(segment) > slot_ms:
            segment = _speed_to_duration(segment, slot_ms)
        segment = _fit_duration(segment, slot_ms)
        track = track.overlay(segment, position=cue.start_ms)
    return track


def _fit_duration(segment: AudioSegment, target_ms: int) -> AudioSegment:
    if len(segment) > target_ms:
        return segment[:target_ms]
    if len(segment) < target_ms:
        return segment + AudioSegment.silent(duration=target_ms - len(segment), frame_rate=segment.frame_rate)
    return segment


def _speed_to_duration(segment: AudioSegment, target_ms: int) -> AudioSegment:
    if target_ms <= 0 or len(segment) <= target_ms:
        return segment
    speed = len(segment) / target_ms
    changed = segment._spawn(segment.raw_data, overrides={"frame_rate": int(segment.frame_rate * speed)})
    return changed.set_frame_rate(segment.frame_rate)[:target_ms]
