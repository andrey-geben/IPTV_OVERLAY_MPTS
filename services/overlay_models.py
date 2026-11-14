"""
Модели данных для Overlay-подсистемы.
"""

from dataclasses import dataclass, field
from pathlib import Path


# Абсолютный путь к дефолтному шрифту.
# Вычисляется от самого файла overlay_models.py — работает независимо от cwd.
_DEFAULT_FONT = str(
    Path(__file__).resolve().parent.parent / "static" / "fonts" / "DejaVuSans.ttf"
)

@dataclass
class OverlayText:
    text: str = "Бегущая строка"
    # seguiemj.ttf поддерживает эмодзи (в Windows)
    font_path: str = _DEFAULT_FONT
    font_size: int = 36
    font_color: str = "white"
    font_alpha: float = 1.0
    speed: int = 100
    repeat_period_sec: int = 10
    box_enabled: bool = True
    box_color: str = "black"
    box_alpha: float = 0.7
    y_offset: int = 50


@dataclass
class TimeWindow:
    days: list[int] = field(default_factory=lambda: list(range(7)))
    start_hhmm: str = "00:00"
    end_hhmm: str = "23:59"


@dataclass
class OverlayRule:
    id: str
    name: str
    text: OverlayText = field(default_factory=OverlayText)
    window: TimeWindow = field(default_factory=TimeWindow)
    channel_urls: list[str] = field(default_factory=list)
    enabled: bool = True
    priority: int = 0

    # НОВОЕ: Даты действия (YYYY-MM-DD, пусто = без ограничений)
    start_date: str = ""
    end_date: str = ""
    # НОВОЕ: Если True — год игнорируется (для 9 Мая, Нового Года)
    is_yearly: bool = False


@dataclass
class OverlayConfig:
    rules: list[OverlayRule] = field(default_factory=list)
    source_playlist: str = ""
    working_channel_urls: list[str] = field(default_factory=list)
    hls_time_sec: int = 2
    hls_list_size: int = 5
    public_base_url: str = "http://127.0.0.1:5000"