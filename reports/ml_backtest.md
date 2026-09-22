# GBM (LightGBM) — roadmap B1-B4, stacké sur Dixon-Coles + features/

⚠ **Statut honnête** — chantier neuf (2026-09-22), validé par le même protocole tune/validation/test + IC bootstrap que M3.5, mais sans l'ampleur de vérification (plusieurs mois, plusieurs itérations indépendantes) qui a précédé le verdict M3.5. Ce rapport ne remplace pas reports/m35_backtest.md — il le complète. Le staking Kelly de production reste basé sur M3.5 tant que ce chantier n'a pas atteint le même niveau de confiance (cf. CLAUDE.md, section override du 2026-09-22).

11138 matchs de test (saisons 2223, 2324, 2425, 2526), 7 ligues (E0, SP1, F1, E1, SP2, I2, N1), refit par frontière de saison (pas hebdomadaire — cf. backtest_ml.py). Hyperparamètres figés sur validation 2021+2122 : num_leaves = 7, learning_rate = 0.08, num_rounds = 80 (Brier validation 0.62273).

## Verdict global (agrégat toutes ligues — voir détail par ligue plus bas)

- Brier GBM (agrégat) : 0.60783
- Brier baseline fréquence (agrégat) : 0.64919
- Brier baseline uniforme (agrégat) : 0.66667
- **Δ Brier vs marché (agrégat, 11138 matchs avec cote de clôture) : +2.07 % [+1.68 ; +2.45 %] — IC exclut 0 — écart distinguable du bruit.**

## GBM vs M3.5 (comparaison directe, 4338 matchs communs aux 3 grandes ligues déjà validées par M3.5)

- Δ Brier GBM vs M3.5 (négatif = le GBM fait MIEUX que M3.5 sur ces matchs) : +0.46 % [-0.14 ; +1.06 %]
- Brier GBM 0.58650 vs Brier M3.5 0.58384

## Détail par ligue

*(ligue principale)*
### E0 (1520 matchs)

- Brier GBM : 0.58942
- Brier baseline fréquence : 0.64534
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 53.2%
- Log-loss : 0.98937
- AUC one-vs-rest (macro) : 0.654
- Δ Brier vs marché (démargé power, 1520 matchs avec cote) : +3.33 % [+2.22 ; +4.47 %]

*(ligue principale)*
### SP1 (1520 matchs)

- Brier GBM : 0.57691
- Brier baseline fréquence : 0.64097
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 53.8%
- Log-loss : 0.97203
- AUC one-vs-rest (macro) : 0.673
- Δ Brier vs marché (démargé power, 1520 matchs avec cote) : +1.46 % [+0.38 ; +2.56 %]

*(ligue principale)*
### F1 (1298 matchs)

- Brier GBM : 0.59431
- Brier baseline fréquence : 0.64694
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 52.1%
- Log-loss : 0.99696
- AUC one-vs-rest (macro) : 0.647
- Δ Brier vs marché (démargé power, 1298 matchs avec cote) : +1.92 % [+0.78 ; +3.06 %]

*(ligue secondaire, roadmap A3 — sans xG, jamais backtestée par M3.5)*
### E1 (2208 matchs)

- Brier GBM : 0.63183
- Brier baseline fréquence : 0.65091
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 46.1%
- Log-loss : 1.04934
- AUC one-vs-rest (macro) : 0.585
- Δ Brier vs marché (démargé power, 2208 matchs avec cote) : +1.61 % [+0.82 ; +2.43 %]

*(ligue secondaire, roadmap A3 — sans xG, jamais backtestée par M3.5)*
### SP2 (1848 matchs)

- Brier GBM : 0.63071
- Brier baseline fréquence : 0.64395
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 46.9%
- Log-loss : 1.04691
- AUC one-vs-rest (macro) : 0.579
- Δ Brier vs marché (démargé power, 1848 matchs avec cote) : +1.92 % [+1.05 ; +2.82 %]

*(ligue secondaire, roadmap A3 — sans xG, jamais backtestée par M3.5)*
### I2 (1520 matchs)

- Brier GBM : 0.64102
- Brier baseline fréquence : 0.65741
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 41.6%
- Log-loss : 1.06174
- AUC one-vs-rest (macro) : 0.584
- Δ Brier vs marché (démargé power, 1520 matchs avec cote) : +2.44 % [+1.49 ; +3.40 %]

*(ligue secondaire, roadmap A3 — sans xG, jamais backtestée par M3.5)*
### N1 (1224 matchs)

- Brier GBM : 0.56433
- Brier baseline fréquence : 0.64660
- Brier baseline uniforme : 0.66667
- Accuracy (classe la plus probable) : 54.9%
- Log-loss : 0.95100
- AUC one-vs-rest (macro) : 0.687
- Δ Brier vs marché (démargé power, 1224 matchs avec cote) : +2.10 % [+0.96 ; +3.29 %]

## Contrôle anti-fuite (shuffle-test)

- Saison de contrôle 2223 : Brier réel 0.61071 / permuté 0.65284 -> **dégradé (attendu, pas de fuite détectée)**

## Limites assumées, pas cachées

- Refit par frontière de saison (pas hebdomadaire comme Dixon-Coles) : plus grossier, compromis de vitesse documenté dans backtest_ml.py.
- Aucune xG sur les ligues secondaires (Understat ne les couvre pas) : le GBM s'appuie sur buts/tirs/tirs cadrés/corners réels pour elles, jamais une xG simulée.
- Hyperparamètres num_leaves/learning_rate/num_rounds réglés par grid search sur la validation ; les autres (feature_fraction, bagging, min_data_in_leaf) restent des valeurs par défaut de la littérature LightGBM, pas optimisées sur ce dataset.
- La possession n'existe dans aucune feature : football-data.co.uk ne la publie pas.

