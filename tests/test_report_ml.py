"""report_ml.py calcule Brier/log-loss/accuracy/AUC du GBM (roadmap B1-B4)
publiés dans reports/ml_backtest.md — jamais testé avant ce fichier. auc_ovr
est une implémentation maison (rang de Mann-Whitney, pas scikit-learn) donc
la plus exposée à un bug arithmétique silencieux."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report_ml as rml


def _row(outcome, model, market=None):
    return {"outcome": outcome, "model": model, "market": market}


class TestFreqBaseline(unittest.TestCase):
    def test_uniform_when_empty(self):
        self.assertEqual(rml._freq_baseline([]), (1 / 3, 1 / 3, 1 / 3))

    def test_matches_observed_proportions(self):
        rows = [_row(0, None), _row(0, None), _row(1, None), _row(2, None)]
        h, d, a = rml._freq_baseline(rows)
        self.assertAlmostEqual(h, 0.5, places=9)
        self.assertAlmostEqual(d, 0.25, places=9)
        self.assertAlmostEqual(a, 0.25, places=9)


class TestAccuracy(unittest.TestCase):
    def test_all_correct(self):
        rows = [_row(0, (0.9, 0.05, 0.05)), _row(2, (0.1, 0.1, 0.8))]
        self.assertAlmostEqual(rml.accuracy(rows), 1.0, places=9)

    def test_half_correct(self):
        rows = [_row(0, (0.9, 0.05, 0.05)), _row(2, (0.9, 0.05, 0.05))]
        self.assertAlmostEqual(rml.accuracy(rows), 0.5, places=9)

    def test_none_on_no_data(self):
        self.assertIsNone(rml.accuracy([{"outcome": 0, "model": None}]))

    def test_uses_market_key_when_requested(self):
        rows = [_row(0, (0.1, 0.1, 0.8), market=(0.9, 0.05, 0.05))]
        self.assertAlmostEqual(rml.accuracy(rows, key="market"), 1.0, places=9)


class TestLoglossMean(unittest.TestCase):
    def test_confident_correct_prediction_gives_low_logloss(self):
        rows = [_row(0, (0.99, 0.005, 0.005))]
        self.assertLess(rml.logloss_mean(rows), 0.02)

    def test_confident_wrong_prediction_gives_high_logloss(self):
        rows = [_row(0, (0.01, 0.01, 0.98))]
        self.assertGreater(rml.logloss_mean(rows), 4.0)

    def test_clamped_to_avoid_log_zero(self):
        # p=0 sur l'issue réelle ne doit jamais produire -inf/NaN.
        rows = [_row(0, (0.0, 0.5, 0.5))]
        import math
        self.assertTrue(math.isfinite(rml.logloss_mean(rows)))


class TestAucOvr(unittest.TestCase):
    def test_perfect_separation_gives_auc_one(self):
        rows = [_row(0, (0.9, 0.05, 0.05)), _row(0, (0.8, 0.1, 0.1)),
                _row(1, (0.1, 0.8, 0.1)), _row(1, (0.05, 0.9, 0.05)),
                _row(2, (0.05, 0.05, 0.9)), _row(2, (0.1, 0.1, 0.8))]
        self.assertAlmostEqual(rml.auc_ovr(rows), 1.0, places=9)

    def test_none_below_two_rows(self):
        self.assertIsNone(rml.auc_ovr([_row(0, (0.5, 0.3, 0.2))]))

    def test_inverted_ranking_gives_auc_near_zero(self):
        # Le modèle donne systématiquement la proba la plus BASSE à la bonne issue.
        rows = [_row(0, (0.1, 0.45, 0.45)), _row(0, (0.05, 0.5, 0.45)),
                _row(1, (0.45, 0.1, 0.45)), _row(1, (0.5, 0.05, 0.45))]
        self.assertLess(rml.auc_ovr(rows), 0.2)


class TestBrierSeries(unittest.TestCase):
    def test_skips_none_and_computes_brier(self):
        rows = [_row(0, (1.0, 0.0, 0.0)), _row(0, None)]
        series = rml.brier_series(rows, "model")
        self.assertEqual(len(series), 1)
        self.assertAlmostEqual(series[0], 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
