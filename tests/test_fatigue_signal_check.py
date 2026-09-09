import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fatigue_signal_check as fsc


class TestRestBucket(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(fsc.rest_bucket(None))

    def test_boundaries(self):
        self.assertEqual(fsc.rest_bucket(0), "court (<=3j)")
        self.assertEqual(fsc.rest_bucket(3), "court (<=3j)")
        self.assertEqual(fsc.rest_bucket(4), "normal (4-6j)")
        self.assertEqual(fsc.rest_bucket(6), "normal (4-6j)")
        self.assertEqual(fsc.rest_bucket(7), "long (>=7j)")
        self.assertEqual(fsc.rest_bucket(20), "long (>=7j)")


class TestSummarize(unittest.TestCase):
    def test_no_difference_is_not_significant(self):
        # Même distribution (moyenne 0, bruit réaliste ±0.15, alternant) dans les
        # trois tranches : aucun écart systématique à détecter.
        records = []
        for i in range(50):
            jitter = 0.15 if i % 2 else -0.15
            records.append(("court (<=3j)", "home_attack", jitter))
            records.append(("normal (4-6j)", "home_attack", jitter))
            records.append(("long (>=7j)", "home_attack", jitter))
        text, significant = fsc.summarize(records)
        self.assertFalse(significant)
        self.assertIn("non significatif", text)
        self.assertIn("ne pas construire la calibration fatigue", text)

    def test_large_consistent_gap_is_significant(self):
        records = []
        for i in range(80):
            # écart net et systématique : repos court = -1.0 but sous l'attendu
            jitter = 0.01 if i % 2 else -0.01
            records.append(("court (<=3j)", "home_attack", -1.0 + jitter))
            records.append(("normal (4-6j)", "home_attack", 0.0 + jitter))
            records.append(("long (>=7j)", "home_attack", 0.0 + jitter))
        text, significant = fsc.summarize(records)
        self.assertTrue(significant)
        self.assertIn("SIGNIFICATIF", text)

    def test_axes_are_independent(self):
        records = [
            ("court (<=3j)", "home_attack", -1.0), ("court (<=3j)", "home_attack", -0.9),
            ("normal (4-6j)", "home_attack", 0.0), ("normal (4-6j)", "home_attack", 0.1),
            ("long (>=7j)", "home_attack", 0.0), ("long (>=7j)", "home_attack", 0.1),
            ("court (<=3j)", "away_attack", 0.0), ("court (<=3j)", "away_attack", 0.05),
            ("normal (4-6j)", "away_attack", 0.0), ("normal (4-6j)", "away_attack", 0.02),
            ("long (>=7j)", "away_attack", 0.0), ("long (>=7j)", "away_attack", -0.02),
        ]
        text, _ = fsc.summarize(records)
        self.assertIn("home_attack", text)
        self.assertIn("away_attack", text)


if __name__ == "__main__":
    unittest.main()
