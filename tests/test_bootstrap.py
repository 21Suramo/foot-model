"""Bootstrap : reproductibilité, appariement, et cas dégénérés.

Ce que ces tests protègent avant tout, c'est la lecture des rapports : un IC
qui bouge d'une exécution à l'autre rend le diff git illisible, et un IC calculé
sans appariement serait systématiquement trop large — donc rassurant à tort sur
un écart qui n'existe pas.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import bootstrap


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_interval(self):
        vals = list(np.linspace(0.1, 0.9, 60))
        first = bootstrap.ci_mean(vals)
        second = bootstrap.ci_mean(vals)
        self.assertEqual(first, second)

    def test_different_seed_changes_interval(self):
        """La graine par défaut n'est pas un no-op déguisé : elle pilote bien
        le tirage (sinon « graine figée » ne voudrait rien dire)."""
        vals = list(np.linspace(0.1, 0.9, 60))
        self.assertNotEqual(bootstrap.ci_mean(vals, seed=1),
                            bootstrap.ci_mean(vals, seed=2))


class TestCiMean(unittest.TestCase):
    def test_interval_brackets_the_point(self):
        vals = [0.2, 0.5, 0.9, 0.4, 0.6, 0.3, 0.7, 0.55]
        point, lo, hi = bootstrap.ci_mean(vals)
        self.assertAlmostEqual(point, sum(vals) / len(vals))
        self.assertLessEqual(lo, point)
        self.assertLessEqual(point, hi)

    def test_single_value_has_no_interval(self):
        point, lo, hi = bootstrap.ci_mean([0.42])
        self.assertAlmostEqual(point, 0.42)
        self.assertIsNone(lo)
        self.assertIsNone(hi)

    def test_empty_returns_nothing(self):
        self.assertEqual(bootstrap.ci_mean([]), (None, None, None))

    def test_constant_sample_gives_degenerate_interval(self):
        point, lo, hi = bootstrap.ci_mean([0.3] * 20)
        self.assertAlmostEqual(lo, 0.3)
        self.assertAlmostEqual(hi, 0.3)


class TestCiRelativeDelta(unittest.TestCase):
    def test_point_matches_the_reports_formula(self):
        """Même définition que « Écart rel. marché » / « Δ vs marché »."""
        a = [0.6, 0.5, 0.7, 0.4]
        b = [0.5, 0.5, 0.6, 0.4]
        point, _, _ = bootstrap.ci_relative_delta(a, b)
        expected = (np.mean(a) - np.mean(b)) / np.mean(b) * 100
        self.assertAlmostEqual(point, expected)

    def test_identical_series_give_zero_and_zero_width(self):
        a = [0.6, 0.5, 0.7, 0.4, 0.55]
        point, lo, hi = bootstrap.ci_relative_delta(a, list(a))
        self.assertAlmostEqual(point, 0.0)
        self.assertAlmostEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0)

    def test_mismatched_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            bootstrap.ci_relative_delta([0.1, 0.2], [0.1])

    def test_pairing_narrows_the_interval_versus_shuffling(self):
        """Le cœur du module : sur deux séries corrélées, l'IC apparié est
        NETTEMENT plus serré que si on cassait l'appariement. Un IC non apparié
        contiendrait 0 et laisserait croire qu'il n'y a pas d'écart."""
        rng = np.random.default_rng(0)
        base = rng.uniform(0.1, 1.2, size=300)
        model = base + 0.02              # écart constant, faible, mais réel
        point, lo, hi = bootstrap.ci_relative_delta(model, base)
        self.assertGreater(point, 0)
        self.assertTrue(bootstrap.excludes_zero(lo, hi))
        # même écart moyen, appariement détruit : l'intervalle explose
        shuffled = rng.permutation(model)
        _, slo, shi = bootstrap.ci_relative_delta(shuffled, base)
        self.assertGreater(shi - slo, (hi - lo) * 5)


