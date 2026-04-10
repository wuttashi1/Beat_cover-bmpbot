import logging
from PIL import Image
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

from menu_manager import (
    MAIN_MENU, STYLE_CHOICE, SETTINGS_CHOICE,
    VEVO_SETTINGS, EXPLICIT_SETTINGS, CUSTOM_INPUT,
    VEVO_WM_SIZE, EXPLICIT_BLUR, EXPLICIT_FG_SIZE,
    EXPLICIT_QUALITY, EXPLICIT_FORMAT, NOTIFICATIONS_TOGGLE,
    get_main_keyboard, get_style_keyboard, get_settings_style_keyboard,
    get_vevo_wm_keyboard, get_explicit_menu_keyboard, get_explicit_wm_keyboard,
    get_blur_keyboard, get_fg_size_keyboard, get_quality_keyboard,
    get_format_keyboard, get_back_keyboard, get_notifications_keyboard
)
from database import get_user_settings, update_user_setting, get_all_users
from styles import style_vevo, style_explicit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7378399292:AAE8iXgwZE5Tymi7NO9B201K5T6NbUF4oBs"

QUALITY_MAP = {
    "draft": 6,
    "good": 10,
    "best": 18
}

SHOWN_STARTUP_MESSAGE = set()


async def init_user(uid):
    """Инициализируем пользователя при первом контакте"""
    settings = get_user_settings(uid)
    logger.info(f"User {uid} initialized")
    return settings


async def show_main_menu(update: Update):
    """Показывает главное меню"""
    await update.message.reply_text(
        "🎛 STUDIO BOT — главное меню",
        reply_markup=get_main_keyboard()
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
    except Exception as e:
        logger.error(f"Error sending startup notifications: {e}")


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

    elif text == "🔔 Уведомления":
        await update.message.reply_text(
            "Выберите, включить вам уведомления или выключить:",
            reply_markup=get_notifications_keyboard()
        )
        return NOTIFICATIONS_TOGGLE

    logger.warning(f"Unknown main menu input: {text}")
    return MAIN_MENU


async def style_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    logger.info(f"Style choice: {text}")

    if text == "🎬 VEVO":
        update_user_setting(uid, "style", "vevo")
        await update.message.reply_text("✅ Стиль VEVO активирован", reply_markup=get_main_keyboard())
        return MAIN_MENU

    elif text == "🔞 EXPLICIT":
        update_user_setting(uid, "style", "explicit")
        await update.message.reply_text("✅ Стиль EXPLICIT активирован", reply_markup=get_main_keyboard())
        return MAIN_MENU

    elif text == "⬅️ Назад":
        await update.message.reply_text("🎛 Главное меню", reply_markup=get_main_keyboard())
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
        await update.message.reply_text("🎛 Главное меню", reply_markup=get_main_keyboard())
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
        await update.message.reply_text(f"✅ Размер вотермарки: {value}px", reply_markup=get_main_keyboard())
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
            reply_markup=get_main_keyboard()
        )
        logger.info(f"User {uid} enabled notifications")
        return MAIN_MENU

    elif text == "❌ Выключить":
        update_user_setting(uid, "notifications_enabled", 0)
        await update.message.reply_text(
            "🔔 Уведомления ❌ отключены",
            reply_markup=get_main_keyboard()
        )
        logger.info(f"User {uid} disabled notifications")
        return MAIN_MENU

    elif text == "⬅️ Назад":
        await update.message.reply_text("🎛 Главное меню", reply_markup=get_main_keyboard())
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
            await update.message.reply_text("🎛 STUDIO BOT — главное меню", reply_markup=get_main_keyboard())
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
            await update.message.reply_text(f"✅ Размер вотермарки: {value}px", reply_markup=get_main_keyboard())
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


async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logger.info(f"Processing photo for user {uid}")
    
    settings = get_user_settings(uid)
    logger.info(f"User settings: {settings}")

    photo_file = await update.message.photo[-1].get_file()
    await photo_file.download_to_drive("input.jpg")

    img = Image.open("input.jpg").convert("RGB")

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
        output_path = "edited.png"
        result.save(output_path, "PNG", optimize=False)
        logger.info(f"Saved as PNG with maximum quality")
    else:
        output_path = "edited.jpg"
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
        with open(output_path, "rb") as f:
            await update.message.reply_document(document=f)
    else:
        with open(output_path, "rb") as f:
            await update.message.reply_photo(photo=f)


# -------------------- MAIN --------------------
def main():
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
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_choice),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            STYLE_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, style_choice),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            SETTINGS_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_choice),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            VEVO_SETTINGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vevo_settings),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            EXPLICIT_SETTINGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_settings),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            VEVO_WM_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_wm_size),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            EXPLICIT_BLUR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_blur),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            EXPLICIT_FG_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_fg_size),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            EXPLICIT_QUALITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_quality),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            EXPLICIT_FORMAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, explicit_format),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            CUSTOM_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_input),
                MessageHandler(filters.PHOTO, process_photo)
            ],
            NOTIFICATIONS_TOGGLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notifications_toggle),
                MessageHandler(filters.PHOTO, process_photo)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Command("start"), start),
        ],
    )

    app.add_handler(conv_handler)

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
        reply_markup=get_main_keyboard()
    )
    return MAIN_MENU


if __name__ == "__main__":
    main()