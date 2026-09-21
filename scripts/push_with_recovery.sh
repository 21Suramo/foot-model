#!/usr/bin/env bash
# Committe un ensemble de fichiers et pousse, avec récupération automatique
# d'un conflit de rebase (revue 2026-09-21). Partagé par weekly.yml et
# odds_snapshot.yml : les deux committent data/football.db sans coordination
# entre eux (concurrency.group les sérialise déjà dans le cas commun, ce
# script est le filet de sécurité pour tout ce qu'elle ne couvre pas — push
# manuel, autre déclencheur pendant la fenêtre du job).
#
# data/football.db est un fichier SQLite BINAIRE : un conflit de rebase
# dessus ne se résout jamais en éditant un diff (git ne sait pas fusionner
# un binaire ligne à ligne), et re-tenter `git pull --rebase` sur un rebase
# resté en conflit échoue à chaque fois ("rebase in progress"). Seule
# récupération saine : abandonner le rebase, repartir du HEAD distant à
# jour, REJOUER la commande qui a produit nos changements (le rejeu doit
# couvrir TOUT ce qui écrit dans les fichiers commités, pas un sous-ensemble
# — sinon on committe une base avancée avec un journal/rapport restés en
# retard), puis recommitter dessus.
#
# Usage :
#   scripts/push_with_recovery.sh "<message de commit>" "<commande de rejeu>" <fichier...>
#
# La commande de rejeu est exécutée via `bash -c` après le reset --hard ; si
# une de ses sous-commandes peut légitimement échouer sans que ça invalide
# ce qu'elle a quand même produit (ex. pipeline.py --update qui sort en
# échec sur sa garde anti-stagnation tout en ayant déjà avancé la base),
# c'est à l'appelant de l'écrire avec un `|| true` sur CETTE sous-commande
# précisément — le script ne masque aucune erreur de son propre chef.
#
# Écrit committed=true|false dans $GITHUB_OUTPUT s'il est défini (sinon sur
# stdout, pour un usage hors CI/tests). Sort en échec si le push échoue
# encore après PUSH_RECOVERY_MAX_ATTEMPTS tentatives.
#
# Variables d'environnement (valeurs par défaut = comportement de
# production ; surchargeables pour les tests, cf. tests/test_workflows.py) :
#   PUSH_RECOVERY_MAX_ATTEMPTS    (défaut 5)
#   PUSH_RECOVERY_BACKOFF_SECONDS (défaut 5, multiplié par le n° de tentative)
#   PUSH_RECOVERY_GIT_NAME        (défaut "github-actions[bot]")
#   PUSH_RECOVERY_GIT_EMAIL       (défaut "github-actions[bot]@users.noreply.github.com")
#
# Ne JAMAIS ajouter `set -x` ici : les deux appelants (weekly.yml,
# odds_snapshot.yml) tournent avec des secrets dans l'environnement
# (ODDS_API_KEY) — une trace de commandes les exposerait dans les logs
# Actions même si aucune commande ici ne les manipule directement.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <message> <commande de rejeu> <fichier...>" >&2
  exit 2
fi

message="$1"
redo_command="$2"
shift 2
files=("$@")

max_attempts="${PUSH_RECOVERY_MAX_ATTEMPTS:-5}"
backoff_seconds="${PUSH_RECOVERY_BACKOFF_SECONDS:-5}"
git_name="${PUSH_RECOVERY_GIT_NAME:-github-actions[bot]}"
git_email="${PUSH_RECOVERY_GIT_EMAIL:-github-actions[bot]@users.noreply.github.com}"

_write_output() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "committed=$1" >> "$GITHUB_OUTPUT"
  else
    echo "committed=$1"
  fi
}

git config user.name "$git_name"
git config user.email "$git_email"

if git diff --quiet -- "${files[@]}"; then
  echo "Rien à committer."
  _write_output false
  exit 0
fi

git add -- "${files[@]}"
git commit -m "$message"

branch="$(git rev-parse --abbrev-ref HEAD)"
attempt=0
while true; do
  if git push; then
    _write_output true
    exit 0
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "::error::push échoué après $max_attempts tentatives (conflit persistant sur ${files[*]})." >&2
    exit 1
  fi
  echo "push échoué (tentative $attempt/$max_attempts)..."
  sleep "$((attempt * backoff_seconds))"

  if git pull --rebase origin "$branch"; then
    echo "rebase propre — nouvel essai de push."
    continue
  fi

  echo "conflit de rebase — abandon, resynchronisation sur origin/$branch, rejeu du travail."
  git rebase --abort || true
  git fetch origin "$branch"
  git reset --hard "origin/$branch"
  bash -c "$redo_command"

  if git diff --quiet -- "${files[@]}"; then
    echo "Plus rien à committer après resynchronisation (déjà couvert par l'autre run)."
    _write_output false
    exit 0
  fi
  git add -- "${files[@]}"
  git commit -m "$message"
done
