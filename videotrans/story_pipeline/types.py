from dataclasses import dataclass
from typing import Any


@dataclass
class SrtItem:
    text: str = ""
    start_time: int | float = 0
    end_time: int | float = 0
    startraw: str = ""
    endraw: str = ""
    line: int | None = 1
    time: str | None = ""
    spk: str | None = ""
    filename: str | None = ""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def items(self):
        for key in ("line", "time", "start_time", "end_time", "startraw", "endraw", "text", "spk", "filename"):
            yield key, getattr(self, key)

    def __iter__(self):
        return iter(("line", "time", "start_time", "end_time", "startraw", "endraw", "text", "spk", "filename"))
