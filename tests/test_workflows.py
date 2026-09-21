"""Régression sur .github/workflows/weekly.yml, odds_snapshot.yml et
scripts/push_with_recovery.sh (audit 2026-09-21, complété par deux revues
le même jour).

football.db était resté figé ~14-15 jours malgré un run weekly-sync réussi :
la cause n'était pas la source ni le pipeline (les deux avancent bien, cf.
tests/test_pipeline.py::TestUpdateLeagueSeason et le diagnostic manuel de
l'audit) mais l'étape "Committer" du workflow, qui ne committait jamais
data/football.db — seulement le journal et le rapport. Une fois corrigé, les
deux workflows (weekly-sync ET odds-snapshot) committent data/football.db
sans coordination entre eux : la récupération sur conflit de rebase (ce
fichier SQLite est binaire, jamais fusionnable ligne à ligne) a d'abord
vécu dupliquée dans les deux YAML, puis a été extraite dans
scripts/push_with_recovery.sh (revue suivante, même jour) pour être
réellement testable — TestPushWithRecoveryScript la fait tourner pour de
vrai contre un dépôt bare + deux clones qui se marchent dessus, plutôt que
de se contenter d'une lecture de texte. Un test unitaire ne peut toujours
pas exécuter un workflow GitHub Actions complet : TestWeeklySyncWorkflow et
TestConcurrencyAndRebaseRetry restent des vérifications texte brut des YAML
(pas de dépendance PyYAML ajoutée à requirements.txt pour ces seuls tests)
pour qu'un futur edit ne réintroduise pas silencieusement les mêmes oublis.
"""
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
WEEKLY_YML = WORKFLOWS_DIR / "weekly.yml"
ODDS_SNAPSHOT_YML = WORKFLOWS_DIR / "odds_snapshot.yml"
RECOVERY_SCRIPT = REPO_ROOT / "scripts" / "push_with_recovery.sh"


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
                       "régression : data/football.db doit être passé en argument à "
                       "scripts/push_with_recovery.sh, sinon les avancées de "
                       "pipeline.py --update ne sont jamais persistées dans le repo "
                       "(bug de l'audit 2026-09-21)")

    def test_commit_step_redo_command_covers_all_three_writers(self):
        """Revue 2026-09-21 (2e) : le rejeu sur conflit de rebase doit
        reproduire TOUTES les étapes qui écrivent dans les fichiers commités
        (pipeline --update, sync-results, report), pas seulement la base —
        sinon un conflit committerait une base avancée avec un journal/
        rapport restés sur l'état d'avant le reset --hard."""
        step = self._commit_step()
        self.assertIn("push_with_recovery.sh", step)
        for command in ("pipeline.py --update", "predict.py sync-results", "predict.py report"):
            self.assertIn(command, step,
                           f"la commande de rejeu doit inclure '{command}', sinon le "
                           f"commit qui suit un conflit resynchronise la base sans "
                           f"resynchroniser le journal/rapport en même temps")

    def test_workflow_dispatch_present(self):
        self.assertIn("workflow_dispatch", self.text)

    def test_alerts_when_nothing_committed(self):
        # committed=true/false est désormais écrit par scripts/push_with_
        # recovery.sh (TestPushWithRecoveryScript le verrouille côté
        # script) — ici on vérifie seulement que l'étape d'alerte lit bien
        # cette sortie du bon step.
        self.assertIn("steps.commit.outputs.committed", self.text)
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

    def test_both_workflows_delegate_to_the_shared_recovery_script(self):
        """Revue 2026-09-21 (2e) : la récupération sur conflit de rebase
        vivait dupliquée (presque identique) dans les deux YAML — extraite
        dans scripts/push_with_recovery.sh pour être testable une seule fois
        (TestPushWithRecoveryScript) plutôt que vérifiée par lecture de texte
        dans chaque workflow. Les YAML ne doivent plus contenir leur propre
        boucle de retry inline."""
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                self.assertIn("scripts/push_with_recovery.sh", text,
                               f"{name} : doit déléguer au script partagé")
                self.assertNotIn("until git push", text,
                                  f"{name} : boucle de retry inline résiduelle — "
                                  f"devrait vivre uniquement dans le script partagé")
                self.assertNotIn("git rebase --abort", text,
                                  f"{name} : logique de récupération dupliquée en inline — "
                                  f"devrait vivre uniquement dans le script partagé")


