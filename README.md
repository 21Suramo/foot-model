# foot-model — pipeline (M2) + backtest Dixon-Coles (M3/M3.5) + production (M5)

Modèle de pronostics football de bout en bout : un pipeline Python télécharge,
normalise et stocke en SQLite des données historiques de matchs (résultats,
cotes de clôture, xG), un modèle Dixon-Coles (1997) réécrit from scratch est
évalué en walk-forward strict contre les cotes de clôture démargées, puis
**mis en production** (`predict.py`) — le modèle figé prédit les matchs à
venir, sert de garde-fou anti-cotes-périmées face au marché, et son
monitoring continue en conditions réelles via un journal et un rapport de
calibration mensuel.

Rien n'est fait à la main : un seul modèle statistique couvre tout le
parcours, du CSV brut jusqu'à la prédiction du prochain match, avec un
protocole anti-fuite explicite à chaque étape (walk-forward, garde temporelle
dans le fit, contrôle par permutation des scores).

## État du projet

| Étape | Statut | Résumé |
| --- | --- | --- |
| M2 — pipeline de données | ✅ terminé (tag `m2-pipeline`) | football-data.co.uk + xG Understat, SQLite, `check.py` vert |
| M3 — backtest Dixon-Coles (buts) | ✅ implémenté et exécuté | 3 critères sur 4 validés — Brier à +2,47 % du marché (critère < 2 % non atteint) |
| M3.5 — pseudo-buts xG + recalibration | ✅ validé | **4 critères sur 4** — Brier à **+1,78 %** du marché, réglages figés dans `data/m35_frozen.json` |
| M5 — mise en production | ✅ implémenté | `predict.py` prédit les matchs à venir, blend marché/modèle, journal + calibration mensuelle |

