import hashlib
from pathlib import Path

from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import database
from bot_ui import dismiss_input, markup, present

COVER_FIELDS = {
    "vevo_wm_size": ("Логотип VEVO", 10, 550, 10, "px"),
    "explicit_wm_size": ("Логотип EXPLICIT", 10, 400, 10, "px"),
    "explicit_blur": ("Размытие фона", 1, 30, 1, ""),
    "explicit_fg_size": ("Размер фото", 500, 1080, 20, "px"),
}
ALIASES = {
    "🖼 Обложка": "covers",
    "🖼 Обложки": "covers",
    "🎨 Выбор стиля": "covers",
    "⚙️ Настройка стиля": "cover-settings",
    "🎚 BPM": "bpm",
    "🎵 BPM и таймкоды": "bpm",
    "🔢 BPM таймкоды": "bpm",
    "🎬 Видео": "video",
    "🎬 Video Generator": "video",
    "🏠 Главное меню": "home",
    "⬅️ Назад в меню": "home",
    "⌂ Студия": "home",
    "⚙️ Бот": "settings",
    "🔔 Уведомления о перезапуске": "settings",
    "🗂 BPM пресеты": "presets",
    "🛒 BPM магазин": "shared",
    "🎧 MP3 и публикация": "mp3",
}


def host():
    import sys

    running = sys.modules.get("__main__")
    if (
        running
        and hasattr(running, "build_timestamps")
        and hasattr(running, "process_photo")
    ):
        return running
    import bot

    return bot


async def panel(update, title, rows, active=None, note=None):
    if note:
        title += "\n\n" + note
    return await present(update, title, markup(rows, active))


def leave_video(update, context):
    from video_generator.handlers import ACTIVE_USERS

    ACTIVE_USERS.discard(update.effective_user.id)
    for key in ("vg_input", "vg_visual_section", "vg_visual_project"):
        context.user_data.pop(key, None)


async def select(update, context, section):
    leave_video(update, context)
    for key in (
        "ui_input",
        "awaiting_preset_name",
        "awaiting_shared_preset_name",
        "awaiting_custom_structure",
        "awaiting_mp3_cover",
        "awaiting_mp3_field",
    ):
        context.user_data.pop(key, None)
    context.user_data["ui_section"] = section
    if section == "video":
        from video_generator.handlers import open_video

        await open_video(update, context)
    elif section in ("covers", "cover-settings"):
        context.user_data["ui_section"] = "covers"
        if section == "cover-settings":
            await cover_settings(update, context)
        else:
            await covers(update, context)
    elif section in ("bpm", "presets", "shared"):
        context.user_data["ui_section"] = "bpm"
        if section != "bpm":
            await bpm_presets(update, context, section == "shared")
        else:
            await bpm(update, context)
    elif section == "settings":
        await settings(update, context)
    elif section == "mp3":
        await panel(
            update,
            "🎧 MP3 и публикация\n\nОтправьте MP3 для редактирования тегов, обложки и публикации.",
            [],
            "ui:mp3",
        )
    else:
        await home(update, context)


async def home(update, context):
    rows = [
        [("🖼 Создать обложку", "ui:covers")],
        [("🎬 Создать видео", "ui:video")],
        [("🎚 BPM и таймкоды", "ui:bpm")],
        [("🎧 MP3", "ui:mp3"), ("⚙️ Настройки", "ui:settings")],
    ]
    await present(
        update,
        "WUTSHY · STUDIO\n\nВыберите инструмент.\nПроекты видео и настройки обложки сохраняются автоматически.",
        markup(rows, navigate=False),
    )


