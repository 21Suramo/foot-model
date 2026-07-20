# foot-model — pipeline de données (M2)

Pipeline Python qui télécharge, normalise et stocke en SQLite des données
historiques de matchs (résultats, cotes de clôture, xG), prêt à alimenter
un backtest walk-forward Dixon-Coles (M3).

## Sources

- [football-data.co.uk](https://www.football-data.co.uk/) — résultats + cotes
  (clôture Pinnacle prioritaire, sinon moyenne du marché ; repli sur les cotes
  d'ouverture pour 2018-19, qui n'a pas de clôture — voir `odds_source`).
- [Understat](https://understat.com/) — xG par match, jointes via la table
  `team_aliases` avec tolérance ±1 jour sur la date.

Périmètre : E0 (Premier League), SP1 (Liga), F1 (Ligue 1), saisons 2018-19 à 2025-26.

## Installation

```bash
pip install pandas requests
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

## Tests

```bash
python -m unittest discover tests
```

Les tests utilisent des fixtures locales, aucun accès réseau.
