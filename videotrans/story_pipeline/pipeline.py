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
    saved_video: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineDependencies:
    download: Callable[[str, Path, ProgressFn, StoryPipelineSettings], dict] | None = None
    transcribe: Callable[[Path, StoryPipelineSettings, ProgressFn], list[SrtItem]] | None = None
    translate: Callable[[Sequence[SrtItem], StoryPipelineSettings, ProgressFn], list[str]] | None = None
    segment: Callable[[Sequence[SrtItem], Sequence[str], StoryPipelineSettings, ProgressFn], list[dict]] | None = None
    review: Callable[[list[dict], StoryPipelineSettings, ProgressFn], list[dict]] | None = None
    synthesize: Callable[[Sequence[StoryCue], StoryPipelineSettings, Path, ProgressFn], dict[str, str]] | None = None
    compose: Callable[[dict, Sequence[StoryCue], dict[str, str], StoryPipelineSettings, Path, ProgressFn], str] | None = None
    # Manual-mode hook: called with the ready cues + a review manifest, returns the cues
    # to actually synthesize (the caller may block for human edits and return edited cues).
    checkpoint: Callable[[list[StoryCue], "StoryManifest"], list[StoryCue]] | None = None


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

        # Precise, picture-synced anchors: transcribe the original audio with ASR and use
        # its clean segment timings instead of the rolling auto-caption timings.
        if getattr(settings, "asr_alignment", False) and downloaded.get("video_path"):
            transcribe = self.dependencies.transcribe or default_transcribe
            try:
                self.progress("transcribe")
                aligned = transcribe(Path(downloaded["video_path"]), settings, self.progress)
            except Exception as exc:
                aligned = []
                self.progress(f"transcribe failed, keeping caption timing: {exc}")
            if aligned:
                source_subtitles = list(aligned)

        source_path = Path(downloaded.get("subtitle_path") or self.work_dir / "en.srt")
        self._write_source_srt(source_subtitles, source_path)

        self.progress("translate")
        draft_translations = translate(source_subtitles, settings, self.progress)

        self.progress("segment")
        raw_cues = segment(source_subtitles, draft_translations, settings, self.progress)
        # Second LLM pass: re-judge who speaks each line from the whole story context and
        # split any narration+quote lines, so no character's line stays in the wrong voice.
        review = self.dependencies.review or default_review
        raw_cues = review(raw_cues, settings, self.progress)
        valid_voices = load_qwen_voice_names()
        cues, issues = normalize_story_cues(
            raw_cues,
            source_subtitles,
            valid_voices=valid_voices,
            default_voice=settings.qwen_default_voice,
        )
        target_path = self.work_dir / "zh.srt"
        self._write_cues(cues, target_path)

        # Manual-mode checkpoint: hand the ready cues to the caller for human review/edits
        # (timeline, voice, subtitle, speaker) before any audio is generated.
        checkpoint = self.dependencies.checkpoint
        if checkpoint is not None:
            review_manifest = self._build_manifest(
                "awaiting_review", downloaded, cues, {}, source_subtitles, source_path, target_path, issues, "", ""
            )
            reviewed = checkpoint(cues, review_manifest)
            if reviewed:
                cues = list(reviewed)
                self._write_cues(cues, target_path)

        self.progress("tts")
        audio_files = synthesize(cues, settings, self.work_dir, self.progress)

        self.progress("compose")
        final_video = compose(downloaded, cues, audio_files, settings, self.work_dir, self.progress)
        saved_video = self._save_output(final_video, downloaded, settings)
        manifest = self._build_manifest(
            "ready", downloaded, cues, audio_files, source_subtitles, source_path, target_path, issues, final_video, saved_video
        )
        (self.work_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.progress("ready")
        return manifest

    def _write_cues(self, cues: Sequence[StoryCue], target_path: Path) -> None:
        target_path.write_text(cues_to_srt(cues), encoding="utf-8")
        (self.work_dir / "story_cues.json").write_text(
            json.dumps([cue.to_dict() for cue in cues], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_manifest(
        self, status, downloaded, cues, audio_files, source_subtitles, source_path, target_path, issues, final_video, saved_video
    ) -> "StoryManifest":
        return StoryManifest(
            status=status,
            video_id=str(downloaded.get("video_id") or ""),
            title=str(downloaded.get("title") or ""),
            work_dir=self.work_dir.as_posix(),
            final_video=final_video,
            saved_video=saved_video,
            source_subtitle=source_path.as_posix(),
            target_subtitle=target_path.as_posix(),
            source_subtitles=self._source_subtitle_rows(source_subtitles),
            cues=[cue.to_dict() for cue in cues],
            audio_files=audio_files,
            issues=[issue.to_dict() for issue in issues],
        )

    def _save_output(self, final_video: str, downloaded: dict, settings: StoryPipelineSettings) -> str:
        """Copy the finished video to the user's configured output_dir, named by title."""
        out_dir = (getattr(settings, "output_dir", "") or "").strip()
        if not out_dir or not final_video or not Path(final_video).exists():
            return ""
        try:
            import re
            import shutil

            dest_dir = Path(out_dir).expanduser()
            dest_dir.mkdir(parents=True, exist_ok=True)
            base = str(downloaded.get("title") or downloaded.get("video_id") or "story").strip() or "story"
            safe = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", base)[:80].strip() or "story"
            dest = dest_dir / f"{safe}.zh-dub.mp4"
            shutil.copy2(final_video, dest)
            self.progress(f"saved to {dest}")
            return dest.as_posix()
        except Exception as exc:  # pragma: no cover - filesystem/runtime
            self.progress(f"save output failed: {exc}")
            return ""

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


def default_transcribe(video_path: Path, settings: StoryPipelineSettings, progress: ProgressFn) -> list[SrtItem]:
    from .asr import transcribe_audio

    return transcribe_audio(video_path, settings, progress)


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


# Big enough for V4 thinking (reasoning + the full cues JSON answer) on the whole script.
SEGMENT_MAX_TOKENS = 32768


def default_segment(
    source_subtitles: Sequence[SrtItem],
    draft_translations: Sequence[str],
    settings: StoryPipelineSettings,
    progress: ProgressFn,
) -> list[dict]:
    """Segment + cast the whole script in ONE LLM call.

    Sending every subtitle together gives the model full story context, so each
    character is named and voiced consistently start-to-finish (no per-chunk context
    loss). Only if the single response is incomplete — e.g. a very long script whose
    JSON would exceed the output token limit — do we fall back to chunking so no line
    is silently dropped.
    """
    from .story_segments import parse_llm_json, source_subtitles_json

    voices_json = json.dumps(load_qwen_voice_catalog(), ensure_ascii=False, separators=(",", ":"))
    progress(f"segment:1-{len(source_subtitles)}")
    subtitles_json = source_subtitles_json(source_subtitles, draft_translations)
    user_prompt = (
        settings.user_prompt_template.format(voices_json=voices_json, subtitles_json=subtitles_json)
        + "\n一次性处理上面【全部】字幕；纵观整个故事，为每个角色全程保持同一名称和同一音色，不得中途改名或换音色。"
        + "\nReturn compact valid JSON only. Do not add markdown. Keep zh_text concise."
    )
    cues: list[dict] = []
    try:
        cues = parse_llm_json(
            call_llm_chat(
                settings, settings.system_prompt, user_prompt,
                max_tokens=SEGMENT_MAX_TOKENS, reasoning_effort="", model=settings.llm_segment_model,
            )
        )
    except Exception as exc:  # truncated / invalid JSON
        progress(f"segment: single pass failed ({exc}); chunking")
    if cues and _segment_covers_source(cues, source_subtitles):
        return cues
    if cues:
        progress("segment: single pass incomplete; chunking to avoid dropped lines")
    return _segment_in_chunks(source_subtitles, draft_translations, settings, progress, voices_json)


def _segment_covers_source(cues: list[dict], source_subtitles: Sequence[SrtItem]) -> bool:
    from .story_segments import _normal_lines

    reached = 0
    for cue in cues:
        for line in _normal_lines(cue.get("source_lines")):
            reached = max(reached, line)
    return reached >= len(source_subtitles) - 2


def _segment_in_chunks(
    source_subtitles: Sequence[SrtItem],
    draft_translations: Sequence[str],
    settings: StoryPipelineSettings,
    progress: ProgressFn,
    voices_json: str,
) -> list[dict]:
    """Fallback for over-long scripts: chunk, but carry the cast forward for consistency."""
    from .story_segments import parse_llm_json, source_subtitles_json

    cues: list[dict] = []
    speaker_voices: dict[str, str] = {}
    chunk_size = 20
    for start in range(0, len(source_subtitles), chunk_size):
        progress(f"segment:{start + 1}-{min(start + chunk_size, len(source_subtitles))}")
        subtitles_json = source_subtitles_json(
            source_subtitles[start : start + chunk_size], draft_translations[start : start + chunk_size]
        )
        cast_note = ""
        if speaker_voices:
            cast = "；".join(f"{spk}={voice}" for spk, voice in speaker_voices.items())
            cast_note = f"\n已确定的角色与音色（同一角色必须沿用同一名称和同一音色，不得改名或换音色）：{cast}"
        user_prompt = (
            settings.user_prompt_template.format(voices_json=voices_json, subtitles_json=subtitles_json)
            + cast_note
            + "\nReturn compact valid JSON only. Do not add markdown. Keep zh_text concise."
        )
        try:
            chunk_cues = parse_llm_json(
                call_llm_chat(
                    settings, settings.system_prompt, user_prompt,
                    max_tokens=SEGMENT_MAX_TOKENS, reasoning_effort="", model=settings.llm_segment_model,
                )
            )
        except Exception as exc:  # a single bad/empty chunk must not abort the whole run
            progress(f"segment chunk {start + 1} failed ({exc}); skipping")
            continue
        cues.extend(chunk_cues)
        for raw in chunk_cues:
            speaker = str(raw.get("speaker") or "").strip()
            voice = str(raw.get("voice") or "").strip()
            if speaker and voice and speaker not in speaker_voices:
                speaker_voices[speaker] = voice
    return cues


REVIEW_SYSTEM_PROMPT = "你是儿童童话配音的台词归属校对导演，擅长根据整个故事的语境判断每句话到底是谁说的。"

_REVIEW_INSTRUCTIONS = """下面是一段童话中文配音脚本的全部分句(JSON)，每条含 source_lines、speaker、zh_text、instruction。
请通读【整个故事的语境和前后文】，逐条校对“这句到底是谁说出来的”，修正错误的归属：
- 叙述、描写、引导语(如“XX说道”“他喊道”) → speaker 必须是“旁白”。
- 角色真正说出口的话 → speaker 是对应角色名(如“巨魔”“小比利山羊”“中比利山羊”“大比利山羊”)。
- 明显的角色台词，例如“是谁在我的桥上踢踏响？”“我要吃掉你！”“你过桥吧”，必须归到对应角色，绝不能标成旁白。
- 【尽量少拆，绝大多数句子不需要拆】：只有当一条里【确实同时包含"角色说出口的台词"和"叙述引导语"】时才拆，例如「“哦不，你休想！”可怕的巨魔说着，爬上了桥。」拆成：引号台词→对应角色、叙述→旁白，两条共用同一个 source_lines。纯叙述、纯台词的句子一律【保持原样，不要拆】。
- 拆分会让每句时间变紧，所以非必要不要拆；只修正明显的归属错误。
- 同一个角色全程保持同一名称。保持 zh_text 文字内容不变(拆分除外)，不要新增或改写剧情。
- 每条都要有 instruction(情绪/语气/语速)；拆出来的新句补上合适的 instruction。
只返回修正后的完整 JSON 数组(字段：source_lines、speaker、zh_text、instruction)，按故事顺序，不要 Markdown。"""


def default_review(raw_cues: list[dict], settings: StoryPipelineSettings, progress: ProgressFn) -> list[dict]:
    """LLM speaker-attribution review: judge who speaks each line from the WHOLE story.

    Re-checks every cue's speaker against full context, fixes mis-attributions, and splits
    lines that mix narration with a character's quote — so a character's line is never left
    in the narrator's voice. Falls back to the segmentation output if the review looks
    incomplete (e.g. truncated). Voice follows the corrected speaker via the existing cast.
    """
    from collections import Counter

    from .story_segments import _normal_lines, parse_llm_json

    if not raw_cues:
        return raw_cues
    progress("review speakers")
    votes: dict[str, Counter] = {}
    for cue in raw_cues:
        speaker = str(cue.get("speaker") or "").strip()
        voice = str(cue.get("voice") or "").strip()
        if speaker and voice:
            votes.setdefault(speaker, Counter())[voice] += 1
    cast = {speaker: counter.most_common(1)[0][0] for speaker, counter in votes.items() if counter}

    compact = [
        {
            "source_lines": cue.get("source_lines"),
            "speaker": cue.get("speaker"),
            "zh_text": cue.get("zh_text"),
            "instruction": cue.get("instruction", ""),
        }
        for cue in raw_cues
    ]
    user_prompt = _REVIEW_INSTRUCTIONS + "\n配音脚本(JSON):\n" + json.dumps(compact, ensure_ascii=False)
    try:
        reviewed = parse_llm_json(call_llm_chat(settings, REVIEW_SYSTEM_PROMPT, user_prompt, max_tokens=SEGMENT_MAX_TOKENS))
    except Exception as exc:
        progress(f"review failed ({exc}); keeping segmentation")
        return raw_cues
    if not isinstance(reviewed, list) or not reviewed:
        return raw_cues

    def _max_line(cues: list[dict]) -> int:
        return max((max(_normal_lines(c.get("source_lines")), default=0) for c in cues), default=0)

    if _max_line(reviewed) < _max_line(raw_cues) - 2:
        progress("review incomplete; keeping segmentation")
        return raw_cues

    corrected: list[dict] = []
    for cue in reviewed:
        speaker = str(cue.get("speaker") or "旁白").strip()
        corrected.append(
            {
                "source_lines": cue.get("source_lines"),
                "speaker": speaker,
                "speaker_type": "narrator" if speaker == "旁白" else "character",
                "voice": cast.get(speaker) or settings.qwen_default_voice,
                "zh_text": cue.get("zh_text"),
                "instruction": cue.get("instruction", ""),
            }
        )
    return corrected


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


def call_llm_chat(
    settings: StoryPipelineSettings,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    model: str | None = None,
) -> str:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
        raise RuntimeError("The openai package is required for DeepSeek/GLM/OpenAI-compatible LLM calls.") from exc

    base_url = settings.llm_base_url.strip() or _default_llm_base_url(settings.llm_provider)
    client = OpenAI(api_key=settings.llm_api_key, base_url=base_url)
    create_kwargs = dict(
        model=model or settings.llm_model,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=settings.temperature,
        max_tokens=max_tokens or settings.max_tokens,
    )
    # reasoning_effort: None -> use the global setting; "" -> explicitly no thinking
    # (concise output, used for bulk segmentation); "high" -> max DeepSeek V4 thinking.
    effort = reasoning_effort if reasoning_effort is not None else getattr(settings, "llm_reasoning_effort", "")
    effort = (effort or "").strip()
    if effort:
        create_kwargs["reasoning_effort"] = effort
    response = client.chat.completions.create(**create_kwargs)
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
        instruction = (getattr(cue, "instruction", "") or "").strip()
        # Cache key includes the instruction so changing the delivery re-synthesizes.
        filename = (audio_dir / f"{idx + 1:04d}-{cue.id}-{_instruction_tag(instruction)}.wav").as_posix()
        audio_files[cue.id] = filename
        progress(f"tts:{idx + 1}/{len(cues)}")
        if not cue.zh_text.strip():
            continue
        if Path(filename).exists() and Path(filename).stat().st_size > 0:
            continue
        qwen_tts_to_wav(cue.zh_text, cue.voice, settings, Path(filename), instruction=instruction)
    return audio_files


def _instruction_tag(instruction: str) -> str:
    import hashlib

    if not instruction:
        return "plain"
    return hashlib.sha1(instruction.encode("utf-8")).hexdigest()[:8]


def qwen_tts_to_wav(
    text: str,
    voice_name: str,
    settings: StoryPipelineSettings,
    output_path: Path,
    *,
    instruction: str = "",
) -> None:
    try:
        import dashscope
        import requests
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError("Qwen TTS requires the dashscope and requests packages.") from exc

    voice_map = load_qwen_voice_map()
    fallback_voice = voice_map.get(settings.qwen_default_voice) or "Serena"
    voice = voice_map.get(voice_name) or fallback_voice
    model = settings.qwen_tts_model or "qwen3-tts-instruct-flash"
    instruction = (instruction or "").strip()
    last_error: Exception | None = None
    audio_response = None
    for attempt in range(1, 7):
        try:
            kwargs = dict(
                model=model,
                api_key=settings.qwen_tts_key,
                text=text,
                voice=voice,
                language_type="Chinese",
                stream=False,
            )
            # The instruct model takes a natural-language delivery direction
            # (emotion / tone / pace). Plain qwen3-tts ignores it, so only send it
            # to instruct-capable models.
            if instruction and "instruct" in model:
                kwargs["instructions"] = instruction
                kwargs["optimize_instructions"] = True
            response = dashscope.MultiModalConversation.call(**kwargs)
            if response is None:
                raise RuntimeError("Qwen TTS returned no response.")
            url = _qwen_audio_url(response)
            if not url:
                raise RuntimeError(getattr(response, "message", str(response)))
            audio_response = requests.get(url, timeout=120)
            audio_response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            message = str(exc)
            # An unsupported voice must never crash the run — fall back to the default
            # voice and retry (the instruct model supports only a subset of voices).
            if voice != fallback_voice and ("not supported" in message or "InvalidParameter" in message):
                voice = fallback_voice
                continue
            if attempt == 6:
                raise
            time.sleep(min(30, 2 ** attempt))
    if audio_response is None:
        raise last_error or RuntimeError("Qwen TTS failed.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_response.content)


def _qwen_audio_url(response) -> str | None:
    output = getattr(response, "output", None)
    if output is None:
        return None
    audio = output.get("audio") if isinstance(output, dict) else getattr(output, "audio", None)
    if isinstance(audio, dict):
        return audio.get("url")
    return getattr(audio, "url", None)


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
