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
| M3.5 — pseudo-buts xG + recalibration | ✅ validé | **4 critères sur 4** — Brier à **+1,78 %** du marché (IC 95 % [+1,24 ; +2,34 %]), réglages figés dans `data/m35_frozen.json` |
| M5 — mise en production | ✅ implémenté | `predict.py` prédit les matchs à venir, blend marché/modèle, journal + calibration mensuelle |
| M5.1-M5.3 — fiabilisation du monitoring | ✅ implémenté | Résultats réels auto (`sync-results`), ROI théorique, CLV par pari, traçabilité des matchs sans cote |
| M6 — repricing H-1 sur composition confirmée | ✅ implémenté | `--lineup-adjustment` ajuste λ post-fit sur compo officielle, sans re-tuner le modèle |
| M7 — mesurer l'edge avant de l'améliorer | ✅ implémenté | CLV réservé à une clôture sharp, IC bootstrap sur tous les Brier publiés, de-vigging Shin, plafond d'exposition simultanée |
| M9 — corrélation dans le plafond d'exposition | ✅ implémenté | Matchs de la même semaine partageant une équipe comptés ×1,5 dans le plafond (heuristique assumée, pas mesurée) |
| Fatigue/congestion, M8 (roadmap) | 🔍 investigué, pas construit | Aucun signal significatif détecté — construire la calibration figerait du bruit |
| A1 (roadmap) — marchés dérivés (O/U, BTTS, totaux, handicap) | ✅ implémenté et exécuté | Grille 12×12 dédiée (jamais le 1N2), 9 marchés validés tune/validation/test ; handicap asiatique quasi à parité marché, le reste ne bat pas ses baselines |
| C1 (roadmap) — mouvement de cote comme signal d'entrée | 🔍 investigué en entier, verdict **négatif** | Le signal apparent (clôture) était un artefact de fuite temporelle ; le score composite correctement spécifié ne bat pas la baseline sur test — chantier fermé |
| A2 (roadmap) — cotes multi-books | 🟡 démarré, pas terminé | Capture + alias résolus (The Odds API, Pinnacle inclus) ; automatisation et intégration `predict.py` restent à faire |

