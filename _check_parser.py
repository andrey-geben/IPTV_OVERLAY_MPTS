"""
Временный файл для ручной проверки парсера M3U.
Запускается так:  python _check_parser.py
Когда проверка больше не нужна — файл можно удалить.
Имя начинается с подчёркивания, чтобы визуально отличать его
от «настоящих» модулей проекта.
"""

from services.m3u_parser import parse_m3u


SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="1" group-title="Кино",Первый канал
http://example.com/1
#EXTINF:-1 group-title="Спорт",Матч ТВ
http://example.com/2
"""


if __name__ == "__main__":
    channels = parse_m3u(SAMPLE)
    for ch in channels:
        print(ch)