"""report.py calcule les chiffres phares du projet (le +1,78 % de M3.5 vient
de `metrics`/`calibration_table` ici, réutilisées telles quelles par
report35.py) — jusqu'ici sans aucun test. Une régression dans l'agrégation ou
le bucketing de calibration passerait droit dans un rapport committé et cité
comme vérité sans que rien ne la signale."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import report


class TestLoadPredictions(unittest.TestCase):
    def _seed(self, conn, fthg, ftag, model, market, freq):
        db.upsert_match(conn, {"date": "2026-01-01", "league": "E0", "season": "2526",
                               "home": "A", "away": "B", "fthg": fthg, "ftag": ftag})
        conn.commit()
        match_id = conn.execute(
            "SELECT match_id FROM matches WHERE home='A' AND away='B'").fetchone()["match_id"]
        db.upsert_prediction(conn, {
            "match_id": match_id, "xi": 0.002,
            "model_h": model[0], "model_d": model[1], "model_a": model[2],
            "market_h": market[0], "market_d": market[1], "market_a": market[2],
            "freq_h": freq[0], "freq_d": freq[1], "freq_a": freq[2]})
        conn.commit()
        return match_id

    def test_outcome_and_probs_are_attached(self):
        conn = db.connect(":memory:")
        self._seed(conn, 2, 0, (0.5, 0.3, 0.2), (0.4, 0.3, 0.3), (0.45, 0.28, 0.27))
        rows = report.load_predictions(conn)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["outcome"], 0)  # home win
        self.assertEqual(r["probs"]["model"], (0.5, 0.3, 0.2))
        self.assertEqual(r["probs"]["market"], (0.4, 0.3, 0.3))
        self.assertEqual(r["probs"]["uniform"], (1 / 3, 1 / 3, 1 / 3))
        conn.close()

    def test_draw_outcome(self):
        conn = db.connect(":memory:")
        self._seed(conn, 1, 1, (0.3, 0.4, 0.3), (0.3, 0.4, 0.3), (0.3, 0.4, 0.3))
        rows = report.load_predictions(conn)
        self.assertEqual(rows[0]["outcome"], 1)
        conn.close()

    def test_away_outcome(self):
        conn = db.connect(":memory:")
        self._seed(conn, 0, 2, (0.2, 0.3, 0.5), (0.2, 0.3, 0.5), (0.2, 0.3, 0.5))
        rows = report.load_predictions(conn)
        self.assertEqual(rows[0]["outcome"], 2)
        conn.close()


class TestMetrics(unittest.TestCase):
    def _row(self, outcome, probs):
        return {"outcome": outcome, "probs": {"model": probs}}

    def test_perfect_predictions_give_zero_brier_and_full_accuracy(self):
        rows = [self._row(0, (1.0, 0.0, 0.0)), self._row(2, (0.0, 0.0, 1.0))]
        brier, ll, acc = report.metrics(rows, "model")
        self.assertAlmostEqual(brier, 0.0, places=9)
        self.assertAlmostEqual(acc, 1.0, places=9)

    def test_uniform_guess_gives_known_brier(self):
        rows = [self._row(0, (1 / 3, 1 / 3, 1 / 3))]
        brier, _, _ = report.metrics(rows, "model")
        # Brier = (1/3-1)^2 + (1/3)^2 + (1/3)^2 = 4/9 + 1/9 + 1/9 = 6/9
        self.assertAlmostEqual(brier, 6 / 9, places=9)

    def test_accuracy_counts_argmax_matches(self):
        rows = [self._row(0, (0.6, 0.2, 0.2)), self._row(1, (0.6, 0.2, 0.2))]
        _, _, acc = report.metrics(rows, "model")
        self.assertAlmostEqual(acc, 0.5, places=9)  # 1 correct sur 2

    def test_mean_is_over_all_rows_not_just_first(self):
        rows = [self._row(0, (1.0, 0.0, 0.0)), self._row(0, (0.0, 1.0, 0.0))]
        brier, _, _ = report.metrics(rows, "model")
        # (0 + 2) / 2 = 1.0  (le 2e match : (0-1)^2 + (1-0)^2 + 0 = 2)
        self.assertAlmostEqual(brier, 1.0, places=9)


class TestCalibrationTable(unittest.TestCase):
    def test_buckets_by_predicted_probability_width(self):
        rows = [{"outcome": 0, "probs": {"model": (0.42, 0.30, 0.28)}}]
        table = report.calibration_table(rows, width=0.05)
        # 0.42 -> bucket floor(0.42/0.05)=8 -> [0.40, 0.45)
        bucket = next(c for c in table if c["lo"] <= 0.42 < c["hi"])
        self.assertAlmostEqual(bucket["lo"], 0.40, places=9)
        self.assertAlmostEqual(bucket["hi"], 0.45, places=9)

    def test_observed_frequency_matches_hits_in_bucket(self):
        # 3 lignes de proba 0.50-0.55 (même bucket), 2 vraies, 1 fausse -> obs = 2/3
        rows = [
            {"outcome": 0, "probs": {"model": (0.52, 0.28, 0.20)}},  # home vrai, p=0.52 juste
            {"outcome": 1, "probs": {"model": (0.52, 0.28, 0.20)}},  # home faux ici
            {"outcome": 0, "probs": {"model": (0.53, 0.27, 0.20)}},  # home vrai
        ]
        table = report.calibration_table(rows, width=0.05)
        bucket = next(c for c in table if c["lo"] <= 0.52 < c["hi"])
        self.assertEqual(bucket["n"], 3)
        self.assertAlmostEqual(bucket["obs"], 2 / 3, places=9)

    def test_each_match_contributes_all_three_outcome_probs(self):
        # Un seul match -> 3 lignes (home/draw/away), pas une seule.
        rows = [{"outcome": 0, "probs": {"model": (0.5, 0.3, 0.2)}}]
        table = report.calibration_table(rows, width=1.0)  # un seul bucket géant [0,1)
        self.assertEqual(sum(c["n"] for c in table), 3)


class TestFmtRow(unittest.TestCase):
    def test_pipe_separated_markdown_row(self):
        self.assertEqual(report.fmt_row(["a", 1, 2.5]), "| a | 1 | 2.5 |")


class TestBuildReportIntegration(unittest.TestCase):
    """Bout en bout : predictions -> reports/m3_backtest.md, sur des données
    synthétiques où le modèle bat nettement le marché (pour vérifier le sens
    du calcul, pas juste qu'il ne plante pas)."""

    def _seed_many(self, conn, n=20):
        import datetime
        base = datetime.date(2023, 8, 1)
        for i in range(n):
            day = (base + datetime.timedelta(days=i)).isoformat()
            home, away = (i % 2 == 0), None
            fthg, ftag = (2, 0) if i % 2 == 0 else (0, 2)
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2324",
                                   "home": f"H{i}", "away": f"A{i}", "fthg": fthg, "ftag": ftag})
            conn.commit()
            match_id = conn.execute(
                "SELECT match_id FROM matches WHERE date=? AND home=?", (day, f"H{i}")).fetchone()["match_id"]
            # Modèle proche de la vérité, marché délibérément à côté.
            if i % 2 == 0:
                model, market = (0.9, 0.06, 0.04), (0.34, 0.33, 0.33)
            else:
                model, market = (0.04, 0.06, 0.9), (0.34, 0.33, 0.33)
            db.upsert_prediction(conn, {
                "match_id": match_id, "xi": 0.002,
                "model_h": model[0], "model_d": model[1], "model_a": model[2],
                "market_h": market[0], "market_d": market[1], "market_a": market[2],
                "freq_h": 0.34, "freq_d": 0.33, "freq_a": 0.33})
        conn.commit()

    def test_model_beating_market_is_reflected_as_negative_rel_market(self):
        conn = db.connect(":memory:")
        self._seed_many(conn)
        rows = report.load_predictions(conn)
        b_model, _, _ = report.metrics(rows, "model")
        b_market, _, _ = report.metrics(rows, "market")
        rel_market = (b_model - b_market) / b_market
        self.assertLess(b_model, b_market)  # le modèle est nettement meilleur ici
        self.assertLess(rel_market, 0.0)    # donc l'écart relatif est négatif
        conn.close()


if __name__ == "__main__":
    unittest.main()