class TestPushWithRecoveryScript(unittest.TestCase):
    """scripts/push_with_recovery.sh (revue 2026-09-21, 2e) : logique de
    commit+push+récupération extraite des deux workflows pour être testable.
    Deux niveaux : vérifications texte brut (structure, garde-fous), puis un
    test d'intégration qui fait tourner le script pour de vrai contre un
    dépôt bare + deux clones provoquant un vrai conflit binaire sur
    data/football.db — la seule façon de prouver que la récupération marche
    réellement, pas seulement que le texte du script en a l'air."""

    def setUp(self):
        self.text = RECOVERY_SCRIPT.read_text()

    def test_script_is_executable(self):
        mode = RECOVERY_SCRIPT.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "scripts/push_with_recovery.sh doit être exécutable")

    def test_no_set_x_anywhere(self):
        # Les deux appelants tournent avec ODDS_API_KEY dans l'environnement
        # (odds_snapshot.yml) — une trace de commandes (set -x/-o xtrace)
        # risquerait de l'exposer dans les logs Actions si une commande la
        # référençait un jour. Aucune ne le fait aujourd'hui (vérifié par
        # test_no_command_echoes_secret_env_vars ci-dessous), mais le script
        # ne doit de toute façon jamais activer de trace.
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotRegex(stripped, r"^set\s+(-\w*x\w*|-o\s+xtrace)\b",
                                 f"trace de commandes trouvée : {line!r}")

    def test_no_command_echoes_secret_env_vars(self):
        # Garde-fou générique (pas seulement ODDS_API_KEY) : aucune ligne
        # echo/printf ne doit interpoler une variable au nom évocateur d'un
        # secret (*_KEY, *_TOKEN, *_SECRET).
        secret_like = re.compile(r"\$\{?\w*(_KEY|_TOKEN|_SECRET)\b")
        for line in self.text.splitlines():
            if re.match(r"\s*(echo|printf)\b", line):
                self.assertNotRegex(line, secret_like,
                                     f"ligne echo/printf référençant une variable "
                                     f"secrète : {line!r}")

    def test_explicit_fetch_before_reset(self):
        # Revue 2026-09-21 (2e) : git pull --rebase fait déjà un fetch
        # implicite, mais demandé explicitement pour ne pas dépendre du
        # comportement interne d'une commande qui vient de sortir en échec.
        fetch_pos = self.text.find("git fetch origin")
        reset_pos = self.text.find("git reset --hard")
        self.assertNotEqual(fetch_pos, -1, "git fetch origin explicite manquant")
        self.assertNotEqual(reset_pos, -1, "git reset --hard manquant")
        self.assertLess(fetch_pos, reset_pos,
                         "le fetch explicite doit précéder le reset --hard")

    def test_recovery_order_abort_reset_redo_recommit(self):
        abort_pos = self.text.find("git rebase --abort")
        reset_pos = self.text.find("git reset --hard")
        redo_pos = self.text.find('bash -c "$redo_command"')
        recommit_pos = self.text.rfind("git commit")
        for label, pos in [("git rebase --abort", abort_pos), ("git reset --hard", reset_pos),
                            ("rejeu de $redo_command", redo_pos), ("git commit (recommit)", recommit_pos)]:
            self.assertNotEqual(pos, -1, f"{label} introuvable dans le script")
        self.assertTrue(abort_pos < reset_pos < redo_pos < recommit_pos,
                         f"ordre attendu abort -> reset --hard -> rejeu -> recommit, "
                         f"trouvé {(abort_pos, reset_pos, redo_pos, recommit_pos)}")

    def test_never_merges_the_binary_by_hand(self):
        self.assertNotIn("--ours", self.text)
        self.assertNotIn("--theirs", self.text)

    def test_retry_loop_has_a_cap_and_backoff(self):
        self.assertIn("max_attempts", self.text)
        self.assertIn("sleep", self.text)

    def test_writes_committed_output_both_ways(self):
        # weekly.yml lit steps.commit.outputs.committed pour décider d'ouvrir
        # une issue (TestWeeklySyncWorkflow.test_alerts_when_nothing_committed) —
        # les deux branches doivent exister côté script.
        self.assertIn('"committed=$1"', self.text, "_write_output doit formater committed=<valeur>")
        self.assertIn("_write_output true", self.text)
        self.assertIn("_write_output false", self.text)


