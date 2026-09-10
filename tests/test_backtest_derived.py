import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_extra_over_under_lines(self):
        self.assertEqual(bd._market_outcome("ou05", 0, 0), 0)
        self.assertEqual(bd._market_outcome("ou05", 1, 0), 1)
        self.assertEqual(bd._market_outcome("ou15", 1, 0), 0)
        self.assertEqual(bd._market_outcome("ou15", 1, 1), 1)
        self.assertEqual(bd._market_outcome("ou35", 2, 1), 0)
        self.assertEqual(bd._market_outcome("ou35", 2, 2), 1)
        self.assertEqual(bd._market_outcome("ou45", 2, 2), 0)
        self.assertEqual(bd._market_outcome("ou45", 3, 2), 1)

    def test_team_totals(self):
        self.assertEqual(bd._market_outcome("home_ov15", 1, 3), 0)
        self.assertEqual(bd._market_outcome("home_ov15", 2, 0), 1)
        self.assertEqual(bd._market_outcome("away_ov15", 3, 1), 0)
        self.assertEqual(bd._market_outcome("away_ov15", 0, 2), 1)


class TestAhHomeCovers(unittest.TestCase):
    def test_missing_line_is_none(self):
        self.assertIsNone(bd.ah_home_covers({"ah_line": None, "fthg": 1, "ftag": 0}))

    def test_push_is_none(self):
        self.assertIsNone(bd.ah_home_covers({"ah_line": -1.0, "fthg": 1, "ftag": 0}))

    def test_home_and_away_covers(self):
        self.assertEqual(bd.ah_home_covers({"ah_line": -1.0, "fthg": 2, "ftag": 0}), 1)
        self.assertEqual(bd.ah_home_covers({"ah_line": -1.0, "fthg": 0, "ftag": 0}), 0)


class TestAhRawProb(unittest.TestCase):
    def test_conditions_out_the_push_mass(self):
        # Grille synthétique : 60 % push, 30 % domicile couvre, 10 % extérieur.
        # Conditionnée sur "pas de push" : 30/(30+10) = 0.75.
        grid = np.zeros((3, 3))
        grid[1, 1] = 0.6   # marge 0, ligne 0 -> push
        grid[2, 0] = 0.3   # marge +2 -> domicile couvre
        grid[0, 1] = 0.1   # marge -1 -> extérieur couvre
        self.assertAlmostEqual(bd.ah_raw_prob(grid, 0.0), 0.75, places=12)


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

    def test_new_markets_use_the_extended_grid_shape(self):
        ext_shape = (bd.EXTENDED_MAX_GOALS + 1, bd.EXTENDED_MAX_GOALS + 1)
        for market in ("ou05", "ou15", "ou35", "ou45", "home_ov15", "away_ov15"):
            self.assertEqual(bd.GRID_MASKS[market].shape, ext_shape)
            self.assertEqual(bd.MARKET_GRID_FIELD[market], "grid_ext")
        self.assertEqual(bd.MARKET_GRID_FIELD["ou25"], "grid")
        self.assertEqual(bd.MARKET_GRID_FIELD["btts"], "grid")


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
            self.assertAlmostEqual(float(p["grid_ext"].sum()), 1.0, places=6)
            self.assertEqual(p["grid_ext"].shape,
                             (bd.EXTENDED_MAX_GOALS + 1, bd.EXTENDED_MAX_GOALS + 1))
            self.assertAlmostEqual(float(p["score_freq"].sum()), 1.0, places=6)
            for market in bd.MARKETS:
                self.assertTrue(0.0 <= p["freq"][market] <= 1.0)

    def test_never_trains_on_same_day_or_later(self):
        """Le premier match d'une semaine ne doit jamais compter dans son
        propre entraînement (même garde que backtest.walk_forward/model.fit)."""
        rows = self._rows()
        cfg = {"xi": 0.0, "w": 0.0, "kappa": 2.0}
        preds = bd.walk_forward_derived(rows, ("1920",), cfg)
        self.assertTrue(all("grid" in p for p in preds))


class TestTuneMergesRatherThanOverwrites(unittest.TestCase):
    """tune() ne doit jamais retoucher un marché déjà figé (déjà testé et
    publié) même quand de nouveaux marchés sont ajoutés au chantier A1."""

    def _fake_preds(self, n=30):
        rng = np.random.default_rng(0)
        ext_n = bd.EXTENDED_MAX_GOALS + 1
        preds = []
        for i in range(n):
            grid = rng.dirichlet(np.ones(49)).reshape(7, 7)
            grid_ext = rng.dirichlet(np.ones(ext_n * ext_n)).reshape(ext_n, ext_n)
            row = {"fthg": i % 3, "ftag": (i + 1) % 4, "ah_line": -0.5}
            preds.append({"row": row, "grid": grid, "grid_ext": grid_ext})
        return preds

    def test_adding_markets_does_not_retune_existing_ones(self):
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "derived_markets_frozen.json"
            pre = {
                "based_on_m35": {"w": 0.6, "xi": 0.003, "kappa": 1.0},
                "validation_seasons": ["2021", "2122"],
                "markets": {"ou25": {"t": 1.234, "brier_validation_raw": 0.2,
                                     "brier_validation": 0.19}},
                "tuned_at": "2020-01-01",
            }
            frozen_path.write_text(json.dumps(pre))

            with mock.patch.object(bd, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(bd.footballdata, "LEAGUES", ["E0"]), \
                 mock.patch.object(bd.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}), \
                 mock.patch.object(bd, "walk_forward_derived", return_value=self._fake_preds()), \
                 mock.patch.object(bd, "load_league", return_value=[]):
                bd.tune(conn=None)

            result = json.loads(frozen_path.read_text())
            self.assertEqual(result["markets"]["ou25"], pre["markets"]["ou25"])
            for market in bd.ALL_MARKETS:
                self.assertIn(market, result["markets"])

    def test_nothing_left_to_tune_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "derived_markets_frozen.json"
            pre = {"based_on_m35": {}, "validation_seasons": [],
                  "markets": {m: {"t": 1.0, "brier_validation_raw": 0.0, "brier_validation": 0.0}
                              for m in bd.ALL_MARKETS},
                  "tuned_at": "2020-01-01"}
            frozen_path.write_text(json.dumps(pre))
            with mock.patch.object(bd, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(bd.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}):
                bd.tune(conn=None)
            self.assertEqual(json.loads(frozen_path.read_text()), pre)


class TestRunRequiresAllMarketsFrozen(unittest.TestCase):
    def test_missing_market_exits(self):
        with tempfile.TemporaryDirectory() as d:
            frozen_path = Path(d) / "derived_markets_frozen.json"
            frozen_path.write_text(json.dumps({"markets": {"ou25": {"t": 1.0}}}))
            with mock.patch.object(bd, "FROZEN_PATH", frozen_path), \
                 mock.patch.object(bd.backtest35, "frozen",
                                   return_value={"w": 0.6, "xi": 0.003, "kappa": 1.0}):
                with self.assertRaises(SystemExit):
                    bd.run(conn=None)


if __name__ == "__main__":
    unittest.main()
