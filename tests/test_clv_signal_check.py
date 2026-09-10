import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap
import clv_signal_check as csc


def _rec(roi, convergent, brier=0.6):
    return {"roi": roi, "convergent": convergent, "model_brier": brier}


class TestSummarize(unittest.TestCase):
    def test_no_difference_is_not_significant(self):
        records = []
        for i in range(60):
            jitter = 0.2 if i % 2 else -0.2
            records.append(_rec(jitter, True))
            records.append(_rec(jitter, False))
        text, signal = csc.summarize(records)
        self.assertFalse(signal)
        self.assertIn("Aucun signal détecté", text)

    def test_large_consistent_gap_is_significant(self):
        records = []
        for i in range(60):
            jitter = 0.01 if i % 2 else -0.01
            records.append(_rec(0.5 + jitter, True))    # convergents : ROI positif net
            records.append(_rec(-0.5 + jitter, False))  # divergents : ROI négatif net
        text, signal = csc.summarize(records)
        self.assertTrue(signal)
        self.assertIn("Signal détecté", text)

    def test_negative_gap_is_not_a_signal(self):
        # divergents MEILLEURS que convergents : ce n'est pas le signal cherché
        # (la fonction ne doit déclarer un signal que dans le sens attendu).
        records = []
        for i in range(60):
            jitter = 0.01 if i % 2 else -0.01
            records.append(_rec(-0.5 + jitter, True))
            records.append(_rec(0.5 + jitter, False))
        text, signal = csc.summarize(records)
        self.assertFalse(signal)

    def test_empty_group_does_not_crash(self):
        records = [_rec(0.1, True), _rec(0.2, True)]
        text, signal = csc.summarize(records)
        self.assertFalse(signal)
        self.assertIn("aucune observation", text)


class TestAgedTaken(unittest.TestCase):
    def test_age_zero_recovers_close(self):
        p_close, p_open = (0.5, 0.3, 0.2), (0.4, 0.35, 0.25)
        close_odds, open_odds = (1.9, 3.2, 4.5), (2.3, 2.9, 3.7)
        p_taken, odds_taken = csc.aged_taken(p_close, p_open, close_odds, open_odds, age=0)
        for a, b in zip(p_taken, p_close):
            self.assertAlmostEqual(a, b, places=9)

    def test_age_at_horizon_recovers_open(self):
        p_close, p_open = (0.5, 0.3, 0.2), (0.4, 0.35, 0.25)
        close_odds, open_odds = (1.9, 3.2, 4.5), (2.3, 2.9, 3.7)
        p_taken, odds_taken = csc.aged_taken(p_close, p_open, close_odds, open_odds,
                                             age=7, open_horizon=7)
        for a, b in zip(p_taken, p_open):
            self.assertAlmostEqual(a, b, places=9)

    def test_odds_taken_reflects_a_real_margin_not_zero(self):
        # Une cote "prise" à marge nulle (fair) gonflerait le ROI théorique :
        # vérifie que le booksum reconstruit reste > 1 (une vraie marge).
        p_close, p_open = (0.5, 0.3, 0.2), (0.4, 0.35, 0.25)
        close_odds, open_odds = (1.9, 3.2, 4.5), (2.3, 2.9, 3.7)   # marge ~5% chacune
        _, odds_taken = csc.aged_taken(p_close, p_open, close_odds, open_odds, age=2)
        booksum_taken = sum(1.0 / o for o in odds_taken)
        self.assertGreater(booksum_taken, 1.0)

    def test_intermediate_age_is_between_close_and_open(self):
        p_close, p_open = (0.6, 0.25, 0.15), (0.4, 0.35, 0.25)
        close_odds, open_odds = (1.6, 3.8, 6.0), (2.4, 2.7, 3.6)
        p_taken, _ = csc.aged_taken(p_close, p_open, close_odds, open_odds, age=3, open_horizon=7)
        for c, t, o in zip(p_close, p_taken, p_open):
            self.assertTrue(min(c, o) <= t <= max(c, o))


class TestCompositeScoreAndSelect(unittest.TestCase):
    def test_composite_score_arithmetic(self):
        self.assertAlmostEqual(csc.composite_score(0.05, 0.02, w=2.0), 0.09, places=9)
        self.assertAlmostEqual(csc.composite_score(0.05, -0.02, w=2.0), 0.01, places=9)

    def test_select_filters_by_threshold(self):
        records = [{"edge": 0.05, "movement": 0.0}, {"edge": 0.20, "movement": 0.0}]
        sel = csc.select(records, w=0.0, threshold=0.1)
        self.assertEqual(sel, [{"edge": 0.20, "movement": 0.0}])


