import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_blend as bb
import predict


class TestAging(unittest.TestCase):
    close = (0.60, 0.25, 0.15)
    open_ = (0.50, 0.27, 0.23)

    def test_j0_is_closing(self):
        for got, exp in zip(bb.aged_fair(self.close, self.open_, 0), self.close):
            self.assertAlmostEqual(got, exp, places=12)

    def test_j7_is_opening(self):
        for got, exp in zip(bb.aged_fair(self.close, self.open_, 7), self.open_):
            self.assertAlmostEqual(got, exp, places=12)

    def test_j_beyond_horizon_clamps_to_opening(self):
        for got, exp in zip(bb.aged_fair(self.close, self.open_, 30), self.open_):
            self.assertAlmostEqual(got, exp, places=12)

    def test_intermediate_is_between_and_normalized(self):
        p = bb.aged_fair(self.close, self.open_, 3)  # 3/7 vers l'ouverture
        self.assertAlmostEqual(sum(p), 1.0, places=12)
        # home entre ouverture et clôture, plus proche de la clôture (frac<0.5)
        self.assertTrue(self.open_[0] < p[0] < self.close[0])
        self.assertGreater(p[0], (self.close[0] + self.open_[0]) / 2)


class TestBlend(unittest.TestCase):
    def test_weight_zero_is_pure_model(self):
        model = (0.5, 0.3, 0.2)
        for got, exp in zip(bb.blended((0.7, 0.2, 0.1), model, 0.0), model):
            self.assertAlmostEqual(got, exp, places=12)

    def test_weight_one_is_pure_market(self):
        market = (0.7, 0.2, 0.1)
        for got, exp in zip(bb.blended(market, (0.5, 0.3, 0.2), 1.0), market):
            self.assertAlmostEqual(got, exp, places=12)

    def test_blend_sums_to_one(self):
        self.assertAlmostEqual(sum(bb.blended((0.7, 0.2, 0.1), (0.4, 0.3, 0.3), 0.65)), 1.0, places=12)


class TestDecayWeightMatchesCode(unittest.TestCase):
    """Le backtest doit utiliser EXACTEMENT le decay de predict.py."""

    def test_matches_predict_market_weight(self):
        for age in bb.AGES:
            self.assertAlmostEqual(bb.decay_weight(age),
                                   predict.market_weight(bb.BLEND_BASE, age, True)[0], places=12)

    def test_fresh_full_and_stale_zero(self):
        self.assertAlmostEqual(bb.decay_weight(1), bb.BLEND_BASE)
        self.assertAlmostEqual(bb.decay_weight(5), 0.0)
        self.assertAlmostEqual(bb.decay_weight(7), 0.0)


if __name__ == "__main__":
    unittest.main()
