import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Any, cast

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket
from PyQt6.QtWebSockets import QWebSocket

logger = logging.getLogger("glazewm_client")


@dataclass
class Window:
    id: str
    title: str
    handle: int
    class_name: str
    process_name: str
    display_state: str
    is_floating: bool


@dataclass
class Workspace:
    name: str
    display_name: str
    focus: bool = False  # Global focus - only ONE workspace has this True
    is_displayed: bool = False
    num_windows: int = 0
    windows: list[Window] = field(default_factory=list)


@dataclass
class Monitor:
    name: str
    hwnd: int
    workspaces: list[Workspace]


@dataclass
class BindingMode:
    name: str
    display_name: str


@dataclass
class StackWindow:
    id: str
    title: str
    process_name: str
    has_focus: bool


@dataclass
class Stack:
    id: str
    device_name: str | None
    windows: list[StackWindow] = field(default_factory=list)


@dataclass
class FocusedContainer:
    id: str | None
    parent_id: str | None
    type: str


class MessageType(StrEnum):
    EVENT_SUBSCRIPTION = auto()
    CLIENT_RESPONSE = auto()
    HEAR_BROADCAST = auto()


class QueryType(StrEnum):
    MONITORS = "query monitors"
    TILING_DIRECTION = "query tiling-direction"
    BINDING_MODES = "query binding-modes"


class EventType(StrEnum):
    STACK_FOCUS_CHANGED = "stack_focus_changed"
    FOCUS_CHANGED = "focus_changed"


class TilingDirection(StrEnum):
    HORIZONTAL = auto()
    VERTICAL = auto()


