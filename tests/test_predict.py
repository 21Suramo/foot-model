import contextlib
import datetime
import inspect
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import backtest
import backtest35
import db
import footballdata
import model
import predict


class TestTeamResolution(unittest.TestCase):
    teams = ["Man City", "Arsenal", "Nott'm Forest", "Paris SG"]
    aliases = {"Manchester City": "Man City", "Paris Saint Germain": "Paris SG"}

    def test_exact_canonical(self):
        self.assertEqual(predict.resolve_team("Arsenal", self.teams, self.aliases), ("Arsenal", True))

    def test_alias_understat_name(self):
        self.assertEqual(predict.resolve_team("Manchester City", self.teams, self.aliases),
                         ("Man City", True))

    def test_case_insensitive(self):
        self.assertEqual(predict.resolve_team("arsenal", self.teams, self.aliases), ("Arsenal", True))

    def test_partial_unique(self):
        # "Forest" est contenu dans un seul nom d'équipe
        self.assertEqual(predict.resolve_team("Forest", self.teams, self.aliases),
                         ("Nott'm Forest", True))

    def test_unknown_returns_original_not_found(self):
        name, ok = predict.resolve_team("Chelsea", self.teams, self.aliases)
        self.assertEqual(name, "Chelsea")
        self.assertFalse(ok)


class TestFreshnessBridge(unittest.TestCase):
    BASE = predict.DEFAULT_BLEND
    FLOOR = predict.STALE_FLOOR

    def test_fresh_odds_base_weight(self):
        w, factor, _ = predict.market_weight(self.BASE, age_days=1, all_margins_ok=True)
        self.assertAlmostEqual(factor, 1.0)
        self.assertAlmostEqual(w, self.BASE)

    def test_base_below_one_keeps_model_insurance_even_fresh(self):
        # même sur cotes fraîches, on garde une part de modèle (assurance anti-scrape)
        self.assertLess(self.BASE, 1.0)
        self.assertGreaterEqual(self.BASE, 0.90)

    def test_stale_odds_floor_not_zero(self):
        w, _, _ = predict.market_weight(self.BASE, age_days=5, all_margins_ok=True)
        self.assertAlmostEqual(w, self.FLOOR)
        self.assertGreater(w, 0.0)  # on ne jette jamais une vraie ligne
        # au-delà de J-5, le poids reste au plancher
        self.assertAlmostEqual(predict.market_weight(self.BASE, 7, True)[0], self.FLOOR)

    def test_floor_between_25_and_30_pct(self):
        self.assertGreaterEqual(self.FLOOR, 0.25)
        self.assertLessEqual(self.FLOOR, 0.30)

    def test_intermediate_linear_decay(self):
        # J-3 : à mi-chemin entre J-1 (base) et J-5 (plancher)
        w, _, _ = predict.market_weight(self.BASE, age_days=3, all_margins_ok=True)
        self.assertAlmostEqual(w, self.FLOOR + (self.BASE - self.FLOOR) * 0.5)

    def test_unverified_freshness_assumes_fresh(self):
        w, factor, _ = predict.market_weight(self.BASE, age_days=None, all_margins_ok=True)
        self.assertAlmostEqual(factor, 1.0)
        self.assertAlmostEqual(w, self.BASE)

    def test_bad_margin_halves_weight(self):
        w, _, _ = predict.market_weight(self.BASE, age_days=1, all_margins_ok=False)
        self.assertAlmostEqual(w, self.BASE * 0.5)

    def test_margin_ok_bounds(self):
        self.assertTrue(predict.margin_ok((1.9, 3.5, 4.2))[0])       # marge ~106 %
        self.assertFalse(predict.margin_ok((2.1, 3.6, 4.4))[0])      # marge < 100 % (arbitrable)
        self.assertFalse(predict.margin_ok((1.5, 3.0, 3.0))[0])      # marge > 112 %


class TestRiskParameters(unittest.TestCase):
    def test_risk_parameters_are_intentional(self):
        """Ce test échoue volontairement si ces constantes changent — c'est un
        garde-fou, pas un bug. Une modification de ces valeurs doit être
        délibérée et accompagnée d'une mise à jour de CLAUDE.md, jamais un
        changement silencieux."""
        sig = inspect.signature(predict.kelly_stake)
        self.assertEqual(sig.parameters["fraction"].default, 0.25,
                         "kelly_stake fraction : Kelly quart, valeur documentée")
        self.assertEqual(sig.parameters["cap"].default, 0.05,
                         "kelly_stake cap : plafond 5 % de bankroll, valeur documentée")
        self.assertEqual(predict.DEFAULT_BLEND, 0.92,
                         "poids marché de base : 92 % à J-1, barème validé par "
                         "backtest_blend.py")
        self.assertEqual(predict.STALE_FLOOR, 0.28,
                         "plancher de poids marché : 28 % à partir de J-5, barème "
                         "validé par backtest_blend.py")
        self.assertEqual(predict.FRESH_MAX_DAYS, 1,
                         "seuil de cotes fraîches : J-1, borne du barème documenté")
        self.assertEqual(predict.STALE_MIN_DAYS, 5,
                         "seuil de cotes périmées : J-5, borne du barème documenté")
        self.assertEqual(predict.SLATE_EXPOSURE_CAP, 0.15,
                         "plafond d'exposition simultanée : 15 % de bankroll sur un "
                         "slate entier, soit 3 × le plafond individuel — l'augmenter "
                         "expose la bankroll à plusieurs matchs joués en même temps")
        # cohérence des deux plafonds : un match seul (3 issues) ne doit jamais
        # être réduit, sinon le plafond de slate mordrait hors de son objet.
        cap_individuel = inspect.signature(predict.kelly_stake).parameters["cap"].default
        self.assertGreaterEqual(round(predict.SLATE_EXPOSURE_CAP, 9),
                                round(3 * cap_individuel, 9),
                                "le plafond de slate doit couvrir un match seul")
        self.assertEqual(predict.DEVIG_METHOD, "shin",
                         "démargeage de production : Shin, choix de rigueur documenté "
                         "dans devig_check.py (aucun gain de Brier mesuré)")
        self.assertEqual(predict.CLV_SHARP_SOURCES, ("pinnacle_close",),
                         "CLV calculé uniquement contre une clôture sharp : élargir "
                         "cette liste changerait la grandeur mesurée, pas sa précision")
        self.assertEqual(predict.CORRELATED_EXPOSURE_MULTIPLIER, 1.5,
                         "M9 : poids des paris corrélés (équipe partagée entre matchs "
                         "de la même semaine) dans le plafond d'exposition — heuristique "
                         "non calibrée, changer ce chiffre doit être délibéré")
        # le défaut du CLI doit rester branché sur la constante, pas figé à part
        default_blend = predict.build_parser().parse_args(
            ["match", "--league", "E0", "--home", "A", "--away", "B"]).blend
        self.assertEqual(default_blend, predict.DEFAULT_BLEND)


class TestMarketConsensus(unittest.TestCase):
    def test_consensus_demargined_sums_to_one_and_best_odds(self):
        market, best = predict.market_consensus([(1.85, 3.6, 4.4), (1.90, 3.5, 4.3)])
        self.assertAlmostEqual(sum(market.values()), 1.0, places=9)
        self.assertEqual(best["home"], 1.90)  # meilleure cote domicile
        self.assertEqual(best["away"], 4.4)


class TestGridHelpers(unittest.TestCase):
    def setUp(self):
        # petite grille jouet 3x3 normalisée
        self.grid = {(0, 0): 0.20, (1, 0): 0.25, (0, 1): 0.08,
                     (1, 1): 0.15, (2, 0): 0.20, (0, 2): 0.12}

    def test_over_and_btts(self):
        self.assertAlmostEqual(predict.over_prob(self.grid, 1.5), 0.15 + 0.20 + 0.12)
        self.assertAlmostEqual(predict.btts_prob(self.grid), 0.15)

    def test_best_score_per_outcome(self):
        self.assertEqual(predict.best_score_for_outcome(self.grid, "home"), "1-0")
        self.assertEqual(predict.best_score_for_outcome(self.grid, "draw"), "0-0")
        self.assertEqual(predict.best_score_for_outcome(self.grid, "away"), "0-2")


class TestNextSaturday(unittest.TestCase):
    def test_tuesday_gives_coming_saturday(self):
        self.assertEqual(predict.next_saturday(datetime.date(2026, 7, 21)),
                         datetime.date(2026, 7, 25))

    def test_saturday_returns_itself(self):
        sat = datetime.date(2026, 7, 25)
        self.assertEqual(predict.next_saturday(sat), sat)


