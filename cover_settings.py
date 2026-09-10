"""Validated cover profile schema; old VEVO/EXPLICIT values remain the baseline."""
import math
import re

DEFAULTS = dict(
    style="vevo", vevo_wm_size=450, explicit_wm_size=300,
    explicit_blur=10, explicit_fg_size=820, explicit_quality="good", explicit_format="photo",
    resolution="1920x1080", fit="cover", focus_x=50, focus_y=50,
    background="spin", background_color="#101010", background_brightness=100,
    foreground_x=50, foreground_y=50, watermark_enabled="on", watermark_position="bottom_left",
    watermark_opacity=100, watermark_margin=30,
    brightness=100, contrast=100, saturation=100, sharpness=100,
    export_format="JPEG", jpeg_quality=98, delivery="file", filename="cover",
)
# label, choices or numeric bounds, group. All sizes are relative to a 1920x1080 canvas.
FIELDS = {
    "style": ("Стиль", {"vevo":"VEVO", "explicit":"EXPLICIT"}, "layout"),
    "resolution": ("Разрешение", {"1280x720":"1280×720", "1920x1080":"1920×1080", "2560x1440":"2560×1440"}, "export"),
    "fit": ("Вписывание VEVO", {"cover":"Заполнить с обрезкой", "contain":"Вписать целиком"}, "layout"),
    "focus_x": ("Центр обрезки X, %", (0,100), "layout"),
    "focus_y": ("Центр обрезки Y, %", (0,100), "layout"),
    "background": ("Фон EXPLICIT", {"spin":"Оригинальный Spin", "gaussian":"Gaussian blur", "solid":"Однотонный"}, "layout"),
    "background_color": ("Цвет фона / полей", "color", "layout"),
    "explicit_blur": ("Размытие (0 = выкл.)", (0,30), "layout"),
    "background_brightness": ("Яркость фона, %", (0,200), "layout"),
    "explicit_fg_size": ("Размер фото EXPLICIT, px", (100,1080), "layout"),
    "foreground_x": ("Позиция фото X, %", (0,100), "layout"),
    "foreground_y": ("Позиция фото Y, %", (0,100), "layout"),
    "watermark_enabled": ("Водяной знак", {"on":"Включён", "off":"Выключен"}, "watermark"),
    "vevo_wm_size": ("Размер VEVO, px", (10,550), "watermark"),
    "explicit_wm_size": ("Размер EXPLICIT, px", (10,550), "watermark"),
    "watermark_position": ("Положение знака", {"bottom_left":"Слева снизу", "bottom_right":"Справа снизу", "top_left":"Слева сверху", "top_right":"Справа сверху", "center":"По центру"}, "watermark"),
    "watermark_opacity": ("Непрозрачность знака, %", (0,100), "watermark"),
    "watermark_margin": ("Отступ знака, px", (0,200), "watermark"),
    "brightness": ("Яркость, %", (0,200), "color"),
    "contrast": ("Контраст, %", (0,200), "color"),
    "saturation": ("Насыщенность, %", (0,200), "color"),
    "sharpness": ("Резкость, %", (0,300), "color"),
    "filename": ("Имя выходного файла", "filename", "export"),
    "export_format": ("Формат файла", {"JPEG":"JPEG", "PNG":"PNG", "WEBP":"WebP"}, "export"),
    "jpeg_quality": ("Качество JPEG / WebP", (40,100), "export"),
    "delivery": ("Отправка", {"file":"Файл без сжатия Telegram", "photo":"Фото (Telegram сжимает)", "both":"Фото + оригинальный файл"}, "export"),
}
GROUPS = {"layout":"📐 Композиция и фон", "watermark":"🏷 Водяной знак", "color":"🎚 Цвет и резкость", "export":"📤 Выходной файл"}


def validate_value(key, value):
    if key not in FIELDS:
        raise ValueError("Неизвестная настройка")
    _, spec, _ = FIELDS[key]
    if isinstance(spec, dict):
        if value not in spec:
            raise ValueError("Выбери значение кнопкой")
        return value
    if spec == "filename":
        if not isinstance(value,str) or not re.fullmatch(r"[\w -]{1,60}",value) or not value.strip():
            raise ValueError("Имя файла: 1–60 букв, цифр, пробелов, дефисов или подчёркиваний, без расширения")
        return value.strip()
    if spec == "color":
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Введи цвет в формате #101010")
        return value.upper()
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Введи целое число от {spec[0]} до {spec[1]}") from None
    if not math.isfinite(number) or not number.is_integer() or not spec[0] <= number <= spec[1]:
        raise ValueError(f"Введи целое число от {spec[0]} до {spec[1]}")
    return int(number)


def normalize(settings):
    result = DEFAULTS.copy()
    for key, value in settings.items():
        if key in FIELDS:
            result[key] = validate_value(key, value)
        elif key in ("explicit_quality", "explicit_format"):
            result[key] = value
    return result


def builtin(style):
    result = DEFAULTS.copy()
    result["style"] = style
    return result
