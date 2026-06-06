import re
import shutil
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlparse

from .settings import StoryPipelineSettings
from .types import SrtItem

ProgressFn = Callable[[str], None]


def _progress(progress: ProgressFn | None, text: str) -> None:
    if progress:
        progress(text)


def download_youtube(
    url: str,
    work_dir: str | Path,
    progress: ProgressFn | None = None,
    settings: StoryPipelineSettings | None = None,
) -> dict:
    try:
        import yt_dlp
    except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
        raise RuntimeError("yt-dlp is required for YouTube downloads. Install it with `uv add yt-dlp`.") from exc

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    if settings and settings.local_video_path.strip():
        return _import_local_source(settings, work, progress)
    _progress(progress, "Downloading video")

    opts = _yt_dlp_options(work, settings)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise RuntimeError(_download_error_message(exc, settings)) from exc
    else:
        video_id = str(info.get("id") or "video")
        title = str(info.get("title") or video_id)
        video_path = _find_downloaded_video(work, video_id)
        subtitle_path = _download_best_english_subtitle(info, work, video_id, progress)
        if not subtitle_path:
            subtitle_path, source_subtitles = _asr_fallback(video_path, work, progress)
        else:
            if subtitle_path.suffix.lower() == ".vtt":
                subtitle_path = _vtt_to_srt(subtitle_path)
            source_subtitles = parse_srt_file(subtitle_path)
        return {
            "video_id": video_id,
            "title": title,
            "video_path": video_path.as_posix(),
            "subtitle_path": subtitle_path.as_posix(),
            "source_subtitles": source_subtitles,
        }


def _download_strategies() -> list[str]:
    return ["default"]


def _import_local_source(settings: StoryPipelineSettings, work: Path, progress: ProgressFn | None) -> dict:
    video_path = Path(settings.local_video_path.strip()).expanduser()
    if not video_path.exists():
        raise RuntimeError(f"Local video file was not found: {video_path}")
    _progress(progress, "Importing local video")
    target_video = work / video_path.name
    if video_path.resolve() != target_video.resolve():
        shutil.copy2(video_path, target_video)

    subtitle_path: Path | None = None
    if settings.local_subtitle_path.strip():
        source_subtitle = Path(settings.local_subtitle_path.strip()).expanduser()
        if not source_subtitle.exists():
            raise RuntimeError(f"Local subtitle file was not found: {source_subtitle}")
        subtitle_path = work / source_subtitle.name
        if source_subtitle.resolve() != subtitle_path.resolve():
            shutil.copy2(source_subtitle, subtitle_path)

    if subtitle_path:
        if subtitle_path.suffix.lower() == ".vtt":
            subtitle_path = _vtt_to_srt(subtitle_path)
        source_subtitles = parse_srt_file(subtitle_path)
    else:
        subtitle_path, source_subtitles = _asr_fallback(target_video, work, progress)

    return {
        "video_id": "local-" + _safe_stem(video_path.stem),
        "title": video_path.stem,
        "video_path": target_video.as_posix(),
        "subtitle_path": subtitle_path.as_posix(),
        "source_subtitles": source_subtitles,
    }


