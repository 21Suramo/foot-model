# foot-model — pipeline de données (M2) + backtest Dixon-Coles (M3)

Pipeline Python qui télécharge, normalise et stocke en SQLite des données
historiques de matchs (résultats, cotes de clôture, xG), et modèle
Dixon-Coles (1997) from scratch évalué en walk-forward strict contre les
cotes de clôture démargées.

## Sources

- [football-data.co.uk](https://www.football-data.co.uk/) — résultats + cotes
  (clôture Pinnacle prioritaire, sinon moyenne du marché ; repli sur les cotes
  d'ouverture pour 2018-19, qui n'a pas de clôture — voir `odds_source`).
- [Understat](https://understat.com/) — xG par match, jointes via la table
  `team_aliases` avec tolérance ±2 jours sur la date.

Périmètre : E0 (Premier League), SP1 (Liga), F1 (Ligue 1), saisons 2018-19 à 2025-26.

## Installation

```bash
pip install pandas requests numpy scipy
```

## Usage

```bash
# Tout mettre à jour (3 ligues x 8 saisons)
python pipeline.py --update

# Une ligue / une saison
python pipeline.py --update --league E0 --season 2324

# Ignorer le cache et re-télécharger
python pipeline.py --update --force

# Valider la base (comptages, doublons, complétude, xG manquantes)
python check.py
```

- Base : `data/football.db` (tables `matches` et `team_aliases`).
- Fichiers bruts en cache : `data/raw/` — seuls les fichiers manquants sont
  téléchargés (la saison en cours est rafraîchie si le cache a plus de 24 h).
- Re-lancer le pipeline ne crée aucun doublon (UNIQUE sur date+home+away) et
  n'efface pas les xG déjà jointes.

Si `check.py` liste des matchs sans xG, ajouter les correspondances de noms
manquantes dans `aliases.py` (nom Understat → nom football-data) puis relancer
`python pipeline.py --update`.

## Backtest M3 (Dixon-Coles vs marché)

```bash
python backtest.py --tune          # grid search de ξ sur 2020-21 + 2021-22, figé une fois pour toutes
python backtest.py --run           # test 2022-23 → 2025-26 avec ξ figé -> table predictions
python backtest.py --shuffle-test  # contrôle anti-fuite (scores permutés => Brier dégradé attendu)
python report.py                   # métriques + calibration -> reports/m3_backtest.md
```

Protocole : refit hebdomadaire, fit uniquement sur les matchs antérieurs à la
semaine prédite (garde anti-futur dans `model.fit`), burn-in 2018-19 + 2019-20,
un modèle par ligue. Benchmark : cotes de clôture démargées (méthode power),
baselines uniforme et fréquences historiques. Résultats dans
[reports/m3_backtest.md](reports/m3_backtest.md).

## Tests

```bash
python -m unittest discover tests
```

Les tests utilisent des fixtures locales et des données synthétiques,
aucun accès réseau.
