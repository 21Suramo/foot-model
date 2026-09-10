import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clv_signal_check as csc


def _rec(roi, convergent, brier=0.6):
    return {"roi": roi, "convergent": convergent, "model_brier": brier}


class TestSummarize(unittest.TestCase):
    def test_no_difference_is_not_significant(self):
        records = []
        for i in range(60):
            jitter = 0.2 if i % 2 else -0.2
            records.append(_rec(jitter, True))
            records.append(_rec(jitter, False))
        text, signal = csc.summarize(records)
        self.assertFalse(signal)
        self.assertIn("Aucun signal détecté", text)

    def test_large_consistent_gap_is_significant(self):
        records = []
        for i in range(60):
            jitter = 0.01 if i % 2 else -0.01
            records.append(_rec(0.5 + jitter, True))    # convergents : ROI positif net
            records.append(_rec(-0.5 + jitter, False))  # divergents : ROI négatif net
        text, signal = csc.summarize(records)
        self.assertTrue(signal)
        self.assertIn("Signal détecté", text)

    def test_negative_gap_is_not_a_signal(self):
        # divergents MEILLEURS que convergents : ce n'est pas le signal cherché
        # (la fonction ne doit déclarer un signal que dans le sens attendu).
        records = []
        for i in range(60):
            jitter = 0.01 if i % 2 else -0.01
            records.append(_rec(-0.5 + jitter, True))
            records.append(_rec(0.5 + jitter, False))
        text, signal = csc.summarize(records)
        self.assertFalse(signal)

    def test_empty_group_does_not_crash(self):
        records = [_rec(0.1, True), _rec(0.2, True)]
        text, signal = csc.summarize(records)
        self.assertFalse(signal)
        self.assertIn("aucune observation", text)


if __name__ == "__main__":
    unittest.main()