class TestJournal(unittest.TestCase):
    def _res(self, home, away, date, market=None):
        return {
            "league": "E0", "home": home, "away": away,
            "date": datetime.date.fromisoformat(date),
            "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.65 if market else 0.0,
            "odds_age_days": 1 if market else None,
            "final": {"home": 0.55, "draw": 0.25, "away": 0.20},
            "market": market,
            "grid": {(1, 0): 0.3, (1, 1): 0.25, (0, 1): 0.2, (2, 1): 0.25},
        }

    def test_log_is_idempotent_on_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res("A", "B", "2026-08-15"))
            predict.log_prediction(path, self._res("A", "B", "2026-08-15"))
            entries = json.loads(path.read_text())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["predicted_score"], "1-0")  # meilleur score si victoire A

    def test_result_settles_latest_unsettled(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res("A", "B", "2026-08-15"))
            predict.record_result(path, "A-B", "2-1")
            e = json.loads(path.read_text())[0]
            self.assertEqual(e["actual_score"], "2-1")

    def test_report_computes_brier_and_market_gap(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            mkt = {"home": 0.50, "draw": 0.27, "away": 0.23}
            predict.log_prediction(path, self._res("A", "B", "2026-08-15", market=mkt))
            predict.record_result(path, "A-B", "2-1")  # issue home -> pronostic correct
            text, overall = predict.build_calibration_report(path)
            self.assertEqual(overall["n"], 1)
            self.assertEqual(overall["issue_rate"], 1.0)
            self.assertIsNotNone(overall["brier_market"])
            self.assertIn("2026-08", text)


class TestSyncResults(unittest.TestCase):
    """Fermeture automatique de la boucle : journal <- football.db."""

    def _db(self):
        conn = db.connect(":memory:")
        # match connu de la source, avec mi-temps
        db.upsert_match(conn, {"date": "2026-08-22", "league": "E0", "season": "2627",
                               "home": "Arsenal", "away": "Chelsea",
                               "fthg": 2, "ftag": 1, "hthg": 1, "htag": 0})
        # match connu sans mi-temps renseignée
        db.upsert_match(conn, {"date": "2026-08-23", "league": "E0", "season": "2627",
                               "home": "Everton", "away": "Crystal Palace",
                               "fthg": 0, "ftag": 0})
        # affiche programmée mais pas encore jouée côté source (score NULL)
        db.upsert_match(conn, {"date": "2026-08-24", "league": "E0", "season": "2627",
                               "home": "Leeds", "away": "Fulham"})
        db.upsert_alias(conn, "Crystal Palace FC", "Crystal Palace")
        conn.commit()
        return conn

    def _journal(self, d):
        path = Path(d) / "j.json"
        entries = [
            {"match": "Arsenal-Chelsea", "date": "2026-08-22", "competition": "E0",
             "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
             "predicted_score": "2-1", "bets": [], "actual_score": None, "actual_ht": None,
             "meta": {"model": "M5"}},
            {"match": "Everton-Crystal Palace", "date": "2026-08-23", "competition": "E0",
             "probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
             "predicted_score": "1-1", "bets": [], "actual_score": None, "actual_ht": None,
             "meta": {"model": "M5"}},
            # match passé absent de la source (retard football-data)
            {"match": "Hull-Man United", "date": "2026-08-21", "competition": "E0",
             "probs": {"home": 0.2, "draw": 0.2, "away": 0.6},
             "predicted_score": "1-2", "bets": [], "actual_score": None, "actual_ht": None,
             "meta": {"model": "M5"}},
            # match à venir : ni synchronisé ni en attente
            {"match": "Leeds-Fulham", "date": "2026-09-05", "competition": "E0",
             "probs": {"home": 0.35, "draw": 0.3, "away": 0.35},
             "predicted_score": "1-1", "bets": [], "actual_score": None, "actual_ht": None,
             "meta": {"model": "M5"}},
        ]
        path.write_text(json.dumps(entries))
        return path

    def test_sync_fills_known_and_flags_missing(self):
        conn = self._db()
        with tempfile.TemporaryDirectory() as d:
            path = self._journal(d)
            synced, pending = predict.sync_results(conn, path,
                                                   as_of=datetime.date(2026, 8, 30))
            self.assertEqual([s["match"] for s in synced],
                             ["Arsenal-Chelsea", "Everton-Crystal Palace"])
            self.assertEqual([p["match"] for p in pending], ["Hull-Man United"])
            entries = {e["match"]: e for e in json.loads(path.read_text())}
            self.assertEqual(entries["Arsenal-Chelsea"]["actual_score"], "2-1")
            self.assertEqual(entries["Arsenal-Chelsea"]["actual_ht"], "1-0")
            self.assertEqual(entries["Everton-Crystal Palace"]["actual_score"], "0-0")
            self.assertIsNone(entries["Everton-Crystal Palace"]["actual_ht"])
            # jamais de score inventé pour l'absent, ni pour le match à venir
            self.assertIsNone(entries["Hull-Man United"]["actual_score"])
            self.assertIsNone(entries["Leeds-Fulham"]["actual_score"])
        conn.close()

    def test_future_match_is_not_pending(self):
        conn = self._db()
        with tempfile.TemporaryDirectory() as d:
            path = self._journal(d)
            _, pending = predict.sync_results(conn, path, as_of=datetime.date(2026, 8, 30))
            self.assertNotIn("Leeds-Fulham", [p["match"] for p in pending])
        conn.close()

    def test_scheduled_but_unplayed_stays_pending(self):
        """Une ligne en base sans score (fthg NULL) n'est pas un résultat."""
        conn = self._db()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps([
                {"match": "Leeds-Fulham", "date": "2026-08-24", "competition": "E0",
                 "probs": {"home": 0.35, "draw": 0.3, "away": 0.35},
                 "predicted_score": "1-1", "bets": [], "actual_score": None,
                 "actual_ht": None, "meta": {"model": "M5"}}]))
            synced, pending = predict.sync_results(conn, path,
                                                   as_of=datetime.date(2026, 8, 30))
            self.assertEqual(synced, [])
            self.assertEqual(len(pending), 1)
            self.assertIsNone(json.loads(path.read_text())[0]["actual_score"])
        conn.close()

    def test_sync_is_idempotent_and_reports_shift(self):
        conn = self._db()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            # date prévue samedi, match joué le dimanche : tolérance de calendrier
            path.write_text(json.dumps([
                {"match": "Arsenal-Chelsea", "date": "2026-08-21", "competition": "E0",
                 "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
                 "predicted_score": "2-1", "bets": [], "actual_score": None,
                 "actual_ht": None, "meta": {"model": "M5"}}]))
            synced, _ = predict.sync_results(conn, path, as_of=datetime.date(2026, 8, 30))
            self.assertEqual(synced[0]["shift"], 1)
            # deuxième passage : plus rien à faire
            synced2, pending2 = predict.sync_results(conn, path,
                                                     as_of=datetime.date(2026, 8, 30))
            self.assertEqual((synced2, pending2), ([], []))
        conn.close()

    def test_teams_resolved_via_meta_and_alias(self):
        conn = self._db()
        teams = predict.league_teams(conn, "E0")
        aliases = db.load_aliases(conn)
        self.assertEqual(predict.split_match_key("Everton-Crystal Palace", teams, aliases),
                         ("Everton", "Crystal Palace"))
        entry = {"match": "peu importe", "meta": {"home": "Arsenal", "away": "Chelsea"}}
        self.assertEqual(predict.entry_teams(entry, teams, aliases), ("Arsenal", "Chelsea"))
        conn.close()

    def test_unresolvable_match_key_stays_pending(self):
        conn = self._db()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps([
                {"match": "Zzz Unknown-Yyy Unknown", "date": "2026-08-22",
                 "competition": "E0", "probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
                 "predicted_score": "1-1", "bets": [], "actual_score": None,
                 "actual_ht": None, "meta": {"model": "M5"}}]))
            synced, pending = predict.sync_results(conn, path,
                                                   as_of=datetime.date(2026, 8, 30))
            self.assertEqual(synced, [])
            self.assertIn("non résolues", pending[0]["reason"])
        conn.close()


class TestFreshnessSection(unittest.TestCase):
    """Le rapport doit distinguer cotes fraîches et cotes périmées."""

    def _entry(self, i, age, actual="2-1", probs=None, market=None):
        return {
            "match": f"A{i}-B{i}", "date": "2026-08-15", "competition": "E0",
            "probs": probs or {"home": 0.55, "draw": 0.25, "away": 0.20},
            "market_probs": market or {"home": 0.50, "draw": 0.27, "away": 0.23},
            "predicted_score": "2-1", "bets": [],
            "actual_score": actual, "actual_ht": None,
            "meta": {"model": "M5", "odds_age_days": age},
        }

    def _journal(self, d, entries):
        path = Path(d) / "j.json"
        path.write_text(json.dumps(entries))
        return path

    def test_three_buckets_counted(self):
        entries = ([self._entry(i, 0) for i in range(4)]        # fraîches (0 et 1 j)
                   + [self._entry(10 + i, 1) for i in range(3)]
                   + [self._entry(20 + i, 3) for i in range(5)]  # intermédiaires
                   + [self._entry(30 + i, 6) for i in range(2)])  # périmées
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertIn("## Par fraîcheur des cotes", text)
        labels = predict.bucket_labels()
        rows = {line.split("|")[1].strip(): line.split("|")[2].strip()
                for line in text.splitlines() if line.startswith("| ")}
        self.assertEqual(rows[labels["fraiches"]], "7")
        self.assertEqual(rows[labels["intermediaires"]], "5")
        self.assertEqual(rows[labels["perimees"]], "2")

    def test_bucket_boundaries_follow_market_weight_thresholds(self):
        self.assertEqual(predict.freshness_bucket(self._entry(0, predict.FRESH_MAX_DAYS)),
                         "fraiches")
        self.assertEqual(predict.freshness_bucket(self._entry(0, predict.FRESH_MAX_DAYS + 1)),
                         "intermediaires")
        self.assertEqual(predict.freshness_bucket(self._entry(0, predict.STALE_MIN_DAYS - 1)),
                         "intermediaires")
        self.assertEqual(predict.freshness_bucket(self._entry(0, predict.STALE_MIN_DAYS)),
                         "perimees")
        self.assertEqual(predict.freshness_bucket(self._entry(0, None)), "inconnue")

    def test_small_bucket_shows_count_but_no_delta(self):
        entries = [self._entry(i, 7) for i in range(3)]
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        row = next(l for l in text.splitlines()
                   if l.startswith("| " + predict.bucket_labels()["perimees"]))
        self.assertEqual(row.split("|")[2].strip(), "3")
        self.assertEqual(row.split("|")[5].strip(), "—")       # aucun delta
        self.assertEqual(row.split("|")[6].strip(), "—")       # ni IC
        self.assertIn("indicative", row.split("|")[7])

    # Les deux tests d'alerte encadrent le seuil sur l'échelle RELATIVE :
    # marché = {0.55, 0.25, 0.20}, issue domicile -> Brier marché = 0.305.
    MKT = {"home": 0.55, "draw": 0.25, "away": 0.20}

    def test_alert_when_stale_bucket_underperforms(self):
        # fraîches : FINAL = marché (Δ 0) ; périmées : Δ ~ +4.7 % relatif, au-delà
        # du seuil de 3 points
        stale = {"home": 0.54, "draw": 0.26, "away": 0.20}
        entries = ([self._entry(i, 1, probs=dict(self.MKT), market=dict(self.MKT))
                    for i in range(20)]
                   + [self._entry(50 + i, 6, probs=stale, market=dict(self.MKT))
                      for i in range(20)])
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertIn("Les cotes périmées performent moins bien que prévu par le "
                      "backtest", text)

    def test_no_alert_when_stale_bucket_holds(self):
        # même construction, mais Δ périmées ~ +2.3 % relatif : sous le seuil
        stale = {"home": 0.545, "draw": 0.255, "away": 0.20}
        entries = ([self._entry(i, 1, probs=dict(self.MKT), market=dict(self.MKT))
                    for i in range(20)]
                   + [self._entry(50 + i, 6, probs=stale, market=dict(self.MKT))
                      for i in range(20)])
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertNotIn("mérite d'être revu", text)
        self.assertIn("le barème tient", text)

    def test_delta_is_relative_like_report35(self):
        """Le Δ affiché est l'écart RELATIF de report35.py, pas un écart absolu :
        c'est la seule échelle comparable au +1,78 % du backtest M3.5."""
        probs = {"home": 0.50, "draw": 0.27, "away": 0.23}
        entries = [self._entry(i, 1, probs=probs, market=dict(self.MKT))
                   for i in range(20)]
        with tempfile.TemporaryDirectory() as d:
            text, overall = predict.build_calibration_report(self._journal(d, entries))
        b, bmkt = overall["brier"], overall["brier_market"]
        expected = (b - bmkt) / bmkt * 100          # formule de report35.py
        self.assertAlmostEqual(predict.relative_delta(b, bmkt), expected, places=9)
        # ... et ce nombre est bien celui imprimé dans les deux tables
        printed = f"{expected:+.2f} %"
        month_row = next(l for l in text.splitlines() if l.startswith("| 2026-08 |"))
        bucket_row = next(l for l in text.splitlines()
                          if l.startswith("| " + predict.bucket_labels()["fraiches"]))
        self.assertEqual(month_row.split("|")[5].strip(), printed)
        self.assertEqual(bucket_row.split("|")[5].strip(), printed)
        # l'ancienne échelle absolue donnait un autre chiffre : pas un arrondi
        self.assertNotAlmostEqual(expected, (b - bmkt) * 100, places=2)

    def test_unknown_freshness_is_not_counted_as_fresh(self):
        entries = [self._entry(i, None) for i in range(3)] + [self._entry(9, 1)]
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        labels = predict.bucket_labels()
        rows = {line.split("|")[1].strip(): line.split("|")[2].strip()
                for line in text.splitlines() if line.startswith("| ")}
        self.assertEqual(rows[labels["inconnue"]], "3")
        self.assertEqual(rows[labels["fraiches"]], "1")


