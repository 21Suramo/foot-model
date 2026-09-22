import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ml_model


def _rows(n=60, seed=0):
    import random
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        elo_diff = rng.uniform(-200, 200)
        outcome = 0 if elo_diff > 50 else (2 if elo_diff < -50 else 1)
        rows.append({
            "elo_diff": elo_diff, "rest_diff": rng.uniform(-5, 5),
            "league": "E0" if i % 2 == 0 else "E1",
            "outcome": outcome,
            "fthg": max(0, int(rng.gauss(1.4, 1))), "ftag": max(0, int(rng.gauss(1.1, 1))),
        })
    return rows


FEATURE_NAMES = ["elo_diff", "rest_diff", "league"]


class TestGBMModel(unittest.TestCase):
    def test_fit_probs_sum_to_one(self):
        model = ml_model.fit(_rows(), FEATURE_NAMES, num_rounds=10)
        probs = model.probs_1x2({"elo_diff": 100, "rest_diff": 0, "league": "E0"})
        self.assertAlmostEqual(sum(probs), 1.0, places=6)
        self.assertTrue(all(0 <= p <= 1 for p in probs))

    def test_lambdas_are_positive(self):
        model = ml_model.fit(_rows(), FEATURE_NAMES, num_rounds=10)
        lam_h, lam_a = model.lambdas({"elo_diff": 0, "rest_diff": 0, "league": "E1"})
        self.assertGreater(lam_h, 0)
        self.assertGreater(lam_a, 0)

    def test_missing_feature_is_nan_not_a_crash(self):
        model = ml_model.fit(_rows(), FEATURE_NAMES, num_rounds=10)
        probs = model.probs_1x2({"elo_diff": None, "rest_diff": 1.0, "league": "E0"})
        self.assertAlmostEqual(sum(probs), 1.0, places=6)

    def test_empty_training_set_raises(self):
        with self.assertRaises(ValueError):
            ml_model.fit([], FEATURE_NAMES, num_rounds=10)

    def test_save_load_roundtrip(self):
        model = ml_model.fit(_rows(), FEATURE_NAMES, num_rounds=10)
        row = {"elo_diff": 80, "rest_diff": -2, "league": "E0"}
        probs_before = model.probs_1x2(row)
        with tempfile.TemporaryDirectory() as d:
            model.save(d)
            reloaded = ml_model.GBMModel.load(d)
            probs_after = reloaded.probs_1x2(row)
        self.assertEqual(probs_before, probs_after)
        self.assertEqual(reloaded.feature_names, FEATURE_NAMES)

    def test_stronger_team_gets_higher_home_prob_on_average(self):
        # Pas une assertion de précision numérique (un GBM sur 60 lignes
        # synthétiques n'a rien d'un modèle calibré) — juste que le signal
        # elo_diff traverse le pipeline dans le bon sens en moyenne.
        model = ml_model.fit(_rows(200, seed=1), FEATURE_NAMES, num_rounds=30)
        strong_home = model.probs_1x2({"elo_diff": 180, "rest_diff": 0, "league": "E0"})[0]
        weak_home = model.probs_1x2({"elo_diff": -180, "rest_diff": 0, "league": "E0"})[0]
        self.assertGreater(strong_home, weak_home)


if __name__ == "__main__":
    unittest.main()
