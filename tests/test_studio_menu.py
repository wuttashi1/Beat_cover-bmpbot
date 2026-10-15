import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from telegram import Update
from telegram.ext import ApplicationBuilder
from telegram.request import BaseRequest

import bot
import bot_ui
import database
from video_generator import library, projects
from video_generator.handlers import ACTIVE_USERS, register


class TelegramRequest(BaseRequest):
    def __init__(self):
        self.calls = []
        self.next_id = 100
        picture = Image.new("RGB", (64, 96), "#405060")
        data = BytesIO()
        picture.save(data, "PNG")
        self.picture = data.getvalue()
        self.fail_edit = False

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        api = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((api, params))
        if "/file/" in url:
            return 200, self.picture
        if api == "getMe":
            result = {
                "id": 999,
                "is_bot": True,
                "first_name": "Studio",
                "username": "test_bot",
            }
        elif api in ("deleteMessage", "answerCallbackQuery"):
            result = True
        elif api == "getFile":
            result = {
                "file_id": "image",
                "file_unique_id": "image",
                "file_path": "image.png",
            }
        else:
            if self.fail_edit and api == "editMessageText":
                self.fail_edit = False
                return 400, json.dumps(
                    {
                        "ok": False,
                        "description": "Bad Request: message to edit not found",
                    }
                ).encode()
            if api.startswith("send"):
                self.next_id += 1
            result = {
                "message_id": params.get("message_id", self.next_id),
                "date": 1,
                "chat": {"id": params.get("chat_id", 1), "type": "private"},
                "text": params.get("text", "Panel"),
            }
        return 200, json.dumps({"ok": True, "result": result}).encode()


class StudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = database.db, database.cur
        database.db = sqlite3.connect(":memory:", check_same_thread=False)
        database.cur = database.db.cursor()
        database.db.executescript(
            """CREATE TABLE users(user_id INTEGER PRIMARY KEY,style TEXT DEFAULT 'vevo',vevo_wm_size INTEGER DEFAULT 450,explicit_wm_size INTEGER DEFAULT 300,explicit_blur INTEGER DEFAULT 10,explicit_fg_size INTEGER DEFAULT 820,explicit_quality TEXT DEFAULT 'good',explicit_format TEXT DEFAULT 'photo',notifications_enabled INTEGER DEFAULT 0);CREATE TABLE channel_publishers(user_id INTEGER PRIMARY KEY);"""
        )
        database.migrate_video_database()
        self.storage = patch.object(library, "DATA_ROOT", self.root / "media")
        self.storage.start()
        self.presets = patch.object(bot, "PRESETS_DIR", str(self.root / "presets"))
        self.presets.start()
        bot_ui._PANELS.clear()
        ACTIVE_USERS.discard(1)
        self.request = TelegramRequest()
        self.app = (
            ApplicationBuilder()
            .token("999:TEST")
            .request(self.request)
            .get_updates_request(TelegramRequest())
            .build()
        )
        register(self.app)
        await self.app.initialize()
        self.sequence = 0

    async def asyncTearDown(self):
        await self.app.shutdown()
        self.storage.stop()
        self.presets.stop()
        ACTIVE_USERS.discard(1)
        bot_ui._PANELS.clear()
        database.db.close()
        database.db, database.cur = self.original
        self.temp.cleanup()

    async def send(self, text=None, callback=None, photo=False):
        self.sequence += 1
        message = {
            "message_id": self.sequence,
            "date": 1,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "first_name": "User", "is_bot": False},
        }
        data = {"update_id": self.sequence}
        if callback:
            message.update(text="Panel", message_id=101)
            data["callback_query"] = {
                "id": str(self.sequence),
                "from": message["from"],
                "chat_instance": "test",
                "message": message,
                "data": callback,
            }
        else:
            if photo:
                message["photo"] = [
                    {
                        "file_id": "image",
                        "file_unique_id": "image",
                        "width": 64,
                        "height": 96,
                    }
                ]
            else:
                message["text"] = text
                if text.startswith("/"):
                    message["entities"] = [
                        {
                            "type": "bot_command",
                            "offset": 0,
                            "length": len(text.split()[0]),
                        }
                    ]
            data["message"] = message
        await self.app.process_update(Update.de_json(data, self.app.bot))

    def calls(self, api):
        return [p for name, p in self.request.calls if name == api]

    async def test_single_panel_for_start_settings_and_bpm(self):
        await self.send("/start")
        await self.send(callback="ui:covers")
        await self.send(callback="ui:cover-field:vevo_wm_size")
        await self.send(callback="ui:cover-adjust:vevo_wm_size:-10")
        await self.send("410")
        self.assertEqual(database.get_user_settings(1)["vevo_wm_size"], 410)
        await self.send(callback="ui:bpm")
        await self.send("142")
        await self.send(callback="ui:tempo:1")
        self.assertEqual(self.app.user_data[1]["last_bpm"], 143)
        self.assertEqual(len(self.calls("sendMessage")), 1)
        self.assertGreater(len(self.calls("editMessageText")), 5)
        self.assertTrue(self.calls("deleteMessage"))
        self.assertIn("143 BPM", self.calls("editMessageText")[-1]["text"])

    async def test_switching_from_video_clears_overlay_prompt(self):
        await self.send("/video")
        project = self.app.user_data[1]["vg_project"]
        await self.send(callback=f"vg:{project}:addlayer:overlays")
        self.assertIn("vg_input", self.app.user_data[1])
        await self.send(callback="ui:covers")
        self.assertNotIn("vg_input", self.app.user_data[1])
        self.assertNotIn(1, ACTIVE_USERS)
        await self.send(callback="ui:bpm")
        await self.send("150")
        await self.send(callback="ui:video")
        self.assertIn(1, ACTIVE_USERS)
        self.assertNotIn("ui_input", self.app.user_data[1])
        self.assertEqual(len(self.calls("sendMessage")), 1)

    async def test_cover_preview_reuses_image_and_message(self):
        await self.send("/cover")
        await self.send(photo=True)
        self.assertEqual(len(self.calls("sendPhoto")), 1)
        self.assertTrue(Path(self.app.user_data[1]["cover_ui_source"]).exists())
        await self.send(callback="ui:cover-field:vevo_wm_size")
        await self.send("300")
        await self.send(callback="ui:cover-preview")
        self.assertEqual(len(self.calls("sendPhoto")), 1)
        self.assertEqual(len(self.calls("editMessageMedia")), 1)
        self.assertEqual(len(self.calls("getFile")), 1)
        self.assertEqual(len(self.calls("sendMessage")), 1)

    async def test_bpm_preset_command_remains_in_same_panel(self):
        await self.send("/bpm")
        await self.send("142")
        await self.send("/savepreset")
        await self.send("Trap 142")
        self.assertIn("Trap 142", bot.load_presets(1))
        self.assertEqual(len(self.calls("sendMessage")), 1)

    async def test_deleted_panel_recovers_and_values_are_escaped(self):
        await self.send("/start")
        self.request.fail_edit = True
        await self.send(callback="ui:covers")
        self.assertEqual(len(self.calls("sendMessage")), 2)
        await self.send(callback="ui:bpm")
        await self.send("not a number")
        self.assertEqual(len(self.calls("sendMessage")), 2)
        self.assertIn("Проверьте значение", self.calls("editMessageText")[-1]["text"])
        await self.send(callback="ui:video")
        project = projects.load_project(1, self.app.user_data[1]["vg_project"])
        project["name"] = "Beat <demo>&1"
        projects.save_project(project)
        await self.send(callback=f"vg:{project['id']}:home")
        self.assertIn("&lt;demo&gt;&amp;1", self.calls("editMessageText")[-1]["text"])

    async def test_video_settings_have_friendly_names_and_navigation(self):
        await self.send("/video")
        pid = self.app.user_data[1]["vg_project"]
        await self.send(callback=f"vg:{pid}:field:background:blur")
        panel = self.calls("editMessageText")[-1]
        self.assertIn("Размытие", panel["text"])
        self.assertIn("●", panel["text"])
        raw = panel["reply_markup"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        callbacks = [b["callback_data"] for row in raw["inline_keyboard"] for b in row]
        self.assertIn("ui:covers", callbacks)
        self.assertIn("ui:bpm", callbacks)

    async def test_job_stages_edit_one_status_message(self):
        from video_generator.queue import RenderQueue

        queue = RenderQueue(self.app.bot)
        await queue.status(7, 1, "Анализ…")
        await queue.status(7, 1, "Рендер · 37%")
        await queue.status(7, 1, "Готово", terminal=True)
        self.assertEqual(len(self.calls("sendMessage")), 1)
        self.assertEqual(len(self.calls("editMessageText")), 2)
