"""GI-independent behavior for the thin IBus engine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from classscribe_protocol import (
    ActivationMode,
    ConfirmationMode,
    DictationConfig,
    DictationLanguage,
    DictationPunctuation,
    DictationState,
    DictationStatus,
)

ACTIVATION_KEY = 0xFFC6  # F9; the portal can invoke the same daemon actions globally.
ESCAPE_KEY = 0xFF1B


class ControlClient(Protocol):
    def request(
        self, action: str, params: Mapping[str, object] | None = None
    ) -> dict[str, Any]: ...


class IBusBridge(Protocol):
    def update_preedit(self, text: str, stable_characters: int) -> None: ...

    def commit(self, text: str) -> None: ...

    def update_lookup(self, candidates: tuple[str, ...], visible: bool) -> None: ...

    def show_status(self, message: str) -> None: ...

    def update_properties(self, values: Mapping[str, str | bool]) -> None: ...

    def update_available_models(self, model_ids: tuple[str, ...]) -> None: ...


class EngineController:
    """Translate daemon events to IBus calls; ordinary keys always pass through."""

    def __init__(self, client: ControlClient, bridge: IBusBridge) -> None:
        self.client = client
        self.bridge = bridge
        self.config = DictationConfig()
        self.revision = 0
        self.state = DictationState.IDLE
        self._pressed = False

    def handle_key(self, keyval: int, *, released: bool = False) -> bool:
        if keyval == ESCAPE_KEY and self.state is not DictationState.IDLE and not released:
            self._command("cancel")
            return True
        if (
            self.state is DictationState.CANDIDATE_SELECT
            and not released
            and ord("1") <= keyval <= ord("9")
        ):
            self._command("select_candidate", {"index": keyval - ord("1")})
            return True
        if keyval != ACTIVATION_KEY:
            return False
        if self.config.activation is ActivationMode.HOLD:
            if released and self._pressed:
                self._pressed = False
                self._command("release")
            elif not released and not self._pressed:
                self._pressed = True
                self._command("begin")
            return True
        if not released:
            self._command("toggle")
        return True

    def poll(self) -> bool:
        self._command("status")
        return True

    def set_property(self, key: str, value: str | bool) -> None:
        values = self.config.as_dict()
        if key == "language":
            values[key] = DictationLanguage(str(value)).value
        elif key == "confirmation":
            values[key] = ConfirmationMode(str(value)).value
        elif key == "activation":
            values[key] = ActivationMode(str(value)).value
        elif key == "punctuation":
            values[key] = DictationPunctuation(str(value)).value
        elif key == "model_id":
            values[key] = str(value)
        elif key == "show_interim":
            if not isinstance(value, bool):
                raise ValueError("show_interim property is boolean")
            values[key] = value
        else:
            raise ValueError("unknown IBus property")
        updated = DictationConfig.from_mapping(values)
        self._command("configure", {"config": updated.as_dict()})

    def _command(self, action: str, params: Mapping[str, object] | None = None) -> None:
        payload = {"since_revision": self.revision, **dict(params or {})}
        try:
            response = self.client.request(action, payload)
            raw_config = response.get("config")
            if isinstance(raw_config, Mapping):
                self.config = DictationConfig.from_mapping(raw_config)
            raw_models = response.get("available_models")
            if raw_models is not None:
                if not isinstance(raw_models, list) or any(
                    not isinstance(item, str) or not item for item in raw_models
                ):
                    raise ValueError("available dictation models must be a string list")
                self.bridge.update_available_models(tuple(raw_models))
            events = response.get("events", ())
            if not isinstance(events, list):
                raise ValueError("dictation events must be a list")
            for value in events:
                if not isinstance(value, Mapping):
                    raise ValueError("dictation event must be an object")
                self._apply(DictationStatus.from_mapping(value))
        except Exception as exc:
            self.state = DictationState.IDLE
            self._pressed = False
            self.bridge.update_preedit("", 0)
            self.bridge.update_lookup((), False)
            self.bridge.show_status(f"ClassScribe unavailable: {type(exc).__name__}")

    def _apply(self, status: DictationStatus) -> None:
        if status.revision <= self.revision:
            return
        self.revision = status.revision
        self.state = status.state
        if status.properties:
            self.bridge.update_properties(status.properties)
        if status.state in {DictationState.ERROR, DictationState.IDLE} and status.message:
            self.bridge.show_status(status.message)
        elif status.state is DictationState.ARMING and status.expected_accuracy_load_ms is not None:
            self.bridge.show_status(
                f"Accuracy model load may take {status.expected_accuracy_load_ms:.0f} ms"
            )
        if status.state is DictationState.ERROR:
            self.bridge.update_preedit("", 0)
            self.bridge.update_lookup((), False)
            self._pressed = False
            return
        if status.commit_text and status.state in {
            DictationState.INTERIM_UPDATE,
            DictationState.LISTENING,
            DictationState.COMMITTING,
        }:
            self.bridge.commit(status.commit_text)
        if status.state is DictationState.CANDIDATE_SELECT:
            self.bridge.update_lookup(status.candidates, True)
        elif status.state is DictationState.COMMITTING:
            self.bridge.update_lookup((), False)
            self.bridge.update_preedit("", 0)
        elif status.state in {DictationState.LISTENING, DictationState.INTERIM_UPDATE}:
            self.bridge.update_preedit(status.preedit, status.stable_characters)
        elif status.state is DictationState.IDLE:
            self.bridge.update_preedit("", 0)
            self.bridge.update_lookup((), False)
            self._pressed = False
