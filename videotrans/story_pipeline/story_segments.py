import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from .types import SrtItem

# Natural Chinese narration pace (~4.2 chars/sec): how long it takes to speak one character.
# Single source of truth for sizing a cue's text to its window — used by the budget-fit pass
# (pipeline) and the echoed-cue fallback window in _dedupe_source_lines below.
NATURAL_CHAR_MS = 240


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
    # Natural-language delivery direction for qwen3-tts-instruct (emotion + tone +
    # pace), e.g. "凶狠低沉、气势汹汹、语速偏慢". Empty falls back to a default per voice.
    instruction: str = ""

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
    from collections import Counter

    by_line = {int(_item_get(item, "line")): item for item in source_subtitles}
    cues: list[StoryCue] = []
    issues: list[StoryCueIssue] = []

    # Per-speaker gender votes from the LLM (when it supplies a "gender" field), used to fix
    # a character assigned an opposite-gender voice (e.g. a male troll given a female voice).
    speaker_genders: dict[str, Counter] = {}
    for raw in payload:
        speaker_name = str(raw.get("speaker") or "").strip()
        gender = str(raw.get("gender") or "").strip()
        if speaker_name and gender in ("男", "女"):
            speaker_genders.setdefault(speaker_name, Counter())[gender] += 1

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
                instruction=str(raw.get("instruction") or "").strip(),
            )
        )
    cues = _dedupe_source_lines(cues, by_line)
    cues = _split_overlong_cues(cues, by_line)
    cues = _enforce_speaker_voice_consistency(cues)
    cues = _enforce_voice_gender(cues, speaker_genders, valid_voices)
    return _rebalance_internal_splits(cues, source_subtitles), issues


def _enforce_voice_gender(cues: list[StoryCue], speaker_genders: dict, valid_voices: set[str]) -> list[StoryCue]:
    """If a speaker's voice gender contradicts the character's gender (per the LLM), swap to a
    same-gender voice — a deterministic backstop for the prompt's gender rule."""
    from .voices import same_gender_voices, voice_gender

    canonical = {speaker: counter.most_common(1)[0][0] for speaker, counter in speaker_genders.items() if counter}
    used = {cue.voice for cue in cues}
    remap: dict[str, str] = {}
    for cue in cues:
        want = canonical.get(cue.speaker)
        if not want or cue.speaker in remap:
            continue
        have = voice_gender(cue.voice)
        if have and have != want:
            options = same_gender_voices(want, valid_voices)
            if options:
                pick = next((v for v in options if v not in used), options[0])
                remap[cue.speaker] = pick
                used.add(pick)
    for cue in cues:
        if cue.speaker in remap:
            cue.voice = remap[cue.speaker]
    return cues


def _dedupe_source_lines(cues: list[StoryCue], by_line: dict[int, SrtItem]) -> list[StoryCue]:
    """Make source_lines partition the subtitles: every English line belongs to at most one
    cue group and windows run in sequence.

    The LLM sometimes emits overlapping source_lines — e.g. one cue [61,62,63] and the next
    [63,64,65] — which shows the same English line twice, duplicates translation, and makes
    the cue windows overlap in time. Walk the cues in order with a monotonic line pointer:
    a group only keeps lines no earlier group already claimed, and each window is clamped to
    start at/after the previous cue's end. Consecutive cues that share identical source_lines
    are one split group (a "xxx说道" + its quote) and keep their shared lines.
    """
    out: list[StoryCue] = []
    max_claimed = 0
    prev_end = 0
    i = 0
    while i < len(cues):
        signature = tuple(cues[i].source_lines)
        j = i
        while j < len(cues) and tuple(cues[j].source_lines) == signature:
            j += 1
        group = cues[i:j]
        claimed = list(signature)
        owned = [line for line in claimed if line > max_claimed]
        if claimed:
            max_claimed = max(max_claimed, claimed[-1])
        if owned:
            first, last = by_line.get(owned[0]), by_line.get(owned[-1])
            window_start = int(_item_get(first, "start_time", prev_end)) if first is not None else prev_end
            window_end = int(_item_get(last, "end_time", window_start + 1)) if last is not None else window_start + 1
            window_start = max(window_start, prev_end)
            window_end = max(window_end, window_start + 1)
        else:
            # The whole group's lines were already claimed by an earlier cue (the LLM echoed
            # them). Keep the text, but give it a fresh window after the previous cue with no
            # duplicate line numbers — better than re-showing a line that belongs elsewhere.
            text_len = sum(len(re.sub(r"\s+", "", cue.zh_text)) for cue in group) or 1
            window_start = prev_end
            window_end = prev_end + max(600, min(8000, text_len * NATURAL_CHAR_MS))
        for cue in group:
            cue.source_lines = list(owned)
            cue.start_ms = window_start
            cue.end_ms = window_end
        prev_end = window_end
        out.extend(group)
        i = j
    return out


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？；…!?;.])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _even_chunks(items: list, n: int) -> list[list]:
    """Split items into exactly n contiguous, roughly-equal chunks."""
    n = max(1, min(n, len(items)))
    base, extra = divmod(len(items), n)
    chunks, start = [], 0
    for index in range(n):
        size = base + (1 if index < extra else 0)
        chunks.append(items[start : start + size])
        start += size
    return chunks


