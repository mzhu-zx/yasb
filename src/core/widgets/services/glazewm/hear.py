"""GlazeWM `hear` pseudo-keybinding listener.

Subscribes to the GlazeWM IPC `hear` broadcast (`sub -e hear`). Each broadcast
carries a single word; when it matches `yasb-<x>` (or `yasb_<x>`), the matching
keybinding declared as `glazewm-<x>` on any widget is dispatched through the
same `handle_widget_hotkey` path as physical hotkeys — so screen targeting and
per-widget action routing behave identically.
"""

import logging
from contextlib import suppress

from PyQt6.QtCore import QObject, pyqtSlot

from core.utils.win32.hotkeys import (
    HotkeyBinding,
    HotkeyDispatcher,
    hear_word_suffix,
    parse_glazewm_pseudokey,
    resolve_binding_screen,
)
from core.widgets.services.glazewm.client import GlazewmClient

logger = logging.getLogger("glazewm_hear")


class GlazewmHearListener(QObject):
    """Routes GlazeWM `hear` words to `glazewm-*` pseudo-keybindings."""

    def __init__(
        self,
        bindings: list[HotkeyBinding],
        dispatcher: HotkeyDispatcher,
        bar_screens: set[str],
        server_uri: str,
    ) -> None:
        super().__init__()
        self._dispatcher = dispatcher
        self._bar_screens = bar_screens

        # Map normalized suffix -> binding. On conflict the last declaration wins,
        # mirroring the physical-hotkey conflict behavior.
        self._bindings: dict[str, HotkeyBinding] = {}
        for binding in bindings:
            suffix = parse_glazewm_pseudokey(binding.hotkey)
            if suffix is not None:
                self._bindings[suffix] = binding

        self._client = GlazewmClient(server_uri, ["sub -e hear"], refresh_on_event=False)
        self._client.hear_broadcast_processed.connect(self._on_word)  # type: ignore

    def start(self) -> None:
        self._client.connect()

    def stop(self) -> None:
        with suppress(Exception):
            self._client.hear_broadcast_processed.disconnect(self._on_word)  # type: ignore
        with suppress(Exception):
            self._client.close()

    @pyqtSlot(str)
    def _on_word(self, word: str) -> None:
        suffix = hear_word_suffix(word)
        if suffix is None:
            return
        binding = self._bindings.get(suffix)
        if binding is None:
            logger.debug("No glazewm keybinding registered for hear word '%s'", word)
            return
        screen_name = resolve_binding_screen(binding, self._bar_screens)
        self._dispatcher.dispatch(binding.widget_name, binding.action, screen_name)
