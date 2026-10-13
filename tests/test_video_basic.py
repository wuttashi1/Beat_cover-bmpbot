import unittest
from video_generator.media import square_crop, parse_time, format_time
from video_generator.config import VideoRenderConfig, Overlay


class BasicTests(unittest.TestCase):
    def test_crop(self):
        self.assertEqual(square_crop(1920, 1080), (1080, 1080, 420, 0))
        self.assertEqual(square_crop(1080, 1920), (1080, 1080, 0, 420))
        self.assertEqual(square_crop(512, 512), (512, 512, 0, 0))

    def test_time(self):
        self.assertEqual(parse_time("01:23.500"), 83.5)
        self.assertEqual(parse_time("83.5"), 83.5)
        self.assertEqual(format_time(83.5), "01:23")
        with self.assertRaises(ValueError):
            parse_time("nan")

    def test_config(self):
        cfg = VideoRenderConfig()
        self.assertEqual(VideoRenderConfig.from_dict(cfg.to_dict()), cfg)
        cfg.overlays = [Overlay(start=10, end=5)]
        with self.assertRaises(ValueError):
            cfg.validate()
        with self.assertRaises(ValueError):
            VideoRenderConfig.from_dict({"canvas": {"width": 321}})
