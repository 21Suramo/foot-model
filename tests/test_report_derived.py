"""report_derived.py calcule Brier/calibration pour les 9 marchés dérivés
(roadmap A1) publiés dans reports/derived_markets_backtest.md — jamais testé
avant ce fichier, malgré une logique de bucketing propre (pas partagée avec
report.py, contrairement à report35.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report_derived as rd


class TestBrierSeries(unittest.TestCase):
    def test_skips_rows_with_missing_key(self):
        rows = [{"model_p": 0.7, "outcome": 1}, {"model_p": None, "outcome": 1}]
        series = rd.brier_series(rows, "model_p")
        self.assertEqual(len(series), 1)
        self.assertAlmostEqual(series[0], (0.7 - 1) ** 2, places=9)

    def test_uses_the_requested_key(self):
        rows = [{"model_p": 0.7, "market_p": 0.4, "outcome": 0}]
        self.assertAlmostEqual(rd.brier_series(rows, "market_p")[0], 0.16, places=9)


class TestMeanBrier(unittest.TestCase):
    def test_none_on_empty_series(self):
        self.assertIsNone(rd.mean_brier([], "model_p"))

    def test_averages_across_rows(self):
        rows = [{"model_p": 1.0, "outcome": 1}, {"model_p": 0.0, "outcome": 1}]
        # Briers = 0.0 et 1.0 -> moyenne 0.5
        self.assertAlmostEqual(rd.mean_brier(rows, "model_p"), 0.5, places=9)


class TestCalibrationTable(unittest.TestCase):
    def test_buckets_and_observed_frequency(self):
        rows = [
            {"model_p": 0.72, "outcome": 1},
            {"model_p": 0.74, "outcome": 0},
            {"model_p": 0.73, "outcome": 1},
        ]
        table = rd.calibration_table(rows, key="model_p", width=0.05)
        # floor(0.72/0.05)=14, floor(0.74/0.05)=14, floor(0.73/0.05)=14 -> même bucket
        bucket = next(c for c in table if c["lo"] <= 0.72 < c["hi"])
        self.assertEqual(bucket["n"], 3)
        self.assertAlmostEqual(bucket["obs"], 2 / 3, places=9)  # 2 outcomes vrais sur 3

    def test_rows_missing_key_are_excluded(self):
        rows = [{"model_p": 0.5, "outcome": 1}, {"model_p": None, "outcome": 0}]
        table = rd.calibration_table(rows, key="model_p", width=1.0)
        self.assertEqual(sum(c["n"] for c in table), 1)


if __name__ == "__main__":
    unittest.main()