class TestCiGap(unittest.TestCase):
    def test_gap_point_is_difference_of_two_deltas(self):
        a, ra = [0.62] * 30, [0.60] * 30      # +3,33 %
        b, rb = [0.61] * 30, [0.60] * 30      # +1,67 %
        point, lo, hi = bootstrap.ci_gap_relative_delta(a, ra, b, rb)
        self.assertAlmostEqual(point, (0.62 - 0.60) / 0.60 * 100
                               - (0.61 - 0.60) / 0.60 * 100, places=6)
        self.assertAlmostEqual(lo, point, places=6)
        self.assertAlmostEqual(hi, point, places=6)

    def test_noisy_groups_give_an_interval_containing_zero(self):
        """Deux petits groupes bruités autour du même Δ : l'écart apparent ne
        doit pas être déclaré réel — c'est ce qui empêche une alerte sur 15
        matchs de hasard."""
        rng = np.random.default_rng(7)
        ra = rng.uniform(0.2, 1.2, size=15)
        rb = rng.uniform(0.2, 1.2, size=15)
        a = ra + rng.normal(0, 0.3, size=15)
        b = rb + rng.normal(0, 0.3, size=15)
        _, lo, hi = bootstrap.ci_gap_relative_delta(a, ra, b, rb)
        self.assertFalse(bootstrap.excludes_zero(lo, hi))

    def test_too_small_group_has_no_interval(self):
        self.assertEqual(bootstrap.ci_gap_relative_delta([0.5], [0.5], [0.4] * 5, [0.4] * 5),
                         (None, None, None))


class TestCiDiffMean(unittest.TestCase):
    def test_point_is_difference_of_means(self):
        a, b = [1.0] * 20, [0.4] * 20
        point, lo, hi = bootstrap.ci_diff_mean(a, b)
        self.assertAlmostEqual(point, 0.6, places=6)
        self.assertAlmostEqual(lo, point, places=6)
        self.assertAlmostEqual(hi, point, places=6)

    def test_noisy_groups_with_same_mean_include_zero(self):
        rng = np.random.default_rng(3)
        a = rng.normal(0.0, 1.0, size=40)
        b = rng.normal(0.0, 1.0, size=40)
        _, lo, hi = bootstrap.ci_diff_mean(a, b)
        self.assertFalse(bootstrap.excludes_zero(lo, hi))

    def test_clearly_different_groups_exclude_zero(self):
        rng = np.random.default_rng(3)
        a = rng.normal(2.0, 0.2, size=200)
        b = rng.normal(0.0, 0.2, size=200)
        _, lo, hi = bootstrap.ci_diff_mean(a, b)
        self.assertTrue(bootstrap.excludes_zero(lo, hi))

    def test_too_small_group_has_no_interval(self):
        self.assertEqual(bootstrap.ci_diff_mean([1.0], [0.4, 0.5, 0.6]), (0.5, None, None))

    def test_empty_group_returns_none(self):
        self.assertEqual(bootstrap.ci_diff_mean([], [1.0, 2.0]), (None, None, None))


class TestHelpers(unittest.TestCase):
    def test_fmt_ci_and_missing_interval(self):
        self.assertEqual(bootstrap.fmt_ci(1.234, 5.678), "[+1.23 ; +5.68 %]")
        self.assertEqual(bootstrap.fmt_ci(-2.0, 3.0, unit="pts"), "[-2.00 ; +3.00 pts]")
        self.assertIn("indisponible", bootstrap.fmt_ci(None, None))

    def test_excludes_zero(self):
        self.assertTrue(bootstrap.excludes_zero(0.5, 2.0))
        self.assertTrue(bootstrap.excludes_zero(-2.0, -0.5))
        self.assertFalse(bootstrap.excludes_zero(-0.5, 2.0))
        self.assertIsNone(bootstrap.excludes_zero(None, None))


if __name__ == "__main__":
    unittest.main()
