import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    MessageHandler,
    filters,
)
from telegram.request import BaseRequest
from video_generator import library, projects
from video_generator.handlers import ACTIVE_USERS, message, register


class MockRequest(BaseRequest):
    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        return 200, json.dumps(
            {
                "ok": True,
                "result": {
                    "id": 999,
                    "is_bot": True,
                    "first_name": "Studio",
                    "username": "test_bot",
                },
            }
        ).encode()


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = database.db
        database.db = sqlite3.connect(":memory:", check_same_thread=False)
        database.migrate_video_database()
        self.patch = patch.object(library, "DATA_ROOT", Path(self.temp.name))
        self.patch.start()
        self.project = projects.create_project(1)
        ACTIVE_USERS.add(1)
        self.context = SimpleNamespace(user_data={"vg_project": self.project["id"]})

    def tearDown(self):
        ACTIVE_USERS.discard(1)
        self.patch.stop()
        database.db.close()
        database.db = self.original
        self.temp.cleanup()

    def update(self, text=None, photo=None, audio=None):
        incoming = SimpleNamespace(
            text=text, photo=photo, audio=audio, document=None, reply_text=AsyncMock()
        )
        return SimpleNamespace(
            effective_message=incoming, effective_user=SimpleNamespace(id=1)
        )

    def test_old_buttons_leave_even_pending_video_input(self):
        self.context.user_data["vg_input"] = {
            "project_id": self.project["id"],
            "action": "overlay",
        }
        asyncio.run(message(self.update("🎨 Выбор стиля"), self.context))
        self.assertNotIn(1, ACTIVE_USERS)
        self.assertNotIn("vg_input", self.context.user_data)

    def test_unprompted_text_and_cover_photo_pass_through(self):
        for update in (self.update("обычное сообщение"), self.update(photo=[object()])):
            ACTIVE_USERS.add(1)
            asyncio.run(message(update, self.context))
            self.assertNotIn(1, ACTIVE_USERS)
            update.effective_message.reply_text.assert_not_awaited()

    def test_explicit_overlay_and_audio_remain_video_inputs(self):
        async def run():
            with patch(
                "video_generator.handlers.media_input", new_callable=AsyncMock
            ) as media:
                self.context.user_data["vg_input"] = {
                    "project_id": self.project["id"],
                    "action": "overlay",
                }
                with self.assertRaises(ApplicationHandlerStop):
                    await message(self.update(photo=[object()]), self.context)
                self.assertEqual(media.await_args.args[-1], "overlay")
                self.context.user_data.pop("vg_input")
                with self.assertRaises(ApplicationHandlerStop):
                    await message(self.update(audio=object()), self.context)
                self.assertEqual(media.await_args.args[-1], "beat")

        asyncio.run(run())

    def test_application_routes_photo_to_existing_handler(self):
        async def run():
            request = MockRequest()
            app = (
                ApplicationBuilder()
                .token("999:TEST")
                .request(request)
                .get_updates_request(MockRequest())
                .build()
            )
            register(app)
            legacy = AsyncMock()
            app.add_handler(MessageHandler(filters.PHOTO, legacy))
            await app.initialize()
            try:
                app.user_data[1]["vg_project"] = self.project["id"]
                update = Update.de_json(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 1,
                            "date": 1,
                            "chat": {"id": 1, "type": "private"},
                            "from": {"id": 1, "first_name": "User", "is_bot": False},
                            "photo": [
                                {
                                    "file_id": "photo",
                                    "file_unique_id": "photo",
                                    "width": 100,
                                    "height": 100,
                                }
                            ],
                        },
                    },
                    app.bot,
                )
                await app.process_update(update)
                legacy.assert_awaited_once()
                self.assertNotIn(1, ACTIVE_USERS)
            finally:
                await app.shutdown()

        asyncio.run(run())
