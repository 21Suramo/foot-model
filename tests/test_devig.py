"""Démargeage des cotes : propriétés du modèle de Shin et garde-fous.

Le démargeage n'est pas de la plomberie : sur cotes fraîches ses sorties pèsent
92 % du FINAL de production. Ces tests fixent les propriétés qu'on ne veut pas
voir dériver — normalisation, monotonie, comportement sur un livre dégénéré,
et le fait que Shin s'intercale bien entre proportionnel et power.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest
import devig_check


BOOKS = [(1.85, 3.60, 4.40), (1.20, 7.00, 15.0), (2.50, 3.30, 2.80),
         (1.01, 26.0, 60.0), (4.20, 3.50, 1.95)]


class TestShinProperties(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        for book in BOOKS:
            p = backtest.demargin_shin(*book)
            self.assertAlmostEqual(sum(p), 1.0, places=9, msg=str(book))
            self.assertTrue(all(0.0 < x < 1.0 for x in p), msg=str(book))

    def test_order_of_outcomes_is_preserved(self):
        """Démarger ne réordonne jamais les issues : la plus courte cote reste
        la plus probable, sinon on aurait changé l'avis du marché, pas sa marge."""
        for book in BOOKS:
            p = backtest.demargin_shin(*book)
            ranks_odds = sorted(range(3), key=lambda i: book[i])
            ranks_probs = sorted(range(3), key=lambda i: -p[i])
            self.assertEqual(ranks_odds, ranks_probs, msg=str(book))

    def test_shrinks_longshots_relative_to_proportional(self):
        """C'est la raison d'être de Shin : le book charge plus de marge sur les
        grosses cotes, donc retirer la marge au prorata laisse trop de proba aux
        outsiders. Shin doit rendre le favori plus probable et l'outsider moins
        probable que le démargeage proportionnel."""
        for book in BOOKS:
            prop = backtest.demargin_proportional(*book)
            shin = backtest.demargin_shin(*book)
            fav = min(range(3), key=lambda i: book[i])
            dog = max(range(3), key=lambda i: book[i])
            self.assertGreater(shin[fav], prop[fav], msg=str(book))
            self.assertLess(shin[dog], prop[dog], msg=str(book))

    def test_sits_between_proportional_and_power(self):
        """Shin corrige le biais favori-longshot moins agressivement que power
        (qui applique un exposant sans modèle sous-jacent). Vérifié sur le livre
        le plus déséquilibré, là où les trois méthodes s'écartent le plus."""
        book = (1.01, 26.0, 60.0)
        prop = backtest.demargin_proportional(*book)
        shin = backtest.demargin_shin(*book)
        power = backtest.demargin_power(*book)
        self.assertLess(prop[0], shin[0])
        self.assertLess(shin[0], power[0])

    def test_fair_book_is_left_untouched(self):
        """Marge nulle = rien à retirer : z = 0, Shin doit rendre exactement les
        probas implicites."""
        p = (0.5, 0.3, 0.2)
        book = tuple(1.0 / x for x in p)
        self.assertEqual(
            tuple(round(x, 9) for x in backtest.demargin_shin(*book)),
            tuple(round(x, 9) for x in p))

    def test_arbitrable_book_falls_back_instead_of_crashing(self):
        """Marge < 100 % (livre arbitrable, donc cotes suspectes) : l'équation de
        Shin n'a pas de racine. On retombe sur le proportionnel — margin_ok()
        signale déjà la cote côté production, ce n'est pas ici qu'on doit
        planter."""
        book = (2.10, 3.60, 4.40)
        self.assertLess(sum(1 / o for o in book), 1.0)
        self.assertEqual(backtest.demargin_shin(*book),
                         backtest.demargin_proportional(*book))

    def test_registry_exposes_the_three_methods(self):
        self.assertEqual(set(backtest.DEMARGIN_METHODS),
                         {"proportional", "power", "shin"})
        for fn in backtest.DEMARGIN_METHODS.values():
            self.assertAlmostEqual(sum(fn(*BOOKS[0])), 1.0, places=9)


class TestDevigCheck(unittest.TestCase):
    """Le script de comparaison : discipline de protocole et lecture du biais."""

    def _rows(self, specs):
        rows = []
        for book, outcome in specs:
            row = {"league": "E0", "season": "2021", "odds_h": book[0],
                   "odds_d": book[1], "odds_a": book[2],
                   "odds_source": "pinnacle_close", "outcome": outcome}
            row["probs"] = {m: backtest.DEMARGIN_METHODS[m](*book)
                            for m in devig_check.METHODS}
            rows.append(row)
        return rows

    def test_test_seasons_are_never_measured(self):
        """Choisir un démargeage est un réglage : il ne se fait pas sur le test."""
        measured = set(backtest.BURN_IN + backtest.VALIDATION)
        self.assertFalse(measured & set(backtest.TEST))

    def test_scores_are_paired_series(self):
        rows = self._rows([(BOOKS[0], 0), (BOOKS[1], 2), (BOOKS[2], 1)])
        b_power, ll_power = devig_check.scores(rows, "power")
        b_shin, _ = devig_check.scores(rows, "shin")
        self.assertEqual(len(b_power), len(rows))
        self.assertEqual(len(b_shin), len(b_power))
        self.assertEqual(len(ll_power), len(rows))

    def test_longshot_bias_positive_when_outsiders_never_win(self):
        """Des outsiders qui ne gagnent jamais : la proba prédite sur les
        tranches basses dépasse la fréquence observée, le biais doit être
        positif (c'est le sens de lecture affiché dans le rapport)."""
        rows = self._rows([(BOOKS[1], 0)] * 20)
        bias, n = devig_check.longshot_bias(rows, "shin")
        self.assertGreater(n, 0)
        self.assertGreater(bias, 0)

    def test_longshot_bias_none_without_low_probabilities(self):
        rows = self._rows([((2.9, 3.1, 3.0), 1)] * 5)
        self.assertEqual(devig_check.longshot_bias(rows, "shin"), (None, 0))

    def test_table_reports_a_confidence_interval_per_method(self):
        rows = self._rows([(BOOKS[i % len(BOOKS)], i % 3) for i in range(40)])
        text = "\n".join(devig_check.method_table(rows))
        self.assertIn("référence", text)          # la baseline n'a pas d'écart
        for label in devig_check.LABELS.values():
            self.assertIn(label, text)
        self.assertIn("IC 95 %", text)


if __name__ == "__main__":
    unittest.main()