class TestBetsAndRoi(unittest.TestCase):
    """Le journal doit garder la trace des mises Kelly pour mesurer un P&L."""

    def _res(self, home="A", away="B", date="2026-08-15", final=None, best_odds=None):
        return {
            "league": "E0", "home": home, "away": away,
            "date": datetime.date.fromisoformat(date),
            "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
            "final": final or {"home": 0.55, "draw": 0.25, "away": 0.20},
            "market": {"home": 0.50, "draw": 0.27, "away": 0.23},
            "best_odds": best_odds or {"home": 2.20, "draw": 3.60, "away": 4.40},
            "grid": {(1, 0): 0.3, (1, 1): 0.25, (0, 1): 0.2, (2, 1): 0.25},
        }

    def test_bets_persisted_with_odds_and_stake(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res())
            bets = json.loads(path.read_text())[0]["bets"]
            self.assertEqual([b["issue"] for b in bets], ["home"])  # seule issue en value
            self.assertEqual(bets[0]["odds"], 2.20)
            self.assertAlmostEqual(bets[0]["stake_pct"],
                                   predict.kelly_stake(0.55, 2.20), places=6)

    def test_no_stake_leaves_bets_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res(), no_stake=True)
            self.assertEqual(json.loads(path.read_text())[0]["bets"], [])

    def test_no_odds_no_bets(self):
        res = self._res()
        res["best_odds"] = None
        self.assertEqual(predict.prediction_bets(res), [])

    def test_bet_settled_as_win_and_loss(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res())            # pari sur 'home'
            predict.log_prediction(path, self._res(home="C", away="D"))
            predict.record_result(path, "A-B", "2-1")            # issue home : gagné
            predict.record_result(path, "C-D", "0-1")            # issue away : perdu
            entries = {e["match"]: e for e in json.loads(path.read_text())}
            won = entries["A-B"]["bets"][0]
            lost = entries["C-D"]["bets"][0]
            self.assertAlmostEqual(won["realized_pct"], won["stake_pct"] * (2.20 - 1.0),
                                   places=6)
            self.assertAlmostEqual(lost["realized_pct"], -lost["stake_pct"], places=6)

    def test_settled_bet_never_recomputed(self):
        entry = {"bets": [{"issue": "home", "odds": 2.2, "stake_pct": 0.01,
                           "realized_pct": 0.012}]}
        predict.settle_entry(entry, "0-3")   # issue away : recalculer donnerait -0.01
        self.assertEqual(entry["bets"][0]["realized_pct"], 0.012)

    def test_roi_section_sums_pnl_and_warns_on_small_sample(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res())
            predict.record_result(path, "A-B", "2-1")
            text, _ = predict.build_calibration_report(path)
            n, staked, pnl = predict.roi_summary(json.loads(path.read_text()))
        self.assertEqual(n, 1)
        self.assertGreater(pnl, 0)
        self.assertAlmostEqual(staked, predict.kelly_stake(0.55, 2.20), places=6)
        self.assertIn("## ROI réel (mise Kelly théorique)", text)
        self.assertIn("théorique", text)
        self.assertIn("échantillon insuffisant", text)

    def test_roi_section_without_any_bet(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, self._res(), no_stake=True)
            predict.record_result(path, "A-B", "2-1")
            text, _ = predict.build_calibration_report(path)
        self.assertIn("Aucun pari réglé", text)

    def test_large_sample_drops_the_warning(self):
        entries = []
        for i in range(predict.ROI_MIN_BETS):
            entries.append({
                "match": f"A{i}-B{i}", "date": "2026-08-15", "competition": "E0",
                "probs": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "predicted_score": "2-1",
                "bets": [{"issue": "home", "odds": 2.2, "stake_pct": 0.01,
                          "realized_pct": 0.012 if i % 2 else -0.01}],
                "actual_score": "2-1", "actual_ht": None, "meta": {"model": "M5"}})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps(entries))
            text, _ = predict.build_calibration_report(path)
        self.assertNotIn("échantillon insuffisant", text)
        self.assertIn(f"{predict.ROI_MIN_BETS} pari(s) réglé(s)", text)


