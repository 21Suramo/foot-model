import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import coupon


def _entry(match, home, away, bets, odds_age_days=4, actual_score=None, date="2026-09-13"):
    return {"match": match, "date": date, "competition": "E0",
           "bets": bets, "actual_score": actual_score,
           "meta": {"home": home, "away": away, "odds_age_days": odds_age_days}}


def _bet(issue, odds=2.0, stake_pct=0.02):
    return {"issue": issue, "odds": odds, "stake_pct": stake_pct}


class TestCouponReadsJournalAsIs(unittest.TestCase):
    """coupon.py doit refléter exactement les mises du journal, jamais les recalculer."""

    def test_eligible_bet_keeps_the_exact_journal_stake_and_odds(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea",
                          [_bet("home", odds=1.85, stake_pct=0.0421)])]
        eligible, excluded = coupon.eligible_and_excluded(entries)
        self.assertEqual(excluded, [])
        self.assertEqual(len(eligible), 1)
        self.assertEqual(eligible[0]["odds"], 1.85)
        self.assertEqual(eligible[0]["stake_pct"], 0.0421)

    def test_settled_entries_are_ignored(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea",
                          [_bet("home")], actual_score="2-1")]
        eligible, excluded = coupon.eligible_and_excluded(entries)
        self.assertEqual(eligible, [])
        self.assertEqual(excluded, [])

    def test_entry_with_no_bets_produces_nothing(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [])]
        eligible, excluded = coupon.eligible_and_excluded(entries)
        self.assertEqual(eligible, [])
        self.assertEqual(excluded, [])


class TestCouponFiltersB3(unittest.TestCase):
    def test_low_history_team_excluded_regardless_of_home_or_away(self):
        entries = [
            _entry("Coventry-Brighton", "Coventry", "Brighton", [_bet("home")]),
            _entry("Newcastle-Hull", "Newcastle", "Hull", [_bet("away")]),
        ]
        eligible, excluded = coupon.eligible_and_excluded(entries)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 2)
        self.assertTrue(all(any("faible historique" in r for r in row["reasons"])
                            for row in excluded))

    def test_stale_odds_excluded_at_five_days(self):
        entries = [_entry("Leeds-Newcastle", "Leeds", "Newcastle",
                          [_bet("home", stake_pct=0.05)], odds_age_days=5)]
        eligible, excluded = coupon.eligible_and_excluded(entries)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 1)

    def test_bet_excluded_for_both_reasons_reports_both(self):
        # Leeds-Newcastle : Leeds est faible historique ET la cote a 5 jours —
        # les deux raisons doivent apparaître, aucune ne doit être tue.
        entries = [_entry("Leeds-Newcastle", "Leeds", "Newcastle",
                          [_bet("home", stake_pct=0.05)], odds_age_days=5)]
        _, excluded = coupon.eligible_and_excluded(entries)
        reasons_text = " ".join(excluded[0]["reasons"])
        self.assertIn("faible historique", reasons_text)
        self.assertIn("cote périmée", reasons_text)
        self.assertEqual(len(excluded[0]["reasons"]), 2)

    def test_four_days_old_is_not_stale(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea",
                          [_bet("home")], odds_age_days=4)]
        eligible, _ = coupon.eligible_and_excluded(entries)
        self.assertEqual(len(eligible), 1)

    def test_fresh_team_and_odds_pass_through(self):
        entries = [_entry("Man United-Man City", "Man United", "Man City",
                          [_bet("home")], odds_age_days=4)]
        eligible, excluded = coupon.eligible_and_excluded(entries)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(excluded, [])

    def test_total_exposure_matches_manual_sum_of_eligible_stakes(self):
        entries = [
            _entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.03)]),
            _entry("Real Madrid-Barcelona", "Real Madrid", "Barcelona",
                  [_bet("home", stake_pct=0.02), _bet("draw", stake_pct=0.01)]),
            _entry("Coventry-Brighton", "Coventry", "Brighton", [_bet("home", stake_pct=0.05)]),
        ]
        eligible, _ = coupon.eligible_and_excluded(entries)
        self.assertAlmostEqual(sum(r["stake_pct"] for r in eligible), 0.06, places=6)


