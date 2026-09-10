import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


if __name__ == "__main__":
    unittest.main()
