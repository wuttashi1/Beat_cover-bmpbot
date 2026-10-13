import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import database
from video_generator import library, projects
from video_generator.config import VideoRenderConfig
from video_generator.models import TrackAnalysis, Section
from video_generator.renderer import select_clips
from video_generator.queue import RenderQueue


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = database.db
        database.db = sqlite3.connect(":memory:", check_same_thread=False)
        database.migrate_video_database()
        self.patch = patch.object(library, "DATA_ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        database.db.close()
        database.db = self.database
        self.temp.cleanup()

    def test_library_metadata_dedup_move_and_access(self):
        source = self.root / "clip.mp4"
        source.write_bytes(b"test-video")
        folder = library.create_folder(1, "cars")
        other = library.create_folder(1, "japan")
        metadata = {
            "duration": 20,
            "width": 1920,
            "height": 1080,
            "fps": 24,
            "codec": "h264",
        }
        with patch.object(library, "probe", return_value=metadata):
            aid = library.add_video(1, folder, source)
            self.assertEqual(library.add_video(1, folder, source), aid)
        item = library.asset(1, aid)
        self.assertEqual(item["duration"], 20)
        self.assertEqual(item["fps"], 24)
        with self.assertRaises(ValueError):
            library.asset(2, aid)
        library.move_video(1, aid, other)
        self.assertEqual(library.asset(1, aid)["folder_id"], other)
        with self.assertRaises(ValueError):
            library.delete_folder(1, other)
        library.delete_folder(1, folder)
        library.delete_video(1, aid)
        library.delete_folder(1, other)

    def test_cache_and_projects(self):
        data = TrackAnalysis(60, 142, sections=[Section(0, 60, "Section 1")]).to_dict()
        projects.analysis_put("hash-version", data)
        self.assertEqual(projects.analysis_get("hash-version"), data)
        self.assertIsNone(projects.analysis_get("different-version"))
        project = projects.create_project(1, "beat")
        project["advanced"] = True
        projects.save_project(project)
        duplicate = projects.duplicate_project(1, project["id"])
        self.assertEqual(duplicate["seed"], project["seed"])
        self.assertTrue(duplicate["advanced"])
        with self.assertRaises(ValueError):
            projects.load_project(2, project["id"])
        projects.save_preset(1, "Cars", project["settings"])
        preset = projects.presets(1)[1]
        projects.default_preset(1, preset["id"])
        self.assertEqual(projects.create_project(1)["settings"]["name"], "Cars")
        with self.assertRaises(ValueError):
            projects.delete_preset(1, 0)

    def test_job_state(self):
        project = projects.create_project(1)
        jid = database.video_execute(
            "INSERT INTO video_render_jobs(user_id,project_id,status,payload) VALUES (?,?,?,?)",
            (1, project["id"], "queued", "{}"),
        )
        projects.job_update(1, jid, "analyzing")
        projects.job_update(1, jid, "rendering", progress=0.37)
        self.assertEqual(projects.job(1, jid)["progress"], 0.37)
        projects.job_update(1, jid, "cancelled")
        with self.assertRaises(ValueError):
            projects.job_update(1, jid, "done")
        with self.assertRaises(ValueError):
            projects.job(2, jid)

    def test_sequence_seed_no_immediate_repeat(self):
        project = projects.create_project(1)
        project.update(
            video_mode="sequence",
            analysis=TrackAnalysis(
                40,
                120,
                sections=[Section(i, i + 10, "Verse") for i in range(0, 40, 10)],
            ).to_dict(),
        )
        videos = [{"id": i, "path": str(i) + ".mp4", "duration": 5} for i in range(3)]
        first = select_clips(project, videos, 40)
        second = select_clips(project, videos, 40)
        self.assertEqual(first, second)
        self.assertTrue(all(a.path != b.path for a, b in zip(first, first[1:])))
        project["video_mode"] = "random"
        self.assertEqual(len({c.path for c in select_clips(project, videos, 40)}), 1)

    def test_queue_cancel_and_restart(self):
        project = projects.create_project(1)
        project["beat"] = str(self.root / "beat.wav")
        Path(project["beat"]).write_bytes(b"audio")
        bot = AsyncMock()
        queue = RenderQueue(bot)

        async def run():
            # Enqueue independently from workers so queued cancellation is deterministic.
            jid = await queue.enqueue(project, 1, "analysis")
            queue.cancel(1, jid)
            self.assertEqual(projects.job(1, jid)["status"], "cancelled")
            await queue.start()
            await queue.close()

        asyncio.run(run())


class QueueSmokeTests(unittest.TestCase):
    def test_analysis_render_delivery(self):
        import subprocess

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original_db = database.db
            database.db = sqlite3.connect(":memory:", check_same_thread=False)
            database.migrate_video_database()
            try:
                with patch.object(library, "DATA_ROOT", root):
                    source = root / "source.mp4"
                    audio = root / "beat.wav"
                    for args in (
                        [
                            "-f",
                            "lavfi",
                            "-i",
                            "testsrc2=s=160x90:r=24",
                            "-t",
                            "0.4",
                            "-c:v",
                            "libx264",
                            str(source),
                        ],
                        [
                            "-f",
                            "lavfi",
                            "-i",
                            "sine=frequency=220:sample_rate=48000",
                            "-t",
                            "2",
                            str(audio),
                        ],
                    ):
                        subprocess.run(
                            ["ffmpeg", "-v", "error", "-y"] + args, check=True
                        )
                    fid = library.create_folder(1, "cars")
                    aid = library.add_video(1, fid, source)
                    project = projects.create_project(1, "smoke")
                    project.update(beat=str(audio), bpm_hint=142, video_id=aid)
                    cfg = VideoRenderConfig()
                    cfg.canvas.width = 640
                    cfg.canvas.height = 360
                    cfg.foreground.width = 360
                    cfg.foreground.height = 360
                    cfg.export.encoder = "CPU"
                    cfg.export.preset = "ultrafast"
                    project["settings"] = cfg.to_dict()
                    projects.save_project(project)
                    bot = AsyncMock()
                    bot.token = "123:TEST"
                    queue = RenderQueue(bot)

                    async def run():
                        await queue.start()
                        try:
                            jid = await queue.enqueue(project, 1, "analysis")
                            await asyncio.wait_for(queue.queue.join(), 20)
                            self.assertEqual(projects.job(1, jid)["status"], "done")
                            ready = projects.load_project(1, project["id"])
                            self.assertEqual(ready["analysis"]["bpm"], 142)
                            ready["sections_confirmed"] = True
                            projects.save_project(ready)
                            changed = projects.load_project(1, project["id"])
                            changed["settings"]["analysis"]["bpm_source"] = "Manual"
                            changed["settings"]["analysis"]["manual_bpm"] = 130
                            with self.assertRaises(ValueError):
                                await queue.enqueue(changed, 1)

                            render = await queue.enqueue(ready, 1)
                            await asyncio.wait_for(queue.queue.join(), 20)
                            done = projects.job(1, render)
                            self.assertEqual(done["status"], "done", done["error"])
                            self.assertTrue(Path(done["output"]).is_file())
                            self.assertTrue(bot.send_document.called)
                            self.assertEqual(list((root / "temp").iterdir()), [])
                        finally:
                            await queue.close()

                    asyncio.run(run())
            finally:
                database.db.close()
                database.db = original_db
