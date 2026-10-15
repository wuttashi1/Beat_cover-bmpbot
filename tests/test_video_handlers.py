import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import database
from telegram.ext import ApplicationBuilder, ApplicationHandlerStop
from video_generator import library, projects
from video_generator.handlers import callback, register, mutate_field
from video_generator.models import TrackAnalysis, Section, Drop


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = database.db
        database.db = sqlite3.connect(":memory:", check_same_thread=False)
        database.migrate_video_database()
        self.patch = patch.object(library, "DATA_ROOT", Path(self.temp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        database.db.close()
        database.db = self.original
        self.temp.cleanup()

    def test_section_confirmation_moves_drop(self):
        project = projects.create_project(1)
        project["analysis"] = TrackAnalysis(
            60,
            120,
            sections=[Section(0, 30, "Intro"), Section(30, 60, "Drop")],
            drops=[Drop(28, 0.9, 0.1, 0.8)],
        ).to_dict()
        projects.save_project(project)
        query = SimpleNamespace(
            data=f"vg:{project['id']}:confirmsections",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(type="private", id=1),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(user_data={}, application=SimpleNamespace())

        async def run():
            with self.assertRaises(ApplicationHandlerStop):
                await callback(update, context)

        asyncio.run(run())
        saved = projects.load_project(1, project["id"])
        self.assertTrue(saved["sections_confirmed"])
        self.assertEqual(saved["analysis"]["drops"][0]["timestamp"], 30)

    def test_handler_registration_and_validation(self):
        application = ApplicationBuilder().token("123:TEST").build()
        register(application)
        self.assertTrue(application.post_init)
        self.assertTrue(application.post_stop)
        project = projects.create_project(1)
        with self.assertRaises(ValueError):
            mutate_field(project, "foreground", "opacity", 101)
        self.assertEqual(
            projects.load_project(1, project["id"])["settings"]["foreground"][
                "opacity"
            ],
            100,
        )

    def test_existing_bpm_commands_preserved(self):
        from bot import build_timestamps, parse_audio_filename
        from menu_manager import get_main_keyboard

        self.assertEqual(
            build_timestamps([("Intro", 8), ("Drop", 16)], 120),
            "0:00 - Intro\n0:16 - Drop",
        )
        self.assertEqual(parse_audio_filename("beat 142 BPM.mp3")["bpm"], "142")
        keyboard = get_main_keyboard()
        labels = {b.text for row in keyboard.keyboard for b in row}
        self.assertIn("🎚 BPM", labels)
        self.assertIn("🎬 Видео", labels)
