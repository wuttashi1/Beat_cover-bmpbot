import unittest
import numpy as np
from video_generator.analyzer import quiet_regions
from video_generator.drops import score_drop
from video_generator.models import (
    TrackAnalysis,
    Section,
    normalize_sections,
    timestamps,
)


class AnalysisTests(unittest.TestCase):
    def test_model(self):
        data = TrackAnalysis(
            60, 142, sections=[Section(0, 28, "Intro"), Section(28, 60, "Drop")]
        )
        self.assertEqual(TrackAnalysis.from_dict(data.to_dict()), data)
        self.assertEqual(timestamps(data.to_dict()), "00:00 Intro\n00:28 Drop")
        bad = data.to_dict()
        bad["sections"][1]["start"] = 27
        with self.assertRaises(ValueError):
            TrackAnalysis.from_dict(bad)
        with self.assertRaises(ValueError):
            normalize_sections(
                [{"start": 1, "type": "Intro", "confidence": 1, "energy": 0}], 60
            )

    def test_quiet_gap_relative_and_hysteresis(self):
        times = np.arange(0, 20, 0.02)
        rms = np.full(times.shape, 0.2)
        rms[(times >= 9.5) & (times < 9.9)] = 0.005
        rms[:150] = 0.01
        rms[-150:] = 0.01
        for gain in (1, 0.03, 2):
            scaled = rms * gain
            result = quiet_regions(times, 20 * np.log10(scaled), scaled, 20)
            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result[0].start, 9.5, places=1)
            self.assertAlmostEqual(result[0].end, 9.9, places=1)
        normal = np.full(times.shape, 0.2)
        normal[500:502] = 0.002
        self.assertEqual(quiet_regions(times, 20 * np.log10(normal), normal, 20), [])

    def test_drop_multifeature(self):
        pre = {"rms": 0.1, "low": 0.02, "onsets": 0.1, "flux": 0.1, "minimum": 0.02}
        post = {"rms": 0.6, "low": 0.3, "onsets": 0.8, "flux": 0.9}
        self.assertGreater(score_drop(pre, post, 0.9), 0.8)
        loud_only = dict(post, low=0.02, onsets=0.1, flux=0.1)
        self.assertLess(score_drop(pre, loud_only, 0.9), 0.4)


if __name__ == "__main__":
    unittest.main()
