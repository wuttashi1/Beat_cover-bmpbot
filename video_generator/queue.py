import asyncio
import hashlib
import json
import logging
import os
import signal
import shutil
import sys
import tempfile
import time
from pathlib import Path

from database import video_execute, video_rows
from . import library, projects
from .analyzer import ANALYSIS_VERSION
from .config import MAX_RENDERS, VideoRenderConfig
from .media import MediaError, file_hash, probe, safe_name
from .models import TrackAnalysis, timestamps
from .connections import progress_bar
from .renderer import build_command, run_render, select_clips

logger = logging.getLogger(__name__)


def analysis_signature(project):
    options = {
        "bpm": project.get("bpm_hint"),
        "analysis": project["settings"]["analysis"],
        "quiet": {
            key: project["settings"]["blackout"][key]
            for key in ("sensitivity", "minimum_ms", "maximum_ms")
        },
    }
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()


class RenderQueue:
    def __init__(self, bot, concurrency=MAX_RENDERS):
        self.bot = bot
        self.queue = asyncio.Queue()
        self.workers = []
        self.cancellations = {}
        self.concurrency = concurrency
        self.capacity = asyncio.Condition()
        self.running = 0
        self.status_messages = {}

    async def start(self):
        library.storage()
        video_execute(
            "UPDATE video_render_jobs SET status='failed',error='Бот перезапущен: повторите job.',updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','analyzing','rendering')"
        )
        self.workers = [asyncio.create_task(self.worker()) for _ in range(8)]

    async def resize(self, concurrency):
        if not 1 <= concurrency <= 8:
            raise ValueError("Concurrent renders: 1..8.")
        async with self.capacity:
            self.concurrency = concurrency
            self.capacity.notify_all()

    async def close(self):
        for event in self.cancellations.values():
            event.set()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    async def enqueue(self, project, chat_id, kind="render", preview_range=None):
        if not project.get("beat") or not Path(project["beat"]).is_file():
            raise MediaError("Сначала отправьте beat.")
        if kind != "analysis":
            if not project.get("analysis"):
                raise MediaError("Дождитесь анализа.")
            if kind == "render" and not project.get("sections_confirmed"):
                raise MediaError("Перед Render подтвердите Sections.")
            TrackAnalysis.from_dict(project["analysis"])
            if project.get("analysis_signature") and project[
                "analysis_signature"
            ] != analysis_signature(project):
                raise MediaError(
                    "Настройки Audio Analysis / Quiet Detection изменены. Нажмите Analysis → Re-analyze перед Preview / Render."
                )
            videos = library.assets(
                project["user_id"], project["folder_ids"], project.get("tags")
            )
            select_clips(project, videos, project["analysis"]["duration"])
            VideoRenderConfig.from_dict(project["settings"]).validate(
                project["analysis"]["duration"]
            )
        active = video_rows(
            "SELECT id FROM video_render_jobs WHERE user_id=? AND status IN ('queued','analyzing','rendering')",
            (project["user_id"],),
        )
        if len(active) >= 3:
            raise MediaError(
                "У вас уже 3 jobs в очереди. Дождитесь завершения или отмените один."
            )
        payload = {
            "project": project,
            "chat_id": chat_id,
            "kind": kind,
            "preview_range": preview_range,
        }
        jid = video_execute(
            "INSERT INTO video_render_jobs(user_id,project_id,status,payload) VALUES (?,?,?,?)",
            (project["user_id"], project["id"], "queued", json.dumps(payload)),
        )
        self.cancellations[jid] = asyncio.Event()
        await self.queue.put(jid)
        return jid

    def cancel(self, uid, jid):
        current = projects.job(uid, jid)
        if current["status"] not in ("queued", "analyzing", "rendering"):
            raise MediaError("Job уже завершён.")
        event = self.cancellations.get(jid)
        if event:
            event.set()
        projects.job_update(uid, jid, status="cancelled")

    async def notify(self, chat_id, text, **kwargs):
        from telegram.error import TelegramError

        try:
            return await self.bot.send_message(chat_id, text, **kwargs)
        except TelegramError as error:
            logger.warning("Job notification failed: %s", error)
            return None

    async def status(self, jid, chat_id, text, reply_markup=None, terminal=False):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.error import BadRequest, TelegramError

        if reply_markup is None:
            rows = (
                []
                if terminal
                else [
                    [InlineKeyboardButton("Отменить", callback_data=f"vg:cancel:{jid}")]
                ]
            )
            rows.append([InlineKeyboardButton("Задания", callback_data="vg:jobs")])
            reply_markup = InlineKeyboardMarkup(rows)
        previous = self.status_messages.get(jid)
        if previous:
            try:
                await previous.edit_text(text, reply_markup=reply_markup)
                return previous
            except BadRequest as error:
                if "not modified" in str(error).lower():
                    return previous
                if not any(
                    value in str(error).lower()
                    for value in ("not found", "can't be edited", "can not be edited")
                ):
                    raise
            except TelegramError:
                return previous
        message = await self.notify(chat_id, text, reply_markup=reply_markup)
        if message:
            self.status_messages[jid] = message
        return message

    async def analyze(self, project, work, cancel):
        cfg = VideoRenderConfig.from_dict(project["settings"])
        digest = await asyncio.to_thread(file_hash, project["beat"])
        options = {
            "analysis": project["settings"]["analysis"],
            "quiet": {
                key: project["settings"]["blackout"][key]
                for key in ("sensitivity", "minimum_ms", "maximum_ms")
            },
            "bpm": project.get("bpm_hint"),
            "version": ANALYSIS_VERSION,
            "hash": digest,
        }
        key = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()
        cached = projects.analysis_get(key)
        if cached:
            TrackAnalysis.from_dict(cached)
            return cached
        output = Path(work) / "analysis.json"
        settings = Path(work) / "settings.json"
        settings.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
        args = [
            sys.executable,
            "-m",
            "video_generator.analyzer",
            project["beat"],
            str(output),
            "--settings",
            str(settings),
        ]
        if project.get("bpm_hint"):
            args += ["--bpm", str(project["bpm_hint"])]
        with open(Path(work) / "analysis.log", "wb") as log:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            waiter = asyncio.create_task(process.wait())
            try:
                while process.returncode is None:
                    if cancel.is_set():
                        os.killpg(process.pid, signal.SIGTERM)
                        raise asyncio.CancelledError
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter), 0.3)
                    except asyncio.TimeoutError:
                        continue
                if process.returncode:
                    preserved = (
                        library.storage() / "renders" / f"analysis_{project['id']}.log"
                    )
                    shutil.copy2(Path(work) / "analysis.log", preserved)
                    raise MediaError(
                        f"Не удалось проанализировать audio. Log: {preserved}"
                    )
            finally:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), 3)
                    except asyncio.TimeoutError:
                        os.killpg(process.pid, signal.SIGKILL)
                        await process.wait()
                await asyncio.gather(waiter, return_exceptions=True)
        data = json.loads(output.read_text())
        TrackAnalysis.from_dict(data)
        projects.analysis_put(key, data)
        return data

    async def deliver(self, chat_id, path, analysis, jid, preview=False):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.error import TelegramError

        from .runtime import load as runtime_settings

        upload_limit = runtime_settings().upload_limit_mb
        if Path(path).stat().st_size > upload_limit * 1024 * 1024:
            from .connections import load, send_large

            credentials = load(chat_id)
            if credentials.get("tg_peer_hash"):
                from telethon.tl.types import InputPeerUser

                message = await self.notify(
                    chat_id, "📤 Отправляю через Telegram MTProto…"
                )
                last = 0

                async def transfer_progress(value):
                    nonlocal last
                    if time.monotonic() - last > 4 and message:
                        last = time.monotonic()
                        try:
                            await message.edit_text("📤 Upload\n" + progress_bar(value))
                        except TelegramError:
                            pass

                try:
                    await send_large(
                        chat_id,
                        self.bot.token,
                        InputPeerUser(chat_id, credentials["tg_peer_hash"]),
                        path,
                        "👁 Preview" if preview else "✅ Готово",
                        transfer_progress,
                    )
                except MediaError as error:
                    await self.notify(chat_id, f"{error} Файл: {path}")
            else:
                await self.notify(
                    chat_id,
                    f"✅ Видео готово, но превышает лимит Telegram {upload_limit} MB. Настройте MTProto в Connections. Файл на сервере: {path}",
                )
        else:
            try:
                with open(path, "rb") as file:
                    await self.bot.send_document(
                        chat_id,
                        document=file,
                        filename=Path(path).name,
                        caption="👁 Preview"
                        if preview
                        else f"✅ Готово · {analysis['bpm']:g} BPM",
                        read_timeout=120,
                        write_timeout=120,
                    )
            except TelegramError as error:
                logger.warning("Video upload failed job %s: %s", jid, error)
                await self.notify(
                    chat_id,
                    f"Видео готово, но Telegram не принял загрузку. Файл сохранён: {path}. Можно повторить отправку в Jobs.",
                )
        if not preview:
            content = f"{analysis['bpm']:g} BPM · Секции\n\n{timestamps(analysis)}"
            controls = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📋 YouTube timestamps",
                            callback_data=f"vg:jobtimestamps:{jid}",
                        )
                    ]
                ]
            )
            for index in range(0, len(content), 3500):
                await self.notify(
                    chat_id,
                    content[index : index + 3500],
                    reply_markup=controls if index + 3500 >= len(content) else None,
                )

    async def auto_publish(self, uid, chat_id, output, project, analysis, cancel):
        from .connections import load, send_large, upload_youtube
        from telegram.error import TelegramError

        data = load(uid)
        from .runtime import load as runtime_settings

        upload_limit = runtime_settings().upload_limit_mb
        if data.get("auto_channel") and data.get("channel"):
            try:
                channel = (
                    int(data["channel"])
                    if str(data["channel"]).lstrip("-").isdigit()
                    else data["channel"]
                )
                chat = await self.bot.get_chat(channel)
                if chat.type != "channel" or chat.username:
                    raise MediaError(
                        "Draft destination должен быть закрытым Telegram-каналом без public username."
                    )
                caption = f"{project['name']} · {analysis['bpm']:g} BPM"
                if Path(output).stat().st_size > upload_limit * 1024 * 1024:
                    await send_large(uid, self.bot.token, channel, output, caption)
                else:
                    with open(output, "rb") as file:
                        await self.bot.send_document(
                            channel,
                            file,
                            caption=caption,
                            write_timeout=120,
                            read_timeout=120,
                        )
                await self.notify(
                    chat_id, "✅ Видео отправлено в закрытый review channel."
                )
            except Exception as error:
                logger.warning("Review channel upload failed: %s", type(error).__name__)
                await self.notify(
                    chat_id,
                    "❌ Не удалось отправить в review channel. Проверьте destination и права бота. MP4 сохранён.",
                )
        if data.get("auto_youtube") and data.get("youtube_credentials"):
            message = await self.notify(chat_id, "📤 YouTube · Private upload…")
            last = 0

            async def report(value):
                nonlocal last
                if time.monotonic() - last > 4 and message:
                    last = time.monotonic()
                    try:
                        await message.edit_text(
                            "📤 YouTube Private\n" + progress_bar(value)
                        )
                    except TelegramError:
                        pass

            try:
                url = await upload_youtube(
                    uid,
                    output,
                    project["name"],
                    f"BPM: {analysis['bpm']:g}\n\n" + timestamps(analysis),
                    report,
                    cancel,
                )
                await self.notify(chat_id, "✅ YouTube · Private\n" + url)
            except Exception as error:
                logger.warning("YouTube upload failed: %s", type(error).__name__)
                await self.notify(
                    chat_id,
                    "❌ YouTube upload failed. Проверьте OAuth, quota и доступ к YouTube Data API. MP4 сохранён.",
                )

    async def worker(self):
        while True:
            jid = await self.queue.get()
            acquired = False
            try:
                async with self.capacity:
                    await self.capacity.wait_for(
                        lambda: self.running < self.concurrency
                    )
                    self.running += 1
                    acquired = True
                await self.process(jid)
            finally:
                if acquired:
                    async with self.capacity:
                        self.running -= 1
                        self.capacity.notify_all()
                self.queue.task_done()
                self.cancellations.pop(jid, None)
                self.status_messages.pop(jid, None)

    async def process(self, jid):
        rows = video_rows("SELECT * FROM video_render_jobs WHERE id=?", (jid,))
        current = rows[0]
        uid = current["user_id"]
        payload = json.loads(current["payload"])
        project = payload["project"]
        chat_id = payload["chat_id"]
        kind = payload["kind"]
        cancel = self.cancellations[jid]
        if current["status"] == "cancelled" or cancel.is_set():
            return
        root = library.storage()
        output = None
        try:
            if shutil.disk_usage(root).free < 256 * 1024 * 1024:
                raise MediaError("Недостаточно свободного места на диске.")
            with tempfile.TemporaryDirectory(
                prefix=f"job_{jid}_", dir=root / "temp"
            ) as work:
                analysis = project.get("analysis")
                if kind == "analysis" or not analysis:
                    projects.job_update(uid, jid, status="analyzing")
                    await self.status(
                        jid,
                        chat_id,
                        f"🧠 Анализ аудио · #{jid}\n\nОпределяем секции, Drop и тихие паузы…",
                    )
                    analysis = await self.analyze(project, work, cancel)
                    project["analysis"] = analysis
                    project["sections_confirmed"] = False
                    latest = projects.load_project(uid, project["id"])
                    if (
                        latest.get("beat") == project["beat"]
                        and latest["settings"]["analysis"]
                        == project["settings"]["analysis"]
                    ):
                        latest["analysis"] = analysis
                        latest["analysis_signature"] = analysis_signature(project)
                        latest["sections_confirmed"] = False
                        projects.save_project(latest)
                if cancel.is_set():
                    raise asyncio.CancelledError
                if kind == "analysis":
                    projects.job_update(uid, jid, status="done", progress=1)
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                    await self.status(
                        jid,
                        chat_id,
                        f"✓ Анализ готов · #{jid}\n\n{analysis['bpm']:g} BPM\nСекции · {len(analysis['sections'])}\nDrop · {len(analysis['drops'])}\nТихие паузы · {len(analysis['quiet_regions'])}\n\nПроверьте секции перед рендером.",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "📝 Открыть проект",
                                        callback_data=f"vg:open:{project['id']}",
                                    )
                                ]
                            ]
                        ),
                    )
                    return
                cfg = VideoRenderConfig.from_dict(project["settings"])
                selected = select_clips(
                    project,
                    library.assets(uid, project["folder_ids"], project.get("tags")),
                    analysis["duration"],
                )
                range_ = payload.get("preview_range")
                if kind == "preview" and not range_:
                    drops = [d for d in analysis["drops"] if d.get("enabled", True)]
                    point = (
                        max(drops, key=lambda d: d["confidence"])["timestamp"]
                        if drops
                        else 3
                    )
                    range_ = [max(0, point - 3), min(analysis["duration"], point + 7)]
                cache_data = {
                    "project": project,
                    "range": range_,
                    "version": 1,
                    "sources": [
                        (c.path, Path(c.path).stat().st_mtime_ns) for c in selected
                    ],
                    "layers": [
                        (o.source, Path(o.source).stat().st_mtime_ns)
                        for o in cfg.overlays
                    ],
                }
                cache_key = hashlib.sha256(
                    json.dumps(cache_data, sort_keys=True).encode()
                ).hexdigest()
                if kind == "preview":
                    output = root / "previews" / str(uid) / f"{cache_key}.mp4"
                else:
                    tags = project.get("metadata", {})
                    basename = (
                        "_".join(
                            x for x in (tags.get("artist"), tags.get("title")) if x
                        )
                        or Path(project["beat"]).stem
                    )
                    output = (
                        root
                        / "renders"
                        / str(uid)
                        / str(jid)
                        / f"{safe_name(basename)}_video.mp4"
                    )
                output.parent.mkdir(parents=True, exist_ok=True)
                projects.job_update(uid, jid, status="rendering")
                if not (kind == "preview" and output.is_file()):
                    command = await asyncio.to_thread(
                        build_command,
                        project["beat"],
                        selected,
                        cfg,
                        analysis,
                        output,
                        work,
                        project["seed"],
                        range_,
                        project.get("metadata"),
                    )
                    needed = command.duration * (
                        cfg.export.audio_bitrate * 125
                        + command.width * command.height * command.fps * 0.02
                    )
                    if shutil.disk_usage(root).free < max(
                        256 * 1024 * 1024, needed * 1.5
                    ):
                        raise MediaError(
                            "Недостаточно свободного места для render. Освободите место или уменьшите качество."
                        )
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                    message = await self.status(
                        jid,
                        chat_id,
                        f"🎬 Подготовка видео · #{jid}\n\n{progress_bar(0)}",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel render",
                                        callback_data=f"vg:cancel:{jid}",
                                    )
                                ]
                            ]
                        ),
                    )
                    last = 0

                    async def progress(value):
                        nonlocal last
                        if cancel.is_set():
                            return
                        projects.job_update(uid, jid, progress=value)
                        if time.monotonic() - last > 4 and message:
                            last = time.monotonic()
                            from telegram.error import TelegramError

                            try:
                                await message.edit_text(
                                    f"🎬 Рендер · #{jid}\n\n{progress_bar(value)}",
                                    reply_markup=message.reply_markup,
                                )
                            except TelegramError:
                                pass

                    try:
                        await run_render(
                            command, output.with_suffix(".log"), cancel, progress
                        )
                    except MediaError:
                        if not command.hardware or not cfg.export.fallback_cpu:
                            raise
                        shutil.copy2(
                            output.with_suffix(".log"),
                            output.with_suffix(".hardware.log"),
                        )
                        cfg.export.encoder = "CPU"
                        await self.notify(
                            chat_id, "Hardware encoder failed. Повторяю на CPU."
                        )
                        command = await asyncio.to_thread(
                            build_command,
                            project["beat"],
                            selected,
                            cfg,
                            analysis,
                            output,
                            work,
                            project["seed"],
                            range_,
                            project.get("metadata"),
                        )
                        await run_render(
                            command, output.with_suffix(".log"), cancel, progress
                        )
                    info = await asyncio.to_thread(probe, output, "video")
                    audio_info = await asyncio.to_thread(probe, output, "audio")
                    if (
                        info["width"] != command.width
                        or info["height"] != command.height
                        or abs(info["duration"] - command.duration) > 0.15
                        or abs(audio_info["duration"] - command.duration) > 0.15
                    ):
                        raise MediaError(
                            "Output verification failed: duration/resolution."
                        )
                if cancel.is_set():
                    raise asyncio.CancelledError
                projects.job_update(
                    uid, jid, status="done", progress=1, output=str(output)
                )
                await self.status(
                    jid,
                    chat_id,
                    f"✓ Видео готово · #{jid}\n\n{progress_bar(1)}",
                    terminal=True,
                )
                await self.deliver(chat_id, output, analysis, jid, kind == "preview")
                if kind == "render":
                    await self.auto_publish(
                        uid, chat_id, output, project, analysis, cancel
                    )
        except asyncio.CancelledError:
            if output and projects.job(uid, jid)["status"] != "done":
                output.unlink(missing_ok=True)
            if projects.job(uid, jid)["status"] in ("queued", "analyzing", "rendering"):
                projects.job_update(uid, jid, status="cancelled")
            await self.status(jid, chat_id, f"Рендер отменён · #{jid}", terminal=True)
            if asyncio.current_task().cancelling():
                raise
        except Exception as error:
            logger.exception("Render job %s failed", jid)
            if output and projects.job(uid, jid)["status"] != "done":
                output.unlink(missing_ok=True)
            if projects.job(uid, jid)["status"] in ("queued", "analyzing", "rendering"):
                projects.job_update(uid, jid, status="failed", error=str(error))
            text = (
                str(error)
                if isinstance(error, (MediaError, ValueError))
                else "Не удалось выполнить job. Проверьте server log."
            )
            await self.status(
                jid,
                chat_id,
                f"Не удалось завершить · #{jid}\n\n{text[:1500]}",
                terminal=True,
            )
