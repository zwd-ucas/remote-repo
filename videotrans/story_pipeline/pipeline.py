import html
import json
import re
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

from .settings import StoryPipelineSettings
from .story_segments import StoryCue, cues_to_srt, normalize_story_cues, source_to_srt
from .types import SrtItem
from .voices import qwen_voice_catalog

ProgressFn = Callable[[str], None]


@dataclass
class StoryManifest:
    status: str
    video_id: str
    title: str
    work_dir: str
    final_video: str
    source_subtitle: str
    target_subtitle: str
    source_subtitles: list[dict]
    cues: list[dict]
    audio_files: dict[str, str] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineDependencies:
    download: Callable[[str, Path, ProgressFn, StoryPipelineSettings], dict] | None = None
    translate: Callable[[Sequence[SrtItem], StoryPipelineSettings, ProgressFn], list[str]] | None = None
    segment: Callable[[Sequence[SrtItem], Sequence[str], StoryPipelineSettings, ProgressFn], list[dict]] | None = None
    synthesize: Callable[[Sequence[StoryCue], StoryPipelineSettings, Path, ProgressFn], dict[str, str]] | None = None
    compose: Callable[[dict, Sequence[StoryCue], dict[str, str], StoryPipelineSettings, Path, ProgressFn], str] | None = None


