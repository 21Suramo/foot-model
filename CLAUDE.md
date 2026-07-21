# foot-model

Modèle de pronostics football : pipeline de données SQLite (résultats, cotes
de clôture, xG) destiné à alimenter un backtest walk-forward Dixon-Coles.

## État du projet

- **M2 — pipeline de données : terminé** (tag `m2-pipeline`).
  Téléchargement football-data.co.uk + xG Understat (endpoint JSON
  `getLeagueData`, en-tête `X-Requested-With` requis), stockage SQLite,
  jointure xG par alias avec tolérance ±2 jours, validation `check.py` verte.
- **M3 — backtest walk-forward Dixon-Coles : implémenté et exécuté.**
  Modèle en Python pur (numpy/scipy), walk-forward hebdomadaire strict,
  ξ = 0.002 figé sur validation 2020-21+2021-22, test 2022-23 → 2025-26.
  Résultat honnête ([reports/m3_backtest.md](reports/m3_backtest.md)) :
  3 critères sur 4 validés — bat nettement les baselines, calibration
  saine, anti-fuite OK, mais Brier à **+2,47 %** du marché (critère < 2 %).
  Le protocole interdit de re-régler ξ après lecture du test.
- **M3.5 — pseudo-buts xG + recalibration + diagnostic promus : validé.**
  Entraînement sur w×xG + (1-w)×buts (w = 0.6, ξ = 0.003, κ = 1 re-réglés
  conjointement sur la validation), température t = 1.077, figés dans
  `data/m35_frozen.json`. Résultat
  ([reports/m35_backtest.md](reports/m35_backtest.md)) : **4 critères sur 4**,
  Brier à **+1,78 %** du marché. Ces fichiers figés ne doivent jamais être
  régénérés après lecture du test.
- **M5 — mise en production : implémenté.** `predict.py` sort le modèle M3.5
  figé du backtest et l'applique aux matchs à venir : refit à jour (même garde
  anti-fuite que le walk-forward), probas 1N2 recalibrées + grille de scores
  au format `match_model.py`. Pont marché/modèle à fraîcheur variable — le
  poids du marché décroît avec l'âge des cotes (base 92 % à J-1, plancher 28 %
  à partir de J-5 ; barème validé par `backtest_blend.py`), le modèle sert de
  garde-fou anti-cotes-périmées. Mode concours (`--contest-points`)
  branché sur les probas du modèle. Journal automatique
  (`data/production_journal.json`, format `track.py`) et rapport de calibration
  mensuel (`reports/production_calibration.md`) : le monitoring continue en
  conditions réelles.

## Commandes

```bash
pip install pandas requests          # dépendances
python pipeline.py --update          # tout mettre à jour (3 ligues x 8 saisons)
python pipeline.py --update --league E0 --season 2324   # une ligue/saison
python check.py                      # validation (code retour 0 si tout passe)
python backtest.py --tune|--run|--shuffle-test   # backtest M3 (voir backtest.py)
python report.py                     # rapport -> reports/m3_backtest.md
python backtest35.py --tune|--run|--shuffle-test # backtest M3.5 (pseudo-buts xG)
python report35.py                   # rapport -> reports/m35_backtest.md
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --odds 1.85,3.6,4.4 --odds-date 2026-08-14   # prédiction production (M5)
python predict.py result --match "Arsenal-Chelsea" --actual 2-1   # enregistre un résultat
python predict.py report             # rapport de calibration -> reports/production_calibration.md
python backtest_blend.py             # backtest du blend marché/modèle -> reports/m5_blend_backtest.md
python -m unittest discover -s tests # tests unitaires
```

## Architecture

- `db.py` — schéma SQLite (`data/football.db`) : tables `matches`
  (clé unique date+home+away) et `team_aliases`, upserts idempotents.
  Les upserts de matchs ne touchent jamais aux colonnes xG.
- `footballdata.py` — CSV football-data.co.uk avec cache dans
  `data/raw/football-data/` ; priorité cotes de clôture Pinnacle, repli
  moyenne du marché, puis ouverture (saison 2018-19, colonne `odds_source`).
- `understat.py` — xG Understat, cache dans `data/raw/understat/`.
- `xgjoin.py` — jointure xG sur (date, home, away) après résolution
  d'alias, tolérance ±2 jours.
- `aliases.py` — seed de `team_aliases` (nom Understat → nom football-data).
- `pipeline.py` — CLI d'orchestration ; `check.py` — validation de la base.
- `model.py` — Dixon-Coles : MLE pondérée (gradient analytique), shrinkage
  ridge des équipes à faible historique, grille de scores 7×7 + probas 1N2.
- `backtest.py` — protocole walk-forward : `--tune` (fige ξ dans
  `data/xi_frozen.json`), `--run` (table `predictions`), `--shuffle-test`
  (anti-fuite). Le fichier ξ figé ne doit jamais être régénéré après le test.
- `report.py` — Brier/log-loss vs marché démargé power et baselines,
  calibration, verdicts → `reports/m3_backtest.md`.
- `predict.py` — production M5. Sous-commandes `match` (prédit un match ou un
  slate `--fixture`, refit à jour sur l'historique antérieur au lundi visé,
  probas + grille au format `match_model.py`, pont marché/modèle à fraîcheur
  variable, mode `--contest-points`, journalisation auto), `result` (enregistre
  un score réel) et `report` (calibration mensuelle → `reports/production_calibration.md`).
  Lit les réglages figés via `backtest35.frozen()` ; journal JSON compatible
  avec le `track.py` du skill football-match-predictor. `--from-skill-json`
  (fichier ou `-`/stdin) lit l'export `football-match-predictor.skill-export/v1`
  et le mappe sur les arguments (`league/home/away/odds_1x2/match_date/odds_date`) —
  sortie identique au passage manuel ; `ou` et `final_probs_1x2` ignorés,
  `league` hors {E0,SP1,F1} → erreur.
- `backtest_blend.py` — backtest walk-forward du pont marché/modèle de
  `predict.py`. Cotes vieillies par interpolation clôture↔ouverture (les deux
  vraies lignes des CSV bruts), FINAL calculé via le decay réel du code, Brier
  par tranche d'âge vs marché frais / marché vieilli / modèle pur, comparé à
  l'ancien barème ; grid search du poids sur la validation seule. Verdict →
  `reports/m5_blend_backtest.md`. A servi à régler le barème de `predict.py`
  (base 92 %, plancher 28 %) : +0,47 % de Brier vs l'ancien (65 %/coupure J-5),
  sans régression. ⚠️ Ce proxy (deux vraies lignes sharp) **sous-estime** la
  valeur du garde-fou : sur ces données le marché bat le modèle à tous les âges,
  mais la vraie cible est une cote scrapée fausse/périmée que football-data ne
  peut pas simuler — ne pas conclure « le modèle ne sert à rien ».

Périmètre : E0 (Premier League), SP1 (Liga), F1 (Ligue 1), 2018-19 à 2025-26.

## Conventions

- Les données (`data/`) ne sont pas versionnées ; la base se reconstruit
  entièrement avec `python pipeline.py --update`.
- Après toute modification du pipeline : relancer les tests puis `check.py`
  et n'intégrer que si le résultat global est OK (code retour 0).
