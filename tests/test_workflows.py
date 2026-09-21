"""Régression sur .github/workflows/weekly.yml et odds_snapshot.yml (audit
2026-09-21, complété par une revue le même jour).

football.db était resté figé ~14-15 jours malgré un run weekly-sync réussi :
la cause n'était pas la source ni le pipeline (les deux avancent bien, cf.
tests/test_pipeline.py::TestUpdateLeagueSeason et le diagnostic manuel de
l'audit) mais l'étape "Committer" du workflow, qui ne committait jamais
data/football.db — seulement le journal et le rapport. Une fois corrigé, les
deux workflows (weekly-sync ET odds-snapshot) committent data/football.db
sans coordination entre eux : TestConcurrencyAndRebaseRetry verrouille le
groupe de concurrency partagé et le retry pull --rebase/push qui les
protègent d'une course. Un test unitaire ne peut pas exécuter un workflow
GitHub Actions ; ce fichier verrouille donc le contenu des YAML en texte
brut (pas de dépendance PyYAML ajoutée à requirements.txt pour ces seuls
tests) pour qu'un futur edit ne réintroduise pas silencieusement les mêmes
oublis.
"""
import re
import unittest
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WEEKLY_YML = WORKFLOWS_DIR / "weekly.yml"
ODDS_SNAPSHOT_YML = WORKFLOWS_DIR / "odds_snapshot.yml"


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


def _concurrency_group(text):
    m = re.search(r"^concurrency:\s*\n\s+group:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


class TestConcurrencyAndRebaseRetry(unittest.TestCase):
    """Race condition (revue 2026-09-21) : weekly-sync et odds-snapshot
    committent tous les deux data/football.db. Sans coordination, deux runs
    concurrents peuvent se marcher dessus (push rejeté car le remote a
    avancé entretemps pendant le job). Un groupe de concurrency PARTAGÉ
    sérialise les deux workflows entre eux ; git pull --rebase + retry avant
    le push reste un filet de sécurité pour ce que le groupe ne couvre pas
    (push manuel, autre déclencheur)."""

    def setUp(self):
        self.texts = {"weekly.yml": WEEKLY_YML.read_text(),
                       "odds_snapshot.yml": ODDS_SNAPSHOT_YML.read_text()}

    def test_both_workflows_share_the_same_concurrency_group(self):
        groups = {name: _concurrency_group(text) for name, text in self.texts.items()}
        for name, group in groups.items():
            self.assertIsNotNone(group, f"{name} : aucun groupe de concurrency déclaré")
        self.assertEqual(len(set(groups.values())), 1,
                          f"les deux workflows doivent partager le MÊME groupe de "
                          f"concurrency pour se sérialiser mutuellement, trouvé : {groups}")

    def test_concurrency_does_not_cancel_in_progress_runs(self):
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                self.assertIn("cancel-in-progress: false", text,
                               f"{name} : un run en cours (capture réelle de données) ne "
                               f"doit jamais être annulé au profit du suivant")

    def test_push_is_preceded_by_pull_rebase_with_retry(self):
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                self.assertIn("git pull --rebase", text,
                               f"{name} : pull --rebase manquant avant le push")
                # Retry effectif : une boucle qui englobe le pull --rebase ET
                # le push, pas un pull isolé sans nouvel essai en cas d'échec.
                self.assertRegex(text, r"until git pull --rebase.*&&\s*git push",
                                  f"{name} : le push doit être retenté après un pull --rebase, "
                                  f"pas juste précédé d'un pull isolé")
                self.assertIn("sleep", text, f"{name} : pas de backoff entre les tentatives")


if __name__ == "__main__":
    unittest.main()
