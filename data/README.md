# Données versionnées

Ce dossier est ignoré par défaut (`.gitignore`) : la base se reconstruit
entièrement avec `python pipeline.py --update`. **Cinq fichiers** sont
versionnés volontairement (le compte exact vit dans `.gitignore`, à tenir à
jour si cette liste change — cf. CLAUDE.md, section Conventions) :

- `football.db` — base SQLite (résultats, cotes de clôture, xG, `book_odds`)
  **et** la table de production. Contient l'historique des prédictions et
  paris théoriques.
- `production_journal.json` — journal des pronostics théoriques et résultats
  enregistrés en production par `predict.py` (jamais des paris réellement
  placés — voir `real_bets.json` ci-dessous pour ça).
- `real_bets.json` — journal des paris **réellement posés sur 1xbet**, tenu
  à la main (décision du 2026-09-13, CLAUDE.md « Suivi du P&L réel ») :
  `predict.py`/`sync-results` ne l'écrivent jamais, seul `real_pnl.py` le lit.
  Une entrée :
  ```json
  {
    "date_placed": "2026-09-13", "match": "Arsenal-Chelsea", "competition": "E0",
    "issue": "home", "journal_odds": 1.90, "taken_odds_1xbet": 1.85,
    "stake_eur": 20.0, "result": "pending", "notes": ""
  }
  ```
  `result` ∈ `pending`/`win`/`loss`/`void` (annulé/remboursé), à mettre à jour
  à la main une fois le match joué. `journal_odds` est la cote vue dans
  `production_journal.json` au moment du pari, `taken_odds_1xbet` celle
  réellement obtenue — l'écart entre les deux (`real_pnl.py`) mesure le
  slippage d'exécution, pas la qualité du modèle.
- `m35_frozen.json` — réglages M3.5 figés (ξ/w/κ/température), ne doit
  jamais être régénéré après lecture du test (cf. CLAUDE.md).
- `README.md` — ce fichier.

## ⚠️ Données personnelles — repo privé obligatoire

`football.db`, `production_journal.json` et `real_bets.json` contiennent des
**données personnelles de paris** (pronostics, mises, résultats suivis,
montants réellement engagés). Ce dépôt **doit rester privé**. Ne le rendez
jamais public et ne partagez pas ces fichiers hors d'un contexte de confiance.

Le reste du dossier — notamment `data/raw/` (cache football-data.co.uk /
Understat) — reste ignoré : il est re-téléchargeable et inutile à versionner.
