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


class AudioActivity(Protocol):
    def levels(self) -> dict:
        """Optional RMS, updated_monotonic_s, short history and errors; no speaker identity."""
        ...


class InteractionScheduler(Protocol):
    """Optional local Adapter extension; HTTP v1.0 does not expose these methods."""
    def wait_interaction(self, kind: str, params: dict) -> dict: ...
    def session_status(self) -> dict: ...
    def play_cleanup(self, asset: dict, params: dict) -> dict: ...


class DeviceNavigation(Protocol):
    """Optional local extension; action completion is not a verified room exit."""
    def device_action(self, params: dict) -> dict: ...


class SemanticReviewer(Protocol):
    def review(self, context: dict, events: list[dict]) -> dict:
        """Integration contract only; return verdict, reasons, evidence and limitations.

        Logs do not establish audible speech. No provider is configured by default.
        Model inference must not hold the audio scheduler's timing loop.
        """
        ...


class SpeechPhaseClassifier(Protocol):
    def classify(self, context: dict, caption: dict) -> dict | None:
        """Label a specific run/trace/segment/caption; preserve inferred vs native origin.

        None means unknown. This does not establish audible progress or task identity.
        """
        ...


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


class RecordedSpeechRecognizer(Protocol):
    def transcribe(self, audio: Path) -> dict:
        """recording-asr-v1 with audio/model hashes, text and estimated time spans.

        Offline post-processing only. Does not provide speaker attribution,
        calibrated confidence, real-time timing triggers, or a product PASS.
        """
        ...
