"""PyGObject runtime kept behind the GI-independent engine controller."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from classscribe_protocol import DictationIPCClient

from classscribe_ibus_engine.controller import EngineController


def run_ibus(socket_path: Path) -> None:
    try:
        import gi  # type: ignore[import-not-found]

        gi.require_version("IBus", "1.0")
        from gi.repository import GLib, IBus  # type: ignore[import-not-found]
    except (ImportError, ValueError) as exc:
        raise RuntimeError("PyGObject with IBus 1.0 is unavailable") from exc

    IBus.init()

    class Bridge:
        def __init__(self, engine: Any) -> None:
            self.engine = engine

        def update_preedit(self, text: str, stable_characters: int) -> None:
            value = IBus.Text.new_from_string(text)
            if text and stable_characters < len(text):
                attributes = IBus.AttrList()
                attributes.append(
                    IBus.Attribute.new(
                        IBus.AttrType.UNDERLINE,
                        IBus.AttrUnderline.SINGLE,
                        max(0, stable_characters),
                        len(text),
                    )
                )
                value.set_attributes(attributes)
            self.engine.update_preedit_text(value, len(text), bool(text))

        def commit(self, text: str) -> None:
            if text:
                self.engine.commit_text(IBus.Text.new_from_string(text))

        def update_lookup(self, candidates: tuple[str, ...], visible: bool) -> None:
            table = IBus.LookupTable.new(9, 0, True, True)
            for candidate in candidates:
                table.append_candidate(IBus.Text.new_from_string(candidate))
            self.engine.update_lookup_table(table, visible and bool(candidates))

        def show_status(self, message: str) -> None:
            self.engine.update_auxiliary_text(IBus.Text.new_from_string(message), bool(message))

        def update_properties(self, values: Mapping[str, str | bool]) -> None:
            self.engine.apply_properties(values)

        def update_available_models(self, model_ids: tuple[str, ...]) -> None:
            self.engine.set_available_models(model_ids)

    class ClassScribeEngine(IBus.Engine):  # type: ignore[misc]
        def __init__(self, connection: Any, object_path: str) -> None:
            super().__init__(connection=connection, object_path=object_path)
            self._properties: dict[tuple[str, str], Any] = {}
            self._model_ids: tuple[str, ...] = ("auto_best",)
            self.controller = EngineController(DictationIPCClient(socket_path), Bridge(self))
            self._property_values: dict[str, str | bool] = {}
            self._register_properties(IBus)
            GLib.timeout_add(75, self.controller.poll)

        def do_process_key_event(self, keyval: int, keycode: int, state: int) -> bool:
            del keycode
            released = bool(state & int(IBus.ModifierType.RELEASE_MASK))
            return self.controller.handle_key(keyval, released=released)

        def do_property_activate(self, prop_name: str, prop_state: int) -> None:
            del prop_state
            try:
                group, value = prop_name.split(":", maxsplit=1)
                if group == "interim":
                    self.controller.set_property("show_interim", value == "show")
                else:
                    self.controller.set_property(group, value)
                self._property_values[group] = value
            except (RuntimeError, ValueError):
                self.update_auxiliary_text(
                    IBus.Text.new_from_string("Setting changes require an idle dictation"), True
                )

        def apply_properties(self, values: Mapping[str, str | bool]) -> None:
            for group, raw in values.items():
                if group == "show_interim":
                    group = "interim"
                value = "show" if raw is True else "hide" if raw is False else str(raw)
                for (candidate_group, candidate_value), prop in self._properties.items():
                    if candidate_group != group:
                        continue
                    prop.set_state(
                        IBus.PropState.CHECKED
                        if candidate_value == value
                        else IBus.PropState.UNCHECKED
                    )
                    self.update_property(prop)

        def set_available_models(self, model_ids: tuple[str, ...]) -> None:
            values = ("auto_best", *tuple(dict.fromkeys(model_ids)))
            if values == self._model_ids:
                return
            self._model_ids = values
            self._properties.clear()
            self._register_properties(IBus)

        def _register_properties(self, module: Any) -> None:
            root = module.PropList()
            groups = {
                "language": (("zh", "中文"), ("ja", "日本語"), ("en", "English"), ("auto", "Auto")),
                "confirmation": (("fast", "快速"), ("balanced", "平衡"), ("accuracy", "最高精度")),
                "model_id": tuple(
                    (value, "自动最佳" if value == "auto_best" else value)
                    for value in self._model_ids
                ),
                "activation": (("hold", "按住说话"), ("toggle", "单击开始/结束")),
                "interim": (("show", "显示临时结果"), ("hide", "隐藏临时结果")),
                "punctuation": (
                    ("automatic", "自动标点"),
                    ("sentence_end", "仅句末"),
                    ("off", "关闭标点"),
                ),
            }
            for group, options in groups.items():
                submenu = module.PropList()
                for value, label in options:
                    prop = module.Property(
                        key=f"{group}:{value}",
                        prop_type=module.PropType.RADIO,
                        label=module.Text.new_from_string(label),
                        sensitive=True,
                        visible=True,
                        state=module.PropState.UNCHECKED,
                    )
                    self._properties[(group, value)] = prop
                    submenu.append(prop)
                root.append(
                    module.Property(
                        key=f"menu:{group}",
                        prop_type=module.PropType.MENU,
                        label=module.Text.new_from_string(options[0][1]),
                        sensitive=True,
                        visible=True,
                        sub_props=submenu,
                    )
                )
            self.register_properties(root)
            self.apply_properties(self.controller.config.as_dict())

    bus = IBus.Bus()
    if not bus.is_connected():
        raise RuntimeError("cannot connect to the IBus daemon")
    factory = IBus.Factory.new(bus.get_connection())
    factory.add_engine("classscribe-voice", ClassScribeEngine)
    bus.request_name("org.freedesktop.IBus.ClassScribe", 0)
    GLib.MainLoop().run()
