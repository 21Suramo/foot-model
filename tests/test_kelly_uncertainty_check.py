import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_ml
import db
import kelly_uncertainty_check as kuc


class TestTotalVariation(unittest.TestCase):
    def test_identical_distributions_is_zero(self):
        p = {"h": 0.5, "d": 0.3, "a": 0.2}
        self.assertAlmostEqual(kuc.total_variation(p, dict(p)), 0.0)

    def test_disjoint_support_is_one(self):
        p = {"h": 1.0, "d": 0.0, "a": 0.0}
        q = {"h": 0.0, "d": 0.0, "a": 1.0}
        self.assertAlmostEqual(kuc.total_variation(p, q), 1.0)

    def test_symmetric(self):
        p = {"h": 0.6, "d": 0.25, "a": 0.15}
        q = {"h": 0.4, "d": 0.3, "a": 0.3}
        self.assertAlmostEqual(kuc.total_variation(p, q), kuc.total_variation(q, p))


class TestOutcomeIndex(unittest.TestCase):
    def test_home_win(self):
        self.assertEqual(kuc.outcome_index(2, 0), 0)

    def test_draw(self):
        self.assertEqual(kuc.outcome_index(1, 1), 1)

    def test_away_win(self):
        self.assertEqual(kuc.outcome_index(0, 3), 2)


class TestBucketByMedian(unittest.TestCase):
    def test_splits_at_median_inclusive_low(self):
        rows = [{"x": v} for v in [1, 2, 3, 4, 5]]
        low, high, med = kuc.bucket_by_median(rows, "x")
        self.assertEqual(med, 3)
        self.assertEqual([r["x"] for r in low], [1, 2, 3])
        self.assertEqual([r["x"] for r in high], [4, 5])


class TestCollectRowsIntegration(unittest.TestCase):
    """Jointure matches/predictions_m35/predictions_ml de bout en bout sur une
    base en mémoire — le vrai risque était une erreur de jointure ou de calcul
    d'edge (angle 2), pas juste les fonctions pures ci-dessus."""

    def _seed(self, conn, fthg, ftag, m35, gbm, market):
        db.upsert_match(conn, {"date": "2026-01-01", "league": "E0", "season": "2526",
                               "home": "A", "away": "B", "fthg": fthg, "ftag": ftag})
        conn.commit()
        match_id = conn.execute(
            "SELECT match_id FROM matches WHERE home='A' AND away='B'").fetchone()["match_id"]
        db.upsert_prediction_m35(conn, {
            "match_id": match_id, "xi": 0.003, "xg_w": 0.6, "kappa": 1.0, "temperature": 1.0,
            "model_h": m35["h"], "model_d": m35["d"], "model_a": m35["a"],
            "raw_h": m35["h"], "raw_d": m35["d"], "raw_a": m35["a"],
            "market_h": market["h"], "market_d": market["d"], "market_a": market["a"],
            "freq_h": 0.33, "freq_d": 0.33, "freq_a": 0.34})
        conn.executescript(backtest_ml.PREDICTIONS_ML_SCHEMA)
        conn.execute(
            "INSERT INTO predictions_ml (match_id, model_h, model_d, model_a, "
            "market_h, market_d, market_a) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (match_id, gbm["h"], gbm["d"], gbm["a"], market["h"], market["d"], market["a"]))
        conn.commit()
        return match_id

    def test_perfect_agreement_gives_zero_disagreement(self):
        conn = db.connect(":memory:")
        m35 = {"h": 0.5, "d": 0.3, "a": 0.2}
        self._seed(conn, 1, 0, m35, dict(m35), {"h": 0.4, "d": 0.3, "a": 0.3})
        rows = kuc.collect_rows(conn)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["disagreement"], 0.0)
        conn.close()

    def test_brier_matches_manual_computation_for_home_win(self):
        conn = db.connect(":memory:")
        m35 = {"h": 0.5, "d": 0.3, "a": 0.2}
        self._seed(conn, 2, 0, m35, {"h": 0.3, "d": 0.3, "a": 0.4},
                  {"h": 0.4, "d": 0.3, "a": 0.3})
        rows = kuc.collect_rows(conn)
        expected = (0.5 - 1) ** 2 + (0.3 - 0) ** 2 + (0.2 - 0) ** 2
        self.assertAlmostEqual(rows[0]["brier_m35"], expected, places=9)
        conn.close()

    def test_best_edge_picks_the_outcome_model_likes_most_vs_market(self):
        conn = db.connect(":memory:")
        m35 = {"h": 0.6, "d": 0.2, "a": 0.2}
        market = {"h": 0.4, "d": 0.3, "a": 0.3}
        self._seed(conn, 1, 0, m35, dict(m35), market)
        rows = kuc.collect_rows(conn)
        # edge home = 0.6/0.4 - 1 = 0.5 (le plus grand des trois)
        self.assertAlmostEqual(rows[0]["best_edge"], 0.5, places=9)
        # pari gagnant (home réellement gagné) : ROI = cote équitable - 1 = 1/0.4 - 1
        self.assertAlmostEqual(rows[0]["bet_roi"], 1.0 / 0.4 - 1.0, places=9)
        conn.close()

    def test_losing_value_bet_yields_minus_one_roi(self):
        conn = db.connect(":memory:")
        m35 = {"h": 0.6, "d": 0.2, "a": 0.2}
        market = {"h": 0.4, "d": 0.3, "a": 0.3}
        self._seed(conn, 0, 1, m35, dict(m35), market)  # away gagne, pas home
        rows = kuc.collect_rows(conn)
        self.assertAlmostEqual(rows[0]["bet_roi"], -1.0, places=9)
        conn.close()


if __name__ == "__main__":
    unittest.main()
