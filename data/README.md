# Données versionnées

Ce dossier est ignoré par défaut (`.gitignore`) : la base se reconstruit
entièrement avec `python pipeline.py --update`. **Sept fichiers** sont
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
- `context_flags.json` — Niveau 2 de la couche d'analyse sportive
  (CLAUDE.md, section « Analyse sportive — roadmap ») : flags de contexte
  tenus À LA MAIN, lus par `coupon.py` pour exclure certains paris du
  journal théorique (rotation post-Europe, changement d'entraîneur récent,
  derby avec un outsider à grosse cote, météo extrême, enjeu faible). Vide
  au départ (`[]`) ; à compléter match par match, à la main, après recherche
  web (compositions probables, actualité) — comme le repricing M6, pas
  automatisé. Une entrée :
  ```json
  {
    "match": "Leeds-Newcastle", "date": "2026-09-20",
    "post_european_home": false, "post_european_away": false,
    "derby": false,
    "coach_change_home": false, "coach_change_away": false,
    "weather_extreme": false,
    "low_stakes_home": false, "low_stakes_away": false,
    "notes": "optionnel"
  }
  ```
  Tous les flags sont des booléens (défaut implicite `false` si absent du
  fichier pour un match donné — cf. `coupon.py`, un match non documenté est
  GARDÉ, jamais exclu par défaut). `match` doit correspondre exactement à la
  clé `"{home}-{away}"` du journal (`production_journal.json`) ; `date` sert
  uniquement de repère humain, `coupon.py` matche sur `match` seul (un
  déplacement de date recoupe la même entrée `context_flags`, comme pour le
  journal lui-même depuis M5.4).
- `lineup_adjustment_template.json` — Niveau 1 de la couche d'analyse
  sportive : template à copier-coller pour `predict.py match
  --lineup-adjustment FICHIER` (M6). Structure `home`/`away` ×
  `attack`/`defense`, chaque axe a une liste `confirmed` (composition
  officielle/probable) et une liste `reference` (composition type/attendue) —
  chaque élément est soit un nombre (contribution xG/90 en attaque, xG
  concédé/90 en défense), soit `{"name": ..., "value": ...}` (le nom n'est là
  que pour la lisibilité, jamais utilisé par le calcul). Le ratio
  (somme confirmed / somme reference) est **dérivé**, jamais saisi
  directement, et clampé à `LINEUP_RATIO_BOUNDS` = (0,5 ; 1,75) dans
  `predict.py` contre une saisie fautive. D'où vient la donnée : recherche
  web (compos probables/officielles, ex. via le skill
  football-match-predictor), pas de scraping automatisé. Comment lire le
  résultat : `predict.py` affiche les ratios attaque/défense calculés, les λ
  avant/après, l'impact en points sur le 1N2, et une alerte explicite si
  `|ratio − 1| > 0,3` (« ajustement fort, à vérifier manuellement ») — ce
  seuil ne bloque rien, il signale juste un ajustement qui mérite une
  relecture avant de valider un pari dessus. Ce fichier n'est PAS versionné
  comme données personnelles (c'est un template vide de tout match réel),
  simplement gardé accessible à côté du reste.
- `README.md` — ce fichier.

## ⚠️ Données personnelles — repo privé obligatoire

`football.db`, `production_journal.json`, `real_bets.json` et
`context_flags.json` contiennent des **données personnelles de paris**
(pronostics, mises, résultats suivis, montants réellement engagés, contexte
sportif noté match par match). Ce dépôt **doit rester privé**. Ne le rendez
jamais public et ne partagez pas ces fichiers hors d'un contexte de confiance.

Le reste du dossier — notamment `data/raw/` (cache football-data.co.uk /
Understat) — reste ignoré : il est re-téléchargeable et inutile à versionner.
