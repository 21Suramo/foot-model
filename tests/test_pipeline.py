import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import footballdata
import understat
import xgjoin

FIXTURES = Path(__file__).parent / "fixtures"


class TestFootballDataParsing(unittest.TestCase):
    def test_modern_csv_prefers_pinnacle_closing(self):
        rows = footballdata.parse_csv(FIXTURES / "fd_modern.csv", "E0", "2324")
        self.assertEqual(len(rows), 2)
        r = rows[0]
        self.assertEqual(r["date"], "2023-08-11")
        self.assertEqual((r["home"], r["away"]), ("Burnley", "Man City"))
        self.assertEqual((r["fthg"], r["ftag"], r["hthg"], r["htag"]), (0, 3, 0, 2))
        self.assertEqual(r["odds_source"], "pinnacle_close")
        self.assertEqual((r["odds_h"], r["odds_d"], r["odds_a"]), (9.00, 5.75, 1.33))
        self.assertEqual((r["ou25_over"], r["ou25_under"]), (1.75, 1.95))
        self.assertEqual(r["ah_line"], 1.5)
        self.assertEqual((r["ah_home"], r["ah_away"]), (1.97, 1.93))

    def test_modern_csv_falls_back_to_avg_closing(self):
        rows = footballdata.parse_csv(FIXTURES / "fd_modern.csv", "E0", "2324")
        r = rows[1]
        # PSCH manquant sur cette ligne -> moyenne clôture
        self.assertEqual(r["odds_source"], "avg_close")
        self.assertEqual((r["odds_h"], r["odds_d"], r["odds_a"]), (1.28, 6.10, 11.00))
        # PCAHH manquant -> AvgCAHH ; ligne AH clôture prioritaire
        self.assertEqual(r["ah_line"], -1.75)
        self.assertEqual((r["ah_home"], r["ah_away"]), (2.02, 1.90))

    def test_1819_csv_falls_back_to_opening(self):
        rows = footballdata.parse_csv(FIXTURES / "fd_1819.csv", "E0", "1819")
        self.assertEqual(rows[0]["date"], "2018-08-10")  # format %d/%m/%y
        self.assertEqual(rows[0]["odds_source"], "pinnacle_open")
        self.assertEqual(rows[0]["odds_h"], 1.60)
        # Pas de PSH sur la 2e ligne -> moyenne BbAv
        self.assertEqual(rows[1]["odds_source"], "avg_open")
        self.assertEqual(rows[1]["odds_h"], 1.85)
        self.assertEqual((rows[1]["ou25_over"], rows[1]["ou25_under"]), (2.10, 1.78))
        self.assertEqual(rows[1]["ah_line"], -0.5)


class TestDb(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def _row(self, **kw):
        base = {"date": "2023-08-11", "league": "E0", "season": "2324",
                "home": "Burnley", "away": "Man City", "fthg": 0, "ftag": 3,
                "odds_h": 9.0, "odds_d": 5.75, "odds_a": 1.33, "odds_source": "pinnacle_close"}
        base.update(kw)
        return base

    def test_upsert_is_idempotent(self):
        db.upsert_match(self.conn, self._row())
        db.upsert_match(self.conn, self._row(odds_h=8.9))
        rows = self.conn.execute("SELECT * FROM matches").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["odds_h"], 8.9)

    def test_upsert_preserves_xg(self):
        db.upsert_match(self.conn, self._row())
        self.assertTrue(db.update_xg(self.conn, "2023-08-11", "Burnley", "Man City", 0.5, 2.8))
        db.upsert_match(self.conn, self._row())  # re-run du pipeline
        row = self.conn.execute("SELECT xg_home, xg_away FROM matches").fetchone()
        self.assertEqual((row["xg_home"], row["xg_away"]), (0.5, 2.8))


class TestUnderstat(unittest.TestCase):
    def test_extract_dates_data_decodes_hex_escapes(self):
        payload = [{"id": "1", "isResult": True, "datetime": "2023-08-11 19:00:00",
                    "h": {"title": "Alavés"}, "a": {"title": "Sevilla"},
                    "xG": {"h": "1.23", "a": "0.87"}}]
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        escaped = "".join("\\x%02X" % b for b in raw)
        html = f"<script>var datesData = JSON.parse('{escaped}');</script>"
        data = understat.extract_dates_data(html)
        self.assertEqual(data[0]["h"]["title"], "Alavés")

    def test_parse_matches_skips_unplayed(self):
        data = [
            {"isResult": True, "datetime": "2023-08-11 19:00:00",
             "h": {"title": "A"}, "a": {"title": "B"}, "xG": {"h": "1.0", "a": "2.0"}},
            {"isResult": False, "datetime": "2026-05-01 19:00:00",
             "h": {"title": "C"}, "a": {"title": "D"}, "xG": {"h": None, "a": None}},
        ]
        matches = understat.parse_matches(data)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], {"date": "2023-08-11", "home": "A", "away": "B",
                                      "xg_home": 1.0, "xg_away": 2.0})


class TestXgJoin(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        for date, home, away in [("2023-08-11", "Man United", "Wolves"),
                                 ("2023-08-12", "Arsenal", "Everton")]:
            db.upsert_match(self.conn, {"date": date, "league": "E0", "season": "2324",
                                        "home": home, "away": away, "fthg": 1, "ftag": 0})

    def test_join_with_aliases_and_date_tolerance(self):
        aliases = {"Manchester United": "Man United", "Wolverhampton Wanderers": "Wolves"}
        us = [
            {"date": "2023-08-11", "home": "Manchester United", "away": "Wolverhampton Wanderers",
             "xg_home": 1.5, "xg_away": 0.7},
            # date décalée de deux jours côté Understat (max toléré)
            {"date": "2023-08-14", "home": "Arsenal", "away": "Everton",
             "xg_home": 2.1, "xg_away": 0.3},
            {"date": "2023-08-12", "home": "Inconnu FC", "away": "Everton",
             "xg_home": 1.0, "xg_away": 1.0},
        ]
        matched, unmatched = xgjoin.join_xg(self.conn, us, aliases)
        self.assertEqual(matched, 2)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["home"], "Inconnu FC")
        row = self.conn.execute(
            "SELECT xg_home FROM matches WHERE home = 'Man United'").fetchone()
        self.assertEqual(row["xg_home"], 1.5)


if __name__ == "__main__":
    unittest.main()
