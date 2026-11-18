"""
Настройки приложения.
"""

import sys
import os
from pathlib import Path

# -----подсказка------------
# Определяем, запущен ли код как .exe или как обычный python-скрипт.
# Это нужно, чтобы правильно находить пути к data/ и статике.
if getattr(sys, 'frozen', False):
    # Если это скомпилированный exe
    BASE_DIR = Path(sys.executable).parent
else:
    # Если запуск через python app.py
    BASE_DIR = Path(__file__).parent


class Config:
    # Метаданные проекта (используются в шапке, футере, About)
    APP_NAME = "IPTV OVERLAY + MPTS"
    VERSION = "1.1.0"
    AUTHOR = "Andrey Geben"
    AUTHOR_URL = "https://github.com/andrey-geben"
    AUTHOR_EMAIL = ""
    LICENSE_NAME = "MIT"
    PROJECT_URL = "https://github.com/andrey-geben/IPTV_OVERLAY_MPTS"
    # Шрифты (для overlay)
    FONTS_DIR = BASE_DIR / "static" / "fonts"
    DEFAULT_OVERLAY_FONT = FONTS_DIR / "DejaVuSans.ttf"
    # Flask
    SECRET_KEY = "dev-secret-change-me-please"
    DEBUG = os.environ.get("IPTV_DEBUG", "0") == "1"

    # Пути к данным
    PLAYLISTS_DIR = BASE_DIR / "data" / "playlists"
    LOGS_DIR = BASE_DIR / "data" / "logs"
    HLS_DIR = BASE_DIR / "data" / "hls"
    OVERLAY_CONFIG_PATH = BASE_DIR / "data" / "overlay_config.json"
    MPTS_CONFIG_PATH = BASE_DIR / "data" / "mpts_config.json"
    CHANNELS_CONFIG_PATH = BASE_DIR / "data" / "channels.json"
    CHANNELS_DIR = BASE_DIR / "data" / "channels"

    # HLS-сервер для MPTS: живёт отдельно от Flask, поэтому рестарт панели
    # больше не рвёт эфир. Порт слушается только на localhost.
    HLS_HTTP_PORT = int(os.environ.get("IPTV_HLS_PORT", "8099"))

    # TTL multicast-пакетов MPTS. В одной L2-сети с PBI достаточно 1,
    # но 8 безопаснее: работает и через L3-маршрутизатор с PIM.
    MPTS_TTL = int(os.environ.get("IPTV_MPTS_TTL", "8"))

    # Веб-панель: 0.0.0.0 — чтобы оператор мог зайти удалённо (см. README).
    WEB_HOST = os.environ.get("IPTV_HOST", "0.0.0.0")
    USERS_FILE = BASE_DIR / "data" / "users.json"

    # Сессии
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8