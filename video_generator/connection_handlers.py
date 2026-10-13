import asyncio
import json
import re

from telegram.error import TelegramError

from . import connections
from .media import MediaError


def admin(update):
    from bot import is_admin_user

    if update.effective_chat.type != "private" or not is_admin_user(update):
        raise MediaError("Connections доступны только administrator в личном чате.")


async def menu(update, context, project):
    admin(update)
    from .handlers import screen

    data = connections.load(update.effective_user.id)
    text = (
        "🔐 Connections\n\n"
        f"Telegram API ID/hash: {'✅' if data.get('tg_api_id') and data.get('tg_api_hash') else '—'}\n"
        f"Large-file delivery: {'✅ Ready' if data.get('tg_peer_hash') else 'Connect + send message'}\n"
        f"Review channel: {data.get('channel') or '—'}\n"
        f"Auto channel: {'ON' if data.get('auto_channel') else 'OFF'}\n"
        f"YouTube OAuth: {'✅' if data.get('youtube_credentials') else '—'}\n"
        f"Auto YouTube Private: {'ON' if data.get('auto_youtube') else 'OFF'}\n\n"
        "Настройка через бот. Секреты шифруются на диске и не отображаются. Для обычной доставки Bot API ключи не нужны."
    )
    rows = [
        [
            ("Telegram API ID", "conninput:tg_api_id"),
            ("Telegram API hash", "conninput:tg_api_hash"),
        ],
        [("🔗 Connect MTProto", "connconnect")],
        [
            ("📣 Review channel ID", "conninput:channel"),
            ("Auto channel ON/OFF", "conntoggle:auto_channel"),
        ],
        [
            ("YouTube OAuth JSON", "connyoutube"),
            ("Auto Private ON/OFF", "conntoggle:auto_youtube"),
        ],
        [
            ("🔓 Disconnect Telegram", "conndisconnect:telegram"),
            ("🔓 Disconnect YouTube", "conndisconnect:youtube"),
        ],
        [("⚙️ Server Settings", "connruntime")],
        [("↩ Video Generator", "home")],
    ]
    await screen(update, project, text, rows)


async def start_listener(application, uid):
    from telethon import events

    clients = application.bot_data.setdefault("video_mtproto", {})
    if uid in clients:
        await clients[uid].disconnect()
    client = await connections.mtproto_client(uid, application.bot.token)

    async def capture(event):
        if event.is_private and event.sender_id == uid:
            if event.message.media:
                cache = application.bot_data.setdefault("video_mtproto_messages", {})
                cache[(uid, event.message.id)] = event.message
                if len(cache) > 100:
                    cache.pop(next(iter(cache)))
            sender = await event.get_sender()
            if sender and getattr(sender, "access_hash", None):
                data = connections.load(uid)
                data["tg_peer_hash"] = sender.access_hash
                connections.save(uid, data)
        elif event.is_channel:
            channel = await event.get_chat()
            if channel and getattr(channel, "access_hash", None):
                data = connections.load(uid)
                channels = data.setdefault("tg_channels", {})
                channels[str(event.chat_id)] = {
                    "id": channel.id,
                    "hash": channel.access_hash,
                }
                connections.save(uid, data)

    client.add_event_handler(capture, events.NewMessage())
    clients[uid] = client


async def handle_callback(update, context, project, action, parts):
    if not action.startswith("conn"):
        return False
    admin(update)
    uid = update.effective_user.id
    from .handlers import prompt, screen

    if action == "connruntime":
        from .runtime import load as runtime_settings

        runtime = runtime_settings()
        await screen(
            update,
            project,
            "⚙️ Server settings · без .env",
            [
                [
                    (
                        "Concurrent renders: " + str(runtime.concurrent_renders),
                        "conninput:concurrent_renders",
                    )
                ],
                [
                    (
                        "Download limit MB: " + str(runtime.download_limit_mb),
                        "conninput:download_limit_mb",
                    )
                ],
                [
                    (
                        "Upload limit MB: " + str(runtime.upload_limit_mb),
                        "conninput:upload_limit_mb",
                    )
                ],
                [("↩ Connections", "connections")],
            ],
        )
    elif action == "connections":
        await menu(update, context, project)
    elif action == "conninput":
        prompts = {
            "concurrent_renders": "Количество одновременных render jobs: 1..8. Default 1. Уже работающие jobs завершатся нормально.",
            "download_limit_mb": "Download limit MB: 1..2000. Cloud Bot API: 20 MB. Увеличение настройки не меняет лимит Telegram; для больших файлов подключите MTProto.",
            "upload_limit_mb": "Upload limit MB: 1..2000. Cloud Bot API: 50 MB. Для больших файлов используйте MTProto.",
            "tg_api_id": "Введите Telegram API ID с my.telegram.org (целое число).",
            "tg_api_hash": "Введите Telegram API hash с my.telegram.org (32 HEX символа).",
            "channel": "Введите ID закрытого Telegram-канала: -100… Бот должен быть administrator. После подключения MTProto отправьте тестовое сообщение в канал.",
        }
        if parts[0] not in prompts:
            raise MediaError("Unknown credential.")
        await prompt(
            update, context, project, "connection:" + parts[0], prompts[parts[0]]
        )
    elif action == "connconnect":
        await start_listener(context.application, uid)
        await screen(
            update,
            project,
            "✅ MTProto bot session подключена. Отправьте ещё одно сообщение боту (например /videoconnect), чтобы зарегистрировать чат для больших файлов. Личный Telegram login/password не нужен.",
            [[("🔄 Status", "connections"), ("↩ Home", "home")]],
        )
    elif action == "conntoggle":
        key = parts[0]
        if key not in ("auto_channel", "auto_youtube"):
            raise MediaError("Unknown option.")
        data = connections.load(uid)
        if key == "auto_channel" and not data.get("channel"):
            raise MediaError("Сначала укажите review channel ID.")
        if key == "auto_youtube" and not data.get("youtube_credentials"):
            raise MediaError("Сначала подключите YouTube OAuth.")
        data[key] = not data.get(key, False)
        connections.save(uid, data)
        await menu(update, context, project)
    elif action == "connyoutube":
        await prompt(
            update,
            context,
            project,
            "connection:youtube_json",
            "1. В Google Cloud включите YouTube Data API v3.\n2. Создайте OAuth Client: Desktop app, добавьте свой аккаунт в test users.\n3. Отправьте сюда client JSON как document.\n\nAPI key не подходит для upload. Google consent подтверждается в браузере; остальная настройка и сохранение — здесь, без .env.",
        )
    elif action == "conndisconnect":
        data = connections.load(uid)
        if parts[0] == "telegram":
            for key in list(data):
                if key.startswith("tg_"):
                    data.pop(key)
            client = context.application.bot_data.get("video_mtproto", {}).pop(
                uid, None
            )
            if client:
                await client.disconnect()
            data["auto_channel"] = False
        else:
            for key in list(data):
                if key.startswith("youtube_"):
                    data.pop(key)
            data["auto_youtube"] = False
        connections.save(uid, data)
        await menu(update, context, project)
    return True