def _safe_stem(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return safe or "video"


def _yt_dlp_options(work: Path, settings: StoryPipelineSettings | None = None) -> dict:
    settings = settings or StoryPipelineSettings()
    opts = {
        "outtmpl": str(work / "%(id)s.%(ext)s"),
        "format": "bv*[ext=mp4][vcodec!=none]+ba[ext=m4a]/bv*[vcodec!=none]+ba[acodec!=none]/best[ext=mp4][vcodec!=none][acodec!=none]/best",
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_location(),
        "writesubtitles": False,
        "writeautomaticsub": False,
        "quiet": True,
        "no_warnings": True,
    }
    if settings.youtube_proxy.strip():
        opts["proxy"] = settings.youtube_proxy.strip()
    if settings.youtube_cookies_file.strip():
        opts["cookiefile"] = settings.youtube_cookies_file.strip()
    browser = _parse_cookies_from_browser(settings.youtube_cookies_from_browser)
    if browser:
        opts["cookiesfrombrowser"] = browser
    extractor_args = _youtube_extractor_args(settings)
    if extractor_args:
        opts["extractor_args"] = {"youtube": extractor_args}
    return opts


def _parse_cookies_from_browser(value: str) -> tuple[str, str | None, None, None] | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if ":" in cleaned:
        browser, profile = cleaned.split(":", 1)
        return (browser.strip().lower(), profile.strip() or None, None, None)
    return (cleaned.lower(), None, None, None)


def _youtube_extractor_args(settings: StoryPipelineSettings) -> dict[str, list[str]]:
    args: dict[str, list[str]] = {}
    client = settings.youtube_player_client.strip()
    if client:
        args["player_client"] = [client]
    token = settings.youtube_po_token.strip()
    if token:
        token_client = client or "mweb"
        args["po_token"] = [_normalize_po_token(token, token_client)]
    return args


def _normalize_po_token(token: str, client: str) -> str:
    if "." in token and "+" in token:
        return token
    return f"{client}.gvs+{token}"


def _download_error_message(exc: Exception, settings: StoryPipelineSettings | None) -> str:
    text = str(exc)
    hints = [
        text,
        "YouTube did not provide a downloadable video stream to the current yt-dlp client.",
        "If this video plays in a browser, set YouTube cookies or a PO Token in Settings > YouTube 下载.",
        "For browser cookies, close Chrome/Edge before running, or export a cookies.txt file and set its path.",
        "The workflow does not fall back to android client because that often limits downloads to 360p.",
    ]
    if settings and not any(
        [
            settings.youtube_cookies_from_browser.strip(),
            settings.youtube_cookies_file.strip(),
            settings.youtube_po_token.strip(),
        ]
    ):
        hints.append("Current run has no cookies file, cookies-from-browser, or PO Token configured.")
    return "\n".join(hints)


def _find_downloaded_video(work: Path, video_id: str) -> Path:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        candidate = work / f"{video_id}{ext}"
        if candidate.exists():
            return candidate
    matches = [p for p in work.glob(f"{video_id}.*") if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if matches:
        return matches[0]
    raise RuntimeError("Downloaded video file was not found.")


def _ffmpeg_location() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _find_subtitle(work: Path, video_id: str) -> Path | None:
    candidates: Sequence[Path] = list(work.glob(f"{video_id}*.en*.srt")) + list(work.glob(f"{video_id}*.en*.vtt"))
    return candidates[0] if candidates else None


def _download_best_english_subtitle(info: dict, work: Path, video_id: str, progress: ProgressFn | None) -> Path | None:
    try:
        import requests
    except Exception:
        return None

    choices = _english_subtitle_choices(info)
    if not choices:
        _progress(progress, "No English subtitle metadata found")
        return None

    errors = []
    for lang, entry in choices:
        url = entry.get("url")
        if not url:
            continue
        ext = str(entry.get("ext") or _subtitle_ext_from_url(url) or "vtt").lower()
        if ext not in {"srt", "vtt"}:
            continue
        path = work / f"{video_id}.{lang}.{ext}"
        _progress(progress, f"Downloading English subtitle: {lang}")
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            path.write_bytes(response.content)
            return path
        except Exception as exc:
            errors.append(f"{lang}: {exc}")
    if errors:
        _progress(progress, "English subtitle download failed: " + " | ".join(errors[:3]))
    return None


def _english_subtitle_choices(info: dict) -> list[tuple[str, dict]]:
    tracks: dict[str, list[dict]] = {}
    for section in ("subtitles", "automatic_captions"):
        for lang, entries in (info.get(section) or {}).items():
            if _is_direct_english_lang(lang):
                tracks.setdefault(lang, []).extend([entry for entry in entries if isinstance(entry, dict)])

    ordered_langs = []
    for lang in ("en", "en-US", "en-GB", "en-orig"):
        if lang in tracks:
            ordered_langs.append(lang)
    ordered_langs.extend(sorted(lang for lang in tracks if lang not in ordered_langs))

    choices: list[tuple[str, dict]] = []
    for lang in ordered_langs:
        entries = sorted(tracks[lang], key=_subtitle_entry_rank)
        choices.extend((lang, entry) for entry in entries)
    return choices


def _is_direct_english_lang(lang: str) -> bool:
    normalized = str(lang).strip()
    if normalized in {"en", "en-US", "en-GB", "en-orig"}:
        return True
    if not normalized.startswith("en-"):
        return False
    # yt-dlp exposes translated captions as keys like en-ar; avoid those because
    # they are not source English subtitles and can trigger avoidable 429s.
    region = normalized.removeprefix("en-")
    return region.isupper() and len(region) in {2, 3}


def _subtitle_entry_rank(entry: dict) -> tuple[int, int]:
    ext = str(entry.get("ext") or "").lower()
    protocol = str(entry.get("protocol") or "")
    ext_rank = {"srt": 0, "vtt": 1}.get(ext, 9)
    protocol_rank = 0 if protocol in {"https", "http"} else 1
    return ext_rank, protocol_rank


def _subtitle_ext_from_url(url: str) -> str | None:
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    return suffix or None


def _vtt_to_srt(vtt_path: Path) -> Path:
    text = vtt_path.read_text(encoding="utf-8-sig", errors="ignore")
    lines = [line for line in text.splitlines() if not line.startswith(("WEBVTT", "Kind:", "Language:"))]
    blocks = []
    current = []
    for line in lines:
        if "-->" in line:
            line = line.replace(".", ",")
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)

    out = vtt_path.with_suffix(".srt")
    rows = []
    idx = 1
    for block in blocks:
        if not block or "-->" not in block[0]:
            continue
        rows.append(f"{idx}\n" + "\n".join(block))
        idx += 1
    out.write_text("\n\n".join(rows).strip() + "\n", encoding="utf-8")
    return out


def parse_srt_file(path: Path) -> list[SrtItem]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    blocks = [block.strip() for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    items: list[SrtItem] = []
    line_no = 1
    seen: set[tuple[str, str, str]] = set()
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if "-->" not in lines[0] and len(lines) > 1:
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip().split(" ")[0] for part in lines[0].split("-->", 1)]
        text = clean_subtitle_text("\n".join(lines[1:]))
        if not text:
            continue
        key = (start_raw, end_raw, text)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            SrtItem(
                line=line_no,
                start_time=srt_time_to_ms(start_raw),
                end_time=srt_time_to_ms(end_raw),
                startraw=start_raw,
                endraw=end_raw,
                text=text,
            )
        )
        line_no += 1
    if not items:
        raise RuntimeError(f"No subtitle cues parsed from {path}")
    return items


def clean_subtitle_text(text: str) -> str:
    cleaned = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s*\n\s*", "\n", cleaned)
    return cleaned.strip()


def srt_time_to_ms(value: str) -> int:
    hours, minutes, rest = value.replace(".", ",").split(":")
    seconds, millis = rest.split(",", 1)
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(millis[:3].ljust(3, "0"))
    )


def _asr_fallback(video_path: Path, work: Path, progress: ProgressFn | None) -> tuple[Path, list[SrtItem]]:
    raise RuntimeError(
        "No English subtitle was found. The slim web-only project no longer includes the legacy ASR runtime; "
        "provide a local English .srt/.vtt file in Settings, or use a YouTube video with downloadable English captions."
    )
