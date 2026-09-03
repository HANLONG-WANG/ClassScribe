"""XDG Desktop Portal GlobalShortcuts registration and daemon forwarding."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from classscribe_protocol import DictationIPCClient, DictationIPCError


class ShortcutBackend(Protocol):
    def register(self, activated: Callable[[], None], deactivated: Callable[[], None]) -> None: ...

    def run(self) -> None: ...


class ControlClient(Protocol):
    def request(
        self, action: str, params: Mapping[str, object] | None = None
    ) -> dict[str, Any]: ...


class PortalController:
    def __init__(
        self,
        backend: ShortcutBackend,
        client: ControlClient,
        *,
        status_path: Path | None = None,
    ) -> None:
        self.backend = backend
        self.client = client
        self.available = False
        self.diagnostic = "IBus input source remains available"
        self._hold_activation = True
        self.status_path = status_path

    def register(self) -> bool:
        try:
            self.backend.register(self._activated, self._deactivated)
        except Exception as exc:
            self.available = False
            self.diagnostic = (
                f"GlobalShortcuts unavailable ({type(exc).__name__}); use ClassScribe Voice "
                "as an IBus input source"
            )
            self._record_status()
            return False
        self.available = True
        self.diagnostic = "GlobalShortcuts registered"
        self._record_status()
        return True

    def _activated(self) -> None:
        try:
            status = self.client.request("status")
            config = status.get("config", {})
            self._hold_activation = not (
                isinstance(config, Mapping) and config.get("activation") == "toggle"
            )
            self.client.request("begin" if self._hold_activation else "toggle")
        except DictationIPCError:
            self.diagnostic = "dictationd unavailable; IBus input source remains usable"
            self._record_status()

    def _deactivated(self) -> None:
        if not self._hold_activation:
            return
        try:
            self.client.request("release")
        except DictationIPCError:
            self.diagnostic = "dictationd unavailable; no text was committed"
            self._record_status()

    def _record_status(self) -> None:
        path = self.status_path
        if path is None:
            return
        with contextlib.suppress(OSError, ValueError):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.parent.is_symlink():
                raise ValueError("portal status directory must not be a symlink")
            path.parent.chmod(0o700)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "available": self.available,
                            "diagnostic": self.diagnostic,
                            "updated_at": datetime.now(UTC).isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                temporary.chmod(0o600)
                os.replace(temporary, path)
            finally:
                if temporary.exists() and not temporary.is_symlink():
                    temporary.unlink()


class GioGlobalShortcuts:
    INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
    PORTAL_NAME = "org.freedesktop.portal.Desktop"
    PORTAL_PATH = "/org/freedesktop/portal/desktop"

    def __init__(self) -> None:
        try:
            import gi  # type: ignore[import-not-found]

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib  # type: ignore[import-not-found]
        except (ImportError, ValueError) as exc:
            raise RuntimeError("PyGObject Gio portal support is unavailable") from exc
        self.Gio = Gio
        self.GLib = GLib
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.loop = GLib.MainLoop()
        self.session_handle = ""
        self._activated: Callable[[], None] | None = None
        self._deactivated: Callable[[], None] | None = None

    def register(self, activated: Callable[[], None], deactivated: Callable[[], None]) -> None:
        self._activated = activated
        self._deactivated = deactivated
        session_token = "classscribe_" + uuid4().hex
        create_token = "request_" + uuid4().hex
        results = self._request(
            "CreateSession",
            self.GLib.Variant(
                "(a{sv})",
                (
                    {
                        "handle_token": self.GLib.Variant("s", create_token),
                        "session_handle_token": self.GLib.Variant("s", session_token),
                    },
                ),
            ),
            create_token,
        )
        handle = results.get("session_handle")
        if handle is None:
            raise RuntimeError("portal did not return a GlobalShortcuts session")
        self.session_handle = str(handle.unpack() if hasattr(handle, "unpack") else handle)
        if not self.session_handle.startswith("/"):
            raise RuntimeError("portal did not return a GlobalShortcuts session")
        description = self.GLib.Variant("s", "Hold to dictate with ClassScribe")
        bind_token = "request_" + uuid4().hex
        self._request(
            "BindShortcuts",
            self.GLib.Variant(
                "(oa(sa{sv})sa{sv})",
                (
                    self.session_handle,
                    [("classscribe-dictate", {"description": description})],
                    "",
                    {"handle_token": self.GLib.Variant("s", bind_token)},
                ),
            ),
            bind_token,
        )
        self.connection.signal_subscribe(
            self.PORTAL_NAME,
            self.INTERFACE,
            "Activated",
            self.PORTAL_PATH,
            None,
            self.Gio.DBusSignalFlags.NONE,
            self._signal,
            None,
        )
        self.connection.signal_subscribe(
            self.PORTAL_NAME,
            self.INTERFACE,
            "Deactivated",
            self.PORTAL_PATH,
            None,
            self.Gio.DBusSignalFlags.NONE,
            self._signal,
            None,
        )

    def run(self) -> None:
        self.loop.run()

    def _request(self, method: str, parameters: Any, handle_token: str) -> dict[str, Any]:
        sender = str(self.connection.get_unique_name()).lstrip(":").replace(".", "_")
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"
        completed: dict[str, Any] = {}
        waiting = self.GLib.MainLoop()

        def response(
            _connection: Any,
            _sender: str,
            _path: str,
            _interface: str,
            _signal: str,
            parameters_value: Any,
            _data: object,
        ) -> None:
            code, values = parameters_value.unpack()
            completed["code"] = int(code)
            completed["values"] = values
            waiting.quit()

        subscription = self.connection.signal_subscribe(
            self.PORTAL_NAME,
            "org.freedesktop.portal.Request",
            "Response",
            request_path,
            None,
            self.Gio.DBusSignalFlags.NONE,
            response,
            None,
        )
        timeout: int | None = None
        try:
            reply = self.connection.call_sync(
                self.PORTAL_NAME,
                self.PORTAL_PATH,
                self.INTERFACE,
                method,
                parameters,
                self.GLib.VariantType("(o)"),
                self.Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            if str(reply.unpack()[0]) != request_path:
                raise RuntimeError("portal did not honor the bounded request handle token")
            if "code" not in completed:
                timeout = self.GLib.timeout_add(5000, waiting.quit)
                waiting.run()
        finally:
            if timeout is not None and "code" in completed:
                self.GLib.source_remove(timeout)
            self.connection.signal_unsubscribe(subscription)
        if completed.get("code") != 0:
            raise RuntimeError(
                f"GlobalShortcuts request failed: {completed.get('code', 'timeout')}"
            )
        values = completed.get("values")
        if not isinstance(values, dict):
            raise RuntimeError("GlobalShortcuts returned invalid results")
        return values

    def _signal(
        self,
        _connection: Any,
        _sender: str,
        _path: str,
        _interface: str,
        signal: str,
        parameters: Any,
        _data: object,
    ) -> None:
        _session, shortcut, _timestamp, _options = parameters.unpack()
        if shortcut != "classscribe-dictate":
            return
        callback = self._activated if signal == "Activated" else self._deactivated
        if callback is not None:
            callback()


def run_portal(socket_path: Path) -> bool:
    try:
        backend: ShortcutBackend = GioGlobalShortcuts()
    except Exception as exc:
        detail = type(exc).__name__

        class UnavailablePortal:
            def register(
                self, activated: Callable[[], None], deactivated: Callable[[], None]
            ) -> None:
                del activated, deactivated
                raise RuntimeError(f"portal initialization failed: {detail}")

            def run(self) -> None:
                return

        backend = UnavailablePortal()
    controller = PortalController(
        backend,
        DictationIPCClient(socket_path),
        status_path=socket_path.parent / "portal-status.json",
    )
    if not controller.register():
        return False
    backend.run()
    return True
