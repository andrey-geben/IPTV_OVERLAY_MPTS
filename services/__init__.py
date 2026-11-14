"""Пакет сервисных модулей."""

from .m3u_parser import parse_m3u, parse_m3u_file
from .process_manager import ProcessManager
from .ffmpeg_builder import MptsChannel, MptsProfile, build_mpts_command, build_udp_url
from .overlay_models import OverlayText, TimeWindow, OverlayRule, OverlayConfig
from .overlay_storage import OverlayStorage
from .overlay_filter import build_drawtext_filter
from .overlay_engine import OverlayEngine
from .mpts_storage import MptsStorage, MptsGroup, MptsConfig
from .monitor import check_channel_alive, check_channels_parallel, read_progress_from_log
from .auth import AuthManager
from .channel_proxy import ChannelProxy

__all__ = [
    "parse_m3u", "parse_m3u_file", "ProcessManager",
    "MptsChannel", "MptsProfile", "build_mpts_command", "build_udp_url",
    "OverlayText", "TimeWindow", "OverlayRule", "OverlayConfig",
    "OverlayStorage", "build_drawtext_filter", "OverlayEngine",
    "MptsStorage", "MptsGroup", "MptsConfig","ChannelProxy",
    "check_channel_alive", "check_channels_parallel", "read_progress_from_log", "AuthManager",
]

from .monitor import (
    check_channel_alive, check_channels_parallel,
    validate_udp_stream, get_process_io,
    read_progress_from_log,
)

# ... и в __all__ добавь:
"validate_udp_stream", "get_process_io",