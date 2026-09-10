import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_derived as bd
import model


class TestMarketOutcome(unittest.TestCase):
    def test_ou25_over_and_under(self):
        self.assertEqual(bd._market_outcome("ou25", 2, 1), 1)   # 3 buts
        self.assertEqual(bd._market_outcome("ou25", 1, 1), 0)   # 2 buts
        self.assertEqual(bd._market_outcome("ou25", 0, 0), 0)

    def test_btts_yes_and_no(self):
        self.assertEqual(bd._market_outcome("btts", 1, 1), 1)
        self.assertEqual(bd._market_outcome("btts", 2, 0), 0)
        self.assertEqual(bd._market_outcome("btts", 0, 0), 0)

    def test_unknown_market_raises(self):
        with self.assertRaises(ValueError):
            bd._market_outcome("handicap", 1, 0)


class TestGridMasks(unittest.TestCase):
    def test_over25_excludes_low_scoring_totals(self):
        mask = bd.GRID_MASKS["ou25"]
        for h, a in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            self.assertFalse(mask[h, a], f"{h}-{a} totalise <= 2 buts")
        for h, a in [(2, 1), (1, 2), (3, 0), (0, 3)]:
            self.assertTrue(mask[h, a], f"{h}-{a} totalise > 2,5 buts")

    def test_btts_requires_both_teams_scoring(self):
        mask = bd.GRID_MASKS["btts"]
        for h, a in [(0, 0), (1, 0), (0, 2), (3, 0)]:
            self.assertFalse(mask[h, a])
        for h, a in [(1, 1), (2, 3), (1, 4)]:
            self.assertTrue(mask[h, a])

    def test_masks_cover_the_whole_grid_shape(self):
        shape = (model.MAX_GOALS + 1, model.MAX_GOALS + 1)
        self.assertEqual(bd.GRID_MASKS["ou25"].shape, shape)
        self.assertEqual(bd.GRID_MASKS["btts"].shape, shape)


class TestBinaryTemperature(unittest.TestCase):
    def test_identity_at_one(self):
        for p in (0.2, 0.5, 0.8):
            self.assertAlmostEqual(bd.apply_binary_temperature(p, 1.0), p, places=12)

    def test_above_one_sharpens_toward_the_favorite(self):
        self.assertGreater(bd.apply_binary_temperature(0.7, 1.5), 0.7)
        self.assertLess(bd.apply_binary_temperature(0.3, 1.5), 0.3)

    def test_below_one_flattens_toward_half(self):
        q = bd.apply_binary_temperature(0.8, 0.5)
        self.assertLess(q, 0.8)
        self.assertGreater(q, 0.5)

    def test_stays_in_unit_interval_at_extremes(self):
        self.assertTrue(0.0 <= bd.apply_binary_temperature(0.999999, 2.5) <= 1.0)
        self.assertTrue(0.0 <= bd.apply_binary_temperature(0.000001, 2.5) <= 1.0)


class TestBinaryBrier(unittest.TestCase):
    def test_perfect_prediction_is_zero(self):
        self.assertEqual(bd.binary_brier(1.0, 1), 0.0)
        self.assertEqual(bd.binary_brier(0.0, 0), 0.0)

    def test_worst_prediction_is_one(self):
        self.assertEqual(bd.binary_brier(0.0, 1), 1.0)


class TestDemargin2Way(unittest.TestCase):
    def test_sums_to_one(self):
        p_over, p_under = bd.demargin_2way(1.9, 1.9)
        self.assertAlmostEqual(p_over + p_under, 1.0, places=12)
        self.assertAlmostEqual(p_over, 0.5, places=12)

    def test_shorter_odds_get_higher_probability(self):
        p_over, p_under = bd.demargin_2way(1.5, 2.8)
        self.assertGreater(p_over, p_under)


class TestWalkForwardDerived(unittest.TestCase):
    """Petite ligue synthétique : la grille renvoyée doit sommer à 1 et la
    fréquence de baseline rester dans [0, 1], sans jamais voir le futur."""

    def _rows(self):
        teams = ["A", "B", "C", "D"]
        rows = []
        mid = 0
        # Burn-in (saison antérieure) : sans lui, la première semaine de 1920
        # n'aurait aucun historique et model.fit lèverait ValueError.
        for week, (h, a) in enumerate([("A", "B"), ("C", "D"), ("B", "C"), ("D", "A")]):
            rows.append({"match_id": mid, "date": f"2019-08-{week + 1:02d}",
                        "season": "1819", "home": h, "away": a,
                        "fthg": week % 3, "ftag": (week + 1) % 3,
                        "xg_home": None, "xg_away": None})
            mid += 1
        import itertools
        for week, (h, a) in enumerate(itertools.permutations(teams, 2)):
            rows.append({"match_id": mid, "date": f"2020-01-{(week % 27) + 1:02d}",
                        "season": "1920", "home": h, "away": a,
                        "fthg": (week % 3), "ftag": ((week + 1) % 3),
                        "xg_home": None, "xg_away": None})
            mid += 1
        return sorted(rows, key=lambda r: r["date"])

    def test_grid_sums_to_one_and_freq_bounded(self):
        rows = self._rows()
        cfg = {"xi": 0.0, "w": 0.0, "kappa": 2.0}
        preds = bd.walk_forward_derived(rows, ("1920",), cfg)
        self.assertTrue(preds)
        for p in preds:
            self.assertAlmostEqual(float(p["grid"].sum()), 1.0, places=6)
            for market in bd.MARKETS:
                self.assertTrue(0.0 <= p["freq"][market] <= 1.0)

    def test_never_trains_on_same_day_or_later(self):
        """Le premier match d'une semaine ne doit jamais compter dans son
        propre entraînement (même garde que backtest.walk_forward/model.fit)."""
        rows = self._rows()
        cfg = {"xi": 0.0, "w": 0.0, "kappa": 2.0}
        preds = bd.walk_forward_derived(rows, ("1920",), cfg)
        self.assertTrue(all("grid" in p for p in preds))


if __name__ == "__main__":
    unittest.main()