class TestClv(unittest.TestCase):
    """CLV (closing line value) : l'écart entre la cote prise et la clôture."""

    def test_settle_entry_computes_clv_from_closing_odds(self):
        entry = {"bets": [{"issue": "home", "odds": 5.1, "stake_pct": 0.05}]}
        closing = {"home": 4.8, "draw": 3.4, "away": 1.9, "source": "pinnacle_close"}
        predict.settle_entry(entry, "1-2", closing_odds=closing)
        self.assertAlmostEqual(entry["bets"][0]["clv_pct"], 5.1 / 4.8 - 1.0, places=6)
        self.assertEqual(entry["closing_odds"], closing)

    def test_negative_clv_when_price_drifted_the_other_way(self):
        entry = {"bets": [{"issue": "home", "odds": 1.8, "stake_pct": 0.02}]}
        closing = {"home": 2.1, "draw": 3.2, "away": 3.6, "source": "pinnacle_close"}
        predict.settle_entry(entry, "1-0", closing_odds=closing)
        self.assertLess(entry["bets"][0]["clv_pct"], 0.0)

    def test_no_closing_odds_leaves_clv_absent(self):
        entry = {"bets": [{"issue": "home", "odds": 2.2, "stake_pct": 0.01}]}
        predict.settle_entry(entry, "1-0")
        self.assertNotIn("clv_pct", entry["bets"][0])
        self.assertNotIn("closing_odds", entry)

    def test_clv_never_recomputed_once_set(self):
        entry = {"bets": [{"issue": "home", "odds": 2.2, "stake_pct": 0.01,
                           "clv_pct": 0.05}]}
        predict.settle_entry(entry, "1-0",
                             closing_odds={"home": 9.9, "source": "pinnacle_close"})
        self.assertEqual(entry["bets"][0]["clv_pct"], 0.05)

    def test_missing_issue_in_closing_odds_leaves_clv_absent(self):
        entry = {"bets": [{"issue": "draw", "odds": 3.4, "stake_pct": 0.01}]}
        predict.settle_entry(entry, "1-0", closing_odds={"home": 2.0, "away": 3.5,
                                                         "source": "pinnacle_close"})
        self.assertNotIn("clv_pct", entry["bets"][0])

    def test_sync_results_fills_clv_from_matches_table(self):
        conn = db.connect(":memory:")
        db.upsert_match(conn, {"date": "2026-09-14", "league": "E0", "season": "2627",
                               "home": "Leeds", "away": "Newcastle",
                               "fthg": 1, "ftag": 2,
                               "odds_h": 4.8, "odds_d": 3.4, "odds_a": 1.9,
                               "odds_source": "pinnacle_close"})
        conn.commit()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps([
                {"match": "Leeds-Newcastle", "date": "2026-09-14", "competition": "E0",
                 "probs": {"home": 0.36, "draw": 0.25, "away": 0.39},
                 "predicted_score": "1-2",
                 "bets": [{"issue": "home", "odds": 5.1, "stake_pct": 0.05}],
                 "actual_score": None, "actual_ht": None,
                 "meta": {"model": "M5", "home": "Leeds", "away": "Newcastle"}}]))
            synced, _ = predict.sync_results(conn, path, as_of=datetime.date(2026, 9, 20))
            self.assertAlmostEqual(synced[0]["bets_clv"][0], 5.1 / 4.8 - 1.0, places=6)
            entry = json.loads(path.read_text())[0]
            self.assertAlmostEqual(entry["bets"][0]["clv_pct"], 5.1 / 4.8 - 1.0, places=6)
            self.assertEqual(entry["closing_odds"]["source"], "pinnacle_close")
        conn.close()

    def test_sync_results_without_closing_odds_in_db_skips_clv(self):
        conn = db.connect(":memory:")
        db.upsert_match(conn, {"date": "2026-09-14", "league": "E0", "season": "2627",
                               "home": "Leeds", "away": "Newcastle", "fthg": 1, "ftag": 2})
        conn.commit()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps([
                {"match": "Leeds-Newcastle", "date": "2026-09-14", "competition": "E0",
                 "probs": {"home": 0.36, "draw": 0.25, "away": 0.39},
                 "predicted_score": "1-2",
                 "bets": [{"issue": "home", "odds": 5.1, "stake_pct": 0.05}],
                 "actual_score": None, "actual_ht": None,
                 "meta": {"model": "M5", "home": "Leeds", "away": "Newcastle"}}]))
            predict.sync_results(conn, path, as_of=datetime.date(2026, 9, 20))
            entry = json.loads(path.read_text())[0]
            self.assertNotIn("clv_pct", entry["bets"][0])
        conn.close()

    def test_clv_section_reports_average_and_small_sample_warning(self):
        settled = [{"bets": [{"issue": "home", "odds": 2.0, "stake_pct": 0.01,
                              "clv_pct": 0.03}]},
                  {"bets": [{"issue": "away", "odds": 3.0, "stake_pct": 0.01,
                              "clv_pct": -0.01}]}]
        lines = predict.clv_section(settled)
        text = "\n".join(lines)
        self.assertIn("## CLV (closing line value)", text)
        self.assertIn("2 pari(s) avec clôture sharp", text)
        self.assertIn("indicative", text)

    def test_clv_section_without_any_clv_data(self):
        text = "\n".join(predict.clv_section([{"bets": []}]))
        self.assertIn("Aucun pari réglé avec clôture sharp connue", text)

    def test_report_includes_clv_section(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            entry = {
                "match": "A-B", "date": "2026-08-15", "competition": "E0",
                "probs": {"home": 0.55, "draw": 0.25, "away": 0.20},
                "predicted_score": "2-1",
                "bets": [{"issue": "home", "odds": 2.2, "stake_pct": 0.01,
                          "realized_pct": 0.022, "clv_pct": 0.02}],
                "actual_score": "2-1", "actual_ht": None, "meta": {"model": "M5"},
            }
            path.write_text(json.dumps([entry]))
            text, _ = predict.build_calibration_report(path)
        self.assertIn("## CLV (closing line value)", text)
        self.assertIn("CLV moyen +2.00%", text)


class TestRps(unittest.TestCase):
    def test_perfect_prediction_zero_rps(self):
        self.assertAlmostEqual(predict.rps((1.0, 0.0, 0.0), 0), 0.0)

    def test_uniform_reference(self):
        self.assertAlmostEqual(predict.rps((1 / 3, 1 / 3, 1 / 3), 1), 0.5 * ((1 / 3) ** 2 + (1 / 3) ** 2))


class TestPredictMatchIntegration(unittest.TestCase):
    """Intègre fit + résolution + blend sur une base SQLite en mémoire."""

    def _mini_db(self):
        conn = db.connect(":memory:")
        # deux équipes, plusieurs journées d'historique, A nettement plus forte
        base = datetime.date(2025, 8, 1)
        mid = 1
        for wk in range(20):
            day = (base + datetime.timedelta(days=wk * 7)).isoformat()
            hg, ag = (3, 0) if wk % 2 == 0 else (2, 1)
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2526",
                                   "home": "Alpha", "away": "Beta", "fthg": hg, "ftag": ag})
            day2 = (base + datetime.timedelta(days=wk * 7 + 1)).isoformat()
            db.upsert_match(conn, {"date": day2, "league": "E0", "season": "2526",
                                   "home": "Beta", "away": "Alpha", "fthg": 0, "ftag": 2})
        conn.commit()
        return conn

    def test_model_only_when_no_odds(self):
        conn = self._mini_db()
        cfg = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        res = predict.predict_match(conn, cfg, "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {})
        self.assertAlmostEqual(sum(res["final"].values()), 1.0, places=9)
        self.assertIsNone(res["market"])
        self.assertEqual(res["market_weight"], 0.0)
        self.assertGreater(res["final"]["home"], res["final"]["away"])  # Alpha favorite
        conn.close()

    def test_blend_moves_toward_market_when_fresh(self):
        conn = self._mini_db()
        cfg = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        # marché quasi équilibré : le blend doit tirer la proba domicile vers le bas
        res_model = predict.predict_match(conn, cfg, "E0", "Alpha", "Beta",
                                          datetime.date(2026, 8, 15), [], None, 0.65, {})
        res_blend = predict.predict_match(conn, cfg, "E0", "Alpha", "Beta",
                                          datetime.date(2026, 8, 15), ["2.5,3.2,2.8"], 1, 0.65, {})
        self.assertGreater(res_blend["market_weight"], 0.0)
        self.assertLess(res_blend["final"]["home"], res_model["final"]["home"])
        conn.close()

    def test_unknown_team_flagged(self):
        conn = self._mini_db()
        cfg = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        res = predict.predict_match(conn, cfg, "E0", "Alpha", "Zzz Unknown",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {})
        self.assertTrue(res["home_ok"])
        self.assertFalse(res["away_ok"])
        conn.close()

    def test_lineup_adjustment_shifts_lambda_when_applied(self):
        conn = self._mini_db()
        cfg = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        without = predict.predict_match(conn, cfg, "E0", "Alpha", "Beta",
                                        datetime.date(2026, 8, 15), [], None, 0.65, {})
        adjustment = {"home": {"attack": {
            "confirmed": [0.55, 0.40, 0.10, 0.05],  # buteur titulaire absent, remplaçant à 0.10
            "reference": [0.55, 0.40, 0.50, 0.05],
        }}}
        with_adj = predict.predict_match(conn, cfg, "E0", "Alpha", "Beta",
                                         datetime.date(2026, 8, 15), [], None, 0.65, {},
                                         lineup_adjustment=adjustment)
        self.assertTrue(with_adj["lineup_adjustment"]["applied"])
        expected_ratio = 1.10 / 1.50
        self.assertAlmostEqual(with_adj["lineup_adjustment"]["home"]["attack_ratio"],
                               expected_ratio, places=4)
        self.assertLess(with_adj["lam_h"], without["lam_h"])  # bon sens : λ domicile baisse
        self.assertAlmostEqual(with_adj["lam_h"], without["lam_h"] * expected_ratio, places=6)
        self.assertAlmostEqual(with_adj["lam_a"], without["lam_a"])  # away non touché
        conn.close()

    def test_no_lineup_adjustment_leaves_prediction_unchanged(self):
        conn = self._mini_db()
        cfg = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        res = predict.predict_match(conn, cfg, "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {},
                                    lineup_adjustment=None)
        self.assertEqual(res["lineup_adjustment"], {"applied": False})
        conn.close()


class TestLineupAdjustment(unittest.TestCase):
    """Ajustement post-fit des λ sur compositions confirmées (chantier 3.1)."""

    def test_ratio_is_one_when_confirmed_equals_reference(self):
        self.assertAlmostEqual(predict.compute_lineup_ratio([0.2, 0.1], [0.2, 0.1]), 1.0)

    def test_ratio_accepts_named_entries(self):
        confirmed = [{"name": "Titulaire A", "value": 0.3}, {"name": "Remplaçant", "value": 0.1}]
        reference = [{"name": "Titulaire A", "value": 0.3}, {"name": "Titulaire B", "value": 0.5}]
        self.assertAlmostEqual(predict.compute_lineup_ratio(confirmed, reference), 0.4 / 0.8)

    def test_ratio_defaults_to_one_without_reference(self):
        self.assertEqual(predict.compute_lineup_ratio([0.3], []), 1.0)
        self.assertEqual(predict.compute_lineup_ratio([], [0.0]), 1.0)

    def test_ratio_clamped_to_bounds(self):
        lo, hi = predict.LINEUP_RATIO_BOUNDS
        self.assertEqual(predict.compute_lineup_ratio([10.0], [1.0]), hi)
        self.assertEqual(predict.compute_lineup_ratio([0.01], [1.0]), lo)

    def test_ratio_is_derived_not_taken_verbatim(self):
        # Deux entrées différentes mais de même ratio (somme/somme) doivent produire
        # le même multiplicateur : ce n'est pas un chiffre saisi directement.
        self.assertAlmostEqual(predict.compute_lineup_ratio([1.0], [2.0]),
                               predict.compute_lineup_ratio([3.0], [6.0]))

    def test_no_adjustment_returns_unchanged_lambdas(self):
        lam_h, lam_a, meta = predict.apply_lineup_adjustment(1.5, 1.1, -0.05, None)
        self.assertEqual((lam_h, lam_a), (1.5, 1.1))
        self.assertEqual(meta, {"applied": False})

    def test_empty_dict_is_also_no_adjustment(self):
        lam_h, lam_a, meta = predict.apply_lineup_adjustment(1.5, 1.1, -0.05, {})
        self.assertEqual((lam_h, lam_a), (1.5, 1.1))
        self.assertFalse(meta["applied"])

    def test_key_scorer_absent_reduces_home_lambda_by_expected_magnitude(self):
        """Cas minimal requis : un titulaire clé absent déplace λ dans le bon sens
        et de l'ordre de grandeur attendu (pas juste "ça tourne sans erreur")."""
        adjustment = {"home": {"attack": {
            "confirmed": [0.55, 0.40, 0.10, 0.05],   # buteur (0.50) remplacé par (0.10)
            "reference": [0.55, 0.40, 0.50, 0.05],
        }}}
        lam_h, lam_a, meta = predict.apply_lineup_adjustment(2.0, 1.0, -0.05, adjustment)
        expected_ratio = 1.10 / 1.50  # ≈ 0.733
        self.assertTrue(meta["applied"])
        self.assertAlmostEqual(meta["home"]["attack_ratio"], expected_ratio, places=4)
        self.assertAlmostEqual(meta["home"]["defense_ratio"], 1.0)
        self.assertAlmostEqual(lam_h, 2.0 * expected_ratio, places=6)
        self.assertLess(lam_h, 2.0)              # bon sens : l'attaque privée de son buteur baisse
        self.assertAlmostEqual(lam_a, 1.0)       # l'équipe adverse n'est pas affectée
        # ordre de grandeur : perte réaliste (~27 %), ni un bruit négligeable ni un effondrement
        loss = 1.0 - expected_ratio
        self.assertGreater(loss, 0.15)
        self.assertLess(loss, 0.40)

    def test_weak_confirmed_defense_increases_opponent_lambda(self):
        adjustment = {"away": {"defense": {
            "confirmed": [1.2, 0.9, 0.8],   # défenseurs remplaçants (xG concédé/90 plus haut)
            "reference": [0.9, 0.7, 0.6],
        }}}
        lam_h, lam_a, meta = predict.apply_lineup_adjustment(1.3, 1.4, 0.03, adjustment)
        self.assertGreater(lam_h, 1.3)           # défense adverse affaiblie -> plus de buts en face
        self.assertAlmostEqual(lam_a, 1.4)       # l'attaque adverse elle-même n'est pas ajustée ici

    def test_grid_and_probs_from_lambdas_matches_direct_fit(self):
        """Le mini-modèle (predict.py) doit reproduire EXACTEMENT model.DixonColes
        (jamais modifié) quand on lui repasse les λ tels quels — la correction
        tau/rho n'est pas dupliquée, juste réutilisée."""
        rows = []
        base = datetime.date(2025, 1, 1)
        for wk in range(15):
            day = (base + datetime.timedelta(days=wk * 7)).isoformat()
            rows.append({"date": day, "home": "Alpha", "away": "Beta", "fthg": 2, "ftag": 1})
            day2 = (base + datetime.timedelta(days=wk * 7 + 1)).isoformat()
            rows.append({"date": day2, "home": "Beta", "away": "Alpha", "fthg": 1, "ftag": 1})
        fitted = model.fit(rows, xi=0.0)
        lam_h, lam_a = fitted.lambdas("Alpha", "Beta")
        grid_direct = fitted.score_grid("Alpha", "Beta")
        probs_direct = fitted.probs_1x2("Alpha", "Beta")
        grid_mini, probs_mini = predict.grid_and_probs_from_lambdas(lam_h, lam_a, fitted.rho)
        np.testing.assert_allclose(grid_direct, grid_mini, atol=1e-9)
        for a, b in zip(probs_direct, probs_mini):
            self.assertAlmostEqual(a, b, places=9)

    def test_load_lineup_adjustment_rejects_empty_document(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({}, f)
            path = f.name
        try:
            with self.assertRaises(SystemExit):
                predict.load_lineup_adjustment(path)
        finally:
            Path(path).unlink()

    def test_load_lineup_adjustment_reads_file(self):
        doc = {"home": {"attack": {"confirmed": [0.1], "reference": [0.2]}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(doc, f)
            path = f.name
        try:
            loaded = predict.load_lineup_adjustment(path)
            self.assertEqual(loaded, doc)
        finally:
            Path(path).unlink()


class TestNoOddsReason(unittest.TestCase):
    """Traçabilité des prédictions sans cote marché (meta.no_odds_reason)."""

    def _mini_db(self):
        conn = db.connect(":memory:")
        base = datetime.date(2025, 8, 1)
        for wk in range(20):
            day = (base + datetime.timedelta(days=wk * 7)).isoformat()
            hg, ag = (3, 0) if wk % 2 == 0 else (2, 1)
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2526",
                                   "home": "Alpha", "away": "Beta", "fthg": hg, "ftag": ag})
            day2 = (base + datetime.timedelta(days=wk * 7 + 1)).isoformat()
            db.upsert_match(conn, {"date": day2, "league": "E0", "season": "2526",
                                   "home": "Beta", "away": "Alpha", "fthg": 0, "ftag": 2})
        conn.commit()
        return conn

    def _cfg(self):
        return {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}

    def test_default_reason_when_undeclared(self):
        conn = self._mini_db()
        res = predict.predict_match(conn, self._cfg(), "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {})
        self.assertEqual(res["no_odds_reason"], predict.DEFAULT_NO_ODDS_REASON)
        conn.close()

    def test_declared_reason_is_kept(self):
        conn = self._mini_db()
        res = predict.predict_match(conn, self._cfg(), "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {},
                                    no_odds_reason="lookup_failed")
        self.assertEqual(res["no_odds_reason"], "lookup_failed")
        conn.close()

    def test_reason_none_when_odds_provided(self):
        conn = self._mini_db()
        res = predict.predict_match(conn, self._cfg(), "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), ["2.5,3.2,2.8"], 1, 0.65, {},
                                    no_odds_reason="lookup_failed")
        self.assertIsNone(res["no_odds_reason"])
        conn.close()

    def test_logged_entry_carries_reason_in_meta(self):
        conn = self._mini_db()
        res = predict.predict_match(conn, self._cfg(), "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {},
                                    no_odds_reason="margin_rejected")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, res)
            entry = json.loads(path.read_text())[0]
        self.assertEqual(entry["meta"]["no_odds_reason"], "margin_rejected")
        conn.close()

    def test_print_prediction_shows_reason_tag(self):
        conn = self._mini_db()
        res = predict.predict_match(conn, self._cfg(), "E0", "Alpha", "Beta",
                                    datetime.date(2026, 8, 15), [], None, 0.65, {},
                                    no_odds_reason="not_yet_published")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            predict.print_prediction(res, self._cfg())
        self.assertIn("[not_yet_published]", buf.getvalue())
        conn.close()

    def test_recap_empty_without_missing_odds(self):
        self.assertEqual(predict.no_odds_recap([], 3), "")

    def test_recap_groups_by_reason(self):
        without = [
            {"league": "E0", "home": "A", "away": "B", "date": "2026-08-15",
             "no_odds_reason": "lookup_failed"},
            {"league": "E0", "home": "C", "away": "D", "date": "2026-08-15",
             "no_odds_reason": "lookup_failed"},
            {"league": "SP1", "home": "E", "away": "F", "date": "2026-08-16",
             "no_odds_reason": None},
        ]
        recap = predict.no_odds_recap(without, 5)
        self.assertIn("3/5 match(s) sans cote marché", recap)
        self.assertIn("[lookup_failed]", recap)
        self.assertIn(f"[{predict.DEFAULT_NO_ODDS_REASON}]", recap)
        self.assertIn("--no-odds-reason", recap)  # rappel affiché car un 'not_provided' traîne

    def test_recap_no_hint_when_all_declared(self):
        without = [{"league": "E0", "home": "A", "away": "B", "date": "2026-08-15",
                   "no_odds_reason": "margin_rejected"}]
        recap = predict.no_odds_recap(without, 1)
        self.assertNotIn("--no-odds-reason", recap)


class TestSkillJsonParsing(unittest.TestCase):
    def _valid(self, **over):
        doc = {
            "schema": predict.SKILL_SCHEMA, "league": "E0",
            "home": "Alpha", "away": "Beta",
            "match_date": "2026-08-15", "odds_date": "2026-08-14",
            "odds_1x2": {"home": 1.85, "draw": 3.6, "away": 4.4},
            "ou": {"line": 2.5, "over": 1.95, "under": 1.9},
            "final_probs_1x2": {"home": 0.55, "draw": 0.26, "away": 0.19},
        }
        doc.update(over)
        return doc

    def _write(self, doc):
        d = tempfile.mkdtemp()
        p = Path(d) / "skill.json"
        p.write_text(json.dumps(doc))
        return str(p)

    def test_valid_maps_all_fields(self):
        fx = predict.skill_json_to_fixture(self._valid())
        self.assertEqual(fx["league"], "E0")
        self.assertEqual((fx["home"], fx["away"]), ("Alpha", "Beta"))
        self.assertEqual(fx["odds_spec"], "1.85,3.6,4.4")
        self.assertEqual(fx["match_date"], "2026-08-15")
        self.assertEqual(fx["odds_date"], "2026-08-14")

    def test_malformed_json_exits(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "bad.json"
        p.write_text("{not valid json")
        with self.assertRaises(SystemExit):
            predict.load_skill_json(p)

    def test_wrong_schema_exits(self):
        with self.assertRaises(SystemExit):
            predict.load_skill_json(self._write(self._valid(schema="autre/v1")))

    def test_missing_league_exits(self):
        doc = self._valid()
        del doc["league"]
        with self.assertRaises(SystemExit):
            predict.skill_json_to_fixture(doc)

    def test_invalid_league_exits_no_guess(self):
        with self.assertRaises(SystemExit):
            predict.skill_json_to_fixture(self._valid(league="BL1"))

    def test_ou_absent_is_fine(self):
        doc = self._valid()
        del doc["ou"]
        fx = predict.skill_json_to_fixture(doc)  # ne lève pas
        self.assertEqual(fx["odds_spec"], "1.85,3.6,4.4")

    def test_odds_absent_yields_none_spec(self):
        doc = self._valid()
        del doc["odds_1x2"]
        self.assertIsNone(predict.skill_json_to_fixture(doc)["odds_spec"])

    def test_odds_non_numeric_exits(self):
        with self.assertRaises(SystemExit):
            predict.skill_json_to_fixture(self._valid(odds_1x2={"home": "x", "draw": 3.6, "away": 4.4}))

    def test_final_probs_ignored(self):
        # final_probs_1x2 n'est jamais lu (predict.py recalcule son FINAL)
        fx = predict.skill_json_to_fixture(self._valid(final_probs_1x2={"home": 9, "draw": 9, "away": 9}))
        self.assertNotIn("final_probs", fx)

    def test_no_odds_reason_absent_is_fine(self):
        fx = predict.skill_json_to_fixture(self._valid())
        self.assertIsNone(fx["no_odds_reason"])

    def test_declarable_no_odds_reason_mapped(self):
        fx = predict.skill_json_to_fixture(self._valid(no_odds_reason="not_yet_published"))
        self.assertEqual(fx["no_odds_reason"], "not_yet_published")

    def test_non_declarable_no_odds_reason_exits(self):
        # 'slate_odds_ignored' et 'not_provided' sont déduits par le code, pas
        # déclarables par l'appelant — on ne devine pas une raison.
        with self.assertRaises(SystemExit):
            predict.skill_json_to_fixture(self._valid(no_odds_reason="not_provided"))


class TestSkillJsonEquivalence(unittest.TestCase):
    """Le chemin --from-skill-json doit produire EXACTEMENT le même stdout que
    les mêmes valeurs passées en arguments individuels."""

    def _mini_db(self):
        conn = db.connect(":memory:")
        base = datetime.date(2025, 8, 1)
        for wk in range(20):
            day = (base + datetime.timedelta(days=wk * 7)).isoformat()
            hg, ag = (3, 0) if wk % 2 == 0 else (2, 1)
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2526",
                                   "home": "Alpha", "away": "Beta", "fthg": hg, "ftag": ag})
            day2 = (base + datetime.timedelta(days=wk * 7 + 1)).isoformat()
            db.upsert_match(conn, {"date": day2, "league": "E0", "season": "2526",
                                   "home": "Beta", "away": "Alpha", "fthg": 0, "ftag": 2})
        conn.commit()
        return conn

    def _run(self, argv, conn):
        args = predict.build_parser().parse_args(argv)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            args.func(args, conn)
        return buf.getvalue()

    def test_json_path_equals_individual_args(self):
        doc = {
            "schema": predict.SKILL_SCHEMA, "league": "E0",
            "home": "Alpha", "away": "Beta",
            "match_date": "2026-08-15", "odds_date": "2026-08-14",
            "odds_1x2": {"home": 1.85, "draw": 3.6, "away": 4.4},
            "ou": {"line": 2.5, "over": 1.95, "under": 1.9},
            "final_probs_1x2": {"home": 0.55, "draw": 0.26, "away": 0.19},
        }
        d = tempfile.mkdtemp()
        path = str(Path(d) / "skill.json")
        Path(path).write_text(json.dumps(doc))

        frozen = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        orig = backtest35.frozen
        backtest35.frozen = lambda: frozen
        try:
            out_json = self._run(["match", "--from-skill-json", path, "--no-log"], self._mini_db())
            out_args = self._run(["match", "--league", "E0", "--home", "Alpha", "--away", "Beta",
                                  "--date", "2026-08-15", "--odds", "1.85,3.6,4.4",
                                  "--odds-date", "2026-08-14", "--no-log"], self._mini_db())
        finally:
            backtest35.frozen = orig
        self.assertEqual(out_json, out_args)
        self.assertIn("poids marché 92%", out_json)  # cotes fraîches J-1 -> base 92 %

    def test_conflicting_individual_arg_exits(self):
        doc = {"schema": predict.SKILL_SCHEMA, "league": "E0", "home": "Alpha", "away": "Beta"}
        d = tempfile.mkdtemp()
        path = str(Path(d) / "skill.json")
        Path(path).write_text(json.dumps(doc))
        args = predict.build_parser().parse_args(
            ["match", "--from-skill-json", path, "--home", "X", "--no-log"])
        with self.assertRaises(SystemExit):
            args.func(args, self._mini_db())


class TestNoOddsReasonCli(unittest.TestCase):
    """Bout en bout CLI : --no-odds-reason jusqu'au journal, et déduction
    automatique sur un slate dont --odds est ignoré."""

    def _mini_db(self):
        conn = db.connect(":memory:")
        base = datetime.date(2025, 8, 1)
        for wk in range(20):
            day = (base + datetime.timedelta(days=wk * 7)).isoformat()
            hg, ag = (3, 0) if wk % 2 == 0 else (2, 1)
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2526",
                                   "home": "Alpha", "away": "Beta", "fthg": hg, "ftag": ag})
            db.upsert_match(conn, {"date": day, "league": "E0", "season": "2526",
                                   "home": "Gamma", "away": "Delta", "fthg": hg, "ftag": ag})
            day2 = (base + datetime.timedelta(days=wk * 7 + 1)).isoformat()
            db.upsert_match(conn, {"date": day2, "league": "E0", "season": "2526",
                                   "home": "Beta", "away": "Alpha", "fthg": 0, "ftag": 2})
            db.upsert_match(conn, {"date": day2, "league": "E0", "season": "2526",
                                   "home": "Delta", "away": "Gamma", "fthg": 0, "ftag": 2})
        conn.commit()
        return conn

    def _run(self, argv, conn):
        args = predict.build_parser().parse_args(argv)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            args.func(args, conn)
        return buf.getvalue()

    def test_cli_flag_reaches_journal(self):
        frozen = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        orig = backtest35.frozen
        backtest35.frozen = lambda: frozen
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "j.json")
            try:
                out = self._run(["match", "--league", "E0", "--home", "Alpha", "--away", "Beta",
                                 "--date", "2026-08-15", "--no-odds-reason", "not_yet_published",
                                 "--log", log], self._mini_db())
            finally:
                backtest35.frozen = orig
            entry = json.loads(Path(log).read_text())[0]
        self.assertEqual(entry["meta"]["no_odds_reason"], "not_yet_published")
        self.assertIn("[not_yet_published]", out)
        self.assertIn("1/1 match(s) sans cote marché", out)

    def test_slate_with_odds_ignored_records_reason(self):
        frozen = {"w": 0.0, "xi": 0.0, "kappa": 2.0, "temperature": 1.0}
        orig = backtest35.frozen
        backtest35.frozen = lambda: frozen
        with tempfile.TemporaryDirectory() as d:
            log = str(Path(d) / "j.json")
            try:
                out = self._run(["match", "--fixture", "E0,Alpha,Beta",
                                 "--fixture", "E0,Gamma,Delta",
                                 "--odds", "1.85,3.6,4.4", "--date", "2026-08-15",
                                 "--log", log], self._mini_db())
            finally:
                backtest35.frozen = orig
            entries = json.loads(Path(log).read_text())
        self.assertEqual(len(entries), 2)
        for e in entries:
            self.assertEqual(e["meta"]["no_odds_reason"], "slate_odds_ignored")
        self.assertIn("[slate_odds_ignored]", out)
        self.assertIn("2/2 match(s) sans cote marché", out)

    def test_invalid_cli_reason_rejected_by_argparse(self):
        with self.assertRaises(SystemExit):
            predict.build_parser().parse_args(
                ["match", "--league", "E0", "--home", "A", "--away", "B",
                 "--no-odds-reason", "not_provided"])