async def hide_message(update):
    try:
        await update.effective_message.delete()
    except TelegramError:
        pass


async def text_input(update, context, project, action):
    if not action.startswith("connection:"):
        return False
    admin(update)
    key = action.split(":", 1)[1]
    value = update.effective_message.text.strip()
    uid = update.effective_user.id
    await hide_message(update)
    if key in ("concurrent_renders", "download_limit_mb", "upload_limit_mb"):
        from .runtime import load as runtime_settings, save as save_runtime

        if not value.isdigit():
            raise MediaError("Нужно целое положительное число.")
        runtime = runtime_settings()
        setattr(runtime, key, int(value))
        save_runtime(runtime)
        if key == "concurrent_renders":
            await context.application.bot_data["video_queue"].resize(int(value))
    elif key == "youtube_callback":
        try:
            data = await asyncio.to_thread(connections.oauth_complete, uid, value)
            connections.save(uid, data)
        except MediaError:
            raise
        except Exception:
            raise MediaError(
                "Google OAuth не завершён. Проверьте code, OAuth JSON и YouTube Data API."
            )
    else:
        data = connections.load(uid)
        if key == "tg_api_id":
            if not value.isdigit() or not 1 <= int(value) < 2**31:
                raise MediaError("API ID должен быть положительным целым числом.")
            value = int(value)
        elif key == "tg_api_hash":
            if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
                raise MediaError("API hash должен содержать 32 HEX символа.")
        elif key == "channel":
            if not re.fullmatch(r"-100\d{6,}", value):
                raise MediaError("Нужен ID закрытого канала -100…")
            chat = await context.bot.get_chat(int(value))
            if chat.type != "channel" or chat.username:
                raise MediaError(
                    "Для review destination используйте закрытый channel без public username."
                )
            member = await context.bot.get_chat_member(chat.id, context.bot.id)
            if not getattr(member, "can_post_messages", False):
                raise MediaError("Дайте боту права administrator / Post Messages.")
        else:
            raise MediaError("Для этого поля загрузите JSON document.")
        data[key] = value
        connections.save(uid, data)
    context.user_data.pop("vg_input", None)
    await menu(update, context, project)
    return True


async def media_input(update, context, project, action):
    if action != "connection:youtube_json":
        return False
    admin(update)
    from .handlers import download, prompt
    from .library import storage
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory(dir=storage() / "temp") as work:
        path = Path(work) / "client.json"
        doc = update.effective_message.document
        if not doc or (doc.file_size and doc.file_size > 64000):
            raise MediaError("Нужен OAuth client JSON document до 64 KB.")
        await download(update, path)
        await hide_message(update)
        try:
            client = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            raise MediaError("Некорректный JSON.")
        url = connections.oauth_begin(update.effective_user.id, client)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    await update.effective_message.reply_text(
        "Откройте Google consent. После подтверждения браузер перейдёт на localhost:8765 — скопируйте полный URL из адресной строки, даже если страница не открылась. Отправьте URL сюда. Code/state будут проверены, сообщение удалено.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Google OAuth", url=url)]]
        ),
    )
    await prompt(
        update,
        context,
        project,
        "connection:youtube_callback",
        "Отправьте полный redirect URL с code и state:",
    )
    return True


async def command(update, context):
    from .handlers import current, ACTIVE_USERS
    from telegram.ext import ApplicationHandlerStop

    try:
        admin(update)
        ACTIVE_USERS.add(update.effective_user.id)
        await menu(update, context, current(update, context))
    except MediaError as error:
        await update.effective_message.reply_text(str(error))
    raise ApplicationHandlerStop
