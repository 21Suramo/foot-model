"""Régression sur .github/workflows/weekly.yml (audit 2026-09-21).

football.db était resté figé ~14-15 jours malgré un run weekly-sync réussi :
la cause n'était pas la source ni le pipeline (les deux avancent bien, cf.
tests/test_pipeline.py::TestUpdateLeagueSeason et le diagnostic manuel de
l'audit) mais l'étape "Committer" du workflow, qui ne committait jamais
data/football.db — seulement le journal et le rapport. Un test unitaire ne
peut pas exécuter un workflow GitHub Actions ; celui-ci verrouille donc le
contenu du fichier YAML en texte brut (pas de dépendance PyYAML ajoutée à
requirements.txt pour un seul test) pour qu'un futur edit ne réintroduise
pas silencieusement le même oubli.
"""
import re
import unittest
from pathlib import Path

WEEKLY_YML = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "weekly.yml"


class TestWeeklySyncWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = WEEKLY_YML.read_text()

    def _commit_step(self):
        # L'étape de commit : de son "run: |" jusqu'au prochain "- name:"
        # (ou la fin du fichier).
        m = re.search(r"- name: Committer.*?\n(.*?)(?=\n\s{6}- name:|\Z)", self.text, re.S)
        self.assertIsNotNone(m, "étape 'Committer' introuvable dans weekly.yml")
        return m.group(1)

    def test_commit_step_includes_football_db(self):
        step = self._commit_step()
        self.assertIn("data/football.db", step,
                       "régression : data/football.db doit être ajouté/vérifié par "
                       "l'étape de commit, sinon les avancées de pipeline.py --update "
                       "ne sont jamais persistées dans le repo (bug de l'audit 2026-09-21)")
        # Les deux commandes qui comptent réellement (diff de détection + add) :
        diff_line = next(l for l in step.splitlines() if "git diff --quiet" in l)
        add_line = next(l for l in step.splitlines() if l.strip().startswith("git add"))
        self.assertIn("data/football.db", diff_line)
        self.assertIn("data/football.db", add_line)

    def test_workflow_dispatch_present(self):
        self.assertIn("workflow_dispatch", self.text)

    def test_alerts_when_nothing_committed(self):
        self.assertIn("committed=false", self.text)
        self.assertIn("committed=true", self.text)
        self.assertIn("gh issue", self.text)

    def test_pipeline_step_failure_still_fails_the_job(self):
        # continue-on-error sur l'étape pipeline (pour laisser tourner
        # sync-results/report/commit même en cas de stagnation détectée),
        # mais le job doit quand même finir en échec pour ne pas cacher le
        # signal derrière un badge vert.
        self.assertIn("continue-on-error: true", self.text)
        self.assertIn("steps.pipeline.outcome == 'failure'", self.text)


if __name__ == "__main__":
    unittest.main()
