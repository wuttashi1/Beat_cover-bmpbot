import asyncio
import json
import logging
import os
import time
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet

from database import video_execute, video_rows
from .library import storage
from .media import MediaError

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def cipher():
    path = storage() / "connections.key"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as file:
            file.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def load(uid):
    rows = video_rows("SELECT encrypted FROM video_connections WHERE user_id=?", (uid,))
    return json.loads(cipher().decrypt(rows[0]["encrypted"])) if rows else {}


def save(uid, data):
    token = cipher().encrypt(json.dumps(data).encode())
    video_execute(
        "INSERT OR REPLACE INTO video_connections(user_id,encrypted) VALUES (?,?)",
        (uid, token),
    )


def progress_bar(value, width=16):
    value = max(0, min(1, value))
    filled = int(value * width)
    return "▰" * filled + "▱" * (width - filled) + f" {round(value * 100)}%"


async def mtproto_client(uid, bot_token):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    data = load(uid)
    if not data.get("tg_api_id") or not data.get("tg_api_hash"):
        raise MediaError("Настройте Telegram API ID и API hash в Connections.")
    client = TelegramClient(
        StringSession(data.get("tg_session", "")),
        int(data["tg_api_id"]),
        data["tg_api_hash"],
        request_retries=2,
        connection_retries=2,
    )
    try:
        await client.start(bot_token=bot_token)
        # Save only the bot session; no personal Telegram account login is needed.
        data["tg_session"] = client.session.save()
        save(uid, data)
        return client
    except Exception:
        await client.disconnect()
        raise MediaError(
            "Не удалось подключить Telegram MTProto. Проверьте API ID / hash и соединение."
        )


async def send_large(uid, bot_token, destination, path, caption, progress=None):
    client = await mtproto_client(uid, bot_token)
    try:

        async def report(sent, total):
            if progress:
                await progress(sent / max(total, 1))

        if isinstance(destination, int) and destination < 0:
            from telethon.tl.types import InputPeerChannel

            channel = load(uid).get("tg_channels", {}).get(str(destination))
            if not channel:
                raise MediaError(
                    "Отправьте test message в канал после подключения MTProto."
                )
            entity = InputPeerChannel(channel["id"], channel["hash"])
        else:
            entity = await client.get_input_entity(destination)
        await client.send_file(
            entity,
            str(path),
            caption=caption,
            force_document=True,
            progress_callback=report,
        )
    except Exception as error:
        logger.warning("MTProto upload failed (%s)", type(error).__name__)
        raise MediaError(
            "Telegram MTProto не принял файл. Проверьте подключение и доступ бота к чату. Файл сохранён."
        )
    finally:
        await client.disconnect()


def oauth_begin(uid, client_config):
    from google_auth_oauthlib.flow import Flow

    if "installed" not in client_config:
        raise MediaError(
            "Создайте OAuth Client типа Desktop app и загрузите его JSON. API key не даёт права upload."
        )
    flow = Flow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = "http://localhost:8765/"
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    data = load(uid)
    data["youtube_client"] = client_config
    data["youtube_pending"] = {
        "state": state,
        "verifier": flow.code_verifier,
        "expires": time.time() + 900,
    }
    save(uid, data)
    return url


def oauth_complete(uid, redirect_url):
    from google_auth_oauthlib.flow import Flow

    data = load(uid)
    pending = data.get("youtube_pending")
    if not pending or pending["expires"] < time.time():
        raise MediaError("OAuth ссылка истекла. Загрузите OAuth JSON снова.")
    query = parse_qs(urlparse(redirect_url).query)
    if query.get("state", [None])[0] != pending["state"] or not query.get("code"):
        raise MediaError("Нужен полный redirect URL с code и совпадающим state.")
    flow = Flow.from_client_config(
        data["youtube_client"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
        state=pending["state"],
        code_verifier=pending["verifier"],
    )
    flow.redirect_uri = "http://localhost:8765/"
    flow.fetch_token(code=query["code"][0])
    credentials = json.loads(flow.credentials.to_json())
    if not credentials.get("refresh_token"):
        raise MediaError("Google не вернул refresh token. Повторите OAuth consent.")
    data["youtube_credentials"] = credentials
    data.pop("youtube_pending", None)
    return data


async def upload_youtube(uid, path, title, description, progress=None, cancel=None):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    data = load(uid)
    if not data.get("youtube_credentials"):
        raise MediaError("Подключите YouTube OAuth в Connections.")
    credentials = Credentials.from_authorized_user_info(data["youtube_credentials"])

    def prepare():
        if not credentials.valid:
            credentials.refresh(Request())
        service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        return service, service.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title[:100] or "Beat",
                    "description": description[:5000],
                    "categoryId": "10",
                },
                "status": {
                    "privacyStatus": "private",
                    "selfDeclaredMadeForKids": False,
                },
            },
            media_body=MediaFileUpload(
                str(path),
                mimetype="video/mp4",
                chunksize=8 * 1024 * 1024,
                resumable=True,
            ),
        )

    service, request = await asyncio.to_thread(prepare)
    try:
        response = None
        while response is None:
            if cancel and cancel.is_set():
                raise asyncio.CancelledError
            status, response = await asyncio.to_thread(
                request.next_chunk, num_retries=3
            )
            if status and progress:
                await progress(status.progress())
        data = load(uid)
        data["youtube_credentials"] = json.loads(credentials.to_json())
        save(uid, data)
        return "https://www.youtube.com/watch?v=" + response["id"]
    finally:
        service.close()
