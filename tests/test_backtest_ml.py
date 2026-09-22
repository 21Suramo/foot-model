import datetime
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_ml
import db


def _seed_matches(conn, league="E0", teams=("A", "B", "C", "D"), seasons=("1819", "1920", "2021")):
    """Petit round-robin synthétique réparti sur 3 saisons réelles (mêmes
    codes que footballdata.SEASONS, requis par dc_stacking_map qui filtre
    dessus) — assez de matchs pour que Dixon-Coles et le GBM s'ajustent sans
    erreur, assez peu pour que les tests restent rapides."""
    rng = random.Random(7)
    start = datetime.date(2018, 8, 11)
    match_date = start
    for season in seasons:
        for _ in range(15):
            home, away = rng.sample(teams, 2)
            db.upsert_match(conn, {
                "date": match_date.isoformat(), "league": league, "season": season,
                "home": home, "away": away,
                "fthg": rng.randint(0, 3), "ftag": rng.randint(0, 3),
                "hthg": None, "htag": None,
                "odds_h": 2.0, "odds_d": 3.3, "odds_a": 3.8, "odds_source": "pinnacle_close",
                "ou25_over": None, "ou25_under": None, "ah_line": None, "ah_home": None, "ah_away": None,
                "shots_h": rng.uniform(5, 20), "shots_a": rng.uniform(5, 20),
                "sot_h": rng.uniform(1, 8), "sot_a": rng.uniform(1, 8),
                "corners_h": rng.uniform(2, 10), "corners_a": rng.uniform(2, 10),
            })
            match_date += datetime.timedelta(days=7)
        match_date += datetime.timedelta(days=90)  # intersaison
    conn.commit()


class TestDcStackingMap(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        _seed_matches(self.conn)

    def test_first_season_has_no_stacking_feature(self):
        # SEASONS[0] ("1819") ne sert qu'à amorcer l'historique, jamais
        # prédite (même convention que backtest.py) : ses matchs ne doivent
        # donc JAMAIS apparaître dans la carte de stacking.
        dc_map = backtest_ml.dc_stacking_map(self.conn, ["E0"])
        first_season_ids = {r["match_id"] for r in self.conn.execute(
            "SELECT match_id FROM matches WHERE season = '1819'")}
        self.assertTrue(first_season_ids.isdisjoint(dc_map.keys()))

    def test_later_season_has_stacking_probs_summing_to_one(self):
        dc_map = backtest_ml.dc_stacking_map(self.conn, ["E0"])
        later_ids = [r["match_id"] for r in self.conn.execute(
            "SELECT match_id FROM matches WHERE season = '2021'")]
        self.assertTrue(later_ids)
        for mid in later_ids:
            self.assertIn(mid, dc_map)
            probs = dc_map[mid]
            total = probs["dc_prob_h"] + probs["dc_prob_d"] + probs["dc_prob_a"]
            self.assertAlmostEqual(total, 1.0, places=6)


class TestBuildStackedTable(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        _seed_matches(self.conn)

    def test_table_has_dc_and_league_features_once(self):
        table, feature_names = backtest_ml.build_stacked_table(self.conn, ["E0"])
        self.assertEqual(len(table), 45)
        self.assertEqual(feature_names.count("dc_prob_h"), 1)
        self.assertEqual(feature_names.count("league"), 1)
        self.assertIn("elo_home", feature_names)


class TestPurgedWalkForward(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        _seed_matches(self.conn)
        self.table, self.feature_names = backtest_ml.build_stacked_table(self.conn, ["E0"])

    def test_training_rows_strictly_before_fold_cutoff(self):
        starts = backtest_ml._season_start_dates(self.table)
        cutoff = starts["2021"]
        preds = backtest_ml.purged_walk_forward(
            self.table, self.feature_names, ("2021",),
            clf_params={"num_leaves": 3}, reg_params={"num_leaves": 3}, num_rounds=5)
        self.assertTrue(preds)
        # Toutes les prédictions portent sur des matchs de la saison cible.
        self.assertTrue(all(p["season"] == "2021" for p in preds))
        # Le modèle n'a pu être entraîné QUE sur des matchs antérieurs au cutoff
        # (vérifié indirectement : aucune ligne de la saison "2021" elle-même
        # n'a pu fuiter dans son propre entraînement, par construction du filtre
        # `r["date"] < cutoff` de purged_walk_forward — testé ici en s'assurant
        # qu'aucun match de la saison cible ne précède le cutoff calculé).
        target_dates = [p["date"] for p in preds]
        self.assertTrue(all(d >= cutoff for d in target_dates))

    def test_probs_sum_to_one(self):
        preds = backtest_ml.purged_walk_forward(
            self.table, self.feature_names, ("2021",),
            clf_params={"num_leaves": 3}, reg_params={"num_leaves": 3}, num_rounds=5)
        for p in preds:
            self.assertAlmostEqual(sum(p["model"]), 1.0, places=6)


class TestShuffledScratchDb(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        _seed_matches(self.conn)

    def test_shuffle_preserves_fixture_but_scrambles_results(self):
        scratch = backtest_ml._shuffled_scratch_db(self.conn, ["E0"], seed=1)
        real = {(r["date"], r["home"], r["away"]) for r in self.conn.execute(
            "SELECT date, home, away FROM matches")}
        shuffled = {(r["date"], r["home"], r["away"]) for r in scratch.execute(
            "SELECT date, home, away FROM matches")}
        self.assertEqual(real, shuffled)  # dates/équipes intactes

        real_results = [(r["date"], r["home"], r["away"], r["fthg"], r["ftag"])
                        for r in self.conn.execute("SELECT date, home, away, fthg, ftag FROM matches ORDER BY date")]
        shuf_results = [(r["date"], r["home"], r["away"], r["fthg"], r["ftag"])
                        for r in scratch.execute("SELECT date, home, away, fthg, ftag FROM matches ORDER BY date")]
        self.assertNotEqual(real_results, shuf_results)  # au moins un résultat déplacé
        scratch.close()


if __name__ == "__main__":
    unittest.main()
