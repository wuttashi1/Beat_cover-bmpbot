import logging
import json
import os
import re
from dotenv import load_dotenv
from mutagen.id3 import APIC, ID3, TBPM, TCON, TIT2, TKEY
from PIL import Image
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

from menu_manager import (
    MAIN_MENU, STYLE_CHOICE, SETTINGS_CHOICE,
    VEVO_SETTINGS, EXPLICIT_SETTINGS, CUSTOM_INPUT,
    VEVO_WM_SIZE, EXPLICIT_BLUR, EXPLICIT_FG_SIZE,
    EXPLICIT_QUALITY, EXPLICIT_FORMAT, NOTIFICATIONS_TOGGLE,
    BPM_INPUT, BPM_STRUCTURE,
    get_main_keyboard, get_style_keyboard, get_settings_style_keyboard,
    get_vevo_wm_keyboard, get_explicit_menu_keyboard, get_explicit_wm_keyboard,
    get_blur_keyboard, get_fg_size_keyboard, get_quality_keyboard,
    get_format_keyboard, get_back_keyboard, get_notifications_keyboard,
    get_bpm_structure_keyboard
)
from database import get_user_settings, update_user_setting, get_all_users
from styles import style_vevo, style_explicit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID_RAW = os.getenv("ADMIN_USER_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").lstrip("@").lower()
PUBLISH_CHANNEL = os.getenv("PUBLISH_CHANNEL")

QUALITY_MAP = {
    "draft": 6,
    "good": 10,
    "best": 18
}

SHOWN_STARTUP_MESSAGE = set()
PRESETS_DIR = "presets"
SHARED_PRESETS_FILE = os.path.join(PRESETS_DIR, "shared_presets.json")
ERROR_STATS_FILE = os.path.join("logs", "error_stats.json")
DEFAULT_BPM_STRUCTURE = [
    ("Intro", 8),
    ("Chorus", 16),
    ("Verse", 16),
    ("Bridge", 8),
    ("Chorus", 16),
    ("Outro", 8),
]
STYLE_TOKENS = [
    "new jazz",
    "jerk",
    "trap",
    "rage",
    "drill",
    "pluggnb",
    "boom bap",
    "phonk",
    "hyperpop",
]
KEY_PATTERN = re.compile(r"\b([A-G](?:#|b)?(?:m|maj|min)?)\b", re.IGNORECASE)
BPM_PATTERN = re.compile(r"\b(\d{2,3})\s*BPM\b", re.IGNORECASE)
FALLBACK_BPM_PATTERN = re.compile(r"\b(\d{2,3})\b")

try:
    ADMIN_USER_ID = int(ADMIN_USER_ID_RAW) if ADMIN_USER_ID_RAW else None
except ValueError:
    ADMIN_USER_ID = None


def format_time(seconds):
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes}:{sec:02d}"


def build_timestamps(structure, bpm):
    seconds_per_measure = 60 / bpm * 4
    lines = []
    current_time = 0
    for name, measures in structure:
        lines.append(f"{format_time(current_time)} - {name}")
        current_time += measures * seconds_per_measure
    return "\n".join(lines)


def parse_structure(text):
    structure = []
    chunks = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
    if not chunks:
        raise ValueError("empty structure")
    for chunk in chunks:
        parts = chunk.rsplit(" ", 1)
        if len(parts) != 2:
            raise ValueError("invalid structure format")
        name, measures_text = parts[0].strip(), parts[1].strip()
        measures = int(measures_text)
        if measures <= 0:
            raise ValueError("measures should be positive")
        structure.append((name, measures))
    return structure


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def ensure_json_file(path, default_value):
    parent = os.path.dirname(path)
    if parent:
        ensure_dir(parent)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump(default_value, file_obj, ensure_ascii=False, indent=2)


def get_user_presets_file(user_id):
    ensure_dir(PRESETS_DIR)
    return os.path.join(PRESETS_DIR, f"presets_{user_id}.json")


def load_json(path, default_value):
    ensure_json_file(path, default_value)
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, type(default_value)) else default_value
    except (json.JSONDecodeError, OSError):
        return default_value


def save_json(path, data):
    ensure_json_file(path, data if isinstance(data, (dict, list)) else {})
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)


def load_presets(user_id):
    return load_json(get_user_presets_file(user_id), {})


def save_presets(user_id, presets):
    save_json(get_user_presets_file(user_id), presets)


def load_shared_presets():
    return load_json(SHARED_PRESETS_FILE, {})


def save_shared_presets(presets):
    save_json(SHARED_PRESETS_FILE, presets)


def serialize_structure(structure):
    return [{"name": name, "measures": measures} for name, measures in structure]


def deserialize_structure(payload):
    structure = []
    for item in payload:
        name = str(item.get("name", "")).strip()
        measures = int(item.get("measures", 0))
        if name and measures > 0:
            structure.append((name, measures))
    if not structure:
        raise ValueError("Invalid structure payload")
    return structure


def remember_bpm_result(context, bpm, structure):
    context.user_data["last_bpm"] = bpm
    context.user_data["last_bpm_structure"] = structure
    context.user_data["can_save_bpm_preset"] = True


