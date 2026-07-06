from core.validation.widgets.base_model import CustomBaseModel


class GlazewmStackConfig(CustomBaseModel):
    glazewm_server_uri: str = "ws://localhost:6123"
    offline_label: str = "GlazeWM Offline"
    hide_if_offline: bool = False
    # Only show the stack when it lives on this widget's monitor. A stack is
    # focused on a single monitor at a time, so disabling this mirrors the
    # focused stack across every bar.
    monitor_exclusive: bool = True
    # Per-window label. Supported placeholders: {title}, {process_name}, {index}.
    label: str = "{title}"
    # Titles longer than this are truncated (0 disables truncation).
    max_title_length: int = 20
    truncate_ellipsis: str = "..."
    enable_scroll_switching: bool = True
    reverse_scroll_direction: bool = False
