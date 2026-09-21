import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import footballdata
import pipeline
import understat
import xgjoin

FIXTURES = Path(__file__).parent / "fixtures"


class TestExpectedCurrentSeason(unittest.TestCase):
    """Garde-fou du passage de saison : SEASONS/CURRENT_SEASON sont des listes
    figées, l'oubli de la nouvelle saison est silencieux (la saison en cours
    n'est jamais téléchargée et sync-results blâme la source à tort)."""

    def test_reference_dates(self):
        cases = {
            (2027, 1, 15): "2627",   # milieu de saison : l'année civile a changé
            (2027, 8, 15): "2728",   # première journée de la saison suivante
            (2026, 6, 30): "2526",   # intersaison : encore la saison écoulée
            (2026, 7, 1): "2627",    # bascule en juillet, avant le coup d'envoi
            (2026, 12, 31): "2627",
        }
        for (y, m, d), expected in cases.items():
            with self.subTest(date=f"{y}-{m:02d}-{d:02d}"):
                self.assertEqual(
                    footballdata.expected_current_season(datetime.date(y, m, d)),
                    expected)

    def test_century_rollover_stays_two_digits(self):
        # convention de la source : codes à 2+2 chiffres, 1999-2000 s'écrit "9900"
        self.assertEqual(
            footballdata.expected_current_season(datetime.date(1999, 9, 1)), "9900")

    def test_declared_season_is_consistent(self):
        self.assertIn(footballdata.CURRENT_SEASON, footballdata.SEASONS)
        self.assertEqual(footballdata.CURRENT_SEASON, max(footballdata.SEASONS))


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


class TestUpdateLeagueSeason(unittest.TestCase):
    """Observabilité (audit 2026-09-21) : pipeline.update_league_season doit
    distinguer lues/insérées/mises à jour/ignorées et exposer MAX(date) —
    diagnostic direct de ce qu'un run a réellement fait, plutôt qu'un simple
    'N matchs upsertés' qui ne dit pas si la base a bougé."""

    def setUp(self):
        self.conn = db.connect(":memory:")
        self._orig_fetch = footballdata.fetch
        self._orig_understat_fetch = understat.fetch
        footballdata.fetch = lambda league, season, force=False: FIXTURES / "fd_modern.csv"
        understat.fetch = lambda league, season, current, force=False: None

    def tearDown(self):
        footballdata.fetch = self._orig_fetch
        understat.fetch = self._orig_understat_fetch

    def test_first_run_all_inserted(self):
        stats = pipeline.update_league_season(self.conn, "E0", "2324")
        self.assertEqual(stats["read"], 2)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["ignored"], 0)
        self.assertEqual(stats["max_date"], "2023-08-12")

    def test_rerun_identical_data_is_ignored_not_reinserted(self):
        pipeline.update_league_season(self.conn, "E0", "2324")
        stats = pipeline.update_league_season(self.conn, "E0", "2324")
        self.assertEqual(stats["inserted"], 0)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["ignored"], 2)

    def test_changed_row_counts_as_updated(self):
        pipeline.update_league_season(self.conn, "E0", "2324")
        # Une ligne diverge de ce que le CSV va reposer (ex. cote corrigée à
        # la source) -> classée "mise à jour", pas "ignorée".
        db.upsert_match(self.conn, {
            "date": "2023-08-11", "league": "E0", "season": "2324",
            "home": "Burnley", "away": "Man City", "fthg": 0, "ftag": 3,
            "odds_h": 8.5, "odds_d": 5.5, "odds_a": 1.36, "odds_source": "pinnacle_open",
        })
        stats = pipeline.update_league_season(self.conn, "E0", "2324")
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["ignored"], 1)

    def test_missing_csv_returns_none(self):
        footballdata.fetch = lambda league, season, force=False: None
        self.assertIsNone(pipeline.update_league_season(self.conn, "E0", "2324"))


class TestStagnationGuard(unittest.TestCase):
    """Garde anti-stagnation (audit 2026-09-21, football.db figée au 06-07/09
    pendant deux semaines sans qu'aucun run n'échoue). pipeline.main doit
    sortir en échec si MAX(date) de la saison en cours n'avance pas d'un run
    à l'autre alors que la dernière date connue remonte à plus de
    STALE_DAYS_THRESHOLD jours — mais rester en succès si ça avance, même si
    le résultat reste vieux (retard de publication normal de la source)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self._orig_fetch = footballdata.fetch
        self._orig_understat_fetch = understat.fetch
        understat.fetch = lambda league, season, current, force=False: None

    def tearDown(self):
        footballdata.fetch = self._orig_fetch
        understat.fetch = self._orig_understat_fetch
        self.tmpdir.cleanup()

    def _csv_dated(self, days_old):
        old_date = (datetime.date.today() - datetime.timedelta(days=days_old)).strftime("%d/%m/%Y")
        path = Path(self.tmpdir.name) / f"stale_{days_old}.csv"
        path.write_text(f"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,{old_date},A,B,1,0\n")
        return path

    def _run(self, csv_path):
        footballdata.fetch = lambda league, season, force=False: csv_path
        return pipeline.main(["--update", "--league", "E0", "--season", footballdata.CURRENT_SEASON,
                              "--db", str(self.db_path)])

    def test_first_appearance_is_not_stagnation(self):
        # None -> une date : ça avance, même si cette date est déjà vieille.
        self.assertEqual(self._run(self._csv_dated(10)), 0)

    def test_two_stale_runs_in_a_row_fail(self):
        csv_path = self._csv_dated(10)
        self.assertEqual(self._run(csv_path), 0)   # premier run : avance (None -> date)
        self.assertEqual(self._run(csv_path), 1)   # deuxième run : rien de neuf, et > 5 j

    def test_two_recent_runs_in_a_row_succeed(self):
        csv_path = self._csv_dated(2)   # sous le seuil de 5 j
        self.assertEqual(self._run(csv_path), 0)
        self.assertEqual(self._run(csv_path), 0)

    def test_advancing_run_succeeds_even_if_still_old(self):
        self.assertEqual(self._run(self._csv_dated(10)), 0)
        # Deuxième run avec une date plus récente que la première (mais
        # encore > 5 j) : MAX(date) avance -> pas de stagnation.
        self.assertEqual(self._run(self._csv_dated(8)), 0)


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

    def test_join_falls_back_to_swapped_home_away(self):
        """Régression : Rennes-Paris SG du 2026-08-23, relocalisé à Roazhon
        Park (Rennes à domicile côté football-data.co.uk) mais qu'Understat
        liste home=Paris Saint Germain/away=Rennes. Le xG doit atterrir sur
        la bonne équipe (Man United ici) malgré l'inversion domicile/
        extérieur entre les deux sources."""
        aliases = {}
        us = [
            # Understat inverse home/away par rapport à matches (Wolves-Man United)
            {"date": "2023-08-11", "home": "Wolves", "away": "Man United",
             "xg_home": 0.9, "xg_away": 2.3},
        ]
        matched, unmatched = xgjoin.join_xg(self.conn, us, aliases)
        self.assertEqual(matched, 1)
        self.assertEqual(unmatched, [])
        row = self.conn.execute(
            "SELECT xg_home, xg_away FROM matches WHERE home = 'Man United'").fetchone()
        # Man United est home côté matches -> reçoit le xG que Understat
        # attribuait à son away (2.3), pas celui de son home nominal (0.9).
        self.assertEqual(row["xg_home"], 2.3)
        self.assertEqual(row["xg_away"], 0.9)


if __name__ == "__main__":
    unittest.main()
