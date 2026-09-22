import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_ml
import db
import train
from tests.test_backtest_ml import _seed_matches


class TestTrain(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        _seed_matches(self.conn)
        self._orig_frozen_path = backtest_ml.FROZEN_PATH
        self._tmp = tempfile.TemporaryDirectory()
        backtest_ml.FROZEN_PATH = Path(self._tmp.name) / "ml_frozen.json"
        backtest_ml.FROZEN_PATH.write_text(json.dumps({
            "num_leaves": 3, "learning_rate": 0.1, "num_rounds": 5,
            "leagues": ["E0"], "validation_seasons": ["2021"],
            "brier_validation": 0.6,
        }))

    def tearDown(self):
        backtest_ml.FROZEN_PATH = self._orig_frozen_path
        self._tmp.cleanup()

    def test_fit_production_model_uses_frozen_hyperparams(self):
        model, feature_names, n = train.fit_production_model(self.conn, leagues=["E0"])
        self.assertEqual(n, 45)
        probs = model.probs_1x2({f: None for f in feature_names})
        self.assertAlmostEqual(sum(probs), 1.0, places=6)

    def test_missing_frozen_file_fails_explicitly(self):
        backtest_ml.FROZEN_PATH.unlink()
        with self.assertRaises(SystemExit):
            train.fit_production_model(self.conn, leagues=["E0"])


if __name__ == "__main__":
    unittest.main()