if __name__ == "__main__":
    unittest.main()


class TestDevigIntegration(unittest.TestCase):
    """Le démargeage de production : Shin par défaut, méthode tracée au journal."""

    def test_default_is_shin(self):
        self.assertEqual(predict.DEVIG_METHOD, "shin")
        self.assertIn("shin", predict.DEVIG_METHODS)

    def test_consensus_uses_the_requested_method(self):
        book = (1.20, 7.00, 15.0)
        for method in predict.DEVIG_METHODS:
            market, _ = predict.market_consensus([book], method)
            expected = backtest.DEMARGIN_METHODS[method](*book)
            self.assertAlmostEqual(market["home"], expected[0], places=9)
            self.assertAlmostEqual(sum(market.values()), 1.0, places=9)

    def test_shin_and_power_disagree_on_a_lopsided_book(self):
        """Sanity : les deux méthodes ne sont pas le même code déguisé — sinon
        le choix journalisé ne voudrait rien dire."""
        book = [(1.20, 7.00, 15.0)]
        shin, _ = predict.market_consensus(book, "shin")
        power, _ = predict.market_consensus(book, "power")
        self.assertNotAlmostEqual(shin["away"], power["away"], places=4)

    def test_unknown_method_exits(self):
        with self.assertRaises(SystemExit):
            predict.market_consensus([(1.85, 3.6, 4.4)], "inexistante")

    def test_each_book_is_demargined_before_averaging(self):
        """Démarger la moyenne des cotes mélangerait des marges hétérogènes ;
        on démarge chaque book PUIS on moyenne."""
        books = [(1.85, 3.60, 4.40), (2.10, 3.30, 3.90)]
        market, best = predict.market_consensus(books, "shin")
        fairs = [backtest.demargin_shin(*b) for b in books]
        self.assertAlmostEqual(market["home"], (fairs[0][0] + fairs[1][0]) / 2, places=9)
        self.assertEqual(best["home"], 2.10)      # meilleure cote brute, pas la moyenne

    def test_cli_default_and_choices(self):
        args = predict.build_parser().parse_args(
            ["match", "--league", "E0", "--home", "A", "--away", "B"])
        self.assertEqual(args.devig, predict.DEVIG_METHOD)
        args = predict.build_parser().parse_args(
            ["match", "--league", "E0", "--home", "A", "--away", "B", "--devig", "power"])
        self.assertEqual(args.devig, "power")

    def test_journal_records_the_method(self):
        res = {
            "league": "E0", "home": "A", "away": "B",
            "date": datetime.date(2026, 8, 15),
            "lam_h": 1.5, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
            "final": {"home": 0.5, "draw": 0.28, "away": 0.22},
            "market": {"home": 0.49, "draw": 0.28, "away": 0.23},
            "best_odds": None, "devig": "shin",
            "grid": {(1, 0): 0.4, (1, 1): 0.35, (0, 1): 0.25},
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, res)
            self.assertEqual(json.loads(path.read_text())[0]["meta"]["devig"], "shin")


