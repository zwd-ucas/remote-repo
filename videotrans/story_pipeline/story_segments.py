import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from .types import SrtItem


@dataclass
class StoryCue:
    id: str
    source_lines: list[int]
    start_ms: int
    end_ms: int
    speaker: str
    speaker_type: str
    voice: str
    zh_text: str
    confidence: float = 0.0
    needs_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StoryCueIssue:
    code: str
    message: str
    line: int | None = None
    cue_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _item_get(item: SrtItem, key: str, default: Any = None) -> Any:
    try:
        return item[key]
    except Exception:
        return getattr(item, key, default)


def _normal_lines(raw: Any) -> list[int]:
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        return [int(x) for x in re.findall(r"\d+", raw)]
    if isinstance(raw, Iterable):
        return [int(x) for x in raw]
    return []


def _is_contiguous(lines: Sequence[int]) -> bool:
    if not lines:
        return False
    return list(lines) == list(range(lines[0], lines[-1] + 1))


def normalize_story_cues(
    payload: Sequence[dict[str, Any]],
    source_subtitles: Sequence[SrtItem],
    *,
    valid_voices: set[str],
    default_voice: str,
) -> tuple[list[StoryCue], list[StoryCueIssue]]:
    by_line = {int(_item_get(item, "line")): item for item in source_subtitles}
    cues: list[StoryCue] = []
    issues: list[StoryCueIssue] = []

    for idx, raw in enumerate(payload):
        lines = sorted(dict.fromkeys(_normal_lines(raw.get("source_lines"))))
        if not _is_contiguous(lines):
            issues.append(
                StoryCueIssue(
                    code="non_contiguous_source_lines",
                    message="Cue source_lines must be adjacent English subtitle lines.",
                    line=lines[0] if lines else None,
                    cue_index=idx,
                )
            )
            continue
        missing = [line for line in lines if line not in by_line]
        if missing:
            issues.append(
                StoryCueIssue(
                    code="missing_source_line",
                    message=f"Cue references missing English subtitle line(s): {missing}",
                    line=missing[0],
                    cue_index=idx,
                )
            )
            continue

        first = by_line[lines[0]]
        last = by_line[lines[-1]]
        text = str(raw.get("zh_text") or raw.get("text") or "").strip()
        speaker = str(raw.get("speaker") or "Narrator").strip()
        voice = _resolve_voice(str(raw.get("voice") or default_voice).strip(), valid_voices, default_voice)
        prefix = _extract_role_voice_prefix(text)
        if prefix:
            speaker, prefixed_voice, text = prefix
            voice = _resolve_voice(prefixed_voice, valid_voices, voice)
        speaker_type = str(raw.get("speaker_type") or "narrator").strip()
        confidence = float(raw.get("confidence") or 0.0)
        if voice not in valid_voices:
            issues.append(
                StoryCueIssue(
                    code="invalid_voice",
                    message=f"Voice '{voice}' is not a known Qwen TTS voice; using '{default_voice}'.",
                    line=lines[0],
                    cue_index=idx,
                )
            )
            voice = default_voice
        source_start = int(_item_get(first, "start_time", 0))
        source_end = int(_item_get(last, "end_time", 0))
        start_ms = source_start
        end_ms = source_end
        if len(lines) == 1:
            start_ms = _bounded_time(raw.get("start_ms"), source_start, source_end, source_start)
            end_ms = _bounded_time(raw.get("end_ms"), source_start, source_end, source_end)

        cues.append(
            StoryCue(
                id=str(raw.get("id") or f"cue-{len(cues) + 1}"),
                source_lines=lines,
                start_ms=start_ms,
                end_ms=end_ms,
                speaker=speaker,
                speaker_type=speaker_type,
                voice=voice,
                zh_text=text,
                confidence=confidence,
                needs_review=_speaker_needs_review(text),
            )
        )
    return _rebalance_internal_splits(cues, source_subtitles), issues


def _extract_role_voice_prefix(text: str) -> tuple[str, str, str] | None:
    match = re.match(r"^\s*[\[【]([^\]-【】]+)-([^\]【】]+)[\]】]\s*(.*)$", text, flags=re.S)
    if not match:
        return None
    speaker, voice, body = match.groups()
    return speaker.strip(), voice.strip(), body.strip()


