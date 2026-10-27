"""Component contracts for agents implementing other devices or environments."""
from pathlib import Path
from typing import Protocol


class TtsProvider(Protocol):
    def synthesize(self, text: str, output: Path, **options) -> None:
        """Write PCM16 WAV; never play, record, or change device state."""
        ...


class AudioIO(Protocol):
    def start(self, output: Path, config: dict) -> dict: ...
    def play(self, asset: dict) -> dict: ...
    def stop(self) -> dict: ...


class AudioPlayer(Protocol):
    def play(self, asset: dict, output_device=None) -> dict:
        """Play prebuilt audio; return backend observation, never acoustic proof."""
        ...


class DeviceEventSource(Protocol):
    def start(self, output: Path, config: dict) -> dict: ...
    def all_events(self) -> list[dict]: ...
    def healthy(self) -> None: ...
    def stop(self) -> None: ...


class EvidenceAnalyzer(Protocol):
    def analyze(self, manifest: dict, audio: Path, events: Path) -> dict:
        """Return analysis.schema.json; unknown speech stays unknown, no invented text."""
        ...