class GlazewmClient(QObject):
    workspaces_data_processed = pyqtSignal(list)
    tiling_direction_processed = pyqtSignal(TilingDirection)
    binding_mode_changed = pyqtSignal(BindingMode)
    stack_focus_processed = pyqtSignal(object)
    focus_changed_processed = pyqtSignal(object)
    hear_broadcast_processed = pyqtSignal(str)
    glazewm_connection_status = pyqtSignal(bool)

    def __init__(
        self,
        uri: str,
        initial_messages: list[str] | None = None,
        reconnect_interval: int = 4000,
        refresh_on_event: bool = True,
    ):
        super().__init__()
        self.initial_messages = initial_messages if initial_messages else []
        # When True, every subscribed event triggers a coarse re-query of
        # monitors/tiling-direction/binding-modes. Consumers that parse event
        # payloads directly (e.g. the stack widget) can disable this.
        self._refresh_on_event = refresh_on_event

        self._uri = QUrl(uri)
        self._websocket = QWebSocket()
        self._websocket.connected.connect(self._on_connected)  # type: ignore
        self._websocket.textMessageReceived.connect(self._handle_message)  # type: ignore
        self._websocket.stateChanged.connect(self._on_state_changed)  # type: ignore
        self._websocket.errorOccurred.connect(self._on_error)  # type: ignore

        self._reconnect_timer = QTimer()
        self._reconnect_timer.setInterval(reconnect_interval)
        self._reconnect_timer.timeout.connect(self.connect)  # type: ignore

    def activate_workspace(self, workspace_name: str):
        self._websocket.sendTextMessage(f"command focus --workspace {workspace_name}")

    def toggle_tiling_direction(self):
        self._websocket.sendTextMessage("command toggle-tiling-direction")

    def disable_binding_mode(self, binding_mode_name: str):
        self._websocket.sendTextMessage(f"command wm-disable-binding-mode --name {binding_mode_name}")

    def enable_binding_mode(self, binding_mode_name: str):
        self._websocket.sendTextMessage(f"command wm-enable-binding-mode --name {binding_mode_name}")

    def focus_container(self, container_id: str):
        self._websocket.sendTextMessage(f"command focus --container-id {container_id}")

    def focus_next_workspace(self):
        self._websocket.sendTextMessage("command focus --next-active-workspace-on-monitor")

    def focus_prev_workspace(self):
        self._websocket.sendTextMessage("command focus --prev-active-workspace-on-monitor")

    def focus_next_workspace_global(self):
        self._websocket.sendTextMessage("command focus --next-active-workspace")

    def focus_prev_workspace_global(self):
        self._websocket.sendTextMessage("command focus --prev-active-workspace")

    def connect(self):
        if self._websocket.state() == QAbstractSocket.SocketState.ConnectedState:
            return
        logger.debug("Connecting to %s", self._uri.toString())
        self._websocket.open(self._uri)

    def close(self):
        """Stops reconnection and closes the socket (used on shutdown/reload)."""
        self._reconnect_timer.stop()
        self._websocket.close()

    def _on_connected(self) -> None:
        logger.debug("Connected to %s", self._uri.toString())
        for message in self.initial_messages:
            logger.debug("Sent initial message: %s", message)
            self._websocket.sendTextMessage(message)

        # Stop reconnect timer
        self._reconnect_timer.stop()

    def _on_state_changed(self, state: QAbstractSocket.SocketState):
        logger.debug("WebSocket state changed: %s", state)
        self.glazewm_connection_status.emit(state == QAbstractSocket.SocketState.ConnectedState)

    def _on_error(self, error: QAbstractSocket.SocketError) -> None:
        logger.warning("WebSocket error: %s. Reconnecting...", error)
        self._reconnect_timer.start()

    def _handle_message(self, message: str):
        try:
            response = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Received invalid JSON data.")
            return

        if response.get("messageType") == MessageType.HEAR_BROADCAST:
            # A `hear` broadcast is an internally-tagged message, so its payload
            # (`word`, `subscriptionId`) sits alongside `messageType` rather than
            # nested under `data` like the query/event responses.
            word = response.get("word")
            if isinstance(word, str) and word:
                self.hear_broadcast_processed.emit(word)
            return

        if response.get("messageType") == MessageType.EVENT_SUBSCRIPTION:
            self._handle_event(response.get("data"))
        elif response.get("messageType") == MessageType.CLIENT_RESPONSE:
            raw_data: Any = response.get("data")
            if not isinstance(raw_data, dict):
                logger.warning("Expected 'data' to be a dict, got %s", type(raw_data).__name__)
                return
            data = cast(dict[str, Any], raw_data)
            if response.get("clientMessage") == QueryType.MONITORS:
                monitors = data.get("monitors", [])
                if monitors is None:
                    logger.warning("Expected 'monitors' to be a list, got None")
                    return
                self.workspaces_data_processed.emit(self._process_workspaces(monitors))
            elif response.get("clientMessage") == QueryType.TILING_DIRECTION:
                tiling_direction = TilingDirection(data.get("tilingDirection", TilingDirection.HORIZONTAL))
                self.tiling_direction_processed.emit(tiling_direction)
            elif response.get("clientMessage") == QueryType.BINDING_MODES:
                binding_modes = data.get("bindingModes", [])
                if binding_modes is None:
                    logger.warning("Expected 'bindingModes' to be a list, got %s", type(binding_modes).__name__)
                    return
                self.binding_mode_changed.emit(self._process_binding_modes(binding_modes))

    def _handle_event(self, data: Any):
        """Dispatches a subscribed WM event to the appropriate handler.

        Stack focus events carry their full payload, so they are parsed and
        emitted directly. Focus changes are parsed and emitted as well. Unless
        `refresh_on_event` is disabled, non-stack events also trigger a coarse
        re-query of monitor/tiling/binding state (the original refresh scheme).
        """
        event_type = data.get("eventType") if isinstance(data, dict) else None

        if event_type == EventType.STACK_FOCUS_CHANGED:
            stack = self._process_stack(data.get("stackContainer"))
            if stack is not None:
                self.stack_focus_processed.emit(stack)
            return

        if event_type == EventType.FOCUS_CHANGED:
            focused = self._process_focus(data.get("focusedContainer"))
            if focused is not None:
                self.focus_changed_processed.emit(focused)

        if self._refresh_on_event:
            self._websocket.sendTextMessage(QueryType.MONITORS)
            self._websocket.sendTextMessage(QueryType.TILING_DIRECTION)
            self._websocket.sendTextMessage(QueryType.BINDING_MODES)

    def _process_stack(self, data: Any) -> Stack | None:
        """Parses a `stackContainer` event payload into a `Stack`."""
        if not isinstance(data, dict):
            logger.warning("Expected 'stackContainer' to be a dict, got %s", type(data).__name__)
            return None
        stack_id: str | None = data.get("id")
        if not stack_id:
            logger.warning("Stack container is missing an id")
            return None
        return Stack(
            id=stack_id,
            device_name=data.get("deviceName"),
            windows=self._read_stack_windows(data),
        )

    def _read_stack_windows(self, parent: dict[str, Any]) -> list[StackWindow]:
        """Collects the window descendants of a stack container in order."""
        windows: list[StackWindow] = []
        for child in parent.get("children", []):
            child_type = child.get("type")
            if child_type == "window":
                windows.append(
                    StackWindow(
                        id=child.get("id", ""),
                        title=child.get("title", ""),
                        process_name=child.get("processName", ""),
                        has_focus=child.get("hasFocus", False),
                    )
                )
            elif child_type == "split":
                windows.extend(self._read_stack_windows(child))
        return windows

    def _process_focus(self, data: Any) -> FocusedContainer | None:
        """Parses a `focusedContainer` event payload into a `FocusedContainer`."""
        if not isinstance(data, dict):
            logger.warning("Expected 'focusedContainer' to be a dict, got %s", type(data).__name__)
            return None
        return FocusedContainer(
            id=data.get("id"),
            parent_id=data.get("parentId"),
            type=data.get("type", ""),
        )

    def _process_workspaces(self, data: list[dict[str, Any]]) -> list[Monitor]:
        monitors: list[Monitor] = []
        for mon in data:
            monitor_name: str | None = mon.get("hardwareId")
            handle: int | None = mon.get("handle")
            if not handle:
                logger.warning("Monitor handle not found")
                continue
            if not monitor_name:
                monitor_name = f"Unknown_{handle}"
            workspaces_data = [
                Workspace(
                    name=child.get("name", ""),
                    display_name=child.get("displayName", ""),
                    is_displayed=child.get("isDisplayed", False),
                    focus=child.get("hasFocus", False),
                    num_windows=len(child.get("children", [])),
                    windows=self._read_windows(child),
                )
                for child in mon.get("children", [])
                if child.get("type") == "workspace"
            ]
            monitors.append(
                Monitor(
                    name=monitor_name,
                    hwnd=handle,
                    workspaces=workspaces_data,
                )
            )
        return monitors

    def _process_binding_modes(self, data: list[dict[str, Any]]) -> BindingMode:
        if len(data) == 0:
            return BindingMode(name=None, display_name=None)

        return BindingMode(
            name=data[0].get("name", None),
            display_name=data[0].get("displayName", None),
        )

    def _read_windows(self, parent):
        windows = []
        for child in parent.get("children", []):
            if child.get("type") == "window":
                windows.append(
                    Window(
                        id=child.get("id"),
                        title=child.get("title"),
                        handle=child.get("handle"),
                        class_name=child.get("className"),
                        process_name=child.get("processName"),
                        display_state=child.get("displayState"),
                        is_floating=child.get("state").get("type") == "floating",
                    )
                )
            elif child.get("type") == "split":
                windows.extend(self._read_windows(child))
        return windows