class TestTuneRunShuffleWithSyntheticRecords(unittest.TestCase):
    """Le grid search / la sélection sont testés directement (pas de DB, pas
    de réseau) en patchant collect_composite_records — les mêmes garanties
    (n minimal, jamais de re-tuning, fichiers non-committés) que
    backtest_derived.py, sur des données synthétiques où la réponse est
    connue par construction."""

    def _records(self, n=150, seed=0):
        rng = np.random.default_rng(seed)
        records = []
        for _ in range(n):
            edge = float(rng.uniform(0.0, 0.15))
            movement = float(rng.uniform(-0.1, 0.1))
            # ROI construit pour dépendre POSITIVEMENT du mouvement (signal
            # injecté volontairement, pour vérifier que tune() le retrouve).
            roi = movement * 3.0 + float(rng.normal(0, 0.3))
            records.append({"edge": edge, "movement": movement, "roi": roi})
        return records

    def test_tune_writes_frozen_file_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "frozen.json"
            with mock.patch.object(csc, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(csc, "MIN_BETS_FOR_TUNE", 20), \
                 mock.patch.object(csc.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}), \
                 mock.patch.object(csc, "collect_composite_records",
                                   return_value=self._records()):
                csc.tune(conn=None)
                self.assertTrue(frozen_path.exists())
                first = json.loads(frozen_path.read_text())

                # Un second appel ne doit RIEN changer (déjà figé et testé).
                csc.tune(conn=None)
                second = json.loads(frozen_path.read_text())
                self.assertEqual(first, second)

    def test_tune_picks_positive_weight_when_signal_is_positive(self):
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "frozen.json"
            with mock.patch.object(csc, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(csc, "MIN_BETS_FOR_TUNE", 20), \
                 mock.patch.object(csc.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}), \
                 mock.patch.object(csc, "collect_composite_records",
                                   return_value=self._records(n=400)):
                csc.tune(conn=None)
                result = json.loads(frozen_path.read_text())
                self.assertGreater(result["w"], 0.0)

    def test_run_persists_raw_roi_lists_for_later_ci(self):
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "frozen.json"
            results_path = Path(d) / "results.json"
            frozen_path.write_text(json.dumps({
                "w": 1.0, "threshold": 0.05, "horizon_days": 2,
                "roi_validation": 0.1, "n_validation": 100,
                "baseline_roi_validation": 0.0, "n_baseline_validation": 150,
                "validation_seasons": ["2021", "2122"], "tuned_at": "2026-01-01",
            }))
            with mock.patch.object(csc, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(csc, "RESULTS_PATH", results_path), \
                 mock.patch.object(csc.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}), \
                 mock.patch.object(csc, "collect_composite_records",
                                   return_value=self._records()):
                csc.run(conn=None)
            result = json.loads(results_path.read_text())
            self.assertIn("roi_baseline_all_list", result)
            self.assertIn("roi_selected_list", result)
            self.assertEqual(result["n_test_total"], 150)

    def test_shuffle_test_detects_a_real_planted_signal(self):
        # Signal FORT et déterministe (pas de bruit) : le lift réel doit
        # dominer nettement la distribution des lifts permutés (p petit).
        records = []
        for i in range(300):
            edge = 0.05
            movement = 0.05 if i % 2 == 0 else -0.05
            roi = 1.0 if movement > 0 else -1.0
            records.append({"edge": edge, "movement": movement, "roi": roi})
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "frozen.json"
            shuffle_path = Path(d) / "shuffle.json"
            frozen_path.write_text(json.dumps({
                "w": 1.0, "threshold": 0.08, "horizon_days": 2,
            }))
            with mock.patch.object(csc, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(csc, "SHUFFLE_PATH", shuffle_path), \
                 mock.patch.object(csc.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}), \
                 mock.patch.object(csc, "collect_composite_records", return_value=records):
                csc.shuffle_test(conn=None, n_perm=200, seed=bootstrap.DEFAULT_SEED)
            result = json.loads(shuffle_path.read_text())
            self.assertLess(result["p_value"], 0.05)


if __name__ == "__main__":
    unittest.main()
