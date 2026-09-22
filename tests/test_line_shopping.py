import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import line_shopping


def _odds_row(**overrides):
    row = {"fetched_at": "2026-09-10T10:00:00+00:00", "league": "E0",
          "commence_time": "2026-09-12T14:00:00Z", "home": "Arsenal",
          "away": "Leeds", "book": "pinnacle", "market": "h2h",
          "outcome": "home", "point": None, "price": 1.90}
    row.update(overrides)
    return row


class TestLineShopping(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_no_data_returns_empty(self):
        self.assertEqual(line_shopping.best_odds_1x2(self.conn, "E0", "Arsenal", "Leeds"), {})
        self.assertEqual(line_shopping.summarize(self.conn, "E0", "Arsenal", "Leeds"), {})

    def test_picks_maximum_price_across_books(self):
        db.insert_book_odds(self.conn, _odds_row(book="pinnacle", price=1.85))
        db.insert_book_odds(self.conn, _odds_row(book="winamax_fr", price=1.92))
        db.insert_book_odds(self.conn, _odds_row(book="betclic_fr", price=1.79))
        best = line_shopping.best_odds_1x2(self.conn, "E0", "Arsenal", "Leeds")
        self.assertEqual(best["home"]["price"], 1.92)
        self.assertEqual(best["home"]["book"], "winamax_fr")

    def test_separates_outcomes(self):
        db.insert_book_odds(self.conn, _odds_row(outcome="home", price=1.90, book="a"))
        db.insert_book_odds(self.conn, _odds_row(outcome="draw", price=3.60, book="b"))
        db.insert_book_odds(self.conn, _odds_row(outcome="away", price=4.20, book="c"))
        best = line_shopping.best_odds_1x2(self.conn, "E0", "Arsenal", "Leeds")
        self.assertEqual(set(best.keys()), {"home", "draw", "away"})

    def test_asof_excludes_future_snapshots(self):
        # Une cote capturée APRÈS l'instant demandé ne doit jamais influencer
        # le "meilleur prix" à cet instant (même discipline anti-fuite que le
        # reste du projet).
        db.insert_book_odds(self.conn, _odds_row(fetched_at="2026-09-10T10:00:00+00:00", price=1.80))
        db.insert_book_odds(self.conn, _odds_row(fetched_at="2026-09-15T10:00:00+00:00", price=2.50))
        best = line_shopping.best_odds_1x2(self.conn, "E0", "Arsenal", "Leeds",
                                           asof="2026-09-12T00:00:00+00:00")
        self.assertEqual(best["home"]["price"], 1.80)

    def test_totals_filters_by_point(self):
        db.insert_book_odds(self.conn, _odds_row(market="totals", outcome="Over", point=2.5,
                                                   price=1.90, book="a"))
        db.insert_book_odds(self.conn, _odds_row(market="totals", outcome="Over", point=1.5,
                                                   price=1.30, book="b"))
        best = line_shopping.best_odds_totals(self.conn, "E0", "Arsenal", "Leeds", 2.5)
        self.assertEqual(best["Over"]["price"], 1.90)

    def test_summarize_combines_h2h_and_totals(self):
        db.insert_book_odds(self.conn, _odds_row(outcome="home", price=1.90))
        db.insert_book_odds(self.conn, _odds_row(market="totals", outcome="Over", point=2.5, price=1.87))
        s = line_shopping.summarize(self.conn, "E0", "Arsenal", "Leeds")
        self.assertIn("h2h", s)
        self.assertIn("ou2.5", s)

    def test_different_fixture_is_isolated(self):
        db.insert_book_odds(self.conn, _odds_row(home="Arsenal", away="Leeds", price=1.90))
        best = line_shopping.best_odds_1x2(self.conn, "E0", "Chelsea", "Everton")
        self.assertEqual(best, {})


if __name__ == "__main__":
    unittest.main()