class TestClvRequiresSharpClosing(unittest.TestCase):
    """Le CLV n'a de sens que contre une vraie clôture sharp (Pinnacle)."""

    def _entry(self):
        return {"bets": [{"issue": "home", "odds": 5.1, "stake_pct": 0.05}]}

    def test_sharp_source_is_accepted(self):
        entry = self._entry()
        predict.settle_entry(entry, "1-2", closing_odds={
            "home": 4.8, "draw": 3.4, "away": 1.9, "source": "pinnacle_close"})
        self.assertAlmostEqual(entry["bets"][0]["clv_pct"], 5.1 / 4.8 - 1.0, places=6)
        self.assertNotIn("clv_skipped", entry["bets"][0])

    def test_market_average_closing_is_refused(self):
        """`avg_close` est une moyenne de books aux marges hétérogènes : la
        battre n'est pas battre le marché. On ne pose pas de CLV dessus."""
        entry = self._entry()
        predict.settle_entry(entry, "1-2", closing_odds={
            "home": 4.8, "draw": 3.4, "away": 1.9, "source": "avg_close"})
        self.assertNotIn("clv_pct", entry["bets"][0])
        self.assertEqual(entry["bets"][0]["clv_skipped"], "source_not_sharp")
        # la clôture reste tracée, c'est seulement le CLV qui n'est pas calculé
        self.assertEqual(entry["closing_odds"]["source"], "avg_close")

    def test_opening_line_is_refused(self):
        for source in ("pinnacle_open", "avg_open"):
            entry = self._entry()
            predict.settle_entry(entry, "1-2", closing_odds={
                "home": 4.8, "draw": 3.4, "away": 1.9, "source": source})
            self.assertNotIn("clv_pct", entry["bets"][0])
            self.assertEqual(entry["bets"][0]["clv_skipped"], "source_not_sharp")

    def test_untagged_closing_is_refused(self):
        """Pas de source = on ne sait pas ce qu'on compare : refus, pas pari."""
        entry = self._entry()
        predict.settle_entry(entry, "1-2", closing_odds={"home": 4.8, "draw": 3.4,
                                                         "away": 1.9})
        self.assertNotIn("clv_pct", entry["bets"][0])
        self.assertEqual(entry["bets"][0]["clv_skipped"], "source_not_sharp")

    def test_manual_result_marks_missing_closing(self):
        entry = self._entry()
        predict.settle_entry(entry, "1-2")
        self.assertEqual(entry["bets"][0]["clv_skipped"], "no_closing_odds")

    def test_sync_results_refuses_non_sharp_source_from_db(self):
        conn = db.connect(":memory:")
        db.upsert_match(conn, {"date": "2026-09-14", "league": "E0", "season": "2627",
                               "home": "Leeds", "away": "Newcastle", "fthg": 1, "ftag": 2,
                               "odds_h": 4.8, "odds_d": 3.4, "odds_a": 1.9,
                               "odds_source": "avg_close"})
        conn.commit()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            path.write_text(json.dumps([
                {"match": "Leeds-Newcastle", "date": "2026-09-14", "competition": "E0",
                 "probs": {"home": 0.36, "draw": 0.25, "away": 0.39},
                 "predicted_score": "1-2",
                 "bets": [{"issue": "home", "odds": 5.1, "stake_pct": 0.05}],
                 "actual_score": None, "actual_ht": None,
                 "meta": {"model": "M5", "home": "Leeds", "away": "Newcastle"}}]))
            synced, _ = predict.sync_results(conn, path, as_of=datetime.date(2026, 9, 20))
            self.assertEqual(synced[0]["bets_clv"], [])
            self.assertEqual(synced[0]["clv_skipped"], ["source_not_sharp"])
            self.assertEqual(synced[0]["closing_source"], "avg_close")
            entry = json.loads(path.read_text())[0]
            self.assertNotIn("clv_pct", entry["bets"][0])
            # le résultat, lui, est bien synchronisé : seul le CLV est écarté
            self.assertEqual(entry["actual_score"], "1-2")
        conn.close()

    def test_report_counts_the_refused_bets(self):
        settled = [
            {"bets": [{"issue": "home", "odds": 2.0, "stake_pct": 0.01,
                       "realized_pct": 0.01, "clv_pct": 0.03}]},
            {"bets": [{"issue": "away", "odds": 3.0, "stake_pct": 0.01,
                       "realized_pct": -0.01, "clv_skipped": "source_not_sharp"}]},
            {"bets": [{"issue": "draw", "odds": 3.4, "stake_pct": 0.01,
                       "realized_pct": -0.01, "clv_skipped": "no_closing_odds"}]},
        ]
        self.assertEqual(predict.clv_skipped_summary(settled),
                         {"source_not_sharp": 1, "no_closing_odds": 1})
        text = "\n".join(predict.clv_section(settled))
        self.assertIn("Clôture sharp exigée", text)
        self.assertIn("`source_not_sharp` × 1", text)
        self.assertIn("`no_closing_odds` × 1", text)
        self.assertIn("relancer `sync-results` n'y changera rien", text)

    def test_unsettled_bets_are_not_counted_as_refused(self):
        """Un pari dont le match n'est pas encore joué n'a pas « raté » son CLV."""
        self.assertEqual(
            predict.clv_skipped_summary([{"bets": [{"issue": "home", "odds": 2.0,
                                                    "stake_pct": 0.01}]}]), {})