Le protocole interdit tout re-réglage des hyperparamètres après lecture du
jeu de test — voir [Anti-fuite et protocole](#anti-fuite-et-protocole). Détail
complet de chaque chantier (y compris ceux volontairement non attaqués) dans
[CLAUDE.md](CLAUDE.md).

## Sommaire

- [Comment ça fonctionne](#comment-ça-fonctionne)
- [Au-delà de M7 : roadmap "profit durable"](#au-delà-de-m7--roadmap-profit-durable)
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
- **Traçabilité des prédictions sans cote** : quand aucune cote n'est fournie,
  le journal enregistre *pourquoi* (`meta.no_odds_reason`) au lieu d'un
  `market_weight: 0.0` muet, et la fin d'un slate récapitule les affiches
  parties en modèle pur.

### 5. Mesurer l'edge avant de l'améliorer (M7)

Quatre chantiers qui ne touchent pas au modèle mais rendent lisible s'il a un
edge réel — la question à laquelle le projet ne savait pas répondre.

- **Le CLV n'est calculé que contre une clôture sharp.** Auparavant il était
  posé contre n'importe quel `matches.odds_source`. Contre une moyenne de books
  (`avg_close`) ou une ouverture (`*_open`), ce n'est plus un CLV mais une autre
  grandeur publiée sous le même nom : battre une moyenne tirée par des books
  soft n'est pas battre le marché, et comparer à une ouverture inverse souvent
  le signe. Seul `pinnacle_close` est accepté ; les paris écartés sont comptés
  par raison dans le rapport, plutôt que dilués dans la moyenne.
- **Tout Brier publié porte son intervalle de confiance** (`bootstrap.py`,
  rééchantillonnage apparié, graine figée). Le chiffre de référence du projet
  devient `+1,78 % [+1,24 ; +2,34 %]` — dont la borne haute dépasse le critère
  de +2 %. Le rapport de production publie un IC par mois et par bucket de
  fraîcheur, et son alerte « cotes périmées » exige désormais que l'écart
  dépasse le seuil **et** que son intervalle exclue 0 : sur une quinzaine de
  matchs, 3 points d'écart sortent du bruit une fois sur deux.
- **De-vigging Shin** en production, avec le contrôle honnête qui va avec
  (`devig_check.py` : aucun écart mesurable entre les trois méthodes).
- **Plafond d'exposition simultanée** de 15 % de bankroll sur une semaine de
  matchs, parce que le plafond Kelly individuel de 5 % ne dit rien du cumul de
  dix affiches jouées le même après-midi. **M9** étend le plafond aux matchs
  de la même semaine partageant une équipe (report de calendrier, double
  confrontation), comptés ×1,5 dans la comptabilité interne — une heuristique
  assumée, pas un chiffre mesuré.

## Au-delà de M7 : roadmap "profit durable"

Trois chantiers d'une roadmap externe reçue après M7, chacun avec un
résultat honnête — y compris quand la réponse est non. Détail complet
(y compris les chantiers volontairement NON attaqués — décision fournisseur,
volume de paris qui n'existe pas encore) dans [CLAUDE.md](CLAUDE.md).

**A1 — marchés dérivés depuis la grille de scores : implémenté et exécuté.**
Tous les marchés secondaires (O/U, BTTS, totaux par équipe, handicap
asiatique) sont une simple somme sur la grille de scores. `derived_markets.py`
ajoute une grille 12×12 dédiée (`EXTENDED_MAX_GOALS`) — **elle ne remplace
jamais** la grille 7×7 du 1N2/M3.5 déjà publiée, pour ne faire dériver aucun
résultat déjà lu. `backtest_derived.py`/`report_derived.py` valident 9
marchés avec le même protocole tune/validation/test que M3.5. Résultat
([reports/derived_markets_backtest.md](reports/derived_markets_backtest.md)) :
le handicap asiatique est quasiment à parité avec le marché (Brier −0,11 %) ;
les totaux par équipe battent leurs baselines mais n'ont pas de cote marché
pour comparer ; les O/U additionnels n'apportent rien de neuf sur le 1N2 déjà
connu du modèle. Câblé en production (`predict.py` affiche ces marchés
calibrés, informationnel — le staking Kelly reste 1N2 seul).

**C1 — le mouvement de cote est-il un signal d'entrée exploitable ?
Investigué en entier, verdict final NÉGATIF.** Un diagnostic exploratoire
avait d'abord trouvé un écart net (ROI convergents +4,3 % vs divergents
−10,7 %) en classant les paris par mouvement ouverture→**clôture**. Corrigé
ensuite par un protocole complet (`clv_signal_check.py` : score composite
edge+mouvement réglé sur la validation, testé une fois, shuffle-test étendu
sur 999 permutations) — la clôture n'est PAS connue au moment de parier, seul
le mouvement ouverture→cote **prise** est un signal actionnable. Résultat :
le signal disparaît (IC du gap n'exclut pas 0, p=0,549 au shuffle-test). Le
premier écart était très probablement un artefact de fuite temporelle — un
match où le marché finit par bouger vers l'issue value est presque par
construction un match où le marché s'est rapproché de la vérité. Chantier
fermé proprement, comme la fatigue/M8 : rien construit en production. Détail
dans [reports/clv_signal_check.md](reports/clv_signal_check.md).

**A2 — acquisition de cotes multi-books : démarré, pas terminé.** Capture de
snapshots via The Odds API (`oddsapi.py`/`odds_snapshot.py`, clé lue depuis
`ODDS_API_KEY`, jamais committée) — Pinnacle et plusieurs books retail
(`winamax_fr`, `betclic_fr`) sont couverts en `regions=eu`, vérifié par appel
réel. Les 27 alias de noms d'équipe entre l'API et football-data.co.uk sont
résolus (`aliases.py`, table `team_aliases`), donc `book_odds` est
directement joignable à `matches`. **Budget de crédits à respecter avant
toute automatisation** : le free tier (500 crédits/mois) soutient ~2 à 5
snapshots/jour selon les marchés — pas la cadence "toutes les 6h" qu'un vrai
suivi de CLV demanderait. Restent à faire : cadence automatisée (secret
GitHub + choix de cadence, décision utilisateur) et intégration dans
`predict.py` (`--odds` reste manuel).

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
| `db.py` | Schéma SQLite (`matches`, `team_aliases`, `predictions`, `predictions_m35`, `predictions_derived`, `book_odds`), upserts idempotents. Les upserts de matchs ne touchent jamais aux colonnes xG. `book_odds` (roadmap A2) est une série temporelle, jamais un upsert. |
| `footballdata.py` | Téléchargement + parsing des CSV football-data.co.uk, cache dans `data/raw/football-data/`, priorité cotes clôture Pinnacle → moyenne marché → ouverture. |
| `understat.py` | xG Understat (endpoint JSON `getLeagueData`), cache dans `data/raw/understat/`. |
| `xgjoin.py` | Jointure xG sur (date, home, away) après résolution d'alias, tolérance ±2 jours. |
| `pipeline.py` | CLI d'orchestration du téléchargement + stockage. |
| `check.py` | Validation de la base (comptages, doublons, complétude, xG manquantes). Code retour 0 si tout passe. |
| `model.py` | Dixon-Coles : NLL pondérée + gradient analytique, fit L-BFGS-B, shrinkage ridge, grille de scores 7×7 et probas 1N2. `score_grid` accepte un `max_goals` optionnel (défaut inchangé) pour `derived_markets.py`. |
| `backtest.py` | Protocole walk-forward M3 : `--tune` (fige ξ), `--run` (remplit `predictions`), `--shuffle-test` (contrôle anti-fuite). Expose aussi les 3 démargeages (`demargin_proportional`/`power`/`shin`). |
| `report.py` | Métriques (Brier/log-loss) vs marché démargé et baselines, calibration → `reports/m3_backtest.md`. |
| `backtest35.py` | Idem M3 avec pseudo-buts xG, grid 2D (w, ξ), κ, température → `predictions_m35`, `data/m35_frozen.json`. |
| `report35.py` | Rapport M3.5 → `reports/m35_backtest.md`. |
| `predict.py` | Production M5 : sous-commandes `match`, `result`, `sync-results`, `report`. Refit à jour, blend marché/modèle, journalisation, `--from-skill-json`, de-vigging `--devig`, plafond d'exposition `--exposure-cap`, repricing `--lineup-adjustment` (M6), marchés dérivés A1 affichés/journalisés. |
| `backtest_blend.py` | Backtest walk-forward du pont marché/modèle (decay réel, cotes vieillies par interpolation clôture↔ouverture) → `reports/m5_blend_backtest.md`. |
| `bootstrap.py` | IC par bootstrap percentile, **apparié** (`ci_relative_delta`), **disjoint** (`ci_diff_mean`, `ci_gap_relative_delta`) et à **graine figée** (rapports committés reproductibles). |
| `devig_check.py` | Compare proportionnel / power / Shin sur les saisons hors test → `reports/devig_check.md`. |
| `fatigue_signal_check.py` | Le signal fatigue/congestion existe-t-il avant de calibrer ? → `reports/fatigue_signal_check.md` (réponse : non). |
| `derived_markets.py` | Roadmap A1 : fonctions pures grille→marché (O/U, BTTS, totaux par équipe, handicap asiatique, top-k). Grille 12×12 dédiée, ne touche jamais celle du 1N2. |
| `backtest_derived.py` / `report_derived.py` | Roadmap A1 : validation tune/validation/test de 9 marchés dérivés → `predictions_derived`, `reports/derived_markets_backtest.md`. |
| `clv_signal_check.py` | Roadmap C1 : diagnostic exploratoire + score composite mouvement de cote (tune/run/shuffle-test) → `reports/clv_signal_check.md` (verdict final : non). |
| `oddsapi.py` / `odds_snapshot.py` | Roadmap A2 : cotes multi-books via The Odds API → table `book_odds` (série temporelle). Clé lue depuis `ODDS_API_KEY`, jamais committée. |
| `aliases.py` | Table de correspondance nom Understat OU The Odds API → nom football-data (un seul namespace `team_aliases`). |

Périmètre : **E0** (Premier League), **SP1** (Liga), **F1** (Ligue 1), saisons
2018-19 à 2026-27.

## Schéma de données

`data/football.db` (SQLite) :

- **`matches`** — un match par ligne, clé unique `(date, home, away)` :
  scores mi-temps/final (`fthg`/`ftag`/`hthg`/`htag`), cotes 1N2
  (`odds_h`/`odds_d`/`odds_a` + `odds_source`), over/under 2.5 et handicap
  asiatique (cotes brutes du CSV), xG (`xg_home`/`xg_away`, posées après
  jointure, jamais écrasées par un refresh des cotes).
- **`team_aliases`** — `alias → canonical` (nom Understat OU The Odds API →
  nom football-data, un seul namespace).
- **`predictions`** / **`predictions_m35`** — sorties du walk-forward
  (probas modèle, marché démargé, fréquences baseline, hyperparamètres du
  fit), une ligne par match testé ; remplies par `backtest.py --run` /
  `backtest35.py --run`, lues par `report.py` / `report35.py`.
- **`predictions_derived`** (roadmap A1) — probas des 9 marchés dérivés
  (calibrée/brute/marché/fréquence) par match et par marché, remplie par
  `backtest_derived.py --run`, lue par `report_derived.py`.
- **`book_odds`** (roadmap A2) — cotes multi-books, une SÉRIE TEMPORELLE
  (`fetched_at` horodate chaque snapshot, jamais écrasé), remplie par
  `odds_snapshot.py`. Noms d'équipe déjà résolus vers la convention
  football-data.co.uk avant insertion : directement joignable à `matches`.

`data/production_journal.json` — journal des prédictions de production
(format `track.py` du skill `football-match-predictor`), lu et écrit par
`predict.py`.

## Installation

```bash
pip install -r requirements.txt
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

# Sans cotes, en précisant la cause (journalisée dans meta.no_odds_reason)
python predict.py match --league SP1 --home "Betis" --away "Real Madrid" \
    --no-odds-reason not_yet_published

# Repricing H-1 : composition officielle confirmée (buteur titulaire absent)
cat > lineup.json <<'EOF'
{"home": {"attack": {"confirmed": [0.55, 0.40, 0.10, 0.05],
                      "reference": [0.55, 0.40, 0.50, 0.05]}}}
EOF
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --lineup-adjustment lineup.json

# Démargeage des cotes : Shin par défaut, power ou proportionnel au besoin
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --odds 1.85,3.6,4.4 --devig power

# Plafond d'exposition simultanée de la semaine (défaut 15 % de bankroll)
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --odds 1.85,3.6,4.4 --exposure-cap 0.10

# Enregistrer un résultat, puis produire le rapport de calibration mensuel
python predict.py result --match "Arsenal-Chelsea" --actual 2-1
python predict.py report            # -> reports/production_calibration.md
```

- **Plafond d'exposition simultanée** : `kelly_stake` plafonne chaque pari à
  5 % de bankroll, ce qui suppose des paris séquentiels avec re-mesure de la
  bankroll entre deux. Un week-end de 10 affiches viole cette hypothèse : les
  matchs se jouent en même temps et l'exposition s'additionne. `predict.py`
  additionne donc les mises **non réglées de la même semaine de matchs** (même
  lundi de référence que le walk-forward) lues dans le journal — c'est là que
  le cumul se forme, puisque `--odds` ne s'applique qu'à un match unique et
  qu'un slate se génère match par match. Au-delà de 15 %, toutes les mises sont
  réduites du même facteur : les tronquer reviendrait à parier sur l'ordre des
  fixtures. Les mises brutes restent au journal (`stake_pct_uncapped`).
- **Démargeage** : `--devig` choisit comment retirer la marge du book avant le
  blend. Le défaut est **Shin** (marge dérivée d'un modèle explicite de
  parieurs informés) plutôt que `power` (exposant d'ajustement). Attention à la
  lecture : `devig_check.py` ne trouve **aucun écart de Brier distinguable du
  bruit** entre les trois méthodes sur 4 459 matchs hors test — c'est un choix
  de rigueur, pas un gain mesuré.

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
- **Pourquoi pas de cote** : une prédiction sans cote marché tourne en modèle
  pur, garde-fou marché désactivé. La cause est journalisée dans
  `meta.no_odds_reason` — `not_yet_published`, `lookup_failed`,
  `margin_rejected` (déclarés par `--no-odds-reason` ou par le champ homonyme
  de l'export du skill), `slate_odds_ignored` (déduit : `--odds` ne s'applique
  qu'à un match unique, il est ignoré sur un slate) ou `not_provided` quand rien
  n'est déclaré. Cette dernière valeur veut dire « raison non précisée » : elle
  n'est jamais remplacée par une cause plausible devinée après coup.
- **Repricing H-1 sur composition confirmée** : `--lineup-adjustment FICHIER`
  (JSON, fichier ou `-` pour stdin) ajuste λ_domicile/λ_extérieur du refit
  figé à partir de la composition officielle, plutôt que de rester sur la
  prédiction du lundi. Pour chaque équipe et chaque axe (`attack`/`defense`),
  fournir la somme des contributions xG/90 (attaque) ou xG concédé/90
  (défense) des titulaires **confirmés** vs une composition de **référence**
  (typique) — le ratio est calculé par le code, jamais saisi directement.
  Optionnel (comportement inchangé sans le flag), ignoré sur un slate
  (`--fixture` répété), et toujours journalisé dans `meta.lineup_adjustment`.

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

### Signal fatigue/congestion : investigué, pas construit

```bash
python fatigue_signal_check.py   # -> reports/fatigue_signal_check.md
```

Avant de lancer une calibration (protocole tune/validation/test comme
M3/M3.5), vérifie que le signal existe : les équipes à ≤3 jours de repos
sous-performent-elles significativement par rapport à ce que le modèle
M3.5 (sans aucune notion de repos) prédit déjà ? Walk-forward identique à
`backtest.walk_forward` sur 1920+validation (jamais le test), résidu (buts
réels − λ) par tranche de repos sur 4 axes attaque/défense × domicile/
extérieur.

**Résultat : aucun écart significatif** (voir
[reports/fatigue_signal_check.md](reports/fatigue_signal_check.md)) — la
calibration fatigue n'a donc pas été construite, elle figerait du bruit.

### Démargeage des cotes : proportionnel vs power vs Shin

```bash
python devig_check.py            # -> reports/devig_check.md
```

Le démargeage n'est pas de la plomberie : sur cotes fraîches ses sorties pèsent
92 % du FINAL. Trois méthodes comparées sur les saisons **hors test**
(burn-in + validation — choisir un démargeage est un réglage, il ne se fait pas
sur le jeu de test) : Brier, log-loss, biais favori-longshot et calibration par
tranche, chacun avec son IC bootstrap apparié.

**Résultat : aucun écart de Brier distinguable du bruit** sur 4 459 matchs
(IC à ±0,02 %). Shin est retenu en production comme choix de rigueur, pas pour
un gain mesuré. Le contrôle révèle en passant que sur ces cotes (clôture
Pinnacle, marge basse) le biais favori-longshot résiduel est **négatif** :
power et Shin sur-corrigent légèrement les outsiders au lieu de leur laisser de
la marge — donc ils manqueraient plutôt de la value qu'ils n'en fabriqueraient.

### Marchés dérivés (roadmap A1) : O/U, BTTS, totaux, handicap asiatique

```bash
python backtest_derived.py --tune          # recalibration binaire par marché (validation)
python backtest_derived.py --run           # test avec réglages figés -> predictions_derived
python backtest_derived.py --shuffle-test  # contrôle anti-fuite
python report_derived.py                   # -> reports/derived_markets_backtest.md
```

Même protocole tune/validation/test que M3.5, sans jamais retoucher aux
hyperparamètres Dixon-Coles déjà figés. `tune()` fusionne avec un fichier déjà
figé plutôt que de tout refuser (un marché déjà testé n'est jamais retouché).
Voir [Au-delà de M7](#au-delà-de-m7--roadmap-profit-durable) pour le résultat.

### Signal du mouvement de cote (roadmap C1) : investigué en entier

```bash
python clv_signal_check.py                 # diagnostic exploratoire seul
python clv_signal_check.py --tune          # règle le score composite sur la validation
python clv_signal_check.py --run           # test une fois avec le score figé
python clv_signal_check.py --shuffle-test  # permute le mouvement, vérifie l'absence d'artefact mécanique
```

Verdict final négatif — voir [Au-delà de M7](#au-delà-de-m7--roadmap-profit-durable)
et [reports/clv_signal_check.md](reports/clv_signal_check.md).

### Cotes multi-books (roadmap A2)

```bash
export ODDS_API_KEY=...   # jamais en dur dans le code, jamais committé
python odds_snapshot.py [--markets h2h,totals] [--regions eu,uk]   # -> table book_odds
```

⚠️ Budget de crédits à lire avant toute automatisation — voir
[Au-delà de M7](#au-delà-de-m7--roadmap-profit-durable) et la docstring de
`odds_snapshot.py` (le free tier ne soutient pas une cadence "toutes les 6h").

## Résultats

> **Synthèse honnête.** À ce jour (2026-09-10), aucun chantier testé n'a
> produit d'edge mesurable contre le marché de clôture sharp. Le meilleur
> résultat reste M3.5 ci-dessous : Brier à **+1,78 % du marché**, IC 95 %
> [+1,24 ; +2,34 %] — un modèle qui *approche* le marché sans le battre.
> Les marchés dérivés (A1) et le signal de mouvement de cote (C1, protocole
> complet tune/test/shuffle) ont tous les deux des verdicts finaux négatifs.
> Détail complet, y compris les règles qui décident quand fermer un
> chantier, dans [CLAUDE.md](CLAUDE.md#critères-darrêt).

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

- ✅ Brier modèle à **+1,78 %** du marché, IC 95 % **[+1,24 ; +2,34 %]** (critère < +2 %)
- ✅ Bat les baselines : +9,5 % vs fréquences, +12,4 % vs uniforme (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 3,1 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés (`--shuffle-test`)

⚠️ **Le premier critère tient sur l'estimation ponctuelle, pas sur
l'intervalle.** L'IC bootstrap apparié (10 000 rééchantillonnages des 4 338
matchs de test) va jusqu'à +2,34 %, au-delà du seuil de +2 % : sur ces données,
un écart réel supérieur au critère n'est pas exclu. Le +1,78 % ne doit jamais
être cité sans son intervalle. Cet IC est une **lecture** du test — le
protocole interdit toujours d'y re-régler quoi que ce soit.

Détail par ligue/saison et courbe de calibration complète dans
[reports/m35_backtest.md](reports/m35_backtest.md) ; comparatif M3 dans
[reports/m3_backtest.md](reports/m3_backtest.md) ; backtest du pont
marché/modèle dans [reports/m5_blend_backtest.md](reports/m5_blend_backtest.md) ;
marchés dérivés (roadmap A1) dans
[reports/derived_markets_backtest.md](reports/derived_markets_backtest.md) ;
signal du mouvement de cote (roadmap C1, verdict négatif) dans
[reports/clv_signal_check.md](reports/clv_signal_check.md).

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
- Les fichiers de réglages figés (`data/xi_frozen.json`, `data/m35_frozen.json`,
  `data/derived_markets_frozen.json`, `data/clv_composite_frozen.json`) ne
  doivent jamais être régénérés après lecture des métriques du test
  correspondant. Ils ne sont volontairement PAS versionnés (contrairement à
  `football.db`) : ils se régénèrent à l'identique par construction
  (optimisation déterministe), et `--tune` refuse de re-régler un réglage déjà
  présent dans le fichier.
- **Secrets (clés API) : jamais en dur dans le code, jamais dans un fichier
  committé — toujours via variable d'environnement** (`ODDS_API_KEY` pour
  `oddsapi.py`). Une automatisation ajoutée à `.github/workflows/` doit lire
  la clé depuis un secret du dépôt GitHub, jamais une valeur en clair dans le
  YAML.
- Après toute modification du pipeline : relancer les tests puis `check.py`
  et n'intégrer que si le résultat global est OK (code retour 0).