def _flags(match, **kwargs):
    flags = {"match": match, "date": "2026-09-20",
             "post_european_home": False, "post_european_away": False, "derby": False,
             "coach_change_home": False, "coach_change_away": False,
             "weather_extreme": False, "low_stakes_home": False, "low_stakes_away": False,
             "notes": ""}
    flags.update(kwargs)
    return flags


class TestCouponContextFlagsNiveau2(unittest.TestCase):
    """Filtres de contexte sportif (Niveau 2) : lisent context_flags.json, ne
    modifient jamais le journal, absence de documentation = gardé."""

    def test_match_without_any_flags_entry_is_kept(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.03)])]
        context_flags = {}  # aucun match documenté
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(excluded, [])

    def test_post_european_excludes(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.03)])]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea", post_european_home=True)}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 1)
        self.assertTrue(any("européen" in r for r in excluded[0]["reasons"]))

    def test_coach_change_excludes(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("away", stake_pct=0.02)])]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea", coach_change_away=True)}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 1)
        self.assertTrue(any("entraîneur" in r for r in excluded[0]["reasons"]))

    def test_weather_extreme_excludes(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.02)])]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea", weather_extreme=True)}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 1)

    def test_low_stakes_excludes(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.02)])]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea", low_stakes_away=True)}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 1)

    def test_derby_with_outsider_odds_excludes(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea",
                          [_bet("away", odds=5.0, stake_pct=0.01)])]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea", derby=True)}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(eligible, [])
        self.assertEqual(len(excluded), 1)
        self.assertTrue(any("derby" in r for r in excluded[0]["reasons"]))

    def test_derby_with_favorite_odds_is_kept(self):
        entries = [_entry("Arsenal-Chelsea", "Arsenal", "Chelsea",
                          [_bet("home", odds=2.5, stake_pct=0.01)])]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea", derby=True)}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(excluded, [])
        # Le derby reste visible dans le contexte du pari retenu (rien à cacher).
        self.assertIn("derby local", eligible[0]["context"]["flags"])

    def test_match_absent_from_context_flags_is_kept_and_reported_undocumented(self):
        entries = [
            _entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.03)]),
            _entry("Real Madrid-Barcelona", "Real Madrid", "Barcelona", [_bet("home", stake_pct=0.02)]),
        ]
        context_flags = {"Arsenal-Chelsea": _flags("Arsenal-Chelsea")}
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        self.assertEqual(len(eligible), 2)
        self.assertEqual(excluded, [])
        undocumented = coupon.undocumented_matches(entries, context_flags)
        self.assertEqual(undocumented, ["Real Madrid-Barcelona"])

    def test_exposure_total_recalculated_after_context_filters(self):
        entries = [
            _entry("Arsenal-Chelsea", "Arsenal", "Chelsea", [_bet("home", stake_pct=0.03)]),
            _entry("Real Madrid-Barcelona", "Real Madrid", "Barcelona", [_bet("home", stake_pct=0.02)]),
            _entry("Newcastle-Fulham", "Newcastle", "Fulham", [_bet("home", stake_pct=0.05)]),
        ]
        context_flags = {
            "Arsenal-Chelsea": _flags("Arsenal-Chelsea", weather_extreme=True),
            "Real Madrid-Barcelona": _flags("Real Madrid-Barcelona"),
        }
        eligible, excluded = coupon.eligible_and_excluded(entries, context_flags)
        # Arsenal-Chelsea exclu par la météo, Newcastle-Fulham gardé (non documenté).
        self.assertAlmostEqual(sum(r["stake_pct"] for r in eligible), 0.07, places=6)
        self.assertEqual(len(excluded), 1)
        undocumented = coupon.undocumented_matches(entries, context_flags)
        self.assertEqual(undocumented, ["Newcastle-Fulham"])


class TestCouponContextFlagsLoading(unittest.TestCase):
    def test_load_context_flags_missing_file_returns_empty(self):
        self.assertEqual(coupon.load_context_flags("/nonexistent/context_flags.json"), {})

    def test_load_context_flags_indexes_by_match(self):
        import json
        import tempfile
        rows = [_flags("Arsenal-Chelsea", derby=True)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(rows, f)
            path = f.name
        try:
            loaded = coupon.load_context_flags(path)
            self.assertIn("Arsenal-Chelsea", loaded)
            self.assertTrue(loaded["Arsenal-Chelsea"]["derby"])
        finally:
            import os
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
