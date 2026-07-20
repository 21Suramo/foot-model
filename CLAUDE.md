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

Périmètre : E0 (Premier League), SP1 (Liga), F1 (Ligue 1), 2018-19 à 2025-26.

## Conventions

- Les données (`data/`) ne sont pas versionnées ; la base se reconstruit
  entièrement avec `python pipeline.py --update`.
- Après toute modification du pipeline : relancer les tests puis `check.py`
  et n'intégrer que si le résultat global est OK (code retour 0).