class TestSlateExposureCap(unittest.TestCase):
    """Kelly plafonne chaque pari ; rien ne plafonnait la somme d'un slate."""

    def _res(self, i, final=None, odds=None):
        return {
            "league": "E0", "home": f"H{i}", "away": f"A{i}",
            "date": datetime.date(2026, 8, 15),
            "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
            "final": final or {"home": 0.60, "draw": 0.22, "away": 0.18},
            "market": {"home": 0.50, "draw": 0.27, "away": 0.23},
            "best_odds": odds or {"home": 2.50, "draw": 3.60, "away": 4.40},
            "grid": {(1, 0): 0.4, (1, 1): 0.35, (0, 1): 0.25},
        }

    def test_single_match_is_never_capped(self):
        """3 issues × 5 % = 15 % = le plafond : un match seul ne peut pas mordre."""
        res = self._res(0)
        summary = predict.apply_exposure_cap([res])
        self.assertEqual(summary["factor"], 1.0)
        self.assertEqual(predict.final_stakes(res), predict.match_stakes(res))

    def test_single_match_at_maximum_stakes_is_still_not_capped(self):
        """Cas limite : les trois issues au plafond individuel, donc exactement
        15 % — l'arithmétique flottante ne doit pas déclencher une réduction."""
        res = self._res(0, final={"home": 0.9, "draw": 0.9, "away": 0.9},
                        odds={"home": 5.0, "draw": 5.0, "away": 5.0})
        stakes = predict.match_stakes(res)
        self.assertEqual(len(stakes), 3)
        self.assertAlmostEqual(sum(stakes.values()), predict.SLATE_EXPOSURE_CAP)
        self.assertEqual(predict.apply_exposure_cap([res])["factor"], 1.0)

    def test_slate_above_cap_is_scaled_down(self):
        results = [self._res(i) for i in range(10)]
        summary = predict.apply_exposure_cap(results)
        self.assertGreater(summary["gross"], predict.SLATE_EXPOSURE_CAP)
        self.assertLess(summary["factor"], 1.0)
        total = sum(sum(predict.final_stakes(r).values()) for r in results)
        self.assertAlmostEqual(total, predict.SLATE_EXPOSURE_CAP, places=9)

    def test_scaling_is_proportional_not_truncation(self):
        """On réduit tout le monde du même facteur : tronquer les dernières
        affiches reviendrait à parier sur l'ordre des fixtures."""
        strong = self._res(0, final={"home": 0.75, "draw": 0.15, "away": 0.10})
        weak = self._res(1, final={"home": 0.45, "draw": 0.30, "away": 0.25})
        results = [strong, weak] + [self._res(i) for i in range(2, 12)]
        raw_ratio = (predict.match_stakes(strong)["home"]
                     / predict.match_stakes(weak)["home"])
        predict.apply_exposure_cap(results)
        capped_ratio = (predict.final_stakes(strong)["home"]
                        / predict.final_stakes(weak)["home"])
        self.assertAlmostEqual(raw_ratio, capped_ratio, places=9)
        self.assertGreater(predict.final_stakes(strong)["home"],
                           predict.final_stakes(weak)["home"])

    def test_slate_below_cap_is_untouched(self):
        results = [self._res(i, final={"home": 0.42, "draw": 0.30, "away": 0.28})
                   for i in range(2)]
        summary = predict.apply_exposure_cap(results)
        self.assertEqual(summary["factor"], 1.0)
        self.assertLessEqual(summary["gross"], predict.SLATE_EXPOSURE_CAP)

    def test_no_stake_yields_no_exposure(self):
        results = [self._res(i) for i in range(10)]
        summary = predict.apply_exposure_cap(results, no_stake=True)
        self.assertEqual(summary["gross"], 0.0)
        self.assertEqual(summary["n_bets"], 0)
        self.assertEqual(predict.exposure_recap(summary), "")

    def test_journal_keeps_capped_and_uncapped_stakes(self):
        results = [self._res(i) for i in range(10)]
        predict.apply_exposure_cap(results)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            for r in results:
                predict.log_prediction(path, r)
            entries = json.loads(path.read_text())
        bet = entries[0]["bets"][0]
        self.assertLess(bet["stake_pct"], bet["stake_pct_uncapped"])
        self.assertAlmostEqual(bet["stake_pct"],
                               round(bet["stake_pct_uncapped"] * bet["exposure_factor"], 6),
                               places=5)
        self.assertLess(entries[0]["meta"]["exposure_factor"], 1.0)

    def test_journal_omits_uncapped_fields_when_cap_does_not_bite(self):
        res = self._res(0)
        predict.apply_exposure_cap([res])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            predict.log_prediction(path, res)
            entry = json.loads(path.read_text())[0]
        self.assertNotIn("stake_pct_uncapped", entry["bets"][0])
        self.assertEqual(entry["meta"]["exposure_factor"], 1.0)

    def test_bets_fall_back_to_raw_stakes_without_a_cap_pass(self):
        """prediction_bets reste utilisable hors du flux `match` (autre script,
        test unitaire) : sans passe de plafonnement, les mises sont les brutes."""
        res = self._res(0)
        bets = predict.prediction_bets(res)
        self.assertAlmostEqual(bets[0]["stake_pct"],
                               round(predict.kelly_stake(0.60, 2.50), 6), places=6)

    def test_recap_mentions_the_reduction(self):
        results = [self._res(i) for i in range(10)]
        summary = predict.apply_exposure_cap(results)
        text = predict.exposure_recap(summary)
        self.assertIn("Exposition simultanée", text)
        self.assertIn("réduites", text)
        self.assertIn(f"{predict.SLATE_EXPOSURE_CAP:.0%}", text)

    def test_recap_says_nothing_alarming_below_cap(self):
        results = [self._res(0)]
        text = predict.exposure_recap(predict.apply_exposure_cap(results))
        self.assertIn("sous le plafond", text)
        self.assertNotIn("⚠", text)

    def test_cli_exposes_the_cap(self):
        args = predict.build_parser().parse_args(
            ["match", "--league", "E0", "--home", "A", "--away", "B"])
        self.assertEqual(args.exposure_cap, predict.SLATE_EXPOSURE_CAP)