def record_error(error_type, details=None):
    stats = load_json(ERROR_STATS_FILE, {})
    if error_type not in stats:
        stats[error_type] = {"count": 0, "last_details": ""}
    stats[error_type]["count"] += 1
    stats[error_type]["last_details"] = str(details or "")
    save_json(ERROR_STATS_FILE, stats)


def is_admin_user(update: Update):
    user = update.effective_user
    if not user or ADMIN_USER_ID is None:
        return False
    username = (user.username or "").lower()
    if ADMIN_USERNAME and username != ADMIN_USERNAME:
        return False
    return user.id == ADMIN_USER_ID


def get_main_menu_for(update: Update):
    return get_main_keyboard(is_admin=is_admin_user(update))


def normalize_key(raw_key):
    key = raw_key.replace("♯", "#").strip()
    if key.lower().endswith("min"):
        key = f"{key[:-3]}m"
    if key.lower().endswith("maj"):
        key = key[:-3]
    if len(key) == 1:
        return key.upper()
    return f"{key[0].upper()}{key[1:]}"


def parse_audio_filename(file_name):
    stem = os.path.splitext(file_name or "")[0]
    working = stem

    bpm_match = BPM_PATTERN.search(working)
    bpm = int(bpm_match.group(1)) if bpm_match else None
    if bpm_match:
        working = BPM_PATTERN.sub(" ", working)
    if bpm is None:
        fallback = FALLBACK_BPM_PATTERN.search(working)
        if fallback:
            maybe_bpm = int(fallback.group(1))
            if 60 <= maybe_bpm <= 220:
                bpm = maybe_bpm
                working = FALLBACK_BPM_PATTERN.sub(" ", working, count=1)

    key_match = KEY_PATTERN.search(working)
    key = normalize_key(key_match.group(1)) if key_match else ""
    if key_match:
        working = KEY_PATTERN.sub(" ", working, count=1)

    style = ""
    lowered = working.lower()
    for token in STYLE_TOKENS:
        if token in lowered:
            style = token.title()
            working = re.sub(re.escape(token), " ", working, flags=re.IGNORECASE)
            break

    title = re.sub(r"\s+", " ", working).strip(" -_|")
    return {
        "title": title or stem.strip(),
        "style": style or "Unknown",
        "bpm": str(bpm) if bpm else "",
        "key": key or "",
    }


def get_mp3_edit_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Изменить Title", callback_data="mp3_edit_title"),
                InlineKeyboardButton("Изменить Style", callback_data="mp3_edit_style"),
            ],
            [
                InlineKeyboardButton("Изменить BPM", callback_data="mp3_edit_bpm"),
                InlineKeyboardButton("Изменить Key", callback_data="mp3_edit_key"),
            ],
            [
                InlineKeyboardButton("Опубликовать", callback_data="mp3_publish"),
                InlineKeyboardButton("Отмена", callback_data="mp3_cancel"),
            ],
        ]
    )


def format_mp3_preview(meta):
    return (
        "Черновик публикации:\n"
        f"Title: {meta.get('title', '')}\n"
        f"Style: {meta.get('style', '')}\n"
        f"BPM: {meta.get('bpm', '')}\n"
        f"Key: {meta.get('key', '')}"
    )


def get_mp3_caption(meta):
    fields = [meta.get("title", ""), meta.get("style", ""), meta.get("bpm", ""), meta.get("key", "")]
    labels = ["Title", "Style", "BPM", "Key"]
    parts = [f"{label}: {value}" for label, value in zip(labels, fields) if value]
    return " | ".join(parts) if parts else "Новый релиз"


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error_text = str(context.error)
    logger.exception("Unhandled exception: %s", error_text)
    record_error("unhandled_exception", error_text)


async def init_user(uid):
    """Инициализируем пользователя при первом контакте"""
    settings = get_user_settings(uid)
    logger.info(f"User {uid} initialized")
    return settings


async def show_main_menu(update: Update):
    """Показывает главное меню"""
    await update.message.reply_text(
        "🎛 STUDIO BOT — главное меню",
        reply_markup=get_main_menu_for(update)
    )


