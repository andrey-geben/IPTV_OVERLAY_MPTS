"""
Настройки приложения.
"""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    # Метаданные проекта (используются в шапке, футере, About)
    APP_NAME = "IPTV OVERLAY + MPTS"
    VERSION = "1.0.0"
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
    DEBUG = True

    # Пути к данным
    PLAYLISTS_DIR = BASE_DIR / "data" / "playlists"
    LOGS_DIR = BASE_DIR / "data" / "logs"
    HLS_DIR = BASE_DIR / "data" / "hls"
    OVERLAY_CONFIG_PATH = BASE_DIR / "data" / "overlay_config.json"
    MPTS_CONFIG_PATH = BASE_DIR / "data" / "mpts_config.json"
    USERS_FILE = BASE_DIR / "data" / "users.json"

    # Сессии
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8