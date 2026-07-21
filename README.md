# foot-model — pipeline (M2) + backtest Dixon-Coles (M3/M3.5) + production (M5)

Pipeline Python qui télécharge, normalise et stocke en SQLite des données
historiques de matchs (résultats, cotes de clôture, xG), modèle Dixon-Coles
(1997) from scratch évalué en walk-forward strict contre les cotes de clôture
démargées, puis **mis en production** (`predict.py`) : le modèle figé prédit
les matchs à venir, sert de garde-fou anti-cotes-périmées, et son monitoring
continue en conditions réelles via un journal et un rapport de calibration
mensuel.

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

## Backtest M3.5 (pseudo-buts xG + recalibration)

```bash
python backtest35.py --tune          # grid 2D (w, ξ) + κ + température sur la validation
python backtest35.py --run           # test avec réglages figés -> table predictions_m35
python backtest35.py --shuffle-test  # contrôle anti-fuite
python report35.py                   # rapport -> reports/m35_backtest.md
```

Trois améliorations, mêmes protocole et saisons que M3 : entraînement sur
pseudo-buts `w×xG + (1-w)×buts` (w et ξ re-réglés conjointement sur la
validation), recalibration monotone en température, shrinkage κ re-testé
suite au diagnostic promus. Résultats dans
[reports/m35_backtest.md](reports/m35_backtest.md).

## Production M5 (prédiction des matchs à venir + monitoring)

Une fois les réglages M3.5 figés (`data/m35_frozen.json`), `predict.py` sort le
modèle du backtest et l'applique aux matchs à venir.

```bash
# Un match, avec cotes fraîches (blend marché/modèle)
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --odds 1.85,3.6,4.4 --odds 1.88,3.55,4.3 --odds-date 2026-08-14

# Sans cotes (ou cotes périmées) : le modèle reprend la main
python predict.py match --league SP1 --home "Barcelona" --away "Real Madrid"

# Slate de week-end (une affiche par --fixture)
python predict.py match --fixture "E0,Liverpool,Everton" --fixture "F1,Paris SG,Marseille"

# Mode concours : maximise l'espérance de points, pas la probabilité brute
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --contest-points 13,50,68 --contest-exact-bonus 30

# Enregistrer un résultat, puis produire le rapport de calibration mensuel
python predict.py result --match "Arsenal-Chelsea" --actual 2-1
python predict.py report            # -> reports/production_calibration.md
```

- **Refit à jour** : à chaque appel, le Dixon-Coles pseudo-buts xG est réajusté
  sur tout l'historique joué antérieur au lundi de la semaine visée (même garde
  anti-fuite que le walk-forward), puis probas 1N2 recalibrées + grille de
  scores sont sorties **au format de `match_model.py`** (le moteur du skill).
- **Pont marché/modèle (garde-fou anti-cotes-périmées)** : le poids du marché
  décroît avec l'âge des cotes (`--odds-date` ou `--odds-age-days`) — plein à
  J-1, nul à partir de J-5, et une marge implicite aberrante le divise encore
  par deux. C'est le vrai apport du modèle : quand la ligne est périmée ou
  absente, il prend le relais.
- **Journal automatique** : chaque prédiction est écrite dans
  `data/production_journal.json` (format `track.py`, donc relisible par le
  skill football-match-predictor). Ré-exécuter le même match ne duplique rien.
- **Rapport mensuel** : `predict.py report` agrège le journal par mois (Brier,
  RPS, taux d'issues/scores exacts, calibration des nuls, FINAL vs marché) dans
  `reports/production_calibration.md`.

## Tests

```bash
python -m unittest discover tests
```

Les tests utilisent des fixtures locales et des données synthétiques,
aucun accès réseau.
