import math
import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.optimize import check_grad
from scipy.special import gammaln

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest35
import model


class TestMixedGoals(unittest.TestCase):
    def test_nll_with_fractional_goals_by_hand(self):
        # λh = λa = 1, cible 1.5-0.8 : NLL = 2 + gammaln(2.5) + gammaln(1.8)
        theta = np.zeros(6)
        masks = tuple(np.zeros(1, dtype=bool) for _ in range(4))
        nll, _ = model._nll_grad(theta, 2, np.array([0]), np.array([1]),
                                 np.array([1.5]), np.array([0.8]), np.ones(1), 0.0, masks)
        self.assertAlmostEqual(nll, 2.0 + gammaln(2.5) + gammaln(1.8), places=12)

    def test_tau_masks_follow_real_score_not_mixed(self):
        # Score réel 0-0 (masque m00 actif) mais pseudo-buts fractionnaires :
        # la correction tau doit s'appliquer quand même
        theta = np.concatenate([np.zeros(4), [0.0], [-0.1]])
        m00 = np.array([True]); off = np.array([False])
        nll_tau, _ = model._nll_grad(theta, 2, np.array([0]), np.array([1]),
                                     np.array([0.3]), np.array([0.2]), np.ones(1), 0.0,
                                     (m00, off, off, off))
        nll_no, _ = model._nll_grad(theta, 2, np.array([0]), np.array([1]),
                                    np.array([0.3]), np.array([0.2]), np.ones(1), 0.0,
                                    (off, off, off, off))
        # tau(0,0) = 1 - λλρ = 1.1 -> la NLL baisse de log(1.1)
        self.assertAlmostEqual(nll_no - nll_tau, math.log(1.1), places=12)

    def test_gradient_with_fractional_goals_and_masks(self):
        rng = np.random.default_rng(11)
        n, m = 4, 30
        h_idx = rng.integers(0, n, m)
        a_idx = (h_idx + rng.integers(1, n, m)) % n
        hg_real = rng.poisson(1.4, m)
        ag_real = rng.poisson(1.0, m)
        hg = 0.6 * hg_real + 0.4 * rng.gamma(2.0, 0.7, m)
        ag = 0.6 * ag_real + 0.4 * rng.gamma(2.0, 0.6, m)
        masks = ((hg_real == 0) & (ag_real == 0), (hg_real == 0) & (ag_real == 1),
                 (hg_real == 1) & (ag_real == 0), (hg_real == 1) & (ag_real == 1))
        w = rng.uniform(0.3, 1.0, m)
        theta = np.concatenate([rng.normal(0, 0.15, 2 * n), [0.25], [-0.08]])
        err = check_grad(
            lambda t: model._nll_grad(t, n, h_idx, a_idx, hg, ag, w, 2.0, masks)[0],
            lambda t: model._nll_grad(t, n, h_idx, a_idx, hg, ag, w, 2.0, masks)[1],
            theta)
        self.assertLess(err, 1e-4)

    def test_fit_accepts_xg_weight_and_falls_back_on_missing_xg(self):
        rows = [{"date": f"2020-01-{d:02d}", "home": h, "away": a, "fthg": 2, "ftag": 1,
                 "xg_home": 1.4, "xg_away": 0.9} for d, (h, a) in enumerate(
                    [("A", "B"), ("B", "C"), ("C", "A"), ("B", "A"), ("C", "B"), ("A", "C")], 1)]
        rows[0]["xg_home"] = None  # repli attendu sur les buts réels
        m = model.fit(rows, xg_weight=0.6)
        h, d, a = m.probs_1x2("A", "B")
        self.assertAlmostEqual(h + d + a, 1.0, places=12)


class TestTemperature(unittest.TestCase):
    def test_identity_at_one(self):
        p = (0.5, 0.3, 0.2)
        for got, exp in zip(backtest35.apply_temperature(p, 1.0), p):
            self.assertAlmostEqual(got, exp, places=12)

    def test_above_one_sharpens_favorite_and_sums_to_one(self):
        p = (0.5, 0.3, 0.2)
        q = backtest35.apply_temperature(p, 1.4)
        self.assertAlmostEqual(sum(q), 1.0, places=12)
        self.assertGreater(q[0], p[0])
        self.assertLess(q[2], p[2])
        self.assertEqual(np.argmax(q), np.argmax(p))  # monotone : ordre préservé


class TestPromoted(unittest.TestCase):
    def test_detects_new_teams_per_season(self):
        rows = [{"season": "1819", "home": "A", "away": "B"},
                {"season": "1819", "home": "C", "away": "A"},
                {"season": "1920", "home": "A", "away": "B"},
                {"season": "1920", "home": "P", "away": "C"}]
        promo = backtest35.promoted_teams(rows)
        self.assertEqual(promo["1920"], {"P"})
        self.assertNotIn("1819", promo)  # pas de saison précédente en base


if __name__ == "__main__":
    unittest.main()
