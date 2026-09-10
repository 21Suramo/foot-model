import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


def _row(**overrides):
    row = {"fetched_at": "2026-09-10T10:00:00+00:00", "league": "E0",
          "commence_time": "2026-09-12T14:00:00Z", "home": "Aston Villa",
          "away": "Nottingham Forest", "book": "pinnacle", "market": "h2h",
          "outcome": "home", "point": None, "price": 2.23}
    row.update(overrides)
    return row


class TestBookOdds(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_insert_and_read_back(self):
        db.insert_book_odds(self.conn, _row())
        rows = self.conn.execute("SELECT * FROM book_odds").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["book"], "pinnacle")
        self.assertEqual(rows[0]["price"], 2.23)
        self.assertIsNone(rows[0]["point"])

    def test_same_snapshot_reinserted_is_not_duplicated(self):
        # INSERT OR IGNORE sur la contrainte UNIQUE : relancer le même fetch
        # (mêmes horodatages) ne doit pas dupliquer la ligne.
        db.insert_book_odds(self.conn, _row())
        db.insert_book_odds(self.conn, _row())
        n = self.conn.execute("SELECT COUNT(*) AS n FROM book_odds").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_different_fetched_at_is_a_new_row_not_an_overwrite(self):
        # C'est une série temporelle (roadmap A2) : un nouveau snapshot s'AJOUTE,
        # il ne remplace jamais l'ancien (contrairement aux upserts de matches/predictions).
        db.insert_book_odds(self.conn, _row(fetched_at="2026-09-10T10:00:00+00:00", price=2.20))
        db.insert_book_odds(self.conn, _row(fetched_at="2026-09-10T16:00:00+00:00", price=2.30))
        rows = self.conn.execute("SELECT fetched_at, price FROM book_odds ORDER BY fetched_at").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["price"] for r in rows], [2.20, 2.30])

    def test_totals_point_is_stored(self):
        db.insert_book_odds(self.conn, _row(market="totals", outcome="Over", point=2.5, price=1.87))
        row = self.conn.execute("SELECT * FROM book_odds").fetchone()
        self.assertEqual(row["point"], 2.5)
        self.assertEqual(row["outcome"], "Over")


if __name__ == "__main__":
    unittest.main()
