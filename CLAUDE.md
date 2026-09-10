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
  Brier à **+1,78 %** du marché — IC 95 % [+1,24 ; +2,34 %] mesuré en M7, donc
  un verdict qui tient sur l'estimation ponctuelle mais dont la borne haute
  dépasse le critère de +2 % : citer le chiffre avec son intervalle. Ces
  fichiers figés ne doivent jamais être régénérés après lecture du test.
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
- **M5.1 — fiabilisation du monitoring : implémenté.** Le journal de production
  n'avait aucun résultat réel (`actual_score` null partout) : la boucle ne se
  refermait pas. `predict.py sync-results` va chercher les scores dans
  `football.db` sans saisie manuelle ; le rapport découpe la performance **par
  fraîcheur des cotes** (fraîches / intermédiaires / périmées, seuils de
  `market_weight`) au lieu de tout agréger par mois ; les mises Kelly sont
  persistées dans `bets` et réglées en `realized_pct` pour un **ROI théorique**
  a posteriori. Les paramètres de risque (fraction et plafond Kelly, base et
  plancher du poids marché) sont verrouillés par
  `tests/test_predict.py::TestRiskParameters::test_risk_parameters_are_intentional` :
  les changer fait échouer un test, exprès.
- **M5.2 — CLV (closing line value) des paris théoriques : implémenté.** Le
  ROI a besoin d'environ 100 paris réglés pour dire quoi que ce soit (variance
  du foot) ; en dessous, impossible de savoir si un pari isolé (une grosse
  cote sur un match qui n'est pas encore réglé) avait une vraie value ou était
  simplement une cote pourrie. `predict.py sync-results` pose désormais, sur
  chaque pari théorique réglé, son CLV : l'écart entre la cote prise
  (`bets[].odds`) et la cote de clôture retrouvée dans `matches.odds_*`
  (`clv_pct = cote_prise / cote_clôture − 1`, positif = la cote a raccourci
  après la prise). Le CLV converge beaucoup plus vite que le ROI — c'est le
  premier signal à lire sur un petit échantillon, avant que le ROI réel ne
  devienne exploitable. Nouvelle section « CLV (closing line value) » dans
  `reports/production_calibration.md`. Le CLV n'est posé que par
  `sync-results` (qui a accès à `football.db`) — `predict.py result`, la
  saisie manuelle, ne le renseigne pas.
- **M5.3 — traçabilité des matchs sans cote marché : implémenté.** Un match
  parti en modèle pur (aucune cote fournie) ne laissait dans le journal qu'un
  `market_weight: 0.0` muet, impossible à interpréter a posteriori : cotes pas
  encore ouvertes chez les books (structurel, rien à corriger) ou recherche
  infructueuse/alias manquant (à corriger) se ressemblaient. `predict.py`
  enregistre désormais `meta.no_odds_reason` sur ces entrées :
  `not_yet_published` / `lookup_failed` / `margin_rejected` déclarés par
  `--no-odds-reason` (ou le champ homonyme de l'export du skill,
  `--from-skill-json`) ; `slate_odds_ignored` déduit quand `--odds` est ignoré
  sur un slate (`--fixture` répété) ; `not_provided` = raison non précisée,
  jamais devinée. `predict.py match` récapitule en fin de run les affiches
  parties sans cote, groupées par raison. Purement additif — n'interagit pas
  avec le CLV par pari de M5.2 (deux champs distincts, aucun recouvrement).
- **M6 — repricing H-1 sur compositions confirmées : implémenté.** Quand une
  composition officielle est connue (~1h avant coup d'envoi), `predict.py
  match --lineup-adjustment FICHIER` ajuste λ_domicile/λ_extérieur du refit
  figé plutôt que de rester sur la prédiction du lundi. Entrée manuelle (JSON,
  fichier ou stdin) : pour chaque équipe et chaque axe (`attack`/`defense`),
  la somme des contributions xG/90 (attaque) ou xG concédé/90 (défense) des
  titulaires CONFIRMÉS vs une composition de RÉFÉRENCE — le ratio est
  **dérivé** par `compute_lineup_ratio` (jamais saisi directement), clampé à
  `LINEUP_RATIO_BOUNDS` (0,5–1,75) contre une saisie fautive. Le ratio
  d'attaque d'une équipe multiplie SON PROPRE λ ; son ratio de défense
  multiplie le λ ADVERSE. Vit entièrement dans `predict.py`
  (`apply_lineup_adjustment`, `grid_and_probs_from_lambdas`) — `model.py`
  n'est jamais touché, `grid_and_probs_from_lambdas` réutilise
  `model.DixonColes` via une instance à deux équipes fictives dont les
  log-forces d'attaque encodent directement les λ ajustés, pour ne pas
  dupliquer la correction tau/rho. Optionnel (comportement inchangé si le
  flag est omis), ignoré pour un slate (`--fixture` répété, un seul match à
  la fois), et journalisé dans `meta.lineup_adjustment` (`applied`, ratios,
  λ avant/après) que l'ajustement soit appliqué ou non. Pas de scraping —
  la composition est fournie à la main (recherche web via le skill) ; c'est
  un ajustement en aval du modèle M3.5 figé, pas un re-tuning de ses
  hyperparamètres — `data/m35_frozen.json` n'est jamais régénéré pour ça.
- **M7 — mesurer l'edge avant de l'améliorer : implémenté.** Quatre chantiers
  qui ne changent pas le modèle mais rendent lisible s'il a un edge réel.
  1. **CLV réservé à une clôture sharp.** Le CLV était calculé contre
     n'importe quel `matches.odds_source`. Contre `avg_close` (moyenne de
     books aux marges hétérogènes) ou une ouverture (`*_open`), ce n'est plus
     un CLV : battre une moyenne tirée par des books soft n'est pas battre le
     marché, et comparer à une ouverture inverse souvent le signe. Seul
     `pinnacle_close` (`CLV_SHARP_SOURCES`) est accepté ; les paris écartés
     portent `bets[].clv_skipped` et sont comptés dans le rapport par raison
     (`source_not_sharp` = structurel, relancer `sync-results` n'y changera
     rien ; `no_closing_odds` = clôture absente ou saisie manuelle).
  2. **IC bootstrap sur tous les Brier publiés** (`bootstrap.py`,
     rééchantillonnage APPARIÉ par match, graine figée pour que les rapports
     se régénèrent à l'identique). Résultat marquant :
     **+1,78 % du marché → IC 95 % [+1,24 ; +2,34 %]**. La borne haute dépasse
     le critère de +2 % : le verdict « 4 critères sur 4 » tient sur
     l'estimation ponctuelle, pas sur l'intervalle. Ne plus jamais citer le
     +1,78 % sans son IC. Le rapport de production publie de même un IC par
     mois et par bucket de fraîcheur, et l'alerte « cotes périmées » exige
     désormais que l'écart dépasse le seuil ET que son IC exclue 0 (sur
     n ≈ 15, 3 points d'écart sortent du bruit une fois sur deux).
  3. **De-vigging Shin** (`backtest.demargin_shin`, défaut de production,
     `--devig {proportional,power,shin}`, journalisé dans `meta.devig`).
     `devig_check.py` compare les trois méthodes sur les saisons HORS TEST
     (burn-in + validation, 4 459 matchs) → `reports/devig_check.md`.
     **Honnêteté du résultat : aucun écart de Brier n'est distinguable du
     bruit** (IC à ±0,02 %). Shin est un choix de rigueur (marge dérivée d'un
     modèle explicite plutôt qu'un exposant libre), PAS un gain mesuré — ne
     jamais écrire « Shin améliore les probas ». Le contrôle montre en outre
     que sur ces cotes le biais favori-longshot résiduel est négatif : power
     et Shin sur-corrigent légèrement plutôt que de laisser de la marge.
  4. **Plafond d'exposition simultanée** (`SLATE_EXPOSURE_CAP = 0.15`, soit
     3 × le plafond individuel). Kelly plafonne chaque pari à 5 % en supposant
     des paris séquentiels ; un week-end de 10 affiches expose 30 % en même
     temps. Le cumul est lu dans le JOURNAL sur la même semaine de matchs
     (lundi de référence `backtest.monday_of`, paris non réglés) — pas sur le
     run courant : `--odds` ne s'applique qu'à un match unique, donc le flux
     réel génère un match à la fois et un plafond « par run » n'aurait jamais
     rien plafonné. Au-delà du budget restant, toutes les mises sont réduites
     du MÊME facteur (jamais tronquées : tronquer reviendrait à parier sur
     l'ordre des fixtures) ; les mises brutes restent journalisées
     (`stake_pct_uncapped`, `exposure_factor`). Un match seul n'est jamais
     réduit. Verrouillé, comme les autres paramètres de risque, par
     `tests/test_predict.py::TestRiskParameters`.
- **Fatigue/congestion (Δjours) : investigué, PAS construit.** Avant de
  lancer un chantier de calibration (tune/validation/test comme M3/M3.5), la
  roadmap demandait de vérifier que le signal existe : les équipes à ≤3 jours
  de repos sous-performent-elles significativement par rapport à ce que le
  modèle M3.5 (sans aucune notion de repos) prédit déjà ?
  `fatigue_signal_check.py` répond par un walk-forward identique à
  `backtest.walk_forward` (même garde anti-fuite), restreint à
  1920+VALIDATION (jamais le TEST), comparant le résidu (buts réels - λ) par
  tranche de repos sur les 4 axes attaque/défense × domicile/extérieur.
  **Résultat : aucun écart significatif** (|z| < 1 partout, ~250 matchs à
  repos court sur 13 242 observations, voir
  [reports/fatigue_signal_check.md](reports/fatigue_signal_check.md)).
  Conclusion : la calibration fatigue N'A PAS été construite — l'aurait été,
  elle aurait figé du bruit. Ne pas relancer ce chantier sans donnée
  nouvelle (davantage de saisons) ; ne pas le confondre avec un simple oubli
  à rattraper.

## Roadmap post-M7 (revue du 2026-09-10)

⚠ Numérotation : la roadmap externe qui a motivé cette section réutilise le
nom « M7 » pour un chantier différent du M7 déjà documenté plus haut (CLV
sharp/bootstrap/Shin/plafond d'exposition). Pour éviter toute confusion,
cette section désigne le nouveau chantier « M7 (roadmap) » explicitement.

- **M7 (roadmap) — Validation indépendante des marchés dérivés (O/U 2,5,
  BTTS) : implémenté et exécuté.** Le skill vendait ces deux marchés depuis
  le début (grille de score de `predict.py`) sans jamais les avoir mesurés
  au niveau de rigueur du 1N2. `backtest_derived.py` applique le MÊME
  protocole que M3.5 (tune/validation/test, IC bootstrap apparié), SANS
  retoucher aux hyperparamètres Dixon-Coles déjà figés (w/ξ/κ de
  `data/m35_frozen.json`) : seule une recalibration binaire propre à chaque
  marché (q = p^t/(p^t+(1-p)^t)) est réglée sur la même validation, figée
  dans `data/derived_markets_frozen.json`. Résultat honnête
  ([reports/derived_markets_backtest.md](reports/derived_markets_backtest.md)) :
  - O/U 2,5 : Brier modèle à +1,81 % du marché IC 95 % [+1,16 ; +2,56 %]
    (démargeage proportionnel à 2 issues — critère < +2 % validé sur le
    point, borne haute au-dessus, même lecture prudente que M3.5) ; calibration
    et anti-fuite OK ; **ne bat PAS nettement les baselines** (+2,5 % vs
    fréquences, +3,2 % vs uniforme, sous le seuil de 3 % chacune).
  - BTTS : comparaison au marché **inapplicable** — football-data.co.uk ne
    fournit aucune cote BTTS (seuls 1X2, O/U 2,5, handicap asiatique) ; pas de
    critère fabriqué en son absence. Calibration et anti-fuite OK, mais bat
    encore moins les baselines que l'O/U (+0,8 % / +1,4 %).
  - Lecture honnête : ces deux marchés dérivés sont mesurablement plus
    faibles que le 1N2 M3.5. Ne pas les présenter avec la même confiance que
    le 1N2 tant que ce verdict tient.
  - BTTS a de plus une température figée sur le BORD de `TEMP_BOUNDS`
    (t = 0,500, borne basse) : l'aplatissement optimal est peut-être plus
    fort que la plage actuelle ne le permet — signalé dans le rapport,
    volontairement pas corrigé après coup (élargir la plage après avoir vu
    où l'optimiseur bute serait re-régler sur le test).
- **M8 (roadmap) — Correction de la feature fatigue : investigué, PAS
  construit** ([reports/m8_congestion_source_investigation.md](reports/m8_congestion_source_investigation.md)).
  Deux sources candidates pour les dates de coupe/C1/C3 testées en
  connectivité réelle : football-data.org (métadonnées accessibles sans
  clé, mais les endpoints de matchs exigent un compte/`X-Auth-Token` — une
  inscription qu'un humain doit faire, pas une session autonome) et le
  scraping de pages Wikipédia de saison (sans identifiants, mais un chantier
  de parsing long et fragile sur 3 ligues × 9 saisons × plusieurs
  compétitions). Le sous-échantillon « repos court » déjà mesuré par
  `fatigue_signal_check.py` (~250/13 242 matchs, |z| < 1) rend le gain de
  puissance statistique attendu modeste face au coût des deux options. À
  reprendre si l'utilisateur fournit une clé API dédiée, ou quand
  davantage de saisons auront accumulé.
- **M9 (roadmap) — Corrélation réelle dans le plafond d'exposition :
  implémenté.** `SLATE_EXPOSURE_CAP` (predict.py) traitait chaque match comme
  un tirage indépendant. `CORRELATED_EXPOSURE_MULTIPLIER = 1.5` (verrouillé
  par `TestRiskParameters`, même statut que les autres paramètres de risque)
  détecte les paris sur des matchs de la MÊME semaine partageant une équipe
  (report de calendrier, double confrontation) et les compte ×1,5 dans la
  comptabilité interne du plafond — jamais dans les mises réellement posées,
  toujours réduites par le même facteur commun documenté depuis M7. C'est
  une heuristique non calibrée (aucune donnée de paris multi-matchs corrélés
  n'existe pour l'estimer), au même titre que la division par 2 sur marge
  aberrante de `market_weight` — assumé et documenté comme tel, pas présenté
  comme un chiffre mesuré. `pending_bet_matches` (predict.py) étend la
  détection aux paris déjà engagés dans le journal la même semaine.
- **Phase B (M10 cotes programmatiques, M11 automatisation compos) : NON
  ATTAQUÉE.** Les deux dépendent d'une décision que cette session ne peut pas
  prendre à la place de l'utilisateur : quel fournisseur de cotes/actus
  (compte à créer, clé API, coût éventuel, conditions d'utilisation). Écrire
  une couche d'abstraction sans fournisseur réel à brancher dessus serait de
  la spéculation non demandée (contraire à la discipline du projet : pas de
  code à moitié fini, pas d'abstraction sans besoin concret). À lancer une
  fois le choix de fournisseur fait explicitement par l'utilisateur.
- **Gate 1 (n=50 CLV, ~fin octobre 2026), Phase C (palier d'attente jusqu'à
  n=100), Gate 2 (n=100, ~mi-février 2027) et Phases D/E/F : NON ATTAQUÉES,
  délibérément.** Ces jalons dépendent d'un volume de paris théoriques réels
  qui n'existe pas encore à la date de cette revue (2026-09-10) — le journal
  de production se remplit un match à la semaine via la routine hebdomadaire
  ci-dessous, pas en une session. Les fabriquer maintenant (avancer le
  calendrier, gonfler artificiellement le journal, ou construire M12-M19 par
  anticipation) reviendrait très exactement à ce que ce fichier interdit
  ailleurs : re-régler ou décider sur la base d'un signal qui n'existe pas
  encore. La routine de suivi mensuelle/trimestrielle ci-dessous reste le
  seul mécanisme qui fait avancer ces jalons.

## Commandes

```bash
pip install -r requirements.txt      # dépendances (scipy requis par model.py)
python pipeline.py --update          # tout mettre à jour (3 ligues x 9 saisons)
python pipeline.py --update --league E0 --season 2324   # une ligue/saison
python check.py                      # validation (code retour 0 si tout passe)
python backtest.py --tune|--run|--shuffle-test   # backtest M3 (voir backtest.py)
python report.py                     # rapport -> reports/m3_backtest.md
python backtest35.py --tune|--run|--shuffle-test # backtest M3.5 (pseudo-buts xG)
python report35.py                   # rapport -> reports/m35_backtest.md
python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
    --odds 1.85,3.6,4.4 --odds-date 2026-08-14   # prédiction production (M5)
python predict.py result --match "Arsenal-Chelsea" --actual 2-1   # enregistre un résultat
python pipeline.py --update && python predict.py sync-results  # résultats réels depuis football.db
python predict.py report             # rapport de calibration -> reports/production_calibration.md
python backtest_blend.py             # backtest du blend marché/modèle -> reports/m5_blend_backtest.md
python fatigue_signal_check.py       # le signal fatigue existe-t-il ? -> reports/fatigue_signal_check.md (réponse : non)
python devig_check.py                # proportionnel vs power vs Shin -> reports/devig_check.md (hors test)
python backtest_derived.py --tune|--run|--shuffle-test  # M7 (roadmap) : validation O/U 2.5 + BTTS
python report_derived.py             # rapport -> reports/derived_markets_backtest.md
python -m unittest discover -s tests # tests unitaires
```

## Architecture

- `db.py` — schéma SQLite (`data/football.db`) : tables `matches`
  (clé unique date+home+away) et `team_aliases`, upserts idempotents.
  Les upserts de matchs ne touchent jamais aux colonnes xG.
- `footballdata.py` — CSV football-data.co.uk avec cache dans
  `data/raw/football-data/` ; priorité cotes de clôture Pinnacle, repli
  moyenne du marché, puis ouverture (saison 2018-19, colonne `odds_source`).
  `expected_current_season(date)` déduit le code de saison du calendrier
  (bascule en juillet) — sert au garde-fou de `check.py`.
- `understat.py` — xG Understat, cache dans `data/raw/understat/`.
- `xgjoin.py` — jointure xG sur (date, home, away) après résolution
  d'alias, tolérance ±2 jours.
- `aliases.py` — seed de `team_aliases` (nom Understat → nom football-data).
- `pipeline.py` — CLI d'orchestration ; `check.py` — validation de la base.
- `model.py` — Dixon-Coles : MLE pondérée (gradient analytique), shrinkage
  ridge des équipes à faible historique, grille de scores 7×7 + probas 1N2.
  `backtest.py` expose aussi les trois démargeages (`demargin_proportional`,
  `demargin_power`, `demargin_shin`, registre `DEMARGIN_METHODS`) ; les
  backtests (M3/M3.5 et `backtest_blend.py`) gardent `power` pour leur colonne
  « Marché » — changer la définition du marché après lecture du test
  reviendrait à bouger la référence a posteriori, et `devig_check.py` montre de
  toute façon que le choix ne déplace pas le Brier. Seule la production passe à
  Shin.
- `backtest.py` — protocole walk-forward : `--tune` (fige ξ dans
  `data/xi_frozen.json`), `--run` (table `predictions`), `--shuffle-test`
  (anti-fuite). Le fichier ξ figé ne doit jamais être régénéré après le test.
- `report.py` — Brier/log-loss vs marché démargé power et baselines,
  calibration, verdicts → `reports/m3_backtest.md`.
- `bootstrap.py` — intervalles de confiance par bootstrap percentile, avec deux
  règles non négociables : rééchantillonnage **apparié** (modèle et marché sont
  notés sur les mêmes matchs, on tire des matchs — sans appariement l'IC de
  l'écart est massivement trop large) et **graine figée** (les rapports sont
  committés, ils doivent se régénérer à l'identique). `ci_mean`,
  `ci_relative_delta` (écart relatif au marché), `ci_gap_relative_delta`
  (différence entre deux écarts sur des groupes disjoints — sert à l'alerte
  périmées vs fraîches), `fmt_ci`, `excludes_zero`.
- `devig_check.py` — comparaison proportionnel / power / Shin sur les saisons
  HORS TEST (choisir un démargeage est un réglage) : Brier, log-loss, biais
  favori-longshot, calibration par tranche, IC appariés →
  `reports/devig_check.md`. Verdict actuel : aucun écart de Brier distinguable
  du bruit — Shin est retenu par rigueur, pas pour un gain mesuré.
- `backtest_derived.py` / `report_derived.py` — M7 (roadmap) : validation
  indépendante des marchés dérivés O/U 2,5 et BTTS, même protocole
  tune/validation/test + IC bootstrap que M3.5, sans retoucher aux
  hyperparamètres Dixon-Coles déjà figés (grille de score du modèle M3.5
  telle quelle) — seule une recalibration binaire par marché est réglée sur
  la validation, figée dans `data/derived_markets_frozen.json`. Écrit dans la
  table `predictions_derived` (`db.py`) → `reports/derived_markets_backtest.md`.
  BTTS n'a pas de cote marché dans football-data.co.uk : le critère « vs
  marché » y est explicitement marqué inapplicable, jamais simulé.
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
  `league` hors {E0,SP1,F1} → erreur. `no_odds_reason` (export ou
  `--no-odds-reason` en CLI, valeurs déclarables `not_yet_published` /
  `lookup_failed` / `margin_rejected`) journalisé dans `meta.no_odds_reason`
  quand aucune cote n'est fournie ; déduit à `slate_odds_ignored` si `--odds`
  est ignoré sur un `--fixture` répété, sinon `not_provided` — jamais deviné.
  Récapitulatif des matchs sans cote en fin de run `match`. `--lineup-adjustment`
  (voir M6 ci-dessus) ajuste λ post-fit sur composition confirmée, journalisé
  dans `meta.lineup_adjustment`. Sous-commande
  `--devig {proportional,power,shin}` choisit le démargeage des cotes (défaut
  `shin`, journalisé dans `meta.devig`) ; `--exposure-cap` borne l'exposition
  SIMULTANÉE de la semaine (défaut 15 %, cumul lu dans le journal sur le même
  lundi de référence, mises réduites d'un facteur commun et jamais tronquées,
  `meta.exposure_factor` + `bets[].stake_pct_uncapped`). M9 (roadmap) :
  `CORRELATED_EXPOSURE_MULTIPLIER` (1,5, verrouillé comme les autres
  paramètres de risque) compte ×1,5 dans la comptabilité du plafond les
  paris sur des matchs de la même semaine partageant une équipe (report de
  calendrier, double confrontation) — jamais dans les mises réellement
  posées, toujours réduites par le même facteur commun ; journalisé dans
  `meta.correlated_exposure`. Sous-commande
  `sync-results` : remplit `actual_score` (et `actual_ht`) des matchs passés depuis la table `matches`
  après résolution d'alias, tolérance ±2 jours sur la date (report de
  calendrier) ; ce qui reste introuvable est listé « en attente de données
  source » et jamais deviné. Le rapport ajoute une section par fraîcheur des
  cotes (alerte si le bucket périmées dérive de plus de 3 points relatifs vs le
  bucket fraîches, n ≥ 15 requis dans les deux, **et IC de l'écart excluant 0** —
  sans quoi l'alerte se déclencherait sur du bruit), une section ROI théorique
  (avertissement sous 100 paris réglés) et une section CLV (`clv_pct` par pari,
  posé par `sync-results` depuis `matches.odds_*` **uniquement si
  `odds_source = pinnacle_close`** ; les autres sont comptés par raison de
  refus — avertissement sous 20 paris avec clôture sharp). Chaque Δ vs marché
  (par mois et par bucket) est publié avec son IC bootstrap apparié. La colonne « Δ vs marché » des deux premières tables
  est un écart **relatif** — même formule que « Écart rel. marché » de
  `report35.py` — donc directement comparable au +1,78 % du backtest.
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
- `fatigue_signal_check.py` — vérifie AVANT toute calibration si le signal
  fatigue/congestion existe : walk-forward identique à
  `backtest.walk_forward` sur 1920+VALIDATION (jamais le TEST), résidu
  (buts réels - λ M3.5) par tranche de repos (`rest_bucket`, seuil ≤3j) sur
  4 axes attaque/défense × domicile/extérieur, z-test court vs reste.
  Verdict → `reports/fatigue_signal_check.md`. Résultat actuel : aucun
  signal détecté — la calibration (walk-forward tune/validation/test comme
  M3/M3.5) n'a donc PAS été construite, elle figerait du bruit. M8 (roadmap) :
  source calendrier complémentaire (coupes, C1/C3) investiguée, pas
  construite (deux sources candidates testées en connectivité réelle, toutes
  deux disproportionnées pour le gain attendu) →
  `reports/m8_congestion_source_investigation.md`.
- `.github/workflows/tests.yml` — CI : sur push/PR vers `main`, installe
  `requirements.txt`, lance `python -m unittest discover -s tests` puis
  `python check.py` ; échoue si l'un des deux retourne un code non nul.
  Pas de secret réseau requis (fixtures locales, `football.db` versionnée).
- `.github/workflows/weekly.yml` — automatise la partie « lundi suivant »
  de la routine de suivi ci-dessous : `pipeline.py --update` puis
  `predict.py sync-results` puis `predict.py report`, commit+push de
  `data/production_journal.json` et `reports/production_calibration.md`
  s'ils ont changé. Archive aussi `football.db` + le journal en artefact
  GitHub Actions (rétention 90 j) comme backup secondaire léger — ne sort
  pas du compte/repo GitHub unique, mais protège d'une corruption locale
  des fichiers suivis en histoire git. La génération des prédictions de la
  semaine (`predict.py match --fixture ...`) reste manuelle : dépend des
  cotes fraîches récupérées via le skill football-match-predictor
  (recherche web), pas automatisable sans source de cotes programmatique.

Périmètre : E0 (Premier League), SP1 (Liga), F1 (Ligue 1), 2018-19 à 2026-27.

**Passage de saison** : `SEASONS` et `CURRENT_SEASON` (footballdata.py) sont des
constantes à rallonger chaque été. L'oubli est silencieux — la saison en cours
n'est jamais téléchargée, donc `predict.py sync-results` ne trouve aucun résultat
et blâme la source (« absent de football.db ») alors que la vraie cause est la
liste figée. `check.py` compare désormais `expected_current_season()` (déduite du
calendrier) à `SEASONS`/`CURRENT_SEASON` et affiche un avertissement dès la
bascule, sans faire échouer le code retour (hors-saison ou publication tardive de
la source ne sont pas des erreurs).

## Routine de suivi (à partir de M5.1)

- **Chaque lundi** (génération des prédictions de la semaine, déjà en
  place) : `python predict.py match --fixture ...` pour E0/SP1/F1.
- **Chaque lundi suivant** (avant de regénérer les prédictions de la
  semaine d'après) : `python pipeline.py --update` puis
  `python predict.py sync-results` pour clore les matchs de la semaine
  précédente. **Automatisé** depuis `.github/workflows/weekly.yml` (cron
  lundi 06:00 UTC) — vérifier quand même le résumé "en attente de données
  source" dans le run Actions ou en relançant en local ; s'il grossit,
  creuser la source (football-data.co.uk en retard, alias manquant).
- **1er de chaque mois** : `python predict.py report`, committer
  reports/production_calibration.md, comparer le delta vs marché du mois
  au chiffre du backtest (+1,78 %, IC [+1,24 ; +2,34 %]) — et à son IC mensuel,
  pas au seul point. Si le delta réel est significativement
  pire (n ≥ 15) sur plusieurs mois consécutifs, ouvrir une entrée dans ce
  fichier documentant l'écart et son investigation — ne pas re-régler les
  hyperparamètres figés sur la base du monitoring de production seul (ce
  serait re-tuner sur ce qui devrait rester un test).
- **Trimestriel** : relire la section "Par fraîcheur des cotes" cumulée
  sur le trimestre. Le poids marché sur cotes périmées (base 92 % →
  plancher 28 %) ne doit être révisé qu'après un trimestre complet avec
  n ≥ 15 sur le bucket périmées, jamais sur un aperçu partiel.
- **Avant toute augmentation du plafond Kelly ou de la fraction** :
  exiger au minimum 100 paris réglés dans le ROI réel avec un ROI positif
  net de la marge — sinon rester sur les valeurs actuelles.
- **À chaque rapport mensuel, lire le CLV avant le ROI** : il converge plus
  vite (n ≥ 20 déjà indicatif) et dit si les paris pris ont ou non devancé le
  marché — un CLV moyen négatif sur un échantillon exploitable est un signal
  d'alerte plus rapide qu'un ROI qui restera non-informatif encore des mois.

## Conventions

- Les données (`data/`) ne sont pas versionnées ; la base se reconstruit
  entièrement avec `python pipeline.py --update`.
- Après toute modification du pipeline : relancer les tests puis `check.py`
  et n'intégrer que si le résultat global est OK (code retour 0).
