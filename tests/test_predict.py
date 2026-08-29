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

import backtest35
import db
import footballdata
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
        self.assertIn("indicative", row.split("|")[6])

    def test_alert_when_stale_bucket_underperforms(self):
        # fraîches : FINAL ~= marché ; périmées : FINAL nettement pire que le marché
        good = {"home": 0.55, "draw": 0.25, "away": 0.20}
        mkt = {"home": 0.55, "draw": 0.25, "away": 0.20}
        bad = {"home": 0.25, "draw": 0.25, "away": 0.50}
        entries = ([self._entry(i, 1, probs=good, market=mkt) for i in range(20)]
                   + [self._entry(50 + i, 6, probs=bad, market=mkt) for i in range(20)])
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertIn("Les cotes périmées performent moins bien que prévu par le "
                      "backtest", text)

    def test_no_alert_when_stale_bucket_holds(self):
        same = {"home": 0.55, "draw": 0.25, "away": 0.20}
        mkt = {"home": 0.54, "draw": 0.26, "away": 0.20}
        entries = ([self._entry(i, 1, probs=same, market=mkt) for i in range(20)]
                   + [self._entry(50 + i, 6, probs=same, market=mkt) for i in range(20)])
        with tempfile.TemporaryDirectory() as d:
            text, _ = predict.build_calibration_report(self._journal(d, entries))
        self.assertNotIn("mérite d'être revu", text)
        self.assertIn("le barème tient", text)

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


if __name__ == "__main__":
    unittest.main()
