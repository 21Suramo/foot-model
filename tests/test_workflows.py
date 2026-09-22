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


class TestOddsSnapshotAlert(unittest.TestCase):
    """Audit 2026-09-22 : odds_snapshot.yml n'avait aucune alerte si la capture
    échouait silencieusement (clé invalide, quota épuisé) — contrairement à
    weekly.yml (alerte "rien committé", ajoutée le 2026-09-21). Alerter sur
    "rien committé" n'aurait pas eu de sens ici (2 runs/jour, souvent rien de
    neuf à capturer — le cas normal, pas une panne) : le signal pertinent est
    l'échec de la capture elle-même."""

    def setUp(self):
        self.text = ODDS_SNAPSHOT_YML.read_text()

    def test_alerts_on_capture_failure(self):
        self.assertIn("if: failure()", self.text)
        self.assertIn("gh issue", self.text)

    def test_alert_step_precedes_commit_step(self):
        # L'alerte doit se déclencher même si l'étape de capture a échoué —
        # donc placée avant "Committer" (que le job saute de toute façon par
        # défaut sur un échec antérieur, sans "if:" particulier à vérifier ici).
        alert_pos = self.text.find("if: failure()")
        commit_pos = self.text.find("- name: Committer")
        self.assertNotEqual(alert_pos, -1)
        self.assertNotEqual(commit_pos, -1)
        self.assertLess(alert_pos, commit_pos)


class TestIssuesPermission(unittest.TestCase):
    """gh issue create/comment (weekly.yml depuis le 2026-09-21,
    odds_snapshot.yml depuis le 2026-09-22) exige `issues: write` — trouvé
    absent des deux fichiers en auditant odds_snapshot.yml (2026-09-22) :
    déclarer `permissions:` du tout restreint tout scope non listé à `none`,
    donc l'alerte de weekly.yml aurait échoué en 403 sans jamais avoir été
    testée en conditions réelles (pas encore déclenchée depuis son ajout)."""

    def test_both_workflows_declare_issues_write(self):
        for path in (WEEKLY_YML, ODDS_SNAPSHOT_YML):
            with self.subTest(workflow=path.name):
                text = path.read_text()
                self.assertIn("gh issue", text, f"{path.name} : n'utilise plus gh issue ?")
                m = re.search(r"^permissions:\n(.*?)(?=\n\S|\Z)", text, re.M | re.S)
                self.assertIsNotNone(m, f"{path.name} : bloc permissions introuvable")
                self.assertIn("issues: write", m.group(1),
                               f"{path.name} : issues: write manquant — gh issue create/comment "
                               f"échouerait en 403 (permissions: restreint tout scope non listé "
                               f"à none)")


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

    def _push_retry_block(self, text):
        # Le corps de la boucle "until git push; do ... done" du filet de
        # sécurité, isolé du reste du fichier (en particulier de l'étape de
        # capture initiale, qui invoque aussi pipeline.py/odds_snapshot.py).
        m = re.search(r"until git push; do\n(.*?)\n\s*done\n", text, re.S)
        self.assertIsNotNone(m, "boucle 'until git push; do ... done' introuvable")
        return m.group(1)

    def test_push_is_retried_with_pull_rebase_and_backoff(self):
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                block = self._push_retry_block(text)
                self.assertIn("git pull --rebase", block,
                               f"{name} : pull --rebase manquant dans la boucle de retry")
                self.assertIn("sleep", block, f"{name} : pas de backoff entre les tentatives")
                self.assertIn('if [ "$attempt" -ge 5 ]', text,
                               f"{name} : pas de plafond explicite à 5 tentatives")

    def test_rebase_conflict_resets_and_redoes_the_work_before_recommitting(self):
        """Revue 2026-09-21 : data/football.db est un fichier SQLite binaire,
        jamais fusionnable ligne à ligne — un CONFLIT de git pull --rebase
        dessus ne peut pas se résoudre en éditant un diff. La récupération
        attendue : abandonner le rebase, repartir du HEAD distant à jour
        (reset --hard), rejouer la commande qui a produit nos changements
        (idempotente dans les deux cas : upsert pour pipeline.py --update,
        INSERT OR IGNORE série temporelle pour odds_snapshot.py), puis
        recommitter — jamais tenter de résoudre le conflit binaire lui-même."""
        redo_command = {
            "weekly.yml": "python pipeline.py --update",
            "odds_snapshot.yml": "python odds_snapshot.py",
        }
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                block = self._push_retry_block(text)
                abort_pos = block.find("git rebase --abort")
                reset_pos = block.find("git reset --hard")
                redo_pos = block.find(redo_command[name])
                recommit_pos = block.rfind("git commit")
                self.assertNotEqual(abort_pos, -1, f"{name} : git rebase --abort manquant")
                self.assertNotEqual(reset_pos, -1, f"{name} : git reset --hard manquant")
                self.assertIn("origin/$GITHUB_REF_NAME", block,
                               f"{name} : le reset doit cibler la branche distante à jour, "
                               f"pas un état local potentiellement périmé")
                self.assertNotEqual(redo_pos, -1,
                                     f"{name} : {redo_command[name]} doit être rejoué après le reset "
                                     f"(sans ça, le commit suivant ne capturerait rien de neuf)")
                self.assertNotEqual(recommit_pos, -1, f"{name} : recommit manquant après le rejeu")
                self.assertTrue(
                    abort_pos < reset_pos < redo_pos < recommit_pos,
                    f"{name} : ordre attendu rebase --abort -> reset --hard -> rejeu -> "
                    f"recommit, positions trouvées {(abort_pos, reset_pos, redo_pos, recommit_pos)}")

    def test_rebase_conflict_recovery_never_touches_football_db_by_hand(self):
        # Garde-fou négatif : la récupération ne doit jamais tenter de
        # merger/éditer le binaire elle-même (ex. git checkout --theirs/-ours
        # sur data/football.db), seulement reset --hard + rejeu.
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                block = self._push_retry_block(text)
                self.assertNotIn("--ours", block)
                self.assertNotIn("--theirs", block)
                self.assertNotIn("git checkout data/football.db", block)


if __name__ == "__main__":
    unittest.main()
