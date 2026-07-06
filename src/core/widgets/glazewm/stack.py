import logging
from typing import override

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QShowEvent, QWheelEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget
from win32api import GetMonitorInfo, MonitorFromWindow

from core.utils.utilities import refresh_widget_style
from core.validation.widgets.glazewm.stack import GlazewmStackConfig
from core.widgets.base import BaseWidget
from core.widgets.services.glazewm.client import FocusedContainer, GlazewmClient, Stack, StackWindow

logger = logging.getLogger("glazewm_stack")


class GlazewmStackButton(QPushButton):
    """A single tab of the focused stack, representing one stacked window."""

    def __init__(
        self,
        window: StackWindow,
        index: int,
        client: GlazewmClient,
        config: GlazewmStackConfig,
        parent_widget: QWidget | None = None,
    ):
        super().__init__(parent=parent_widget)
        self.glazewm_client = client
        self.config = config
        self.window = window
        self.index = index
        self.setSizePolicy(QSizePolicy.Policy.Fixed, self.sizePolicy().verticalPolicy())
        self.clicked.connect(self._focus_window)  # type: ignore
        self.update_button()

    def set_window(self, window: StackWindow, index: int):
        """Rebinds the button to a (possibly updated) window and re-renders it."""
        self.window = window
        self.index = index
        self.update_button()

    def update_button(self):
        self._update_label()
        button_class = "stack-btn focused" if self.window.has_focus else "stack-btn"
        self.setProperty("class", button_class)
        refresh_widget_style(self)

    def _truncate_title(self, title: str) -> str:
        max_length = self.config.max_title_length
        if max_length and len(title) > max_length:
            ellipsis = self.config.truncate_ellipsis
            keep = max(0, max_length - len(ellipsis))
            return title[:keep] + ellipsis
        return title

    def _update_label(self):
        replacements = {
            "title": self._truncate_title(self.window.title or ""),
            "process_name": self.window.process_name or "",
            "index": self.index + 1,
        }
        self.setText(self.config.label.format_map(replacements))

    @pyqtSlot()
    def _focus_window(self):
        self.glazewm_client.focus_container(self.window.id)


class GlazewmStackWidget(BaseWidget):
    validation_schema = GlazewmStackConfig

    def __init__(self, config: GlazewmStackConfig):
        super().__init__(class_name="glazewm-stack")
        self.config = config
        self._buttons: list[GlazewmStackButton] = []
        self._current_stack_id: str | None = None
        self._device_name: str | None = None

        self.stack_container_layout = QHBoxLayout()
        self.stack_container_layout.setSpacing(0)
        self.stack_container_layout.setContentsMargins(0, 0, 0, 0)

        self.stack_container = QFrame(self)
        self.stack_container.setLayout(self.stack_container_layout)
        self.stack_container.setProperty("class", "widget-container")
        self.stack_container.setVisible(False)

        self.offline_text = QLabel(self.config.offline_label, self)
        self.offline_text.setProperty("class", "offline-status")
        self.offline_text.setVisible(False)

        self.widget_layout.addWidget(self.offline_text)
        self.widget_layout.addWidget(self.stack_container)

        # A stack focus event is derived from `focus_changed`, so subscribing to
        # both lets us track the focused stack and detect when focus leaves it.
        # The payloads are self-contained, so the coarse re-query is disabled.
        self.glazewm_client = GlazewmClient(
            self.config.glazewm_server_uri,
            ["sub -e stack_focus_changed focus_changed"],
            refresh_on_event=False,
        )
        self.glazewm_client.glazewm_connection_status.connect(self._update_connection_status)  # type: ignore
        self.glazewm_client.stack_focus_processed.connect(self._on_stack_focus)  # type: ignore
        self.glazewm_client.focus_changed_processed.connect(self._on_focus_changed)  # type: ignore

    @override
    def showEvent(self, a0: QShowEvent | None):
        super().showEvent(a0)
        if self.config.monitor_exclusive and self._device_name is None:
            self._device_name = self._resolve_device_name()
        self.glazewm_client.connect()

    def _resolve_device_name(self) -> str | None:
        """Resolves the GlazeWM device name (e.g. `\\\\.\\DISPLAY1`) of this bar's monitor."""
        try:
            monitor = MonitorFromWindow(int(QWidget.winId(self)))
            return GetMonitorInfo(monitor).get("Device")
        except Exception:
            logger.debug("Failed to resolve monitor device name", exc_info=True)
            return None

    @pyqtSlot(bool)
    def _update_connection_status(self, status: bool):
        if not status:
            self._clear()
        self.offline_text.setVisible(not status if not self.config.hide_if_offline else False)

    @pyqtSlot(object)
    def _on_stack_focus(self, stack: Stack):
        if (
            self.config.monitor_exclusive
            and self._device_name is not None
            and stack.device_name != self._device_name
        ):
            return
        self._render(stack)

    @pyqtSlot(object)
    def _on_focus_changed(self, focused: FocusedContainer):
        # Focus moved to a container outside the tracked stack -> stop showing it.
        if self._current_stack_id is not None and focused.parent_id != self._current_stack_id:
            self._clear()

    def _render(self, stack: Stack):
        self._current_stack_id = stack.id
        windows = stack.windows

        # Grow the button pool to match the number of stacked windows.
        while len(self._buttons) < len(windows):
            index = len(self._buttons)
            btn = GlazewmStackButton(
                windows[index],
                index,
                self.glazewm_client,
                self.config,
                parent_widget=self,
            )
            self.stack_container_layout.addWidget(btn)
            self._buttons.append(btn)

        # Shrink it if the stack now holds fewer windows.
        while len(self._buttons) > len(windows):
            btn = self._buttons.pop()
            self.stack_container_layout.removeWidget(btn)
            btn.deleteLater()

        for index, (btn, window) in enumerate(zip(self._buttons, windows)):
            btn.set_window(window, index)

        self.offline_text.setVisible(False)
        self.stack_container.setVisible(len(windows) > 0)

    def _clear(self):
        self._current_stack_id = None
        for btn in self._buttons:
            self.stack_container_layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons = []
        self.stack_container.setVisible(False)

    def _focused_index(self) -> int | None:
        """Returns the index of the currently focused window in the stack, if any."""
        for index, btn in enumerate(self._buttons):
            if btn.window.has_focus:
                return index
        return None

    def wheelEvent(self, event: QWheelEvent):
        if not self.config.enable_scroll_switching or len(self._buttons) < 2:
            return
        direction = event.angleDelta().y()
        if self.config.reverse_scroll_direction:
            direction = -direction
        if direction == 0:
            return

        current = self._focused_index()
        if current is None:
            current = 0
        step = 1 if direction < 0 else -1
        target = self._buttons[(current + step) % len(self._buttons)]
        self.glazewm_client.focus_container(target.window.id)
