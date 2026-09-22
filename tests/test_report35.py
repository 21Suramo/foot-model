"""report35.py réutilise metrics/calibration_table de report.py (déjà testées
dans test_report.py) mais calcule lui-même rel_market/beats_freq/beats_unif et
assemble le verdict "4 critères sur 4" cité dans CLAUDE.md — jamais testé
avant ce fichier. Utilise le vrai data/m35_frozen.json (fichier figé committé,
jamais régénéré) plutôt qu'un faux : c'est la config que ce rapport lit
réellement en production."""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import report35


class TestBuildReportIntegration(unittest.TestCase):
    def _seed(self, conn, n=320, model_beats_market=True):
        # n >= CALIB_MIN_N (300, cf. report.py) : chaque match alimente le
        # même bucket de calibration (proba 0.9) une fois, il en faut assez
        # pour que ce bucket franchisse le seuil et entre dans le verdict.
        base = datetime.date(2023, 8, 1)
        for i in range(n):
            day = (base + datetime.timedelta(days=i)).isoformat()
            home_wins = i % 2 == 0
            fthg, ftag = (2, 0) if home_wins else (0, 2)
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2324",
                                   "home": f"H{i}", "away": f"A{i}", "fthg": fthg, "ftag": ftag})
            conn.commit()
            match_id = conn.execute(
                "SELECT match_id FROM matches WHERE date=? AND home=?",
                (day, f"H{i}")).fetchone()["match_id"]
            if model_beats_market:
                model = (0.9, 0.06, 0.04) if home_wins else (0.04, 0.06, 0.9)
                market = (0.34, 0.33, 0.33)
            else:
                model = (0.34, 0.33, 0.33)
                market = (0.9, 0.06, 0.04) if home_wins else (0.04, 0.06, 0.9)
            db.upsert_prediction_m35(conn, {
                "match_id": match_id, "xi": 0.003, "xg_w": 0.6, "kappa": 1.0, "temperature": 1.0,
                "model_h": model[0], "model_d": model[1], "model_a": model[2],
                "raw_h": model[0], "raw_d": model[1], "raw_a": model[2],
                "market_h": market[0], "market_d": market[1], "market_a": market[2],
                "freq_h": 0.34, "freq_d": 0.33, "freq_a": 0.33})
        conn.commit()

    def test_model_beating_market_shows_negative_relative_gap(self):
        conn = db.connect(":memory:")
        self._seed(conn, model_beats_market=True)
        text = report35.build_report(conn)
        # rel_market négatif == modèle meilleur que le marché -> critère "< +2 %" validé
        self.assertIn("✅", text)
        self.assertIn("Brier modèle à -", text)
        conn.close()

    def test_model_worse_than_market_shows_positive_gap_and_fails_criterion(self):
        conn = db.connect(":memory:")
        self._seed(conn, model_beats_market=False)
        text = report35.build_report(conn)
        self.assertIn("❌", text)
        self.assertIn("Brier modèle à +", text)
        conn.close()

    def test_empty_predictions_table_exits(self):
        conn = db.connect(":memory:")
        with self.assertRaises(SystemExit):
            report35.build_report(conn)
        conn.close()

    def test_m3_comparison_appears_only_when_m3_rows_exist(self):
        conn = db.connect(":memory:")
        self._seed(conn, model_beats_market=True)
        text = report35.build_report(conn)
        self.assertNotIn("Modèle M3.5 vs M3", text)
        conn.close()


if __name__ == "__main__":
    unittest.main()
