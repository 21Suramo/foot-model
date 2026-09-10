import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import oddsapi

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "oddsapi_sample.json").read_text())


class TestApiKey(unittest.TestCase):
    def test_missing_key_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                oddsapi.api_key()

    def test_present_key_is_returned(self):
        with mock.patch.dict("os.environ", {"ODDS_API_KEY": "abc123"}):
            self.assertEqual(oddsapi.api_key(), "abc123")


class TestParseSnapshot(unittest.TestCase):
    def test_h2h_outcomes_remapped_to_home_draw_away(self):
        rows = oddsapi.parse_snapshot("E0", FIXTURE, "2026-09-10T10:00:00+00:00")
        h2h = [r for r in rows if r["market"] == "h2h" and r["book"] == "pinnacle"]
        by_outcome = {r["outcome"]: r["price"] for r in h2h}
        self.assertEqual(set(by_outcome), {"home", "draw", "away"})
        self.assertEqual(by_outcome["home"], 2.23)   # Aston Villa (domicile)
        self.assertEqual(by_outcome["away"], 3.3)    # Nottingham Forest
        self.assertEqual(by_outcome["draw"], 3.53)
        self.assertTrue(all(r["point"] is None for r in h2h))

    def test_totals_outcomes_keep_over_under_and_point(self):
        rows = oddsapi.parse_snapshot("E0", FIXTURE, "2026-09-10T10:00:00+00:00")
        totals = [r for r in rows if r["market"] == "totals"]
        self.assertEqual({r["outcome"] for r in totals}, {"Over", "Under"})
        self.assertTrue(all(r["point"] == 2.5 for r in totals))

    def test_league_commence_time_and_fetched_at_propagated(self):
        rows = oddsapi.parse_snapshot("E0", FIXTURE, "2026-09-10T10:00:00+00:00")
        self.assertTrue(all(r["league"] == "E0" for r in rows))
        self.assertTrue(all(r["fetched_at"] == "2026-09-10T10:00:00+00:00" for r in rows))
        self.assertTrue(all(r["commence_time"] == "2026-09-12T14:00:00Z" for r in rows))
        self.assertTrue(all(r["home"] == "Aston Villa" and r["away"] == "Nottingham Forest"
                            for r in rows))

    def test_row_count_matches_fixture(self):
        # 1 match, 2 books : winamax_fr (h2h seul, 3 issues) + pinnacle (h2h 3 + totals 2)
        rows = oddsapi.parse_snapshot("E0", FIXTURE, "2026-09-10T10:00:00+00:00")
        self.assertEqual(len(rows), 3 + 3 + 2)


if __name__ == "__main__":
    unittest.main()
