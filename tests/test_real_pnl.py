import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import real_pnl

FIXTURE = Path(__file__).parent / "fixtures" / "real_bets_sample.json"


class TestLoadBets(unittest.TestCase):
    def test_missing_file_returns_empty_list_no_crash(self):
        self.assertEqual(real_pnl.load_bets("/nonexistent/path/real_bets.json"), [])

    def test_loads_fixture(self):
        bets = real_pnl.load_bets(FIXTURE)
        self.assertEqual(len(bets), 4)


class TestPnlPerBet(unittest.TestCase):
    def test_win_pnl_uses_taken_odds_not_journal_odds(self):
        bet = {"result": "win", "stake_eur": 20.0, "taken_odds_1xbet": 1.85, "journal_odds": 1.90}
        self.assertAlmostEqual(real_pnl.pnl_eur(bet), 20.0 * 0.85, places=6)

    def test_loss_pnl_is_negative_stake(self):
        bet = {"result": "loss", "stake_eur": 10.0, "taken_odds_1xbet": 3.5}
        self.assertEqual(real_pnl.pnl_eur(bet), -10.0)

    def test_void_pnl_is_zero(self):
        bet = {"result": "void", "stake_eur": 15.0, "taken_odds_1xbet": 4.0}
        self.assertEqual(real_pnl.pnl_eur(bet), 0.0)

    def test_pending_pnl_is_none(self):
        bet = {"result": "pending", "stake_eur": 5.0, "taken_odds_1xbet": 2.4}
        self.assertIsNone(real_pnl.pnl_eur(bet))


class TestSlippage(unittest.TestCase):
    def test_worse_1xbet_odds_than_journal_is_positive_slippage(self):
        # cote journal 1.90, cote prise 1.85 (moins bonne) -> slippage positif
        bet = {"journal_odds": 1.90, "taken_odds_1xbet": 1.85}
        self.assertGreater(real_pnl.slippage(bet), 0)

    def test_better_1xbet_odds_than_journal_is_negative_slippage(self):
        bet = {"journal_odds": 3.40, "taken_odds_1xbet": 3.50}
        self.assertLess(real_pnl.slippage(bet), 0)

    def test_missing_odds_yields_none(self):
        self.assertIsNone(real_pnl.slippage({"journal_odds": 1.9}))
        self.assertIsNone(real_pnl.slippage({"taken_odds_1xbet": 1.9}))
        self.assertIsNone(real_pnl.slippage({}))


class TestSummarizeOnFixture(unittest.TestCase):
    """Vérifie les 4 calculs demandés sur le fixture synthétique :
    1 win (stake 20, cote 1.85), 1 loss (stake 10), 1 void (stake 15),
    1 pending (stake 5) — tous avec journal_odds et taken_odds_1xbet renseignés."""

    def setUp(self):
        self.bets = real_pnl.load_bets(FIXTURE)
        self.s = real_pnl.summarize(self.bets)

    def test_status_counts(self):
        self.assertEqual(self.s["by_status"], {"pending": 1, "win": 1, "loss": 1, "void": 1})
        self.assertEqual(self.s["n_total"], 4)

    def test_pnl_and_roi_exclude_void_and_pending(self):
        # pnl = 20*(1.85-1) - 10 = 17.0 - 10.0 = 7.0 ; stake réglée = 20+10 = 30
        self.assertAlmostEqual(self.s["pnl_eur"], 7.0, places=6)
        self.assertAlmostEqual(self.s["roi_pct"], 7.0 / 30.0 * 100, places=6)
        self.assertEqual(self.s["n_win_loss"], 2)

    def test_slippage_counts_all_four_bets_including_void_and_pending(self):
        # Le slippage mesure l'écart d'exécution, pas le résultat du pari :
        # les 4 entrées ont journal_odds ET taken_odds_1xbet renseignés.
        self.assertEqual(self.s["n_slippage"], 4)
        expected = sum(real_pnl.slippage(b) for b in self.bets) / 4
        self.assertAlmostEqual(self.s["avg_slippage"], expected, places=6)


class TestSummarizeEmpty(unittest.TestCase):
    """n=0 ne doit jamais faire planter le résumé ou le rapport."""

    def test_summarize_empty_list(self):
        s = real_pnl.summarize([])
        self.assertEqual(s["n_total"], 0)
        self.assertEqual(s["n_win_loss"], 0)
        self.assertIsNone(s["roi_pct"])
        self.assertIsNone(s["avg_slippage"])

    def test_build_report_empty_list_no_crash(self):
        text, s = real_pnl.build_report([])
        self.assertIn("Aucun pari réel enregistré", text)
        self.assertEqual(s["n_total"], 0)


class TestBuildReportOnFixture(unittest.TestCase):
    def test_report_mentions_pnl_roi_and_slippage(self):
        bets = real_pnl.load_bets(FIXTURE)
        text, _ = real_pnl.build_report(bets)
        self.assertIn("P&L réel cumulé", text)
        self.assertIn("ROI réel", text)
        self.assertIn("Slippage moyen", text)
        self.assertIn("lecture indicative", text)  # n_win_loss=2 < MIN_BETS_FOR_ROI


class TestMainWritesReport(unittest.TestCase):
    def test_main_writes_report_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "real_pnl.md"
            real_pnl.main(["--bets", str(FIXTURE), "--out", str(out)])
            self.assertTrue(out.exists())
            self.assertIn("Suivi du P&L réel", out.read_text())

    def test_main_on_missing_bets_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "real_pnl.md"
            missing = Path(d) / "nope.json"
            real_pnl.main(["--bets", str(missing), "--out", str(out)])
            self.assertIn("Aucun pari réel enregistré", out.read_text())


if __name__ == "__main__":
    unittest.main()
