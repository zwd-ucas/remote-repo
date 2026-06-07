from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

from .story_segments import StoryCue


@dataclass
class VoicePlacement:
    cue_id: str
    natural_start_ms: int  # placement position on the voice track (== out_start_ms)
    natural_dur_ms: int  # the clip's natural (pre-compression) duration
    out_start_ms: int  # where the clip is placed / where its subtitle starts
    out_end_ms: int  # placement end (start + compressed duration) / subtitle end
    speed: float = 1.0  # per-cue pitch-preserving tempo factor (1.0 = natural)

    def to_dict(self):
        return asdict(self)


@dataclass
class VoiceTimeline:
    placements: list[VoicePlacement]
    speed: float  # global tempo factor applied to the whole track (>= 1.0)
    natural_total_ms: int
    out_total_ms: int

    def to_dict(self):
        return {
            "placements": [p.to_dict() for p in self.placements],
            "speed": self.speed,
            "natural_total_ms": self.natural_total_ms,
            "out_total_ms": self.out_total_ms,
        }


def plan_voice_timeline(
    items: Iterable[tuple[str, int, int]],
    *,
    video_duration_ms: int,
    min_gap_ms: int = 120,
    max_speed: float = 1.0,
    min_slot_ms: int = 400,
) -> VoiceTimeline:
    """Place dubbing clips anchored to the picture, correcting drift per cue.

    ``items`` is an iterable of ``(cue_id, anchor_start_ms, audio_dur_ms)`` where the
    anchor is when the line is actually spoken on screen. Each clip is placed at its
    anchor (never before the previous clip + ``min_gap_ms``, so no overlap). When a
    clip would overrun the window until the next meaningful anchor, ONLY that clip is
    gently sped up — pitch-preserving, capped at ``max_speed`` — so it stays on its
    scene instead of pushing every later cue late. Clips that fit keep ``speed == 1.0``
    (natural), and because ``start = max(anchor, prev_end)`` the timeline RE-ANCHORS to
    the picture at the next slack, so drift recovers instead of accumulating.

    ``max_speed == 1.0`` disables compression (pure anchored placement; the caller
    extends the picture for any overrun). ``min_slot_ms`` makes the window look-ahead
    skip coincident/near-zero-width anchors so a tiny slot never demands absurd speed.
    """
    valid: list[tuple[str, int, int]] = []
    for cue_id, anchor, dur in items:
        dur = max(0, int(dur))
        if dur <= 0:
            continue
        valid.append((str(cue_id), int(anchor), dur))
    valid.sort(key=lambda it: it[1])  # picture-anchor order

    n = len(valid)
    gap = max(0, int(min_gap_ms))
    cap = max(1.0, float(max_speed))
    min_slot = max(1, int(min_slot_ms))
    video = max(1, int(video_duration_ms))

    placements: list[VoicePlacement] = []
    natural_total = 0
    prev_end = 0
    for i, (cue_id, anchor, dur) in enumerate(valid):
        start = max(anchor, prev_end + gap)
        # Window = span to the next anchor far enough away to be a real slot.
        win_end = video
        for j in range(i + 1, n):
            if valid[j][1] >= start + min_slot:
                win_end = valid[j][1]
                break
        window = max(min_slot, win_end - start)
        speed = min(cap, dur / window) if dur > window else 1.0
        out_dur = max(1, round(dur / speed))
        end = start + out_dur
        placements.append(
            VoicePlacement(
                cue_id=cue_id,
                natural_start_ms=start,
                natural_dur_ms=dur,
                out_start_ms=start,
                out_end_ms=end,
                speed=speed,
            )
        )
        prev_end = end
        natural_total += dur + gap

    out_total = placements[-1].out_end_ms if placements else 0
    return VoiceTimeline(
        placements=placements,
        speed=1.0,  # legacy/global field; tempo is now per-cue
        natural_total_ms=natural_total,
        out_total_ms=out_total,
    )


@dataclass
class AudioSlot:
    cue_id: str
    start_ms: int
    end_ms: int
    target_duration_ms: int
    source_audio_ms: int
    tail_silence_ms: int
    speed_target_ms: int | None

    def to_dict(self):
        return asdict(self)


@dataclass
class AudioSlotPlan:
    slots: list[AudioSlot]
    final_duration_ms: int

    def to_dict(self):
        return {"slots": [slot.to_dict() for slot in self.slots], "final_duration_ms": self.final_duration_ms}


def plan_audio_slots(
    cues: Sequence[StoryCue],
    audio_durations_ms: Mapping[str, int],
    *,
    video_duration_ms: int,
) -> AudioSlotPlan:
    slots: list[AudioSlot] = []
    for cue in cues:
        target = max(1, int(cue.end_ms - cue.start_ms))
        audio_ms = max(0, int(audio_durations_ms.get(cue.id, 0)))
        if audio_ms <= target:
            tail = target - audio_ms
            speed_target = None
        else:
            tail = 0
            speed_target = target
        slots.append(
            AudioSlot(
                cue_id=cue.id,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                target_duration_ms=target,
                source_audio_ms=audio_ms,
                tail_silence_ms=tail,
                speed_target_ms=speed_target,
            )
        )
    return AudioSlotPlan(slots=slots, final_duration_ms=max(int(video_duration_ms), 0))
