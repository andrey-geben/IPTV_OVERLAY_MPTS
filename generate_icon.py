from PIL import Image, ImageDraw


def create_icon():
    # Создаем базовый холст 512x512 (максимальный размер для ICO)
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Фон (темный, как в твоем UI)
    # Закругленный квадрат
    draw.rounded_rectangle([(20, 20), (492, 492)], radius=60, fill="#1A1C23", outline="#00A8FF", width=8)

    # 2. Экран (внутренняя часть)
    draw.rounded_rectangle([(60, 80), (452, 432)], radius=20, fill="#0A0B0E")

    # 3. Элементы трансляции (символ плей / видео)
    draw.polygon([(200, 180), (200, 280), (280, 230)], fill="#00A8FF")  # Треугольник Play

    # 4. Бегущая строка (полоса Overlay)
    # Синяя полоса, как на твоем скриншоте
    draw.rectangle([(60, 340), (452, 390)], fill="#040cfb", outline="#2A2E3D", width=2)

    # 5. Имитация текста на бегущей строке (белые штрихи)
    draw.rectangle([(100, 355), (180, 375)], fill="#FFFFFF")  # Слово 1
    draw.rectangle([(200, 355), (280, 375)], fill="#FFFFFF")  # Слово 2
    draw.rectangle([(300, 355), (380, 375)], fill="#FFFFFF")  # Слово 3

    # 6. Верхняя плашка (как в новостях)
    draw.rectangle([(60, 80), (200, 110)], fill="#00A8FF")
    draw.rectangle([(60, 120), (300, 150)], fill="#2A2E3D")

    # Сохраняем в формате ICO со всеми нужными размерами для Windows
    icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

    # Принудительно конвертируем в RGB для сохранения в ICO (ICO не поддерживает прозрачность в некоторых старых версиях Windows)
    img = img.convert("RGB")

    img.save("static/favicon.ico", format="ICO", sizes=icon_sizes)
    print("Иконка успешно создана: static/favicon.ico")


if __name__ == "__main__":
    create_icon()