import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from features.build import build_feature_table, features_for_fixture, state_asof
from features.elo import EloRatings, expected_score
from features.form import FormTracker, make_record
from features.pi_rating import PiRatings
from features.rest import RestTracker
import datetime


class TestElo(unittest.TestCase):
    def test_unknown_team_gets_default_rating(self):
        elo = EloRatings()
        self.assertEqual(elo.get("Nouvelle Equipe"), elo.default)

    def test_get_before_update_is_pre_match(self):
        elo = EloRatings()
        pre_home, pre_away = elo.update("A", "B", 2, 0)
        self.assertEqual(pre_home, elo.default)
        self.assertEqual(pre_away, elo.default)
        # après update, le rating a bougé (A a gagné à domicile)
        self.assertGreater(elo.get("A"), elo.default)
        self.assertLess(elo.get("B"), elo.default)

    def test_zero_sum_between_two_teams(self):
        elo = EloRatings()
        elo.update("A", "B", 1, 1)
        # un match nul entre deux ratings égaux (avantage domicile mis à part)
        # ne doit pas laisser le pool total dériver arbitrairement : la somme
        # des deltas des deux équipes est nulle par construction (delta / -delta).
        r_a, r_b = elo.get("A"), elo.get("B")
        self.assertAlmostEqual((r_a - elo.default) + (r_b - elo.default), 0.0)

    def test_expected_score_symmetric(self):
        self.assertAlmostEqual(expected_score(1500, 1500), 0.5)
        self.assertGreater(expected_score(1600, 1500), 0.5)


class TestPiRating(unittest.TestCase):
    def test_unknown_team_default(self):
        pi = PiRatings()
        self.assertEqual(pi.get("X"), (pi.default, pi.default))

    def test_win_increases_home_rating(self):
        pi = PiRatings()
        pi.update("A", "B", 3, 0)
        rh, ra = pi.get("A")
        self.assertGreater(rh, pi.default)

    def test_get_is_pre_match(self):
        pi = PiRatings()
        rh_home, ra_home, rh_away, ra_away = pi.update("A", "B", 2, 0)
        self.assertEqual(rh_home, pi.default)
        self.assertEqual(ra_away, pi.default)


class TestRest(unittest.TestCase):
    def test_default_for_unseen_team(self):
        rest = RestTracker(default_days=14)
        self.assertEqual(rest.get("A", datetime.date(2026, 1, 1)), 14)

    def test_days_since_last_match(self):
        rest = RestTracker()
        rest.update("A", datetime.date(2026, 1, 1))
        self.assertEqual(rest.get("A", datetime.date(2026, 1, 8)), 7)


class TestForm(unittest.TestCase):
    def test_empty_history_is_nan(self):
        form = FormTracker()
        feats = form.get("A")
        self.assertTrue(all(v != v for v in feats.values()))  # NaN != NaN

    def test_rolling_window_respects_size(self):
        form = FormTracker(windows=(2,))
        for gf in (1, 2, 3):
            form.update("A", make_record(True, gf, 0, None, None, None, None, None, None, None, None))
        feats = form.get("A")
        # dernière fenêtre de 2 : (2+3)/2 = 2.5, pas (1+2+3)/3
        self.assertAlmostEqual(feats["form_gf_2_overall"], 2.5)

    def test_home_away_split(self):
        form = FormTracker(windows=(5,))
        form.update("A", make_record(True, 3, 0, None, None, None, None, None, None, None, None))
        form.update("A", make_record(False, 1, 1, None, None, None, None, None, None, None, None))
        feats = form.get("A")
        self.assertAlmostEqual(feats["form_gf_5_home"], 3.0)
        self.assertAlmostEqual(feats["form_gf_5_away"], 1.0)
        self.assertAlmostEqual(feats["form_gf_5_overall"], 2.0)


class TestBuildNoLeakage(unittest.TestCase):
    """Le coeur de la garde anti-fuite : les features d'un match ne doivent
    JAMAIS dépendre de son propre résultat ni d'un match futur."""

    def setUp(self):
        self.conn = db.connect(":memory:")
        rows = [
            ("2026-01-01", "A", "B", 3, 0),
            ("2026-01-08", "B", "A", 1, 1),
            ("2026-01-15", "A", "B", 0, 2),
        ]
        for i, (date, home, away, fthg, ftag) in enumerate(rows):
            db.upsert_match(self.conn, {
                "date": date, "league": "E0", "season": "2526", "home": home, "away": away,
                "fthg": fthg, "ftag": ftag, "hthg": None, "htag": None,
                "odds_h": None, "odds_d": None, "odds_a": None, "odds_source": None,
                "ou25_over": None, "ou25_under": None, "ah_line": None, "ah_home": None, "ah_away": None,
            })
        self.conn.commit()

    def test_first_match_has_default_state(self):
        table = build_feature_table(self.conn, leagues=["E0"])
        self.assertEqual(len(table), 3)
        first = table[0]
        self.assertEqual(first["elo_home"], 1500.0)
        self.assertEqual(first["elo_away"], 1500.0)
        self.assertEqual(first["rest_days_home"], 14)

    def test_third_match_reflects_first_two_not_itself(self):
        table = build_feature_table(self.conn, leagues=["E0"])
        third = table[2]
        # A a gagné le 1er match (3-0) et fait nul le 2e (1-1) : son elo doit
        # avoir bougé par rapport au défaut, sans refléter le 0-2 du 3e match
        # lui-même (sinon son elo baisserait, ce qui est vérifié séparément
        # en comparant à un état recalculé strictement avant ce match).
        elo_before_third, _, _, _ = state_asof(self.conn, third["date"], leagues=["E0"])
        self.assertAlmostEqual(third["elo_home"], elo_before_third.get("A"))

    def test_state_asof_matches_walk_forward_value(self):
        table = build_feature_table(self.conn, leagues=["E0"])
        trackers = state_asof(self.conn, "2026-01-15", leagues=["E0"])
        feats = features_for_fixture(trackers, "A", "B", "2026-01-15")
        self.assertAlmostEqual(feats["elo_home"], table[2]["elo_home"])
        self.assertAlmostEqual(feats["rest_days_home"], table[2]["rest_days_home"])

    def test_state_asof_excludes_matches_on_ref_date_itself(self):
        # upto_date utilise "date < ref_date" (strict), donc un match daté
        # EXACTEMENT du jour visé ne doit jamais influencer son propre état.
        trackers_before = state_asof(self.conn, "2026-01-01", leagues=["E0"])
        elo_before, _, _, _ = trackers_before
        self.assertEqual(elo_before.get("A"), 1500.0)
        self.assertEqual(elo_before.get("B"), 1500.0)


if __name__ == "__main__":
    unittest.main()
