from telegram import ReplyKeyboardMarkup

# State constants
MAIN_MENU, STYLE_CHOICE, SETTINGS_CHOICE = 0, 1, 2
VEVO_SETTINGS, EXPLICIT_SETTINGS, CUSTOM_INPUT = 3, 4, 5
VEVO_WM_SIZE, EXPLICIT_WM_SIZE, EXPLICIT_BLUR = 6, 7, 8
EXPLICIT_FG_SIZE, EXPLICIT_QUALITY, EXPLICIT_FORMAT = 9, 10, 11
NOTIFICATIONS_TOGGLE, BPM_INPUT, BPM_STRUCTURE = 12, 13, 14


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [["🎨 Выбор стиля", "⚙️ Настройка стиля"],
         ["🔢 BPM таймкоды", "🔔 Уведомления о перезапуске"],
         ["🗂 BPM пресеты", "🛒 BPM магазин"]],
        resize_keyboard=True
    )


def get_style_keyboard():
    return ReplyKeyboardMarkup(
        [["🎬 VEVO", "🔞 EXPLICIT"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_settings_style_keyboard():
    return ReplyKeyboardMarkup(
        [["⚙️ VEVO", "⚙️ EXPLICIT"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_vevo_wm_keyboard():
    return ReplyKeyboardMarkup(
        [["300", "450 (дефолт)"], ["500", "550"], ["✏️ Своё значение"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_explicit_menu_keyboard():
    return ReplyKeyboardMarkup(
        [["🎨 Вотермарка", "🌫️ Сила размытия"],
         ["📐 Размер центр. фото", "✨ Качество"],
         ["📤 Формат отправки"],
         ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_explicit_wm_keyboard():
    return ReplyKeyboardMarkup(
        [["200", "300 (дефолт)"], ["350", "400"], ["✏️ Своё значение"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_blur_keyboard():
    return ReplyKeyboardMarkup(
        [["5", "10 (дефолт)"], ["15", "20"], ["✏️ Своё значение"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_fg_size_keyboard():
    return ReplyKeyboardMarkup(
        [["700", "820 (дефолт)"], ["950", "1000"], ["✏️ Своё значение"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_quality_keyboard():
    return ReplyKeyboardMarkup(
        [["🎬 draft (70)", "🎞️ good (90 дефолт)"], ["🏆 best (PNG)"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_format_keyboard():
    return ReplyKeyboardMarkup(
        [["📸 Фото (сжато)", "📄 Файл (без сжатия)"], ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_back_keyboard():
    return ReplyKeyboardMarkup([["⬅️ Назад"]], resize_keyboard=True)


def get_notifications_keyboard():
    """Клавиатура для управления уведомлениями"""
    return ReplyKeyboardMarkup(
        [["✅ Включить", "❌ Выключить"],
         ["⬅️ Назад"]],
        resize_keyboard=True
    )


def get_bpm_structure_keyboard():
    return ReplyKeyboardMarkup(
        [["🧩 Структура по умолчанию", "✍️ Своя структура"],
         ["⬅️ Назад"]],
        resize_keyboard=True
    )