async def covers(update, context, note=None):
    settings = database.get_user_settings(update.effective_user.id)
    quality = {"draft": "Быстро", "good": "Высокое", "best": "PNG без потерь"}[
        settings["explicit_quality"]
    ]
    delivery = "Файл" if settings["explicit_format"] == "file" else "Фото"
    rows = [
        [
            (
                ("✓ " if settings["style"] == name else "") + name.upper(),
                "ui:style:" + name,
            )
            for name in ("vevo", "explicit")
        ],
        [
            ("⚙️ Параметры", "ui:cover-settings"),
            ("👁 Обновить превью", "ui:cover-preview"),
        ],
    ]
    await panel(
        update,
        f"🖼 Обложка\n\nСтиль · {settings['style'].upper()}\nХолст · 1920 × 1080\nКачество · {quality}\nОтправка · {delivery}\n\nОтправьте фото или изображение файлом.",
        rows,
        "ui:covers",
        note,
    )


async def cover_settings(update, context):
    s = database.get_user_settings(update.effective_user.id)
    keys = (
        ("vevo_wm_size",)
        if s["style"] == "vevo"
        else ("explicit_wm_size", "explicit_blur", "explicit_fg_size")
    )
    rows = [
        [
            (
                f"{COVER_FIELDS[key][0]} · {s[key]} {COVER_FIELDS[key][4]}",
                "ui:cover-field:" + key,
            )
        ]
        for key in keys
    ]
    rows += [
        [
            (
                "Качество · "
                + {"draft": "Быстро", "good": "Высокое", "best": "PNG"}[
                    s["explicit_quality"]
                ],
                "ui:cover-quality",
            )
        ],
        [
            (
                "Отправка · " + ("Файл" if s["explicit_format"] == "file" else "Фото"),
                "ui:cover-delivery",
            )
        ],
        [("👁 Обновить превью", "ui:cover-preview"), ("‹ Обложка", "ui:covers")],
    ]
    await panel(
        update,
        f"🖼 Обложка · настройки\n\n{s['style'].upper()}  /  1920 × 1080\nИзменения сохраняются автоматически.",
        rows,
        "ui:covers",
    )


async def cover_field(update, context, key):
    label, low, high, step, unit = COVER_FIELDS[key]
    value = database.get_user_settings(update.effective_user.id)[key]
    context.user_data["ui_input"] = ("cover-field", key)
    ratio = (value - low) / (high - low)
    bar = "●" * round(ratio * 12) + "○" * (12 - round(ratio * 12))
    rows = [
        [
            (f"{delta:+g}", f"ui:cover-adjust:{key}:{delta}")
            for delta in (-step * 5, -step, step, step * 5)
        ],
        [("‹ Параметры", "ui:cover-settings"), ("👁 Превью", "ui:cover-preview")],
    ]
    await panel(
        update,
        f"🖼 {label}\n\n{value} {unit}\n{bar}\n\nДиапазон · {low}–{high} {unit}\nМожно отправить точное число сообщением.",
        rows,
        "ui:covers",
    )


def bpm_value(context):
    return context.user_data.get("last_bpm", context.user_data.get("bpm", 120))


async def bpm(update, context, note=None):
    h = host()
    value = bpm_value(context)
    structure = context.user_data.get("last_bpm_structure") or h.DEFAULT_BPM_STRUCTURE
    context.user_data["ui_input"] = ("bpm", "")
    h.remember_bpm_result(context, value, structure)
    rows = [
        [
            ("−5", "ui:tempo:-5"),
            ("−1", "ui:tempo:-1"),
            (f"{value} BPM", "ui:noop"),
            ("+1", "ui:tempo:1"),
            ("+5", "ui:tempo:5"),
        ],
        [("🧩 Структура", "ui:structure"), ("📋 Копировать", "ui:copy-timestamps")],
        [("💾 Сохранить", "ui:bpm-save"), ("🗂 Пресеты", "ui:presets")],
    ]
    await panel(
        update,
        f"🎚 BPM и таймкоды\n\nТемп · {value} BPM\nРазмер · 4/4\n\n{h.build_timestamps(structure, value)}\n\nОтправьте число, чтобы изменить BPM.",
        rows,
        "ui:bpm",
        note,
    )