Le protocole interdit tout re-réglage des hyperparamètres après lecture du
jeu de test — voir [Anti-fuite et protocole](#anti-fuite-et-protocole).

## Sommaire

- [Comment ça fonctionne](#comment-ça-fonctionne)
- [Architecture](#architecture)
- [Schéma de données](#schéma-de-données)
- [Installation](#installation)
- [Usage](#usage)
- [Résultats](#résultats)
- [Anti-fuite et protocole](#anti-fuite-et-protocole)
- [Tests](#tests)
- [Conventions](#conventions)

## Comment ça fonctionne

### 1. Le pipeline de données (M2)

`pipeline.py` télécharge et fusionne deux sources indépendantes par match :

- **football-data.co.uk** : résultats + cotes 1N2. Priorité aux cotes de
  clôture Pinnacle (`odds_source = "pinnacle_close"`), repli sur la moyenne
  du marché puis sur les cotes d'ouverture pour 2018-19 (seule saison sans
  clôture dans les CSV).
- **Understat** : xG (buts attendus) par match, via l'endpoint JSON interne
  `getLeagueData` (nécessite l'en-tête `X-Requested-With`).

Les deux sources n'utilisent pas les mêmes noms d'équipe (`Man United` vs
`Manchester Utd`) ni exactement les mêmes horodatages : `aliases.py` résout
les noms, `xgjoin.py` joint sur (date, home, away) avec une tolérance de
±2 jours. Tout est upserté de façon idempotente (clé unique date+home+away) —
relancer le pipeline ne crée jamais de doublon et ne réécrit jamais une xG déjà
posée. `check.py` valide la base à la fin (comptages par ligue/saison,
doublons, complétude des cotes, part de matchs sans xG).

### 2. Le modèle Dixon-Coles (M3)

Implémentation from scratch (numpy/scipy, pas de lib de modélisation sportive)
du modèle de Dixon & Coles (1997) :

- Chaque équipe a une force d'**attaque** α et de **défense** β ; il y a un
  avantage à domicile γ global et une correction ρ pour les scores fermés
  (0-0, 1-0, 0-1, 1-1), où l'hypothèse d'indépendance de Poisson est la plus
  fausse. `λ_domicile = α_dom × β_ext × γ`, `λ_extérieur = α_ext × β_dom`.
- Buts marqués modélisés par deux lois de Poisson indépendantes (corrigées
  par ρ), ce qui donne directement une **grille de scores** 7×7 (0-0 à 6-6,
  renormalisée) puis les probas 1N2 par sommation triangulaire.
- Estimation par **maximum de vraisemblance pondérée**, gradient analytique,
  `scipy.optimize.minimize` (L-BFGS-B) :
  - poids temporels `exp(-ξ × jours_écoulés)` — les matchs récents comptent
    plus ; ξ est réglé une fois pour toutes par validation, jamais ré-estimé
    dans le fit lui-même ;
  - pénalité **ridge** sur les log-forces (prior gaussien centré sur la
    moyenne de la ligue) — une équipe promue ou avec peu d'historique est
    rétrécie vers la moyenne, le prior s'efface quand les matchs
    s'accumulent ;
  - identifiabilité : après optimisation, renormalisation exacte à
    moyenne(α) = 1 (les λ sont invariants par cette transformation).
- **Walk-forward hebdomadaire strict** (`backtest.py`) : à chaque semaine
  testée, le modèle est refit uniquement sur les matchs strictement
  antérieurs (garde dans `model.fit`, `ValueError` si une date ≥ référence
  s'y glisse), un modèle indépendant par ligue.

Résultat M3 : le modèle bat nettement les baselines (fréquences historiques,
uniforme) et sa calibration est saine, mais son Brier reste à +2,47 % du
marché — au-dessus du seuil de succès (< 2 %) fixé avant le test.

### 3. Pseudo-buts xG + recalibration (M3.5)

Trois améliorations, même protocole et mêmes saisons que M3, réglées
**conjointement** sur la validation (jamais sur le test) :

- **Pseudo-buts xG** : au lieu d'entraîner sur les buts réels, la cible
  Poisson devient `w × xG + (1-w) × buts` (repli sur les buts si les xG
  manquent pour ce match) — les xG portent plus d'information sur la
  performance sous-jacente que le score brut, bruité par la finition. La
  correction ρ reste calée sur les *scores réels* (c'est un artefact de
  buts entiers, pas de xG). `w` est re-réglé conjointement avec ξ (grid
  search 2D).
- **Shrinkage κ** re-testé suite au diagnostic des équipes promues (peu
  d'historique en ligue 1).
- **Recalibration en température** : une transformation monotone (`t`)
  appliquée aux probas 1N2 brutes du modèle, réglée sur la validation pour
  corriger un biais de calibration résiduel.

Réglages figés (**ne doivent jamais être régénérés après lecture du test**) :
`w = 0.6`, `ξ = 0.003`, `κ = 1.0`, `t = 1.077`, dans `data/m35_frozen.json`.
Résultat : **4 critères sur 4** validés, Brier à **+1,78 %** du marché — voir
[Résultats](#résultats) et [reports/m35_backtest.md](reports/m35_backtest.md).

### 4. Production et pont marché/modèle (M5)

`predict.py` sort le modèle M3.5 figé du cadre de backtest et l'applique aux
matchs à venir :

- **Refit à jour** à chaque appel, sur tout l'historique antérieur au lundi
  de la semaine visée — même garde anti-fuite que le walk-forward.
- **Pont marché/modèle à fraîcheur variable** : le marché est en général plus
  fort que le modèle (voir Résultats), donc quand une cote fraîche est
  disponible on lui donne un poids élevé ; mais une cote scrapée peut être
  périmée, mal recopiée ou venir d'un book soft. Le poids du marché décroît
  avec l'âge de la cote (`--odds-date` / `--odds-age-days`) : base **92 %**
  à J-1, décroissance vers un plancher de **28 %** à partir de J-5 (barème
  validé par `backtest_blend.py`, +0,47 % de Brier vs l'ancien barème 65 % /
  coupure nette à J-5). Sans cote ou cote trop vieille, le modèle prend
  seul la main — c'est le garde-fou anti-cotes-périmées.
- **Mode concours** (`--contest-points`) : au lieu de maximiser la probabilité
  brute, maximise l'espérance de points d'un barème de pronostic (issue /
  score exact avec bonus).
- **Journal automatique** (`data/production_journal.json`, format compatible
  avec le skill `football-match-predictor` / `track.py`) et **rapport de
  calibration mensuel** (`reports/production_calibration.md`) : chaque
  prédiction et chaque résultat enregistré alimentent un suivi en conditions
  réelles, hors échantillon de backtest.

## Architecture

```
pipeline.py ──> footballdata.py ─┐
                understat.py    ─┼─> db.py (SQLite) ──> check.py
                xgjoin.py        ┘        │
                aliases.py                │
                                           ▼
                              backtest.py / backtest35.py ──> model.py
                                           │
                              report.py / report35.py ──> reports/*.md
                                           │
                              data/xi_frozen.json / m35_frozen.json (figés)
                                           │
                                           ▼
                                     predict.py ──> data/production_journal.json
                                           │                    │
                              backtest_blend.py            predict.py report
                                           │                    │
                              reports/m5_blend_backtest.md   reports/production_calibration.md
```

| Fichier | Rôle |
| --- | --- |
| `db.py` | Schéma SQLite (`matches`, `team_aliases`, `predictions`, `predictions_m35`), upserts idempotents. Les upserts de matchs ne touchent jamais aux colonnes xG. |
| `footballdata.py` | Téléchargement + parsing des CSV football-data.co.uk, cache dans `data/raw/football-data/`, priorité cotes clôture Pinnacle → moyenne marché → ouverture. |
| `understat.py` | xG Understat (endpoint JSON `getLeagueData`), cache dans `data/raw/understat/`. |
| `xgjoin.py` | Jointure xG sur (date, home, away) après résolution d'alias, tolérance ±2 jours. |
| `aliases.py` | Table de correspondance nom Understat → nom football-data. |
| `pipeline.py` | CLI d'orchestration du téléchargement + stockage. |
| `check.py` | Validation de la base (comptages, doublons, complétude, xG manquantes). Code retour 0 si tout passe. |
| `model.py` | Dixon-Coles : NLL pondérée + gradient analytique, fit L-BFGS-B, shrinkage ridge, grille de scores et probas 1N2. |
| `backtest.py` | Protocole walk-forward M3 : `--tune` (fige ξ), `--run` (remplit `predictions`), `--shuffle-test` (contrôle anti-fuite). |
| `report.py` | Métriques (Brier/log-loss) vs marché démargé et baselines, calibration → `reports/m3_backtest.md`. |
| `backtest35.py` | Idem M3 avec pseudo-buts xG, grid 2D (w, ξ), κ, température → `predictions_m35`, `data/m35_frozen.json`. |
| `report35.py` | Rapport M3.5 → `reports/m35_backtest.md`. |
| `predict.py` | Production M5 : sous-commandes `match`, `result`, `report`. Refit à jour, blend marché/modèle, journalisation, `--from-skill-json`. |
| `backtest_blend.py` | Backtest walk-forward du pont marché/modèle (decay réel, cotes vieillies par interpolation clôture↔ouverture) → `reports/m5_blend_backtest.md`. |

Périmètre : **E0** (Premier League), **SP1** (Liga), **F1** (Ligue 1), saisons
2018-19 à 2025-26.

## Schéma de données

`data/football.db` (SQLite) :

- **`matches`** — un match par ligne, clé unique `(date, home, away)` :
  scores mi-temps/final (`fthg`/`ftag`/`hthg`/`htag`), cotes 1N2
  (`odds_h`/`odds_d`/`odds_a` + `odds_source`), over/under 2.5 et handicap
  asiatique (cotes brutes du CSV), xG (`xg_home`/`xg_away`, posées après
  jointure, jamais écrasées par un refresh des cotes).
- **`team_aliases`** — `alias → canonical` (nom Understat → nom
  football-data).
- **`predictions`** / **`predictions_m35`** — sorties du walk-forward
  (probas modèle, marché démargé, fréquences baseline, hyperparamètres du
  fit), une ligne par match testé ; remplies par `backtest.py --run` /
  `backtest35.py --run`, lues par `report.py` / `report35.py`.

`data/production_journal.json` — journal des prédictions de production
(format `track.py` du skill `football-match-predictor`), lu et écrit par
`predict.py`.

## Installation

```bash
pip install pandas requests numpy scipy
```

## Usage

### Pipeline de données

```bash
python pipeline.py --update                              # tout mettre à jour (3 ligues x 8 saisons)
python pipeline.py --update --league E0 --season 2324     # une ligue / une saison
python pipeline.py --update --force                       # ignorer le cache et re-télécharger
python check.py                                            # valider la base (0 = OK)
```

- Fichiers bruts en cache : `data/raw/` — seuls les fichiers manquants sont
  téléchargés (la saison en cours est rafraîchie si le cache a plus de 24 h).
- Relancer le pipeline ne crée aucun doublon et n'efface pas les xG déjà
  jointes.
- Si `check.py` liste des matchs sans xG, ajouter les correspondances de noms
  manquantes dans `aliases.py` puis relancer `python pipeline.py --update`.

### Backtest M3 (Dixon-Coles vs marché)

```bash
python backtest.py --tune          # grid search de ξ sur 2020-21 + 2021-22, figé une fois pour toutes
python backtest.py --run           # test 2022-23 → 2025-26 avec ξ figé -> table predictions
python backtest.py --shuffle-test  # contrôle anti-fuite (scores permutés => Brier dégradé attendu)
python report.py                   # métriques + calibration -> reports/m3_backtest.md
```

### Backtest M3.5 (pseudo-buts xG + recalibration)

```bash
python backtest35.py --tune          # grid 2D (w, ξ) + κ + température sur la validation
python backtest35.py --run           # test avec réglages figés -> table predictions_m35
python backtest35.py --shuffle-test  # contrôle anti-fuite
python report35.py                   # rapport -> reports/m35_backtest.md
```

### Production M5 (prédiction des matchs à venir + monitoring)

```bash
# Un match, avec cotes fraîches (blend marché/modèle)
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --odds 1.85,3.6,4.4 --odds-date 2026-08-14

# Sans cotes (ou cotes périmées) : le modèle reprend la main
python predict.py match --league SP1 --home "Barcelona" --away "Real Madrid"

# Slate de week-end (une affiche par --fixture)
python predict.py match --fixture "E0,Liverpool,Everton" --fixture "F1,Paris SG,Marseille"

# Depuis l'export JSON du skill football-match-predictor (fichier ou stdin)
python predict.py match --from-skill-json export.json
cat export.json | python predict.py match --from-skill-json -

# Mode concours : maximise l'espérance de points, pas la probabilité brute
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --contest-points 13,50,68 --contest-exact-bonus 30

# Enregistrer un résultat, puis produire le rapport de calibration mensuel
python predict.py result --match "Arsenal-Chelsea" --actual 2-1
python predict.py report            # -> reports/production_calibration.md
```

- **Pont d'entrée depuis le skill** : `--from-skill-json` lit l'export
  `football-match-predictor.skill-export/v1` (fichier ou `-` pour stdin) et
  mappe `league/home/away/odds_1x2/match_date/odds_date` sur les arguments —
  résultat strictement identique à ces valeurs passées à la main. Les champs
  `ou` (le modèle price ses scores depuis sa propre grille) et
  `final_probs_1x2` (`predict.py` recalcule son FINAL) sont ignorés ; une
  `league` absente ou hors {E0, SP1, F1} lève une erreur claire plutôt que de
  deviner.
- **Journal automatique** : chaque prédiction est écrite dans
  `data/production_journal.json` ; ré-exécuter le même match ne duplique rien.

### Backtest du blend marché/modèle

```bash
python backtest_blend.py   # -> reports/m5_blend_backtest.md
```

Valide le decay du pont marché/modèle sous le même protocole walk-forward que
M3.5. Les cotes sont vieillies artificiellement (J-0 à J-7) par interpolation
clôture↔ouverture — les deux vraies lignes présentes dans les CSV
football-data —, FINAL est recalculé via la formule de decay réelle de
`predict.py`, et le poids est cherché par grid search sur la validation
seule.

> ⚠️ **Ce proxy sous-estime la vraie valeur du garde-fou.** Il compare deux
> vraies lignes de book (ouverture vs clôture), toutes deux *sharp* : sur ces
> données le marché bat le modèle pur à tous les âges, donc le blend n'améliore
> jamais le Brier vs le marché seul — il n'en garde que l'essentiel. Mais le
> garde-fou vise une cote **scrapée sur le web, mal recopiée, figée à J-3+ ou
> issue d'un book soft** — strictement pire qu'une ouverture et non simulable
> avec football-data. Le chiffre mesuré ici est donc un **plancher** de
> l'utilité du modèle, pas sa valeur réelle. Détail dans
> [reports/m5_blend_backtest.md](reports/m5_blend_backtest.md).

## Résultats

4338 matchs de test (saisons 2022-23 à 2025-26, 3 ligues), refit hebdomadaire.
Réglages M3.5 figés sur validation 2020-21 + 2021-22 : `w = 0.6`, `ξ = 0.003`,
`κ = 1.0`, `t = 1.077`.

| Méthode | Brier | Log-loss | Bonne issue (argmax) |
| --- | --- | --- | --- |
| Marché (démargé, méthode power) | 0.57361 | 0.96501 | 54.4 % |
| **Modèle M3.5 (recalibré)** | **0.58384** | **0.98088** | 53.3 % |
| Modèle M3 (buts seuls, rappel) | 0.58781 | 0.98683 | 52.7 % |
| Fréquences historiques (baseline) | 0.64535 | 1.06749 | 44.9 % |
| Uniforme (baseline) | 0.66667 | 1.09861 | 44.9 % |

- ✅ Brier modèle à **+1,78 %** du marché (critère < +2 %)
- ✅ Bat les baselines : +9,5 % vs fréquences, +12,4 % vs uniforme (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 3,1 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés (`--shuffle-test`)

Détail par ligue/saison et courbe de calibration complète dans
[reports/m35_backtest.md](reports/m35_backtest.md) ; comparatif M3 dans
[reports/m3_backtest.md](reports/m3_backtest.md) ; backtest du pont
marché/modèle dans [reports/m5_blend_backtest.md](reports/m5_blend_backtest.md).

## Anti-fuite et protocole

Le risque principal d'un backtest de pronostics est la fuite d'information
du futur vers le passé. Trois garde-fous, à chaque étage :

1. **Walk-forward strict** : chaque semaine testée n'est prédite qu'à partir
   des matchs strictement antérieurs ; `model.fit` lève une `ValueError` si
   un match daté ≥ la date de référence s'y glisse.
2. **Séparation tune / validation / test** : les hyperparamètres (ξ, w, κ,
   température) sont réglés sur 2020-21 + 2021-22 uniquement, puis **figés**
   dans `data/xi_frozen.json` / `data/m35_frozen.json` et jamais régénérés
   après avoir lu les métriques du test (2022-23 → 2025-26).
3. **Contrôle par permutation** (`--shuffle-test`) : les scores réels sont
   permutés aléatoirement entre matchs ; un modèle qui apprendrait par fuite
   verrait son Brier s'améliorer ou rester stable, un modèle honnête voit son
   Brier se dégrader nettement — vérifié sur les 3 ligues.

## Tests

```bash
python -m unittest discover -s tests
```

Les tests utilisent des fixtures locales (`tests/fixtures/`) et des données
synthétiques, aucun accès réseau.

## Conventions

- Les données (`data/`) ne sont pas versionnées par défaut ; la base se
  reconstruit entièrement avec `python pipeline.py --update`. Exceptions
  versionnées volontairement : `data/football.db` et
  `data/production_journal.json` (voir `data/README.md`) — ces deux fichiers
  contiennent des données personnelles de paris, **le dépôt doit rester
  privé**.
- Les fichiers de réglages figés (`data/xi_frozen.json`,
  `data/m35_frozen.json`) ne doivent jamais être régénérés après lecture des
  métriques du test correspondant.
- Après toute modification du pipeline : relancer les tests puis `check.py`
  et n'intégrer que si le résultat global est OK (code retour 0).