def _split_pieces(text: str, target: int) -> list[str]:
    """Break Chinese text into at least ``target`` pieces — preferring sentence then comma
    boundaries, falling back to even character chunks for unpunctuated prose."""
    pieces = _split_sentences(text)
    if len(pieces) >= target:
        return pieces
    expanded: list[str] = []
    for piece in pieces or [text.strip()]:
        for part in re.split(r"(?<=[，,、])", piece):
            part = part.strip()
            if part:
                expanded.append(part)
    if len(expanded) >= target:
        return expanded
    chars = list(text.strip())
    if target >= 2 and len(chars) >= target:
        return ["".join(chunk) for chunk in _even_chunks(chars, target)]
    return expanded or pieces or [text.strip()]


def _split_overlong_cues(cues: list[StoryCue], by_line: dict[int, SrtItem], max_lines: int = 4) -> list[StoryCue]:
    """Break a cue that swallowed too many subtitle lines (the LLM over-merging a whole
    paragraph into one ~30s cue) into smaller cues, distributing its text across proportional
    sub-ranges of its lines. Deterministic backstop for the merge cap — always splits a
    too-long cue, even if the text has no sentence punctuation."""
    out: list[StoryCue] = []
    for cue in cues:
        lines = list(cue.source_lines)
        if len(lines) <= max_lines or not cue.zh_text.strip():
            out.append(cue)
            continue
        target = math.ceil(len(lines) / 3)
        pieces = _split_pieces(cue.zh_text, target)
        n = min(target, len(pieces), len(lines))
        if n < 2:
            out.append(cue)  # genuinely unsplittable (e.g. a single character)
            continue
        line_chunks = _even_chunks(lines, n)
        text_chunks = _even_chunks(pieces, n)
        for k in range(n):
            sub_lines = line_chunks[k]
            sub_text = "".join(text_chunks[k]).strip()
            if not sub_lines or not sub_text:
                continue
            first, last = by_line.get(sub_lines[0]), by_line.get(sub_lines[-1])
            window_start = int(_item_get(first, "start_time", cue.start_ms)) if first is not None else cue.start_ms
            window_end = int(_item_get(last, "end_time", cue.end_ms)) if last is not None else cue.end_ms
            out.append(
                StoryCue(
                    id=f"{cue.id}-{k + 1}",
                    source_lines=sub_lines,
                    start_ms=window_start,
                    end_ms=max(window_start + 1, window_end),
                    speaker=cue.speaker,
                    speaker_type=cue.speaker_type,
                    voice=cue.voice,
                    zh_text=sub_text,
                    confidence=cue.confidence,
                    needs_review=True,
                    instruction=cue.instruction,
                )
            )
    return out


def _enforce_speaker_voice_consistency(cues: list[StoryCue]) -> list[StoryCue]:
    """Guarantee one voice per character across the whole video.

    The LLM segments in independent chunks and can pick a different voice for the same
    speaker in each chunk (e.g. the troll). Pick each speaker's majority voice (ties →
    first seen) and apply it to every one of that speaker's lines, so a character never
    switches voice mid-story.
    """
    from collections import Counter

    votes: dict[str, Counter] = {}
    for cue in cues:
        if not cue.zh_text.strip():
            continue
        votes.setdefault(cue.speaker, Counter())[cue.voice] += 1
    canonical = {speaker: counter.most_common(1)[0][0] for speaker, counter in votes.items() if counter}
    for cue in cues:
        chosen = canonical.get(cue.speaker)
        if chosen:
            cue.voice = chosen
    return cues


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
    result = [StoryCue(**cue.to_dict()) for cue in cues]
    # Group ALL cues that share the same source_lines (single OR multi line). Several
    # cues bound to the same English line(s) — e.g. a "xxx说道" attribution split from
    # its quoted line — must be spread across that shared window, otherwise they keep
    # identical (often zero-width) timings and collapse onto the same instant.
    groups: dict[tuple[int, ...], list[int]] = {}
    for idx, cue in enumerate(result):
        groups.setdefault(tuple(cue.source_lines), []).append(idx)

    for lines, indexes in groups.items():
        if len(indexes) <= 1 or not lines:
            continue
        # Parent window = the span the group already covers (first line start .. last line end).
        window_start = min(result[idx].start_ms for idx in indexes)
        window_end = max(result[idx].end_ms for idx in indexes)
        duration = max(1, window_end - window_start)
        weights = [max(1, len(re.sub(r"\s+", "", result[idx].zh_text))) for idx in indexes]
        total = sum(weights)
        cursor = window_start
        for pos, idx in enumerate(indexes):
            if pos == len(indexes) - 1:
                next_time = window_end
            else:
                next_time = window_start + round(duration * sum(weights[: pos + 1]) / total)
                next_time = max(cursor + 1, min(next_time, window_end - (len(indexes) - pos - 1)))
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