class TestPushWithRecoveryIntegration(unittest.TestCase):
    """Fait tourner scripts/push_with_recovery.sh pour de vrai contre un
    dépôt bare + deux clones qui se marchent dessus sur data/football.db
    (binaire) — le scénario réel weekly-sync/odds-snapshot, reproduit
    localement. PUSH_RECOVERY_BACKOFF_SECONDS=0 pour ne pas ralentir la
    suite (le comportement de production, backoff inclus, est verrouillé en
    texte par TestPushWithRecoveryScript.test_retry_loop_has_a_cap_and_backoff)."""

    def _git(self, *args, cwd, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check,
                               capture_output=True, text=True)

    def _write_bytes(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_text(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bare = root / "origin.git"
        self.clone_a = root / "clone_a"   # joue le rôle de "l'autre workflow", déjà passé
        self.clone_b = root / "clone_b"   # celui qui va rencontrer le conflit
        self.clone_c = root / "clone_c"   # relecture indépendante de l'état final poussé

        self._git("init", "--bare", "-b", "main", str(self.bare), cwd=root)
        self._git("clone", str(self.bare), str(self.clone_a), cwd=root)
        for repo in (self.clone_a,):
            self._git("config", "user.name", "test", cwd=repo)
            self._git("config", "user.email", "test@example.invalid", cwd=repo)

        # Commit initial (les 3 fichiers que weekly.yml committe), pour que
        # les deux clones partent d'un historique commun.
        self._write_bytes(self.clone_a / "data" / "football.db", b"\x00SQLITE-v0\x00")
        self._write_text(self.clone_a / "data" / "production_journal.json", '{"v": 0}')
        self._write_text(self.clone_a / "reports" / "production_calibration.md", "report v0")
        self._git("add", "-A", cwd=self.clone_a)
        self._git("commit", "-m", "initial", cwd=self.clone_a)
        self._git("push", "-u", "origin", "main", cwd=self.clone_a)

        # clone_b part de ce même point de départ, AVANT que clone_a n'avance.
        self._git("clone", str(self.bare), str(self.clone_b), cwd=root)

        # "L'autre workflow" (clone_a) avance et pousse en premier — football.db
        # change de contenu binaire, c'est ce qui va provoquer le conflit.
        self._write_bytes(self.clone_a / "data" / "football.db", b"\x00SQLITE-v1-other-workflow\x00")
        self._git("commit", "-am", "other workflow's update", cwd=self.clone_a)
        self._git("push", cwd=self.clone_a)

    def test_recovers_from_a_real_binary_conflict_and_pushes(self):
        # clone_b, sans rien savoir du push de clone_a, fait ses propres
        # changements sur les 3 mêmes fichiers.
        self._git("config", "user.name", "test", cwd=self.clone_b)
        self._git("config", "user.email", "test@example.invalid", cwd=self.clone_b)
        self._write_bytes(self.clone_b / "data" / "football.db", b"\x00SQLITE-v1-this-workflow\x00")
        self._write_text(self.clone_b / "data" / "production_journal.json", '{"v": 1}')
        self._write_text(self.clone_b / "reports" / "production_calibration.md", "report v1")

        # "Commande de rejeu" : simule un pipeline.py --update + sync-results +
        # report qui régénèrent les 3 fichiers avec un contenu déterministe et
        # RECONNAISSABLE, pour prouver après coup que c'est bien CE rejeu (et
        # pas les anciens fichiers d'avant le reset) qui a été committé.
        redo_command = (
            "printf '\\x00SQLITE-v2-redone\\x00' > data/football.db && "
            "printf '{\"v\": 2}' > data/production_journal.json && "
            "printf 'report v2 (redone)' > reports/production_calibration.md"
        )

        env = dict(os.environ)
        env.update({
            "PUSH_RECOVERY_BACKOFF_SECONDS": "0",
            "PUSH_RECOVERY_GIT_NAME": "test",
            "PUSH_RECOVERY_GIT_EMAIL": "test@example.invalid",
        })
        result = subprocess.run(
            [str(RECOVERY_SCRIPT), "test commit", redo_command,
             "data/football.db", "data/production_journal.json",
             "reports/production_calibration.md"],
            cwd=self.clone_b, env=env, capture_output=True, text=True,
        )

        self.assertEqual(result.returncode, 0,
                          f"le script doit réussir après récupération — "
                          f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        # GITHUB_OUTPUT n'est pas défini ici (usage hors CI) : le script
        # écrit alors "committed=..." sur stdout.
        self.assertIn("committed=true", result.stdout)

        # Aucun état de rebase résiduel dans clone_b.
        self.assertFalse((self.clone_b / ".git" / "rebase-merge").exists(),
                          "rebase-merge résiduel : le conflit n'a pas été proprement abandonné")
        self.assertFalse((self.clone_b / ".git" / "rebase-apply").exists(),
                          "rebase-apply résiduel : le conflit n'a pas été proprement abandonné")
        status = self._git("status", "--porcelain", cwd=self.clone_b).stdout
        self.assertEqual(status.strip(), "", f"arbre de travail non propre après le script : {status!r}")

        # Relecture indépendante de ce qui a fini par être poussé — via un
        # troisième clone, pas via clone_b, pour ne pas se fier à son propre
        # état local.
        self._git("clone", str(self.bare), str(self.clone_c), cwd=self.tmp.name)
        self.assertEqual((self.clone_c / "data" / "football.db").read_bytes(),
                          b"\x00SQLITE-v2-redone\x00")
        self.assertEqual((self.clone_c / "data" / "production_journal.json").read_text(),
                          '{"v": 2}')
        self.assertEqual((self.clone_c / "reports" / "production_calibration.md").read_text(),
                          "report v2 (redone)")

        # Le commit final doit être un commit normal (pas de fusion bancale) :
        # exactement un parent, celui du push de clone_a.
        log = self._git("log", "--format=%H %P", "-1", cwd=self.clone_c).stdout.strip()
        commit_hash, _, parents = log.partition(" ")
        self.assertEqual(len(parents.split()), 1, f"commit final inattendu (pas un parent) : {log!r}")


if __name__ == "__main__":
    unittest.main()