async def send_startup_notifications(app):
    """Отправляет уведомления о перезагрузке всем пользователям с включенными уведомлениями"""
    try:
        users = get_all_users()
        logger.info(f"Starting bot - checking notifications for {len(users)} users")
        
        for user_id in users:
            try:
                settings = get_user_settings(user_id)
                notifications_enabled = settings.get("notifications_enabled", False)
                
                logger.info(f"User {user_id}: notifications_enabled = {notifications_enabled} (type: {type(notifications_enabled)})")
                
                # Проверяем явно: должно быть True или 1
                if notifications_enabled is True or notifications_enabled == 1:
                    await app.bot.send_message(
                        chat_id=user_id,
                        text="🤖 Бот перезагружен и готов к работе!"
                    )
                    logger.info(f"✅ Sent startup notification to user {user_id}")
                else:
                    logger.info(f"⏭️ Skipped notification for user {user_id} (disabled)")
            except Exception as e:
                logger.warning(f"Failed to send notification to user {user_id}: {e}")
                record_error("startup_notification_failed", e)
    except Exception as e:
        logger.error(f"Error sending startup notifications: {e}")
        record_error("startup_notification_error", e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    settings = await init_user(uid)
    
    await show_main_menu(update)
    
    # Если уведомления включены и это первое взаимодействие, отправляем сообщение о запуске
    if settings.get("notifications_enabled", False) and uid not in SHOWN_STARTUP_MESSAGE:
        SHOWN_STARTUP_MESSAGE.add(uid)
        await update.message.reply_text("🤖 Бот запущен и готов к работе!")
    
    return MAIN_MENU


# -------------------- HANDLERS --------------------
async def main_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logger.info(f"Main menu choice: {text}")

    if text == "🎨 Выбор стиля":
        await update.message.reply_text(
            "🎨 Выбери стиль для обработки:",
            reply_markup=get_style_keyboard()
        )
        return STYLE_CHOICE

    elif text == "⚙️ Настройка стиля":
        await update.message.reply_text(
            "⚙️ Выбери стиль для настройки:",
            reply_markup=get_settings_style_keyboard()
        )
        return SETTINGS_CHOICE

    elif text == "🔔 Уведомления о перезапуске":
        uid = update.effective_user.id
        settings = get_user_settings(uid)
        status = "включены ✅" if settings.get("notifications_enabled", False) else "выключены ❌"
        await update.message.reply_text(
            f"Уведомления о перезапуске сейчас: {status}\nВыберите действие:",
            reply_markup=get_notifications_keyboard()
        )
        return NOTIFICATIONS_TOGGLE
    
    elif text == "🔢 BPM таймкоды":
        await update.message.reply_text(
            "Введите BPM (1-300):",
            reply_markup=get_back_keyboard()
        )
        return BPM_INPUT

    elif text == "🗂 BPM пресеты":
        await mypresets_command(update, context)
        return MAIN_MENU

    elif text == "🛒 BPM магазин":
        await presetshop_command(update, context)
        return MAIN_MENU

    elif text == "🛠 Админ панель":
        if not is_admin_user(update):
            await update.message.reply_text("Доступ запрещен.")
            return MAIN_MENU
        context.user_data["awaiting_admin_audio"] = True
        await update.message.reply_text(
            "Админ-панель MP3 активна.\nОтправь MP3-файл для предпросмотра и публикации в канал."
        )
        return MAIN_MENU

    logger.warning(f"Unknown main menu input: {text}")
    return MAIN_MENU


async def style_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Style choice: {text}")

    if text == "🎬 VEVO":
        update_user_setting(uid, "style", "vevo")
        await update.message.reply_text("✅ Стиль VEVO активирован", reply_markup=get_main_menu_for(update))
        return MAIN_MENU

    elif text == "🔞 EXPLICIT":
        update_user_setting(uid, "style", "explicit")
        await update.message.reply_text("✅ Стиль EXPLICIT активирован", reply_markup=get_main_menu_for(update))
        return MAIN_MENU

    elif text == "⬅️ Назад":
        await update.message.reply_text("🎛 Главное меню", reply_markup=get_main_menu_for(update))
        return MAIN_MENU

    logger.warning(f"Unknown style choice: {text}")
    return STYLE_CHOICE


async def settings_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logger.info(f"Settings choice: {text}")

    if text == "⚙️ VEVO":
        await update.message.reply_text(
            "⚙️ VEVO — размер водяной метки (макс. 550):",
            reply_markup=get_vevo_wm_keyboard()
        )
        return VEVO_SETTINGS

    elif text == "⚙️ EXPLICIT":
        await update.message.reply_text(
            "⚙️ EXPLICIT — выбери параметр для настройки:",
            reply_markup=get_explicit_menu_keyboard()
        )
        return EXPLICIT_SETTINGS

    elif text == "⬅️ Назад":
        await update.message.reply_text("🎛 Главное меню", reply_markup=get_main_menu_for(update))
        return MAIN_MENU

    logger.warning(f"Unknown settings choice: {text}")
    return SETTINGS_CHOICE


async def vevo_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"VEVO settings: {text}")

    if text in ["300", "450 (дефолт)", "500", "550"]:
        value = int(text.split()[0])
        update_user_setting(uid, "vevo_wm_size", value)
        await update.message.reply_text(f"✅ Размер вотермарки: {value}px", reply_markup=get_main_menu_for(update))
        return MAIN_MENU

    elif text == "✏️ Своё значение":
        context.user_data["input_type"] = "vevo_wm"
        context.user_data["return_state"] = SETTINGS_CHOICE
        await update.message.reply_text("Введи значение (10-550):", reply_markup=get_back_keyboard())
        return CUSTOM_INPUT

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ Выбери стиль для настройки:", reply_markup=get_settings_style_keyboard())
        return SETTINGS_CHOICE

    logger.warning(f"Unknown VEVO setting: {text}")
    return VEVO_SETTINGS


async def explicit_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logger.info(f"Explicit settings: {text}")

    if text == "🎨 Вотермарка":
        await update.message.reply_text(
            "🎨 EXPLICIT — размер вотермарки (макс. 400):",
            reply_markup=get_explicit_wm_keyboard()
        )
        return VEVO_WM_SIZE

    elif text == "🌫️ Сила размытия":
        await update.message.reply_text(
            "🌫️ Сила размытия фона (1-30):",
            reply_markup=get_blur_keyboard()
        )
        return EXPLICIT_BLUR

    elif text == "📐 Размер центр. фото":
        await update.message.reply_text(
            "📐 Размер центрального фото (500-1080):",
            reply_markup=get_fg_size_keyboard()
        )
        return EXPLICIT_FG_SIZE

    elif text == "✨ Качество":
        await update.message.reply_text(
            "✨ Качество обработки:",
            reply_markup=get_quality_keyboard()
        )
        return EXPLICIT_QUALITY

    elif text == "📤 Формат отправки":
        await update.message.reply_text(
            "📤 Как отправить результат:",
            reply_markup=get_format_keyboard()
        )
        return EXPLICIT_FORMAT

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ Выбери стиль для настройки:", reply_markup=get_settings_style_keyboard())
        return SETTINGS_CHOICE

    logger.warning(f"Unknown explicit setting: {text}")
    return EXPLICIT_SETTINGS


