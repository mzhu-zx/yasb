from typing import Any, Literal

from core.validation.bar import BarConfig
from core.validation.widgets.base_model import CustomBaseModel


class TooltipBlurEffect(CustomBaseModel):
    enabled: bool = False
    dark_mode: bool = False
    round_corners: bool = False
    round_corners_type: Literal["normal", "small"] = "normal"
    border_color: str = "None"


class TooltipOptions(CustomBaseModel):
    offset: int = 5
    blur_effect: TooltipBlurEffect = TooltipBlurEffect()


class GlazeWMConfig(CustomBaseModel):
    start_command: str | None = None
    stop_command: str | None = None
    reload_command: str | None = None
    # WebSocket URI of the GlazeWM IPC server, used by the `hear` pseudo-key
    # listener to receive `yasb-*` broadcast words.
    server_uri: str = "ws://localhost:6123"


class YasbConfig(CustomBaseModel):
    watch_config: bool = True
    watch_stylesheet: bool = True
    debug: bool = False
    update_check: bool = True
    show_systray: bool = True
    system_colors: bool = False
    tooltip: TooltipOptions = TooltipOptions()
    glazewm: GlazeWMConfig = GlazeWMConfig()
    bars: dict[str, BarConfig] = {"yasb-bar": BarConfig()}
    widgets: dict[str, str | dict[str, Any]] = {}
