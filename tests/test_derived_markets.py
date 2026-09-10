import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import derived_markets as dm


class TestOverUnderMask(unittest.TestCase):
    def test_shape_and_threshold(self):
        mask = dm.over_under_mask(6, 2.5)
        self.assertEqual(mask.shape, (7, 7))
        for h, a in [(0, 0), (1, 1), (2, 0)]:
            self.assertFalse(mask[h, a])
        for h, a in [(2, 1), (3, 0), (0, 3)]:
            self.assertTrue(mask[h, a])

    def test_no_push_possible_on_half_line(self):
        # Aucune cellule à total == 2.5 pile (buts entiers) : la ligne .5 ne
        # peut jamais produire d'ambiguïté, contrairement à une ligne entière.
        mask = dm.over_under_mask(11, 2.5)
        totals = np.add.outer(np.arange(12), np.arange(12))
        self.assertTrue(np.array_equal(mask, totals > 2.5))


class TestBttsMask(unittest.TestCase):
    def test_matches_manual_definition(self):
        mask = dm.btts_mask(6)
        for h, a in [(0, 0), (1, 0), (0, 2)]:
            self.assertFalse(mask[h, a])
        for h, a in [(1, 1), (2, 3)]:
            self.assertTrue(mask[h, a])


class TestTeamTotalMask(unittest.TestCase):
    def test_home_side(self):
        mask = dm.team_total_mask(6, "home", 1.5)
        self.assertFalse(mask[1, 5])   # domicile marque 1 <= 1.5
        self.assertTrue(mask[2, 0])    # domicile marque 2 > 1.5

    def test_away_side(self):
        mask = dm.team_total_mask(6, "away", 1.5)
        self.assertFalse(mask[5, 1])
        self.assertTrue(mask[0, 2])

    def test_unknown_side_raises(self):
        with self.assertRaises(ValueError):
            dm.team_total_mask(6, "both", 1.5)


class TestAsianHandicapOutcome(unittest.TestCase):
    def test_quarter_line_never_pushes(self):
        # 1-1, ligne -0.25 : marge ajustée = 0 - 0.25 = -0.25 -> extérieur
        self.assertEqual(dm.asian_handicap_outcome(1, 1, -0.25), "away")

    def test_whole_line_can_push(self):
        # 1-0, ligne -1 : marge ajustée = 1 - 1 = 0 -> push
        self.assertEqual(dm.asian_handicap_outcome(1, 0, -1.0), "push")

    def test_home_favorite_covers(self):
        # 2-0, ligne -1 : marge ajustée = 2 - 1 = 1 > 0 -> domicile couvre
        self.assertEqual(dm.asian_handicap_outcome(2, 0, -1.0), "home")

    def test_away_covers_on_upset(self):
        # 0-0, ligne -1 : marge ajustée = 0 - 1 = -1 < 0 -> extérieur couvre
        self.assertEqual(dm.asian_handicap_outcome(0, 0, -1.0), "away")


class TestAsianHandicapProbs(unittest.TestCase):
    def test_probs_sum_to_one(self):
        grid = np.ones((7, 7)) / 49.0
        p_home, p_away, p_push = dm.asian_handicap_probs(grid, -1.0)
        self.assertAlmostEqual(p_home + p_away + p_push, 1.0, places=12)

    def test_zero_line_symmetric_grid_has_push_mass_on_draws(self):
        grid = np.zeros((3, 3))
        grid[0, 0] = grid[1, 1] = grid[2, 2] = 1.0 / 3.0  # matchs nuls uniquement
        p_home, p_away, p_push = dm.asian_handicap_probs(grid, 0.0)
        self.assertAlmostEqual(p_push, 1.0, places=12)
        self.assertAlmostEqual(p_home, 0.0, places=12)
        self.assertAlmostEqual(p_away, 0.0, places=12)

    def test_negative_line_favors_home(self):
        grid = np.zeros((3, 3))
        grid[1, 0] = 1.0   # victoire 1-0 certaine
        p_home, p_away, p_push = dm.asian_handicap_probs(grid, -0.5)
        self.assertAlmostEqual(p_home, 1.0, places=12)
        self.assertAlmostEqual(p_away, 0.0, places=12)
        self.assertAlmostEqual(p_push, 0.0, places=12)


class TestTopK(unittest.TestCase):
    def _grid(self):
        grid = np.array([[0.30, 0.10], [0.25, 0.35]])
        return grid / grid.sum()

    def test_topk_scores_sorted_by_probability(self):
        grid = self._grid()
        self.assertEqual(dm.topk_scores(grid, 1), [(1, 1)])
        self.assertEqual(set(dm.topk_scores(grid, 2)), {(1, 1), (0, 0)})

    def test_topk_hit_true_and_false(self):
        grid = self._grid()
        self.assertTrue(dm.topk_hit(grid, 1, 1, 1))
        self.assertFalse(dm.topk_hit(grid, 0, 1, 1))
        self.assertTrue(dm.topk_hit(grid, 0, 1, 4))

    def test_topk_hit_false_beyond_grid_truncation(self):
        grid = self._grid()
        self.assertFalse(dm.topk_hit(grid, 5, 5, 4))


if __name__ == "__main__":
    unittest.main()