async def explicit_wm_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Explicit WM size: {text}")

    if text in ["200", "300 (дефолт)", "350", "400"]:
        value = int(text.split()[0])
        update_user_setting(uid, "explicit_wm_size", value)
        await update.message.reply_text(f"✅ Размер вотермарки: {value}px", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif text == "✏️ Своё значение":
        context.user_data["input_type"] = "explicit_wm"
        context.user_data["return_state"] = EXPLICIT_SETTINGS
        await update.message.reply_text("Введи значение (10-400):", reply_markup=get_back_keyboard())
        return CUSTOM_INPUT

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ EXPLICIT — выбери параметр для настройки:", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    logger.warning(f"Unknown explicit WM size: {text}")
    return VEVO_WM_SIZE


async def explicit_blur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Explicit blur: {text}")

    if text in ["5", "10 (дефолт)", "15", "20"]:
        value = int(text.split()[0])
        update_user_setting(uid, "explicit_blur", value)
        await update.message.reply_text(f"✅ Сила размытия: {value}", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif text == "✏️ Своё значение":
        context.user_data["input_type"] = "explicit_blur"
        context.user_data["return_state"] = EXPLICIT_SETTINGS
        await update.message.reply_text("Введи значение (1-30):", reply_markup=get_back_keyboard())
        return CUSTOM_INPUT

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ EXPLICIT — выбери параметр для настройки:", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    logger.warning(f"Unknown explicit blur: {text}")
    return EXPLICIT_BLUR


async def explicit_fg_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Explicit FG size: {text}")

    if text in ["700", "820 (дефолт)", "950", "1000"]:
        value = int(text.split()[0])
        update_user_setting(uid, "explicit_fg_size", value)
        await update.message.reply_text(f"✅ Размер фото: {value}px", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif text == "✏️ Своё значение":
        context.user_data["input_type"] = "explicit_fg_size"
        context.user_data["return_state"] = EXPLICIT_SETTINGS
        await update.message.reply_text("Введи значение (500-1080):", reply_markup=get_back_keyboard())
        return CUSTOM_INPUT

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ EXPLICIT — выбери параметр для настройки:", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    logger.warning(f"Unknown explicit FG size: {text}")
    return EXPLICIT_FG_SIZE


async def explicit_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Explicit quality: {text}")

    if "draft" in text:
        update_user_setting(uid, "explicit_quality", "draft")
        await update.message.reply_text("✅ Качество: draft", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif "good" in text:
        update_user_setting(uid, "explicit_quality", "good")
        await update.message.reply_text("✅ Качество: good", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif "best" in text:
        update_user_setting(uid, "explicit_quality", "best")
        await update.message.reply_text("✅ Качество: best", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ EXPLICIT — выбери параметр для настройки:", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    logger.warning(f"Unknown explicit quality: {text}")
    return EXPLICIT_QUALITY


async def explicit_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Explicit format: {text}")

    if "Фото" in text:
        update_user_setting(uid, "explicit_format", "photo")
        await update.message.reply_text("✅ Формат: Фото (сжато)", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif "Файл" in text:
        update_user_setting(uid, "explicit_format", "file")
        await update.message.reply_text("✅ Формат: Файл (без сжатия)", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    elif text == "⬅️ Назад":
        await update.message.reply_text("⚙️ EXPLICIT — выбери параметр для настройки:", reply_markup=get_explicit_menu_keyboard())
        return EXPLICIT_SETTINGS

    logger.warning(f"Unknown explicit format: {text}")
    return EXPLICIT_FORMAT


async def notifications_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение уведомлений"""
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Notifications toggle: {text}")

    if text == "✅ Включить":
        update_user_setting(uid, "notifications_enabled", 1)
        await update.message.reply_text(
            "🔔 Уведомления ✅ включены",
            reply_markup=get_main_menu_for(update)
        )
        logger.info(f"User {uid} enabled notifications")
        return MAIN_MENU

    elif text == "❌ Выключить":
        update_user_setting(uid, "notifications_enabled", 0)
        await update.message.reply_text(
            "🔔 Уведомления ❌ отключены",
            reply_markup=get_main_menu_for(update)
        )
        logger.info(f"User {uid} disabled notifications")
        return MAIN_MENU

    elif text == "⬅️ Назад":
        await update.message.reply_text("🎛 Главное меню", reply_markup=get_main_menu_for(update))
        return MAIN_MENU

    logger.warning(f"Unknown notifications input: {text}")
    return NOTIFICATIONS_TOGGLE


async def custom_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    return_state = context.user_data.get("return_state", MAIN_MENU)
    logger.info(f"Custom input: {text}, return_state: {return_state}")

    if text == "⬅️ Назад":
        if return_state == SETTINGS_CHOICE:
            await update.message.reply_text("⚙️ Выбери стиль для настройки:", reply_markup=get_settings_style_keyboard())
        elif return_state == EXPLICIT_SETTINGS:
            await update.message.reply_text("⚙️ EXPLICIT — выбери параметр для настройки:", reply_markup=get_explicit_menu_keyboard())
        else:
            await update.message.reply_text("🎛 STUDIO BOT — главное меню", reply_markup=get_main_menu_for(update))
        return return_state

    try:
        value = int(text)
        input_type = context.user_data.get("input_type")
        logger.info(f"Processing custom input - type: {input_type}, value: {value}")

        if input_type == "vevo_wm":
            if not (10 <= value <= 550):
                await update.message.reply_text("❌ Значение должно быть 10-550")
                return CUSTOM_INPUT
            update_user_setting(uid, "vevo_wm_size", value)
            await update.message.reply_text(f"✅ Размер вотермарки: {value}px", reply_markup=get_main_menu_for(update))
            return MAIN_MENU

        elif input_type == "explicit_wm":
            if not (10 <= value <= 400):
                await update.message.reply_text("❌ Значение должно быть 10-400")
                return CUSTOM_INPUT
            update_user_setting(uid, "explicit_wm_size", value)
            await update.message.reply_text(f"✅ Размер вотермарки: {value}px", reply_markup=get_explicit_menu_keyboard())
            return EXPLICIT_SETTINGS

        elif input_type == "explicit_blur":
            if not (1 <= value <= 30):
                await update.message.reply_text("❌ Значение должно быть 1-30")
                return CUSTOM_INPUT
            update_user_setting(uid, "explicit_blur", value)
            await update.message.reply_text(f"✅ Сила размытия: {value}", reply_markup=get_explicit_menu_keyboard())
            return EXPLICIT_SETTINGS

        elif input_type == "explicit_fg_size":
            if not (500 <= value <= 1080):
                await update.message.reply_text("❌ Значение должно быть 500-1080")
                return CUSTOM_INPUT
            update_user_setting(uid, "explicit_fg_size", value)
            await update.message.reply_text(f"✅ Размер фото: {value}px", reply_markup=get_explicit_menu_keyboard())
            return EXPLICIT_SETTINGS

    except ValueError:
        await update.message.reply_text("❌ Введи число")
        return CUSTOM_INPUT

    logger.warning(f"Unhandled custom input: {text}")
    return CUSTOM_INPUT


async def bpm_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "⬅️ Назад":
        await update.message.reply_text("🎛 STUDIO BOT — главное меню", reply_markup=get_main_menu_for(update))
        return MAIN_MENU
    try:
        bpm = int(text)
        if not (1 <= bpm <= 300):
            raise ValueError
        context.user_data["bpm"] = bpm
        await update.message.reply_text(
            "Выбери способ расчета:\n"
            "• Структура по умолчанию\n"
            "• Своя структура (формат: Intro 8, Verse 16, Chorus 16)",
            reply_markup=get_bpm_structure_keyboard()
        )
        return BPM_STRUCTURE
    except ValueError:
        await update.message.reply_text("❌ Введи BPM целым числом от 1 до 300")
        return BPM_INPUT


async def bpm_structure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    bpm = context.user_data.get("bpm")
    if not bpm:
        await update.message.reply_text("Сначала укажи BPM.", reply_markup=get_back_keyboard())
        return BPM_INPUT

    if text == "⬅️ Назад":
        await update.message.reply_text("Введите BPM (1-300):", reply_markup=get_back_keyboard())
        return BPM_INPUT

    if text == "🧩 Структура по умолчанию":
        structure = DEFAULT_BPM_STRUCTURE.copy()
        timestamps = build_timestamps(structure, bpm)
        remember_bpm_result(context, bpm, structure)
        await update.message.reply_text(
            f"✅ Таймкоды готовы\nBPM: {bpm}\n\n{timestamps}\n\n"
            "Сохранить: /savepreset\nВ общий доступ: /save_to_shared",
            reply_markup=get_main_menu_for(update)
        )
        return MAIN_MENU

    if text == "✍️ Своя структура":
        await update.message.reply_text(
            "Отправь структуру в формате:\n"
            "Intro 8, Verse 16, Chorus 16",
            reply_markup=get_back_keyboard()
        )
        context.user_data["awaiting_custom_structure"] = True
        return BPM_STRUCTURE

    if context.user_data.get("awaiting_custom_structure"):
        try:
            structure = parse_structure(text)
            context.user_data["awaiting_custom_structure"] = False
            timestamps = build_timestamps(structure, bpm)
            remember_bpm_result(context, bpm, structure)
            await update.message.reply_text(
                f"✅ Таймкоды готовы\nBPM: {bpm}\n\n{timestamps}\n\n"
                "Сохранить: /savepreset\nВ общий доступ: /save_to_shared",
                reply_markup=get_main_menu_for(update)
            )
            return MAIN_MENU
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Неверный формат.\nПример: Intro 8, Verse 16, Chorus 16"
            )
            return BPM_STRUCTURE

    await update.message.reply_text("Выбери кнопку ниже.", reply_markup=get_bpm_structure_keyboard())
    return BPM_STRUCTURE


async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logger.info(f"Processing photo for user {uid}")
    
    settings = get_user_settings(uid)
    logger.info(f"User settings: {settings}")

    input_path = f"input_{uid}.jpg"
    output_path = ""

    try:
        photo_file = await update.message.photo[-1].get_file()
        await photo_file.download_to_drive(input_path)

        img = Image.open(input_path).convert("RGB")

        if settings["style"] == "explicit":
            result = style_explicit(
                img,
                wm_size=settings["explicit_wm_size"],
                blur_strength=settings["explicit_blur"],
                fg_size=settings["explicit_fg_size"],
                quality_level=settings["explicit_quality"]
            )
        else:
            result = style_vevo(img, wm_size=settings["vevo_wm_size"])

        result = result.convert("RGB")
        quality_level = settings["explicit_quality"]
        quality_value = QUALITY_MAP.get(quality_level, 10)

        if quality_level == "best":
            output_path = f"edited_{uid}.png"
            result.save(output_path, "PNG", optimize=False)
            logger.info("Saved as PNG with maximum quality")
        else:
            output_path = f"edited_{uid}.jpg"
            save_quality = min(98, 90 + quality_value)
            result.save(
                output_path,
                "JPEG",
                quality=save_quality,
                optimize=False,
                progressive=False
            )
            logger.info(f"Saved as JPEG with quality {save_quality}")

        if settings["explicit_format"] == "file":
            with open(output_path, "rb") as file_obj:
                await update.message.reply_document(document=file_obj)
        else:
            with open(output_path, "rb") as file_obj:
                await update.message.reply_photo(photo=file_obj)
    except Exception as err:
        logger.exception("Photo processing failed: %s", err)
        record_error("photo_processing_failed", err)
        await update.message.reply_text("❌ Не получилось обработать фото. Попробуй еще раз.")
    finally:
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    logger.warning("Failed to cleanup temporary file: %s", path)
                    record_error("temp_file_cleanup_failed", path)


async def savepreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("can_save_bpm_preset"):
        await update.message.reply_text("Сначала рассчитай таймкоды BPM.")
        return
    context.user_data["awaiting_preset_name"] = True
    await update.message.reply_text("Введите имя для пресета:")


async def save_to_shared_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("can_save_bpm_preset"):
        await update.message.reply_text("Сначала рассчитай таймкоды BPM.")
        return
    context.user_data["awaiting_shared_preset_name"] = True
    await update.message.reply_text("Введите имя для общего пресета:")


async def mypresets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    presets = load_presets(update.effective_user.id)
    if not presets:
        await update.message.reply_text("У тебя пока нет BPM пресетов.")
        return
    names = "\n".join([f"• {name}" for name in sorted(presets.keys())])
    await update.message.reply_text(
        "🗂 Твои BPM пресеты:\n"
        f"{names}\n\n"
        "Команды:\n"
        "/loadpreset <имя>\n"
        "/deletepreset <имя>"
    )


async def presetshop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    presets = load_shared_presets()
    visible_names = sorted([name for name, preset in presets.items() if not preset.get("hidden")])
    if not visible_names:
        await update.message.reply_text("В магазине пока нет общих пресетов.")
        return
    names = "\n".join([f"• {name}" for name in visible_names])
    await update.message.reply_text(
        "🛒 Общие BPM пресеты:\n"
        f"{names}\n\n"
        "Команда: /loadshared <имя>"
    )


async def loadpreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /loadpreset <имя>")
        return
    name = " ".join(context.args).strip()
    presets = load_presets(update.effective_user.id)
    preset = presets.get(name)
    if not preset:
        await update.message.reply_text("Пресет не найден.")
        return
    try:
        structure = deserialize_structure(preset["structure"])
        bpm = int(preset["bpm"])
    except (KeyError, TypeError, ValueError):
        await update.message.reply_text("Пресет поврежден и не может быть загружен.")
        record_error("load_user_preset_failed", name)
        return
    remember_bpm_result(context, bpm, structure)
    timestamps = build_timestamps(structure, bpm)
    await update.message.reply_text(f"✅ Загружен пресет '{name}'\nBPM: {bpm}\n\n{timestamps}")


async def deletepreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /deletepreset <имя>")
        return
    name = " ".join(context.args).strip()
    user_id = update.effective_user.id
    presets = load_presets(user_id)
    if name not in presets:
        await update.message.reply_text("Пресет не найден.")
        return
    del presets[name]
    save_presets(user_id, presets)
    await update.message.reply_text(f"🗑 Пресет '{name}' удален.")


async def loadshared_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /loadshared <имя>")
        return
    name = " ".join(context.args).strip()
    presets = load_shared_presets()
    preset = presets.get(name)
    if not preset or preset.get("hidden"):
        await update.message.reply_text("Общий пресет не найден.")
        return
    try:
        structure = deserialize_structure(preset["structure"])
        bpm = int(preset["bpm"])
    except (KeyError, TypeError, ValueError):
        await update.message.reply_text("Общий пресет поврежден и не может быть загружен.")
        record_error("load_shared_preset_failed", name)
        return
    remember_bpm_result(context, bpm, structure)
    timestamps = build_timestamps(structure, bpm)
    await update.message.reply_text(f"✅ Загружен общий пресет '{name}'\nBPM: {bpm}\n\n{timestamps}")


async def handle_preset_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Имя не может быть пустым.")
        return MAIN_MENU
    user_id = update.effective_user.id
    bpm = context.user_data.get("last_bpm")
    structure = context.user_data.get("last_bpm_structure")
    if not bpm or not structure:
        await update.message.reply_text("Сначала рассчитай таймкоды BPM.")
        return MAIN_MENU
    presets = load_presets(user_id)
    presets[name] = {"bpm": bpm, "structure": serialize_structure(structure)}
    save_presets(user_id, presets)
    context.user_data["awaiting_preset_name"] = False
    await update.message.reply_text(f"✅ Пресет '{name}' сохранен.", reply_markup=get_main_menu_for(update))
    return MAIN_MENU


async def handle_shared_preset_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Имя не может быть пустым.")
        return MAIN_MENU
    bpm = context.user_data.get("last_bpm")
    structure = context.user_data.get("last_bpm_structure")
    if not bpm or not structure:
        await update.message.reply_text("Сначала рассчитай таймкоды BPM.")
        return MAIN_MENU
    presets = load_shared_presets()
    presets[name] = {"bpm": bpm, "structure": serialize_structure(structure), "hidden": False}
    save_shared_presets(presets)
    context.user_data["awaiting_shared_preset_name"] = False
    await update.message.reply_text(f"✅ Общий пресет '{name}' сохранен.", reply_markup=get_main_menu_for(update))
    return MAIN_MENU


async def fetch_channel_avatar_bytes(bot):
    try:
        chat = await bot.get_chat(PUBLISH_CHANNEL)
        if not chat.photo:
            return None, None
        file = await bot.get_file(chat.photo.big_file_id)
        temp_path = f"channel_avatar_{chat.id}.jpg"
        await file.download_to_drive(temp_path)
        with open(temp_path, "rb") as image_file:
            data = image_file.read()
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return data, "image/jpeg"
    except Exception as err:
        record_error("channel_avatar_fetch_failed", err)
        return None, None


def apply_id3_metadata(mp3_path, meta, cover_bytes=None, cover_mime=None):
    try:
        tags = ID3(mp3_path)
    except Exception:
        tags = ID3()
    tags.delall("TIT2")
    tags.delall("TCON")
    tags.delall("TBPM")
    tags.delall("TKEY")
    tags.delall("APIC")
    if meta.get("title"):
        tags.add(TIT2(encoding=3, text=meta["title"]))
    if meta.get("style"):
        tags.add(TCON(encoding=3, text=meta["style"]))
    if meta.get("bpm"):
        tags.add(TBPM(encoding=3, text=str(meta["bpm"])))
    if meta.get("key"):
        tags.add(TKEY(encoding=3, text=meta["key"]))
    if cover_bytes and cover_mime:
        tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover_bytes))
    tags.save(mp3_path)


async def show_mp3_preview(update_or_query, context):
    meta = context.user_data.get("mp3_draft_meta", {})
    text = format_mp3_preview(meta)
    keyboard = get_mp3_edit_keyboard()
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text=text, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text, reply_markup=keyboard)


async def process_audio_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update):
        return
    audio = update.message.audio
    if not audio:
        return
    if not context.user_data.get("awaiting_admin_audio"):
        await update.message.reply_text("Открой админ-панель кнопкой '🛠 Админ панель', потом отправь MP3.")
        return
    file_name = audio.file_name or "track.mp3"
    if not file_name.lower().endswith(".mp3"):
        await update.message.reply_text("Нужен именно MP3-файл.")
        return
    draft_name = f"draft_{update.effective_user.id}_{audio.file_unique_id}.mp3"
    telegram_file = await audio.get_file()
    await telegram_file.download_to_drive(draft_name)
    context.user_data["mp3_draft_path"] = draft_name
    context.user_data["mp3_draft_meta"] = parse_audio_filename(file_name)
    context.user_data["awaiting_admin_audio"] = False
    context.user_data["awaiting_mp3_field"] = None
    await show_mp3_preview(update, context)


async def handle_mp3_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data.startswith("mp3_"):
        return
    await query.answer()
    if not is_admin_user(update):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    data = query.data

    if data.startswith("mp3_edit_"):
        field = data.replace("mp3_edit_", "", 1)
        field_map = {"title": "Title", "style": "Style", "bpm": "BPM", "key": "Key"}
        context.user_data["awaiting_mp3_field"] = field
        await query.message.reply_text(f"Введи новое значение для {field_map.get(field, field)}:")
        return

    if data == "mp3_cancel":
        draft_path = context.user_data.pop("mp3_draft_path", "")
        context.user_data.pop("mp3_draft_meta", None)
        context.user_data.pop("awaiting_mp3_field", None)
        if draft_path and os.path.exists(draft_path):
            try:
                os.remove(draft_path)
            except OSError:
                record_error("mp3_draft_cleanup_failed", draft_path)
        await query.edit_message_text("Черновик отменен.")
        return

    if data == "mp3_publish":
        draft_path = context.user_data.get("mp3_draft_path", "")
        meta = context.user_data.get("mp3_draft_meta")
        if not draft_path or not meta or not os.path.exists(draft_path):
            await query.answer("Черновик не найден", show_alert=True)
            return
        cover_bytes, cover_mime = await fetch_channel_avatar_bytes(context.bot)
        apply_id3_metadata(draft_path, meta, cover_bytes=cover_bytes, cover_mime=cover_mime)
        caption = get_mp3_caption(meta)
        try:
            with open(draft_path, "rb") as audio_file:
                await context.bot.send_audio(chat_id=PUBLISH_CHANNEL, audio=audio_file, caption=caption)
            await query.edit_message_text("Опубликовано в канал.")
        except Exception as err:
            record_error("mp3_publish_failed", err)
            await query.edit_message_text("Не удалось опубликовать. Проверь права бота в канале.")
        finally:
            context.user_data.pop("mp3_draft_meta", None)
            context.user_data.pop("awaiting_mp3_field", None)
            context.user_data.pop("mp3_draft_path", None)
            if os.path.exists(draft_path):
                try:
                    os.remove(draft_path)
                except OSError:
                    record_error("mp3_draft_cleanup_failed", draft_path)


async def handle_mp3_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get("awaiting_mp3_field")
    if not field:
        return False
    meta = context.user_data.get("mp3_draft_meta")
    if not meta:
        context.user_data["awaiting_mp3_field"] = None
        return False
    value = update.message.text.strip()
    if field == "bpm":
        if not value.isdigit() or not (1 <= int(value) <= 300):
            await update.message.reply_text("BPM должен быть целым числом от 1 до 300.")
            return True
    if field == "key" and value:
        value = normalize_key(value)
    meta[field] = value
    context.user_data["mp3_draft_meta"] = meta
    context.user_data["awaiting_mp3_field"] = None
    await update.message.reply_text("Поле обновлено.")
    await show_mp3_preview(update, context)
    return True


# -------------------- MAIN --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in .env")
    if ADMIN_USER_ID is None:
        raise RuntimeError("ADMIN_USER_ID is missing or invalid in .env")
    if not PUBLISH_CHANNEL:
        raise RuntimeError("PUBLISH_CHANNEL is missing in .env")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Send startup notifications
    async def startup(application):
        await send_startup_notifications(application)

    app.post_init = startup

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Command("start"), start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_entry),
            MessageHandler(filters.PHOTO, handle_photo_entry),
            MessageHandler(filters.AUDIO, process_audio_file),
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_choice),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            STYLE_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, style_choice),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            SETTINGS_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_choice),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            VEVO_SETTINGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vevo_settings),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            EXPLICIT_SETTINGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_settings),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            VEVO_WM_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_wm_size),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            EXPLICIT_BLUR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_blur),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            EXPLICIT_FG_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_fg_size),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            EXPLICIT_QUALITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_quality),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            EXPLICIT_FORMAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_format),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            CUSTOM_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_input),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            NOTIFICATIONS_TOGGLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notifications_toggle),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            BPM_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bpm_input),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
            BPM_STRUCTURE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bpm_structure),
                MessageHandler(filters.PHOTO, process_photo),
                MessageHandler(filters.AUDIO, process_audio_file),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Command("start"), start),
        ],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("savepreset", savepreset_command))
    app.add_handler(CommandHandler("save_to_shared", save_to_shared_command))
    app.add_handler(CommandHandler("mypresets", mypresets_command))
    app.add_handler(CommandHandler("presetshop", presetshop_command))
    app.add_handler(CommandHandler("loadpreset", loadpreset_command))
    app.add_handler(CommandHandler("deletepreset", deletepreset_command))
    app.add_handler(CommandHandler("loadshared", loadshared_command))
    app.add_handler(CallbackQueryHandler(handle_mp3_callback, pattern=r"^mp3_"))
    app.add_error_handler(global_error_handler)

    app.run_polling()


# -------------------- ENTRY POINTS --------------------
async def handle_text_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для первого текста"""
    uid = update.effective_user.id
    settings = await init_user(uid)
    
    # Если уведомления включены и это первое взаимодействие, отправляем сообщение о запуске
    if settings.get("notifications_enabled", False) and uid not in SHOWN_STARTUP_MESSAGE:
        SHOWN_STARTUP_MESSAGE.add(uid)
        await update.message.reply_text("🤖 Бот запущен и готов к работе!")
    
    if context.user_data.get("awaiting_preset_name"):
        return await handle_preset_name_input(update, context)
    if context.user_data.get("awaiting_shared_preset_name"):
        return await handle_shared_preset_name_input(update, context)
    if await handle_mp3_edit_text(update, context):
        return MAIN_MENU

    # Обрабатываем текст как команду главного меню
    return await main_menu_choice(update, context)


async def handle_photo_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для первого фото"""
    uid = update.effective_user.id
    settings = await init_user(uid)
    
    # Если уведомления включены и это первое взаимодействие, отправляем сообщение о запуске
    if settings.get("notifications_enabled", False) and uid not in SHOWN_STARTUP_MESSAGE:
        SHOWN_STARTUP_MESSAGE.add(uid)
        await update.message.reply_text("🤖 Бот запущен и готов к работе!")
    
    # Обрабатываем фото
    await process_photo(update, context)
    
    await update.message.reply_text(
        "🎛 Фото готово! Выбери что дальше:",
        reply_markup=get_main_menu_for(update)
    )
    return MAIN_MENU


if __name__ == "__main__":
    main()