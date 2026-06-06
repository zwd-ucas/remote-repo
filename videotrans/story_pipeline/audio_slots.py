from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from .story_segments import StoryCue


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