async def bpm_presets(update, context, shared=False, page=0):
    h = host()
    items = (
        h.load_shared_presets() if shared else h.load_presets(update.effective_user.id)
    )
    names = sorted(
        n for n, item in items.items() if not shared or not item.get("hidden")
    )
    page = max(0, min(page, max(0, (len(names) - 1) // 6)))
    rows = [
        [
            (
                name[:40],
                f"ui:load:{int(shared)}:{hashlib.sha256(name.encode()).hexdigest()[:12]}",
            )
        ]
        for name in names[page * 6 : page * 6 + 6]
    ]
    if len(names) > 6:
        rows.append(
            [
                ("‹", f"ui:presets-page:{int(shared)}:{max(0, page - 1)}"),
                (f"{page + 1}/{(len(names) - 1) // 6 + 1}", "ui:noop"),
                ("›", f"ui:presets-page:{int(shared)}:{page + 1}"),
            ]
        )
    rows += [[("Мои", "ui:presets"), ("Общие", "ui:shared")], [("‹ BPM", "ui:bpm")]]
    await panel(
        update,
        "🎚 BPM · пресеты\n\n"
        + (
            "Выберите структуру и темп."
            if names
            else "Пресетов пока нет. Сохраните свой результат в разделе BPM."
        ),
        rows,
        "ui:bpm",
    )


async def settings(update, context):
    s = database.get_user_settings(update.effective_user.id)
    rows = [
        [
            (
                "Уведомления о запуске · "
                + ("Вкл" if s["notifications_enabled"] else "Выкл"),
                "ui:notifications",
            )
        ]
    ]
    if host().is_admin_user(update):
        rows += [
            [("🔐 Подключения", "ui:connections"), ("📣 Публикаторы", "ui:publishers")]
        ]
    await panel(
        update,
        "⚙️ Студия · настройки\n\nВыберите параметр. Текущее значение указано на кнопке.",
        rows,
    )


async def open_command(update, context):
    if update.effective_chat.type != "private":
        return
    command = update.effective_message.text.split()[0].split("@")[0]
    section = {"/cover": "covers", "/bpm": "bpm"}.get(command, "home")
    database.get_user_settings(update.effective_user.id)
    await dismiss_input(update)
    await select(update, context, section)
    raise ApplicationHandlerStop


async def callback(update, context):
    if update.effective_chat.type != "private":
        await update.callback_query.answer()
        raise ApplicationHandlerStop
    await update.callback_query.answer()
    action, *parts = update.callback_query.data.split(":")[1:]
    if action == "noop":
        raise ApplicationHandlerStop
    leave_video(update, context)
    context.user_data.pop("ui_input", None)
    try:
        if action in (
            "home",
            "covers",
            "video",
            "bpm",
            "settings",
            "mp3",
            "cover-settings",
            "presets",
            "shared",
        ):
            await select(update, context, action)
        elif action == "style":
            if parts[0] not in ("vevo", "explicit"):
                raise ValueError("Неизвестный стиль.")
            database.update_user_setting(update.effective_user.id, "style", parts[0])
            context.user_data["ui_section"] = "covers"
            await covers(update, context)
        elif action == "cover-field":
            await cover_field(update, context, parts[0])
        elif action == "cover-adjust":
            key = parts[0]
            _, low, high, _, _ = COVER_FIELDS[key]
            value = database.get_user_settings(update.effective_user.id)[key] + int(
                parts[1]
            )
            database.update_user_setting(
                update.effective_user.id, key, max(low, min(high, value))
            )
            await cover_field(update, context, key)
        elif action in ("cover-quality", "cover-delivery"):
            values = (
                [("Быстро", "draft"), ("Высокое", "good"), ("PNG без потерь", "best")]
                if action == "cover-quality"
                else [("Фото", "photo"), ("Файл", "file")]
            )
            field = (
                "explicit_quality" if action == "cover-quality" else "explicit_format"
            )
            await panel(
                update,
                "🖼 " + ("Качество" if action == "cover-quality" else "Отправка"),
                [[(label, f"ui:cover-set:{field}:{value}")] for label, value in values]
                + [[("‹ Параметры", "ui:cover-settings")]],
                "ui:covers",
            )
        elif action == "cover-set":
            field, value = parts
            choices = {
                "explicit_quality": ("draft", "good", "best"),
                "explicit_format": ("photo", "file"),
            }
            if value not in choices[field]:
                raise ValueError("Недопустимое значение.")
            database.update_user_setting(update.effective_user.id, field, value)
            await cover_settings(update, context)
        elif action == "cover-preview":
            context.user_data["ui_section"] = "covers"
            from video_generator.library import storage

            cached = Path(
                context.user_data.get("cover_ui_source")
                or storage() / "cover_sources" / f"{update.effective_user.id}.image"
            )
            if not cached.is_file():
                await covers(
                    update, context, "Сначала отправьте изображение для превью."
                )
            else:
                await host().process_photo(update, context, repeat=True)
        elif action == "tempo":
            value = max(1, min(300, bpm_value(context) + int(parts[0])))
            context.user_data["last_bpm"] = value
            await bpm(update, context)
        elif action == "structure":
            await panel(
                update,
                "🎚 Структура трека\n\nДлительность секций задаётся в тактах (4/4).",
                [
                    [("По умолчанию", "ui:structure-default")],
                    [("Ввести структуру", "ui:structure-custom")],
                    [("‹ BPM", "ui:bpm")],
                ],
                "ui:bpm",
            )
        elif action == "structure-default":
            context.user_data["last_bpm_structure"] = (
                host().DEFAULT_BPM_STRUCTURE.copy()
            )
            await bpm(update, context)
        elif action == "structure-custom":
            context.user_data["ui_input"] = ("structure", "")
            await panel(
                update,
                "🎚 Своя структура\n\nОтправьте названия секций и количество тактов:\nIntro 8, Verse 16, Chorus 16",
                [[("‹ Отмена", "ui:bpm")]],
                "ui:bpm",
            )
        elif action == "copy-timestamps":
            import html

            await update.effective_message.reply_text(
                "<pre>"
                + html.escape(
                    host().build_timestamps(
                        context.user_data.get("last_bpm_structure")
                        or host().DEFAULT_BPM_STRUCTURE,
                        bpm_value(context),
                    )
                )
                + "</pre>",
                parse_mode="HTML",
            )
        elif action == "bpm-save":
            context.user_data["ui_input"] = ("save", "")
            await panel(
                update,
                "🎚 Сохранить пресет\n\nОтправьте название, например «Trap 142».",
                [[("‹ Отмена", "ui:bpm")]],
                "ui:bpm",
            )
        elif action == "presets-page":
            await bpm_presets(update, context, bool(int(parts[0])), int(parts[1]))
        elif action == "load":
            items = (
                host().load_shared_presets()
                if parts[0] == "1"
                else host().load_presets(update.effective_user.id)
            )
            name = next(
                (
                    name
                    for name, item in items.items()
                    if hashlib.sha256(name.encode()).hexdigest()[:12] == parts[1]
                    and (parts[0] != "1" or not item.get("hidden"))
                ),
                None,
            )
            if name is None:
                raise ValueError("Пресет больше недоступен.")
            item = items[name]
            value = int(item["bpm"])
            if not 1 <= value <= 300:
                raise ValueError("BPM вне диапазона.")
            context.user_data["last_bpm_structure"] = host().deserialize_structure(
                item["structure"]
            )
            context.user_data["last_bpm"] = value
            await bpm(update, context)
        elif action == "notifications":
            uid = update.effective_user.id
            database.update_user_setting(
                uid,
                "notifications_enabled",
                int(not database.get_user_settings(uid)["notifications_enabled"]),
            )
            await settings(update, context)
        elif action == "connections":
            from video_generator.connection_handlers import menu
            from video_generator.handlers import current

            await menu(update, context, current(update, context))
        elif action in ("publishers", "publisher-add", "publisher-remove"):
            if not host().is_admin_user(update):
                raise ValueError("Только для администратора.")
            if action != "publishers":
                context.user_data["ui_input"] = (action, "")
            ids = database.list_channel_publishers()
            await panel(
                update,
                "📣 Публикаторы\n\n"
                + ("\n".join(str(uid) for uid in ids) or "Список пуст.")
                + (
                    "\n\nОтправьте Telegram ID пользователя."
                    if action != "publishers"
                    else ""
                ),
                [
                    [
                        ("Добавить", "ui:publisher-add"),
                        ("Удалить", "ui:publisher-remove"),
                    ],
                    [("‹ Настройки", "ui:settings")],
                ],
            )
        else:
            raise ValueError("Откройте студию командой /menu.")
    except (ValueError, KeyError, IndexError) as error:
        await panel(
            update,
            "Не удалось применить настройку\n\n" + str(error),
            [
                [
                    (
                        "‹ Назад",
                        "ui:"
                        + (
                            "bpm"
                            if context.user_data.get("ui_section") == "bpm"
                            else "covers"
                        ),
                    )
                ]
            ],
        )
    raise ApplicationHandlerStop


async def message(update, context):
    if update.effective_chat.type != "private":
        return
    incoming = update.effective_message
    if incoming.text in ALIASES:
        await dismiss_input(update)
        await select(update, context, ALIASES[incoming.text])
        raise ApplicationHandlerStop
    pending = context.user_data.get("ui_input")
    if incoming.text and pending:
        action, key = pending
        value = incoming.text.strip()
        try:
            if action == "cover-field":
                _, low, high, _, _ = COVER_FIELDS[key]
                try:
                    parsed = int(value)
                except ValueError:
                    raise ValueError(
                        f"Введите целое число от {low} до {high}."
                    ) from None
                if not low <= parsed <= high:
                    raise ValueError(f"Введите число от {low} до {high}.")
                database.update_user_setting(update.effective_user.id, key, parsed)
                await cover_field(update, context, key)
            elif action == "bpm":
                try:
                    parsed = int(value)
                except ValueError:
                    raise ValueError("Введите BPM целым числом от 1 до 300.") from None
                if not 1 <= parsed <= 300:
                    raise ValueError("BPM должен быть от 1 до 300.")
                context.user_data["last_bpm"] = parsed
                await bpm(update, context)
            elif action == "structure":
                context.user_data["last_bpm_structure"] = host().parse_structure(value)
                await bpm(update, context)
            elif action in ("save", "save-shared"):
                if not 1 <= len(value) <= 60 or any(ord(c) < 32 for c in value):
                    raise ValueError("Название: 1–60 символов.")
                uid = update.effective_user.id
                shared = action == "save-shared"
                items = (
                    host().load_shared_presets() if shared else host().load_presets(uid)
                )
                if value in items:
                    raise ValueError("Такое название уже есть.")
                items[value] = {
                    "bpm": bpm_value(context),
                    "structure": host().serialize_structure(
                        context.user_data.get("last_bpm_structure")
                        or host().DEFAULT_BPM_STRUCTURE
                    ),
                }
                if shared:
                    items[value]["owner_id"] = uid
                    host().save_shared_presets(items)
                else:
                    host().save_presets(uid, items)
                await bpm(update, context, "Пресет сохранён · " + value)
            elif action in ("publisher-add", "publisher-remove"):
                if not host().is_admin_user(update):
                    raise ValueError("Только для администратора.")
                uid = int(value)
                if not 0 < uid < 2**63:
                    raise ValueError("Нужен положительный Telegram ID.")
                database.add_channel_publisher(
                    uid
                ) if action == "publisher-add" else database.remove_channel_publisher(
                    uid
                )
                context.user_data.pop("ui_input", None)
                await settings(update, context)
            await dismiss_input(update)
        except ValueError as error:
            await panel(
                update,
                "Проверьте значение\n\n" + str(error),
                [
                    [
                        (
                            "‹ Отмена",
                            "ui:"
                            + (
                                "bpm"
                                if context.user_data.get("ui_section") == "bpm"
                                else "cover-settings"
                            ),
                        )
                    ]
                ],
            )
        raise ApplicationHandlerStop
    image_document = incoming.document and (
        (incoming.document.mime_type or "").startswith("image/")
        or Path(incoming.document.file_name or "").suffix.lower()
        in (".jpg", ".jpeg", ".png", ".webp")
    )
    if context.user_data.get("ui_section") == "covers" and (
        incoming.photo or image_document
    ):
        await host().process_photo(update, context)
        raise ApplicationHandlerStop


async def preset_command(update, context):
    command = update.effective_message.text.split()[0].split("@")[0]
    if command in ("/mypresets", "/presetshop"):
        await select(
            update, context, "shared" if command == "/presetshop" else "presets"
        )
    else:
        leave_video(update, context)
        context.user_data["ui_section"] = "bpm"
        context.user_data["ui_input"] = (
            "save-shared" if command == "/save_to_shared" else "save",
            "",
        )
        await panel(
            update,
            "🎚 Сохранить пресет\n\nОтправьте название пресета.",
            [[("‹ Отмена", "ui:bpm")]],
            "ui:bpm",
        )
    await dismiss_input(update)
    raise ApplicationHandlerStop


def register(application):
    application.add_handler(
        CommandHandler(["start", "menu", "cancel", "cover", "bpm"], open_command),
        group=-2,
    )
    application.add_handler(
        CommandHandler(
            ["savepreset", "save_to_shared", "mypresets", "presetshop"], preset_command
        ),
        group=-2,
    )
    application.add_handler(CallbackQueryHandler(callback, pattern=r"^ui:"), group=-2)
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, message), group=-2
    )


async def deliver_cover(update, context, output_path):
    from telegram import InputMediaDocument, InputMediaPhoto
    from telegram.error import BadRequest

    s = database.get_user_settings(update.effective_user.id)
    document = s["explicit_format"] == "file"
    kind = "document" if document else "photo"
    caption = f"{s['style'].upper()} · 1920 × 1080 · {'PNG' if s['explicit_quality'] == 'best' else 'JPEG'}"
    controls = markup(
        [[("⚙️ Параметры", "ui:cover-settings"), ("👁 Обновить", "ui:cover-preview")]],
        "ui:covers",
    )
    preview_id = context.user_data.get("cover_ui_preview")
    with open(output_path, "rb") as file:
        if preview_id and context.user_data.get("cover_ui_preview_kind") == kind:
            media = (
                InputMediaDocument(file, caption=caption)
                if document
                else InputMediaPhoto(file, caption=caption)
            )
            try:
                await context.bot.edit_message_media(
                    chat_id=update.effective_chat.id,
                    message_id=preview_id,
                    media=media,
                    reply_markup=controls,
                )
                return
            except BadRequest as error:
                reason = str(error).lower()
                if "not modified" in reason:
                    return
                if not any(
                    item in reason
                    for item in ("not found", "can't be edited", "can not be edited")
                ):
                    raise
                file.seek(0)
        if document:
            sent = await update.effective_message.reply_document(
                document=file, caption=caption, reply_markup=controls
            )
        else:
            sent = await update.effective_message.reply_photo(
                photo=file, caption=caption, reply_markup=controls
            )
    context.user_data["cover_ui_preview"] = sent.message_id
    context.user_data["cover_ui_preview_kind"] = kind