class StoryPipeline:
    def __init__(
        self,
        work_dir: str | Path,
        dependencies: PipelineDependencies | None = None,
        *,
        progress: ProgressFn | None = None,
    ):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.dependencies = dependencies or PipelineDependencies()
        self.progress = progress or (lambda _text: None)

    def run(self, youtube_url: str, settings: StoryPipelineSettings) -> StoryManifest:
        download = self.dependencies.download or default_download
        translate = self.dependencies.translate or default_translate
        segment = self.dependencies.segment or default_segment
        synthesize = self.dependencies.synthesize or default_synthesize
        compose = self.dependencies.compose or default_compose

        self.progress("download")
        downloaded = download(youtube_url, self.work_dir, self.progress, settings)
        source_subtitles = list(downloaded["source_subtitles"])
        source_path = Path(downloaded.get("subtitle_path") or self.work_dir / "en.srt")
        self._write_source_srt(source_subtitles, source_path)

        self.progress("translate")
        draft_translations = translate(source_subtitles, settings, self.progress)

        self.progress("segment")
        raw_cues = segment(source_subtitles, draft_translations, settings, self.progress)
        valid_voices = load_qwen_voice_names()
        cues, issues = normalize_story_cues(
            raw_cues,
            source_subtitles,
            valid_voices=valid_voices,
            default_voice=settings.qwen_default_voice,
        )
        target_path = self.work_dir / "zh.srt"
        target_path.write_text(cues_to_srt(cues), encoding="utf-8")
        (self.work_dir / "story_cues.json").write_text(
            json.dumps([cue.to_dict() for cue in cues], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.progress("tts")
        audio_files = synthesize(cues, settings, self.work_dir, self.progress)

        self.progress("compose")
        final_video = compose(downloaded, cues, audio_files, settings, self.work_dir, self.progress)
        manifest = StoryManifest(
            status="ready",
            video_id=str(downloaded.get("video_id") or ""),
            title=str(downloaded.get("title") or ""),
            work_dir=self.work_dir.as_posix(),
            final_video=final_video,
            source_subtitle=source_path.as_posix(),
            target_subtitle=target_path.as_posix(),
            source_subtitles=self._source_subtitle_rows(source_subtitles),
            cues=[cue.to_dict() for cue in cues],
            audio_files=audio_files,
            issues=[issue.to_dict() for issue in issues],
        )
        (self.work_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.progress("ready")
        return manifest

    def _write_source_srt(self, source_subtitles: Sequence[SrtItem], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source_to_srt(source_subtitles), encoding="utf-8")

    def _source_subtitle_rows(self, source_subtitles: Sequence[SrtItem]) -> list[dict]:
        rows = []
        for item in source_subtitles:
            rows.append(
                {
                    "line": int(item["line"]),
                    "start_ms": int(item["start_time"]),
                    "end_ms": int(item["end_time"]),
                    "text": str(item["text"]),
                }
            )
        return rows


def load_qwen_voice_names() -> set[str]:
    return set(load_qwen_voice_map().keys())


def load_qwen_voice_catalog() -> list[dict]:
    return qwen_voice_catalog(load_qwen_voice_map())


def load_qwen_voice_map() -> dict[str, str]:
    voice_path = Path(__file__).resolve().parents[1] / "voicejson" / "qwen3tts.json"
    try:
        return json.loads(voice_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {
            "芊悦(Cherry)": "Cherry",
            "苏瑶(Serena)": "Serena",
            "凯(Kai)": "Kai",
            "萌宝(Bella)": "Bella",
            "沧明子(Eldric Sage)": "Eldric Sage",
            "诡婆婆(Ebona)": "Ebona",
        }


def default_download(url: str, work_dir: Path, progress: ProgressFn, settings: StoryPipelineSettings) -> dict:
    from .youtube import download_youtube

    return download_youtube(url, work_dir, progress, settings)


def default_translate(source_subtitles: Sequence[SrtItem], settings: StoryPipelineSettings, progress: ProgressFn) -> list[str]:
    engine = settings.translation_engine.lower().strip()
    if engine in {"google", "qwenmt"}:
        result = _channel_translate(source_subtitles, settings, engine, progress)
        return [str(item["text"]) for item in result]
    llm_settings = replace(settings, llm_provider="openai-compatible" if engine == "openai-compatible" else engine)
    return llm_translate(source_subtitles, llm_settings, progress)


def _channel_translate(source_subtitles: Sequence[SrtItem], settings: StoryPipelineSettings, engine: str, progress: ProgressFn):
    if engine == "qwenmt":
        return [{"text": text} for text in llm_translate(source_subtitles, settings, progress)]
    translated = google_translate_subtitles(source_subtitles, settings, progress)
    return [{"text": text} for text in translated]


def google_translate_subtitles(source_subtitles: Sequence[SrtItem], settings: StoryPipelineSettings, progress: ProgressFn) -> list[str]:
    results: list[str] = []
    batch_size = 40
    for start in range(0, len(source_subtitles), batch_size):
        progress(f"translate:{start + 1}-{min(start + batch_size, len(source_subtitles))}")
        batch = source_subtitles[start : start + batch_size]
        text = "\n".join(str(item["text"]).replace("\n", " ") for item in batch)
        translated_text = google_translate_text(text, settings)
        lines = [line.strip() for line in translated_text.splitlines() if line.strip()]
        if len(lines) == len(batch):
            results.extend(lines)
        else:
            results.extend(google_translate_text(str(item["text"]), settings) for item in batch)
    return results


def google_translate_text(text: str, settings: StoryPipelineSettings) -> str:
    import requests

    target_code = settings.target_language_code or "zh-cn"
    response = requests.get(
        "https://translate.google.com/m",
        params={
            "sl": settings.source_language_code or "auto",
            "tl": target_code,
            "hl": target_code,
            "q": text,
        },
        headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15"
        },
        timeout=30,
    )
    response.raise_for_status()
    match = re.search(r'<div\s+class=["\']result-container["\']>(.*?)<', response.text, flags=re.S)
    if not match:
        raise RuntimeError("Google Translate returned an unexpected response.")
    return html.unescape(match.group(1)).strip()


def default_segment(
    source_subtitles: Sequence[SrtItem],
    draft_translations: Sequence[str],
    settings: StoryPipelineSettings,
    progress: ProgressFn,
) -> list[dict]:
    from .story_segments import parse_llm_json, source_subtitles_json

    voices_json = json.dumps(load_qwen_voice_catalog(), ensure_ascii=False, separators=(",", ":"))
    cues: list[dict] = []
    chunk_size = 20
    for start in range(0, len(source_subtitles), chunk_size):
        progress(f"segment:{start + 1}-{min(start + chunk_size, len(source_subtitles))}")
        subtitle_chunk = source_subtitles[start : start + chunk_size]
        draft_chunk = draft_translations[start : start + chunk_size]
        subtitles_json = source_subtitles_json(subtitle_chunk, draft_chunk)
        user_prompt = (
            settings.user_prompt_template.format(voices_json=voices_json, subtitles_json=subtitles_json)
            + "\nReturn compact valid JSON only. Do not add markdown. Keep zh_text concise."
        )
        text = call_llm_chat(settings, settings.system_prompt, user_prompt)
        cues.extend(parse_llm_json(text))
    return cues


def llm_translate(source_subtitles: Sequence[SrtItem], settings: StoryPipelineSettings, progress: ProgressFn) -> list[str]:
    from .story_segments import parse_llm_json

    output: list[str] = []
    chunk_size = 80
    for start in range(0, len(source_subtitles), chunk_size):
        progress(f"translate:{start + 1}-{min(start + chunk_size, len(source_subtitles))}")
        chunk = source_subtitles[start : start + chunk_size]
        rows = [{"line": int(item["line"]), "text": item["text"]} for item in chunk]
        prompt = (
            "Translate each English subtitle line into Simplified Chinese. "
            "Return a JSON array of strings with the same length and order.\n"
            + json.dumps(rows, ensure_ascii=False, indent=2)
        )
        data = parse_llm_json(call_llm_chat(settings, "You translate subtitles line by line.", prompt))
        if all(isinstance(item, str) for item in data):
            output.extend(str(item) for item in data)
        else:
            output.extend(str(item.get("text") or item.get("zh_text") or "") for item in data)
    return output


def call_llm_chat(settings: StoryPipelineSettings, system_prompt: str, user_prompt: str) -> str:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
        raise RuntimeError("The openai package is required for DeepSeek/GLM/OpenAI-compatible LLM calls.") from exc

    base_url = settings.llm_base_url.strip() or _default_llm_base_url(settings.llm_provider)
    client = OpenAI(api_key=settings.llm_api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )
    return response.choices[0].message.content or ""


def _default_llm_base_url(provider: str) -> str:
    provider = provider.lower().strip()
    if provider == "deepseek":
        return "https://api.deepseek.com"
    if provider == "glm":
        return "https://open.bigmodel.cn/api/paas/v4"
    return "https://api.openai.com/v1"


def default_synthesize(
    cues: Sequence[StoryCue],
    settings: StoryPipelineSettings,
    work_dir: Path,
    progress: ProgressFn,
) -> dict[str, str]:
    audio_dir = work_dir / "tts"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_files: dict[str, str] = {}
    for idx, cue in enumerate(cues):
        filename = (audio_dir / f"{idx + 1:04d}-{cue.id}.wav").as_posix()
        audio_files[cue.id] = filename
        progress(f"tts:{idx + 1}/{len(cues)}")
        if not cue.zh_text.strip():
            continue
        if Path(filename).exists() and Path(filename).stat().st_size > 0:
            continue
        qwen_tts_to_wav(cue.zh_text, cue.voice, settings, Path(filename))
    return audio_files


def qwen_tts_to_wav(text: str, voice_name: str, settings: StoryPipelineSettings, output_path: Path) -> None:
    try:
        import dashscope
        import requests
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError("Qwen TTS requires the dashscope and requests packages.") from exc

    voice_map = load_qwen_voice_map()
    voice = voice_map.get(voice_name) or voice_map.get(settings.qwen_default_voice) or "Eldric Sage"
    last_error: Exception | None = None
    for attempt in range(1, 7):
        try:
            response = dashscope.audio.qwen_tts.SpeechSynthesizer.call(
                model=settings.qwen_tts_model or "qwen3-tts-flash",
                api_key=settings.qwen_tts_key,
                text=text,
                voice=voice,
            )
            if response is None:
                raise RuntimeError("Qwen TTS returned no response.")
            if not getattr(response, "output", None) or not getattr(response.output, "audio", None):
                raise RuntimeError(getattr(response, "message", str(response)))
            audio_response = requests.get(response.output.audio["url"], timeout=120)
            audio_response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            if attempt == 6:
                raise
            time.sleep(min(30, 2 ** attempt))
    if last_error and "audio_response" not in locals():
        raise last_error
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_response.content)


def default_compose(
    downloaded: dict,
    cues: Sequence[StoryCue],
    audio_files: dict[str, str],
    settings: StoryPipelineSettings,
    work_dir: Path,
    progress: ProgressFn,
) -> str:
    from .render import compose_final_video

    return compose_final_video(downloaded, cues, audio_files, settings, work_dir, progress)
