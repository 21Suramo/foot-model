import datetime
import math
import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.optimize import check_grad

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest
import model


def _match(date, home, away, fthg, ftag):
    return {"date": date, "home": home, "away": away, "fthg": fthg, "ftag": ftag}


class TestLikelihood(unittest.TestCase):
    def test_nll_matches_pure_poisson_by_hand(self):
        # Paramètres nuls, rho=0, kappa=0 : λh = λa = 1, match 2-1
        # NLL = -(2·0 - 1 - log 2!) - (1·0 - 1 - log 1!) = 2 + log 2
        theta = np.zeros(2 * 2 + 2)
        nll, _ = model._nll_grad(theta, 2, np.array([0]), np.array([1]),
                                 np.array([2.0]), np.array([1.0]), np.ones(1), 0.0)
        self.assertAlmostEqual(nll, 2.0 + math.log(2.0), places=12)

    def test_nll_weight_scales_contribution(self):
        theta = np.zeros(6)
        args = (2, np.array([0]), np.array([1]), np.array([2.0]), np.array([1.0]))
        nll1, _ = model._nll_grad(theta, *args, np.ones(1), 0.0)
        nll2, _ = model._nll_grad(theta, *args, np.array([0.5]), 0.0)
        self.assertAlmostEqual(nll2, 0.5 * nll1, places=12)

    def test_gradient_matches_numerical(self):
        rng = np.random.default_rng(7)
        n, m = 4, 30
        h_idx = rng.integers(0, n, m)
        a_idx = (h_idx + rng.integers(1, n, m)) % n
        hg = rng.poisson(1.5, m).astype(float)
        ag = rng.poisson(1.1, m).astype(float)
        w = rng.uniform(0.3, 1.0, m)
        theta = np.concatenate([rng.normal(0, 0.15, 2 * n), [0.25], [-0.08]])
        err = check_grad(lambda t: model._nll_grad(t, n, h_idx, a_idx, hg, ag, w, 2.0)[0],
                         lambda t: model._nll_grad(t, n, h_idx, a_idx, hg, ag, w, 2.0)[1],
                         theta)
        self.assertLess(err, 1e-4)


class TestPrediction(unittest.TestCase):
    def _model(self, rho=0.0, gamma=1.3):
        return model.DixonColes(["A", "B"], np.array([0.1, -0.1]),
                                np.array([-0.05, 0.05]), math.log(gamma), rho)

    def test_score_grid_sums_to_one(self):
        grid = self._model(rho=-0.1).score_grid("A", "B")
        self.assertEqual(grid.shape, (7, 7))
        self.assertAlmostEqual(grid.sum(), 1.0, places=12)
        self.assertTrue((grid >= 0).all())

    def test_probs_1x2_sum_to_one(self):
        h, d, a = self._model(rho=-0.1).probs_1x2("A", "B")
        self.assertAlmostEqual(h + d + a, 1.0, places=12)

    def test_rho_negative_boosts_draws_0_0_and_1_1(self):
        g0 = self._model(rho=0.0).score_grid("A", "B")
        gn = self._model(rho=-0.15).score_grid("A", "B")
        # rho<0 : tau augmente 0-0 et 1-1, diminue 1-0 et 0-1 (à renormalisation près,
        # les ratios intra-grille le montrent sans ambiguïté)
        self.assertGreater(gn[0, 0] / gn[1, 0], g0[0, 0] / g0[1, 0])
        self.assertGreater(gn[1, 1] / gn[0, 1], g0[1, 1] / g0[0, 1])

    def test_unknown_team_gets_league_average(self):
        m = self._model()
        lam_h, lam_a = m.lambdas("X", "Y")
        mean_a = np.mean(np.exp(m.log_attack))
        mean_d = np.mean(np.exp(m.log_defense))
        self.assertAlmostEqual(lam_h, mean_a * mean_d * m.gamma, places=12)
        self.assertAlmostEqual(lam_a, mean_a * mean_d, places=12)


class TestFit(unittest.TestCase):
    def _round_robin(self, seed=3, gamma=1.4, n_rounds=10):
        rng = np.random.default_rng(seed)
        teams = ["T1", "T2", "T3", "T4", "T5", "T6"]
        attack = {t: a for t, a in zip(teams, [1.4, 1.2, 1.0, 0.9, 0.8, 0.7])}
        rows = []
        day = datetime.date(2020, 1, 1)
        for _ in range(n_rounds):
            for h in teams:
                for a in teams:
                    if h == a:
                        continue
                    rows.append(_match(day.isoformat(), h, a,
                                       int(rng.poisson(attack[h] * gamma)),
                                       int(rng.poisson(attack[a]))))
                    day += datetime.timedelta(days=1)
        return rows

    def test_recovers_home_advantage_and_normalizes_attack(self):
        m = model.fit(self._round_robin(), xi=0.0)
        self.assertAlmostEqual(np.mean(np.exp(m.log_attack)), 1.0, places=10)
        self.assertGreater(m.gamma, 1.15)
        self.assertLess(m.gamma, 1.65)
        # L'ordre des forces d'attaque est retrouvé
        strengths = dict(zip(m.teams, np.exp(m.log_attack)))
        self.assertGreater(strengths["T1"], strengths["T6"])

    def test_ref_date_rejects_future_match(self):
        rows = self._round_robin(n_rounds=1)
        with self.assertRaises(ValueError):
            model.fit(rows, xi=0.001, ref_date=datetime.date(2020, 1, 3))

    def test_shrinkage_pulls_sparse_team_to_mean(self):
        rows = self._round_robin()
        # Une équipe vue une seule fois, battue 5-0 : le prior doit la retenir
        rows.append(_match("2021-06-01", "T1", "Promue", 5, 0))
        m = model.fit(rows, xi=0.0)
        promue = np.exp(m.log_attack[m.teams.index("Promue")])
        self.assertGreater(promue, 0.5)  # sans prior, l'attaque partirait vers ~0


class TestDemargin(unittest.TestCase):
    def test_power_probs_sum_to_one_and_keep_order(self):
        p = backtest.demargin_power(2.0, 3.5, 4.0)
        self.assertAlmostEqual(sum(p), 1.0, places=9)
        self.assertGreater(p[0], p[1])
        self.assertGreater(p[1], p[2])

    def test_fair_odds_unchanged(self):
        p = backtest.demargin_power(2.0, 4.0, 4.0)
        for got, exp in zip(p, (0.5, 0.25, 0.25)):
            self.assertAlmostEqual(got, exp, places=9)


class TestMetrics(unittest.TestCase):
    def test_brier_uniform(self):
        self.assertAlmostEqual(backtest.brier((1 / 3, 1 / 3, 1 / 3), 0), 2 / 3, places=12)

    def test_brier_perfect_and_worst(self):
        self.assertAlmostEqual(backtest.brier((1.0, 0.0, 0.0), 0), 0.0, places=12)
        self.assertAlmostEqual(backtest.brier((1.0, 0.0, 0.0), 2), 2.0, places=12)


if __name__ == "__main__":
    unittest.main()