def _resolve_voice(value: str, valid_voices: set[str], default_voice: str) -> str:
    value = value.strip()
    if value in valid_voices:
        return value
    for voice in valid_voices:
        label, param = _voice_label_and_param(voice)
        if value in {label, param}:
            return voice
    return value or default_voice


def _voice_label_and_param(voice: str) -> tuple[str, str]:
    match = re.match(r"^(.*?)\((.*)\)$", voice)
    if not match:
        return voice, ""
    return match.group(1).strip(), match.group(2).strip()


def _bounded_time(value: Any, start: int, end: int, fallback: int) -> int:
    try:
        number = int(value)
    except Exception:
        return fallback
    return min(max(number, start), end)


def _rebalance_internal_splits(cues: Sequence[StoryCue], source_subtitles: Sequence[SrtItem]) -> list[StoryCue]:
    by_line = {int(_item_get(item, "line")): item for item in source_subtitles}
    result = [StoryCue(**cue.to_dict()) for cue in cues]
    groups: dict[int, list[int]] = {}
    for idx, cue in enumerate(result):
        if len(cue.source_lines) == 1:
            groups.setdefault(cue.source_lines[0], []).append(idx)

    for line, indexes in groups.items():
        if len(indexes) <= 1 or line not in by_line:
            continue
        source = by_line[line]
        source_start = int(_item_get(source, "start_time", 0))
        source_end = int(_item_get(source, "end_time", 0))
        duration = max(1, source_end - source_start)
        weights = [max(1, len(re.sub(r"\s+", "", result[idx].zh_text))) for idx in indexes]
        total = sum(weights)
        cursor = source_start
        for pos, idx in enumerate(indexes):
            if pos == len(indexes) - 1:
                next_time = source_end
            else:
                next_time = source_start + round(duration * sum(weights[: pos + 1]) / total)
                next_time = max(cursor + 1, min(next_time, source_end - (len(indexes) - pos - 1)))
            result[idx].start_ms = cursor
            result[idx].end_ms = next_time
            cursor = next_time
    return result


def _speaker_needs_review(text: str) -> bool:
    separators = ["：", ":", " says ", " said "]
    return sum(1 for sep in separators if sep in text) > 1


def cues_to_srt(cues: Sequence[StoryCue]) -> str:
    rows = []
    for idx, cue in enumerate(cues, start=1):
        start = ms_to_srt_time(cue.start_ms)
        end = ms_to_srt_time(cue.end_ms)
        rows.append(f"{idx}\n{start} --> {end}\n{cue.zh_text.strip()}")
    return "\n\n".join(rows).strip() + "\n"


def source_to_srt(source_subtitles: Sequence[SrtItem]) -> str:
    rows = []
    for idx, item in enumerate(source_subtitles, start=1):
        start = _item_get(item, "startraw") or ms_to_srt_time(int(_item_get(item, "start_time", 0)))
        end = _item_get(item, "endraw") or ms_to_srt_time(int(_item_get(item, "end_time", 0)))
        rows.append(f"{idx}\n{start} --> {end}\n{str(_item_get(item, 'text', '')).strip()}")
    return "\n\n".join(rows).strip() + "\n"


def ms_to_srt_time(ms: int) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_llm_json(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.I | re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    data = json.loads(cleaned)
    if isinstance(data, dict):
        data = data.get("cues") or data.get("items") or []
    if not isinstance(data, list):
        raise ValueError("LLM response must be a JSON array or an object with a cues array.")
    return data


def source_subtitles_json(source_subtitles: Sequence[SrtItem], draft_translations: Sequence[str] | None = None) -> str:
    rows = []
    for idx, item in enumerate(source_subtitles):
        rows.append(
            {
                "line": int(_item_get(item, "line")),
                "start_ms": int(_item_get(item, "start_time", 0)),
                "end_ms": int(_item_get(item, "end_time", 0)),
                "text": _item_get(item, "text", ""),
                "draft_zh": draft_translations[idx] if draft_translations and idx < len(draft_translations) else "",
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)
