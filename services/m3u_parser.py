"""
Парсер M3U-плейлистов.

Чистая логика без Flask и GUI. Принимает строку с содержимым M3U
(или путь к файлу) и возвращает список каналов.
"""

from pathlib import Path
from typing import TypedDict


class Channel(TypedDict):
    """
    Описание одного канала.
    TypedDict — это «словарь с известными ключами».
    Удобно для подсказок в PyCharm: channel["name"] подсветится,
    а channel["nmae"] — PyCharm подчеркнёт как ошибку.
    """
    name: str
    url: str
    group: str
    tvg_id: str


def parse_m3u(content: str) -> list[Channel]:
    """
    Парсит строку с содержимым M3U и возвращает список каналов.

    Формат M3U (упрощённо):
        #EXTM3U
        #EXTINF:-1 tvg-id="1" group-title="Кино",Название канала
        http://example.com/stream1

    Логика построчного разбора:
      1. Идём по строкам.
      2. Если строка начинается с "#EXTINF" — это «шапка» следующего канала.
         Запоминаем её во временную переменную pending_meta.
      3. Если строка начинается с "#" (но не #EXTINF) — это служебная
         директива, пока пропускаем.
      4. Если строка не пустая и не начинается с "#" — это URL потока.
         Берём сохранённую ранее «шапку» и создаём канал.
    """
    channels: list[Channel] = []
    pending_meta: str | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            pending_meta = line
            continue

        if line.startswith("#"):
            # #EXTM3U, #EXTVLCOPT и прочие служебные строки — пока игнорируем
            continue

        # Это URL. Если перед ним была шапка — создаём канал.
        if pending_meta is not None:
            channels.append(_build_channel(pending_meta, line))
            pending_meta = None

    return channels


def _build_channel(meta_line: str, url: str) -> Channel:
    """
    Собирает канал из «шапки» #EXTINF и URL.
    """
    name = ""
    # Название канала — часть после ПОСЛЕДНЕЙ запятой в строке #EXTINF.
    # (Известное ограничение: если group-title содержит запятую,
    #  разбор может ошибиться. Для реальных плейлистов встречается редко.)
    if "," in meta_line:
        name = meta_line.rsplit(",", 1)[1].strip()

    group = _extract_attr(meta_line, "group-title")
    tvg_id = _extract_attr(meta_line, "tvg-id")

    return Channel(
        name=name or "Без названия",
        url=url,
        group=group,
        tvg_id=tvg_id,
    )


def _extract_attr(meta_line: str, attr: str) -> str:
    """
    Достаёт значение атрибута из строки #EXTINF.
    Поддерживает два формата:
        attr="value"    ← стандартный
        attr=value      ← без кавычек (встречается в реальных плейлистах)
    Если атрибута нет — возвращает пустую строку.

    Проверка «словесной границы»: добавляем ведущий пробел, чтобы
    не поймать attr=X внутри другого слова (например, xtvg-id=X).
    В M3U атрибуты всегда идут после #EXTINF:duration через пробел.
    """
    padded = " " + meta_line

    # Формат 1: attr="value"
    marker = f' {attr}="'
    start = padded.find(marker)
    if start != -1:
        start += len(marker)
        end = padded.find('"', start)
        if end != -1:
            return padded[start:end]

    # Формат 2: attr=value (значение до пробела или запятой)
    marker = f' {attr}='
    start = padded.find(marker)
    if start != -1:
        start += len(marker)
        end = start
        while end < len(padded) and padded[end] not in ' ,':
            end += 1
        return padded[start:end]

    return ""


def parse_m3u_file(path: str | Path) -> list[Channel]:
    """Удобная обёртка: читает файл с диска и парсит его."""
    return parse_m3u(Path(path).read_text(encoding="utf-8"))