class TestCorrelatedExposure(unittest.TestCase):
    """M9 : deux matchs de la même semaine partageant une équipe consomment le
    plafond d'exposition plus vite qu'une simple somme de mises indépendantes."""

    def _res(self, home, away):
        return {
            "league": "E0", "home": home, "away": away,
            "date": datetime.date(2026, 8, 15),
            "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
            "final": {"home": 0.60, "draw": 0.22, "away": 0.18},
            "market": {"home": 0.50, "draw": 0.27, "away": 0.23},
            "best_odds": {"home": 2.50, "draw": 3.60, "away": 4.40},
            "grid": {(1, 0): 0.4, (1, 1): 0.35, (0, 1): 0.25},
        }

    def test_no_shared_team_is_unaffected(self):
        results = [self._res("H0", "A0"), self._res("H1", "A1")]
        summary = predict.apply_exposure_cap(results)
        self.assertEqual(summary["n_correlated"], 0)
        self.assertFalse(results[0]["correlated"])
        self.assertFalse(results[1]["correlated"])

    def test_shared_team_is_flagged(self):
        results = [self._res("Arsenal", "A0"), self._res("Arsenal", "A1")]
        summary = predict.apply_exposure_cap(results)
        self.assertEqual(summary["n_correlated"], 2)
        self.assertTrue(all(r["correlated"] for r in results))

    def test_correlated_group_is_capped_harder_than_independent(self):
        """À mises brutes identiques, un groupe corrélé réduit le facteur
        davantage qu'un groupe indépendant — le budget se consomme plus vite."""
        independent = [self._res(f"H{i}", f"A{i}") for i in range(10)]
        correlated = [self._res("Arsenal", f"A{i}") for i in range(10)]
        f_indep = predict.apply_exposure_cap(independent)["factor"]
        f_corr = predict.apply_exposure_cap(correlated)["factor"]
        self.assertLess(f_indep, 1.0)   # précondition : le groupe dépasse déjà le plafond
        self.assertLess(f_corr, f_indep)

    def test_pending_matches_extend_correlation_to_the_journal(self):
        """Une équipe déjà engagée cette semaine (journal) et rejouée dans le run
        courant est corrélée même si le run ne contient qu'un seul match."""
        res = self._res("Arsenal", "A0")
        pending = {"Arsenal-B": {"home": "Arsenal", "away": "B", "stake": 0.05, "n_bets": 1}}
        summary = predict.apply_exposure_cap([res], prior=0.05, prior_bets=1,
                                             pending_matches=pending)
        self.assertEqual(summary["n_correlated"], 1)
        self.assertTrue(res["correlated"])

    def test_recap_mentions_correlation(self):
        results = [self._res("Arsenal", f"A{i}") for i in range(2)]
        summary = predict.apply_exposure_cap(results)
        text = predict.exposure_recap(summary)
        self.assertIn("partagent une équipe", text)

    def test_pending_bet_matches_reads_teams_from_meta(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "j.json"
            entries = [{"match": "Arsenal-Chelsea", "date": "2026-09-19",
                       "bets": [{"issue": "home", "odds": 2.0, "stake_pct": 0.03}],
                       "actual_score": None,
                       "meta": {"home": "Arsenal", "away": "Chelsea"}}]
            path.write_text(json.dumps(entries))
            matches = predict.pending_bet_matches(path, datetime.date(2026, 9, 19))
        self.assertEqual(matches["Arsenal-Chelsea"],
                         {"home": "Arsenal", "away": "Chelsea", "stake": 0.03, "n_bets": 1})


class TestCalibrationConfidenceIntervals(unittest.TestCase):
    """Le rapport de production publie ses Δ avec leur incertitude."""

    def _entry(self, i, probs, market, actual="2-1"):
        return {"match": f"H{i}-A{i}", "date": "2026-08-15", "competition": "E0",
                "probs": probs, "market_probs": market, "predicted_score": "2-1",
                "bets": [], "actual_score": actual, "actual_ht": None,
                "meta": {"model": "M5", "odds_age_days": 1}}

    def _journal(self, d, entries):
        path = Path(d) / "j.json"
        path.write_text(json.dumps(entries))
        return path

    def test_paired_series_skip_entries_without_market(self):
        mkt = {"home": 0.5, "draw": 0.3, "away": 0.2}
        entries = [self._entry(0, mkt, mkt), self._entry(1, mkt, None)]
        model_b, market_b = predict.paired_briers(entries)
        self.assertEqual(len(model_b), 1)
        self.assertEqual(len(market_b), 1)

    def test_monthly_table_has_a_confidence_interval(self):
        mkt = {"home": 0.5, "draw": 0.3, "away": 0.2}
        model = {"home": 0.52, "draw": 0.29, "away": 0.19}
        entries = [self._entry(i, model, mkt, "2-1" if i % 3 else "1-1")
                   for i in range(20)]
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertIn("IC 95 % du Δ", text)
        row = next(l for l in text.splitlines() if l.startswith("| 2026-08 |"))
        self.assertRegex(row.split("|")[6], r"\[[-+]\d+\.\d+ ; [-+]\d+\.\d+ %\]")

    def test_alert_is_gated_on_a_significant_gap(self):
        """Écart au-delà du seuil mais noyé dans le bruit : pas d'alerte, et le
        rapport dit explicitement pourquoi. C'est le garde-fou contre une révision
        du barème décidée sur quinze matchs de hasard."""
        mkt = {"home": 0.50, "draw": 0.30, "away": 0.20}
        stale = {"home": 0.44, "draw": 0.33, "away": 0.23}
        entries = []
        for i in range(20):     # fraîches : modèle = marché
            e = self._entry(i, dict(mkt), dict(mkt), "2-1" if i % 2 else "1-1")
            entries.append(e)
        for i in range(20):     # périmées : Δ moyen élevé mais très dispersé
            probs = dict(stale) if i % 2 else dict(mkt)
            e = self._entry(100 + i, probs, dict(mkt), "2-1" if i % 3 else "0-2")
            e["meta"]["odds_age_days"] = 6
            entries.append(e)
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertNotIn("mérite d'être revu", text)
        self.assertIn("l'intervalle contient 0", text)


class TestPendingExposure(unittest.TestCase):
    """L'exposition qui compte est celle du JOURNAL sur la semaine de matchs.

    Le flux réel génère un match à la fois (`--odds` ne s'applique qu'à un match
    unique) : un plafond limité au run courant ne plafonnerait jamais rien.
    """

    def _entry(self, match, date, stake, settled=None):
        return {"match": match, "date": date, "competition": "E0",
                "probs": {"home": 0.5, "draw": 0.3, "away": 0.2},
                "predicted_score": "1-0",
                "bets": [{"issue": "home", "odds": 2.5, "stake_pct": stake}],
                "actual_score": settled, "actual_ht": None, "meta": {"model": "M5"}}

    def _journal(self, d, entries):
        path = Path(d) / "j.json"
        path.write_text(json.dumps(entries))
        return path

    def test_sums_unsettled_bets_of_the_same_week(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._journal(d, [self._entry("A-B", "2026-09-19", 0.04),
                                     self._entry("C-D", "2026-09-20", 0.03)])
            total, n = predict.pending_exposure(path, datetime.date(2026, 9, 19))
        self.assertAlmostEqual(total, 0.07)
        self.assertEqual(n, 2)

    def test_other_weeks_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._journal(d, [self._entry("A-B", "2026-09-19", 0.04),
                                     self._entry("C-D", "2026-09-26", 0.05)])
            total, n = predict.pending_exposure(path, datetime.date(2026, 9, 19))
        self.assertAlmostEqual(total, 0.04)
        self.assertEqual(n, 1)

    def test_settled_bets_no_longer_expose_anything(self):
        """Un match joué a libéré son risque : il ne bloque plus le budget."""
        with tempfile.TemporaryDirectory() as d:
            path = self._journal(d, [self._entry("A-B", "2026-09-19", 0.04, "1-0")])
            total, n = predict.pending_exposure(path, datetime.date(2026, 9, 19))
        self.assertEqual((total, n), (0.0, 0))

    def test_entries_the_run_will_rewrite_are_excluded(self):
        """Ré-run du même match : sa mise ne doit pas être comptée deux fois."""
        with tempfile.TemporaryDirectory() as d:
            path = self._journal(d, [self._entry("A-B", "2026-09-19", 0.04)])
            total, _ = predict.pending_exposure(path, datetime.date(2026, 9, 19),
                                                [("A-B", "2026-09-19")])
        self.assertEqual(total, 0.0)

    def test_missing_journal_is_no_exposure(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                predict.pending_exposure(Path(d) / "absent.json",
                                         datetime.date(2026, 9, 19)),
                (0.0, 0))

    def test_prior_exposure_shrinks_the_budget(self):
        res = {"league": "E0", "home": "H", "away": "A",
               "date": datetime.date(2026, 9, 19),
               "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
               "final": {"home": 0.60, "draw": 0.22, "away": 0.18},
               "market": {"home": 0.50, "draw": 0.27, "away": 0.23},
               "best_odds": {"home": 2.50, "draw": 3.60, "away": 4.40},
               "grid": {(1, 0): 0.6, (1, 1): 0.4}}
        gross = sum(predict.match_stakes(res).values())
        summary = predict.apply_exposure_cap([res], prior=predict.SLATE_EXPOSURE_CAP - 0.001,
                                             prior_bets=4)
        self.assertLess(summary["factor"], 1.0)
        self.assertAlmostEqual(summary["total"], predict.SLATE_EXPOSURE_CAP, places=9)
        self.assertGreater(gross, summary["net"])

    def test_saturated_budget_drops_the_stakes_entirely(self):
        res = {"league": "E0", "home": "H", "away": "A",
               "date": datetime.date(2026, 9, 19),
               "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
               "final": {"home": 0.60, "draw": 0.22, "away": 0.18},
               "market": {"home": 0.50, "draw": 0.27, "away": 0.23},
               "best_odds": {"home": 2.50, "draw": 3.60, "away": 4.40},
               "grid": {(1, 0): 0.6, (1, 1): 0.4}}
        summary = predict.apply_exposure_cap([res], prior=predict.SLATE_EXPOSURE_CAP,
                                             prior_bets=5)
        self.assertEqual(summary["factor"], 0.0)
        self.assertEqual(predict.final_stakes(res), {})
        # aucun pari à 0 % ne doit polluer le journal (ni le ROI, ni le CLV)
        self.assertEqual(predict.prediction_bets(res), [])
        self.assertIn("DÉJÀ ATTEINT", predict.exposure_recap(summary))

    def test_recap_reports_the_prior_commitment(self):
        res = {"league": "E0", "home": "H", "away": "A",
               "date": datetime.date(2026, 9, 19),
               "lam_h": 1.6, "lam_a": 1.1, "market_weight": 0.92, "odds_age_days": 1,
               "final": {"home": 0.60, "draw": 0.22, "away": 0.18},
               "market": {"home": 0.50, "draw": 0.27, "away": 0.23},
               "best_odds": {"home": 2.50, "draw": 3.60, "away": 4.40},
               "grid": {(1, 0): 0.6, (1, 1): 0.4}}
        text = predict.exposure_recap(
            predict.apply_exposure_cap([res], prior=0.02, prior_bets=1))
        self.assertIn("Déjà engagé cette semaine", text)
        self.assertIn("budget restant", text)
