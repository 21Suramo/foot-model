import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import aliases
import db
import odds_snapshot as osnap


class TestResolve(unittest.TestCase):
    def test_alias_hit_is_resolved(self):
        alias_map = {"Manchester United": "Man United"}
        canonical, ok = osnap.resolve("Manchester United", alias_map, {"Man United", "Chelsea"})
        self.assertEqual(canonical, "Man United")
        self.assertTrue(ok)

    def test_name_already_canonical_is_resolved(self):
        canonical, ok = osnap.resolve("Chelsea", {}, {"Chelsea", "Arsenal"})
        self.assertEqual(canonical, "Chelsea")
        self.assertTrue(ok)

    def test_unknown_name_passes_through_unresolved(self):
        canonical, ok = osnap.resolve("Some New Club FC", {}, {"Chelsea", "Arsenal"})
        self.assertEqual(canonical, "Some New Club FC")   # convention aliases.py : tel quel
        self.assertFalse(ok)


class TestKnownTeamsAndSeedIntegration(unittest.TestCase):
    """Vérifie que les alias A2 ajoutés à aliases.py résolvent bien vers un nom
    présent dans matches, sur une base minimale — pas un test de couverture
    complète (ça, c'est le snapshot réel déjà vérifié), juste un garde-fou
    contre une régression de aliases.SEED."""

    def setUp(self):
        self.conn = db.connect(":memory:")
        aliases.seed(self.conn)
        for home, away in [("Man United", "Chelsea"), ("Ath Madrid", "Betis"),
                           ("Monaco", "Lens")]:
            self.conn.execute(
                "INSERT INTO matches (date, league, season, home, away) "
                "VALUES ('2026-01-01', 'X', '2627', ?, ?)", (home, away))
        self.conn.commit()

    def test_api_style_names_resolve_to_known_matches_teams(self):
        alias_map = db.load_aliases(self.conn)
        known = {r[0] for r in self.conn.execute(
            "SELECT home FROM matches UNION SELECT away FROM matches")}
        for api_name in ("Manchester United", "Atlético Madrid", "Real Betis", "AS Monaco", "RC Lens"):
            canonical, ok = osnap.resolve(api_name, alias_map, known)
            self.assertTrue(ok, f"{api_name!r} -> {canonical!r} pas reconnu dans matches")


class TestQuotaFailureIsExplicit(unittest.TestCase):
    """A1 (workflow odds_snapshot.yml) : un quota épuisé ou une clé invalide
    doit faire échouer le run avec un message clair, pas une traceback brute
    perdue dans les logs Actions."""

    def _http_error(self, status_code):
        response = mock.Mock(status_code=status_code)
        return requests.exceptions.HTTPError(f"{status_code} error", response=response)

    @mock.patch.dict("os.environ", {"ODDS_API_KEY": "fake-key-for-test"})
    @mock.patch("odds_snapshot.run")
    def test_429_exits_with_quota_message(self, mock_run):
        mock_run.side_effect = self._http_error(429)
        with self.assertRaises(SystemExit) as cm:
            osnap.main(["--db", ":memory:"])
        self.assertIn("quota", str(cm.exception).lower())

    @mock.patch.dict("os.environ", {"ODDS_API_KEY": "fake-key-for-test"})
    @mock.patch("odds_snapshot.run")
    def test_401_exits_with_invalid_key_message(self, mock_run):
        mock_run.side_effect = self._http_error(401)
        with self.assertRaises(SystemExit) as cm:
            osnap.main(["--db", ":memory:"])
        self.assertIn("invalide", str(cm.exception).lower())

    @mock.patch.dict("os.environ", {"ODDS_API_KEY": "fake-key-for-test"})
    @mock.patch("odds_snapshot.run")
    def test_other_http_error_is_not_swallowed(self, mock_run):
        mock_run.side_effect = self._http_error(500)
        with self.assertRaises(requests.exceptions.HTTPError):
            osnap.main(["--db", ":memory:"])


if __name__ == "__main__":
    unittest.main()
