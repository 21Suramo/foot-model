# foot-model

Modèle de pronostics football : pipeline de données SQLite (résultats, cotes
de clôture, xG) destiné à alimenter un backtest walk-forward Dixon-Coles.

## État du projet

- **M2 — pipeline de données : terminé** (tag `m2-pipeline`).
  Téléchargement football-data.co.uk + xG Understat (endpoint JSON
  `getLeagueData`, en-tête `X-Requested-With` requis), stockage SQLite,
  jointure xG par alias avec tolérance ±2 jours, validation `check.py` verte.
- **M3 — backtest walk-forward Dixon-Coles : en cours.**

## Commandes

```bash
pip install pandas requests          # dépendances
python pipeline.py --update          # tout mettre à jour (3 ligues x 8 saisons)
python pipeline.py --update --league E0 --season 2324   # une ligue/saison
python check.py                      # validation (code retour 0 si tout passe)
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

Périmètre : E0 (Premier League), SP1 (Liga), F1 (Ligue 1), 2018-19 à 2025-26.

## Conventions

- Les données (`data/`) ne sont pas versionnées ; la base se reconstruit
  entièrement avec `python pipeline.py --update`.
- Après toute modification du pipeline : relancer les tests puis `check.py`
  et n'intégrer que si le résultat global est OK (code retour 0).
