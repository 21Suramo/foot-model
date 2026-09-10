# Roadmap "profit durable" A1 — Validation indépendante des marchés dérivés

Marchés calculés depuis la grille de score du modèle M3.5 déjà backtesté (`reports/m35_backtest.md`) — w/ξ/κ inchangés. Seule une recalibration binaire propre à chaque marché a été réglée ici, sur la même validation, avec la même interdiction de retoucher quoi que ce soit après lecture du test — appliquée MARCHÉ PAR MARCHÉ (`backtest_derived.tune` fusionne, n'écrase jamais un marché déjà figé). O/U 2,5 et BTTS restent sur la grille 7×7 exacte déjà publiée (racine M7 (roadmap)) ; tous les marchés ajoutés depuis (O/U 0,5/1,5/3,5/4,5, totaux par équipe, handicap asiatique) utilisent une grille 12×12 (`derived_markets.EXTENDED_MAX_GOALS`), jamais testée avant ce chantier — élargir la base avant de lire un test n'est pas le re-réglage que le protocole interdit.

Réglages figés le 2026-09-10 : Over/Under 2,5 buts t = 0.678 (Brier validation 0.24678 -> 0.24598) ; BTTS (les deux équipes marquent) t = 0.500 (Brier validation 0.25011 -> 0.24903) ; Over/Under 0,5 but t = 1.038 (Brier validation 0.07062 -> 0.07058) ; Over/Under 1,5 but t = 1.011 (Brier validation 0.18849 -> 0.18849) ; Over/Under 3,5 buts t = 0.907 (Brier validation 0.20204 -> 0.20172) ; Over/Under 4,5 buts t = 0.948 (Brier validation 0.12119 -> 0.12107) ; Domicile marque plus de 1,5 but t = 1.066 (Brier validation 0.22034 -> 0.22026) ; Extérieur marque plus de 1,5 but t = 0.917 (Brier validation 0.21161 -> 0.21141) ; Handicap asiatique (ligne du marché, domicile couvre) t = 0.500 (Brier validation 0.25410 -> 0.24978)

⚠ Température au bord de la plage autorisée (0.5–2.5) pour BTTS (les deux équipes marquent), Handicap asiatique (ligne du marché, domicile couvre) : l'optimum réel est peut-être hors plage (aplatissement encore plus fort). Reste dans TEMP_BOUNDS par cohérence avec M3.5 plutôt que d'élargir la plage après avoir vu où l'optimiseur bute — à noter, pas à corriger rétroactivement.

## Over/Under 2,5 buts

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 7×7 legacy, celle de M3.5). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ✅ Brier modèle à +1.81 % du marché [+1.16 ; +2.56 %] (critère < +2 %), sur 4336 match(s) avec cote « Over/Under 2,5 buts ».
- ❌ Bat les baselines : +2.5 % vs fréquences, +3.2 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 3.7 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.24208 | 4338 |
| Modèle (brut, avant recalibration) | 0.24170 | 4338 |
| Marché (démargé proportionnel) | 0.23778 | 4336 |
| Fréquences (walk-forward) | 0.24839 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 30–35 % * | 7 | 33.4 % | 71.4 % | +38.0 pts |
| 35–40 % * | 124 | 38.2 % | 32.3 % | -6.0 pts |
| 40–45 % | 461 | 43.1 % | 42.5 % | -0.6 pts |
| 45–50 % | 972 | 47.7 % | 46.6 % | -1.1 pts |
| 50–55 % | 1233 | 52.6 % | 53.4 % | +0.9 pts |
| 55–60 % | 1005 | 57.3 % | 59.5 % | +2.2 pts |
| 60–65 % | 417 | 62.0 % | 65.7 % | +3.7 pts |
| 65–70 % * | 103 | 66.9 % | 68.0 % | +1.1 pts |
| 70–75 % * | 16 | 71.8 % | 81.2 % | +9.4 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Marché | Écart rel. marché | Fréquences |
| --- | --- | --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.24099 | 0.23824 | +1.15 % | 0.24947 |
| E0 | 2324 | 380 | 0.23422 | 0.22669 | +3.32 % | 0.24102 |
| E0 | 2425 | 380 | 0.24206 | 0.24204 | +0.01 % | 0.24612 |
| E0 | 2526 | 380 | 0.24652 | 0.24497 | +0.64 % | 0.24758 |
| SP1 | 2223 | 380 | 0.24518 | 0.23992 | +2.19 % | 0.25004 |
| SP1 | 2324 | 380 | 0.23619 | 0.23106 | +2.22 % | 0.24856 |
| SP1 | 2425 | 380 | 0.23950 | 0.23323 | +2.69 % | 0.25061 |
| SP1 | 2526 | 380 | 0.24313 | 0.23967 | +1.44 % | 0.25135 |
| F1 | 2223 | 380 | 0.24459 | 0.24082 | +1.57 % | 0.24913 |
| F1 | 2324 | 306 | 0.24643 | 0.24332 | +1.28 % | 0.24944 |
| F1 | 2425 | 306 | 0.24152 | 0.23843 | +1.30 % | 0.24838 |
| F1 | 2526 | 306 | 0.24616 | 0.23576 | +4.41 % | 0.24943 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.24099 | 0.24571 | oui |
| SP1 | 0.24518 | 0.25095 | oui |
| F1 | 0.24459 | 0.25531 | oui |

## BTTS (les deux équipes marquent)

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 7×7 legacy, celle de M3.5). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote BTTS (les deux équipes marquent) dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ❌ Bat les baselines : +0.8 % vs fréquences, +1.4 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 4.8 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.24647 | 4338 |
| Modèle (brut, avant recalibration) | 0.24601 | 4338 |
| Fréquences (walk-forward) | 0.24847 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 40–45 % * | 119 | 43.9 % | 46.2 % | +2.3 pts |
| 45–50 % | 1220 | 48.2 % | 49.0 % | +0.8 pts |
| 50–55 % | 2147 | 52.4 % | 55.9 % | +3.5 pts |
| 55–60 % | 825 | 56.6 % | 61.3 % | +4.8 pts |
| 60–65 % * | 27 | 60.9 % | 59.3 % | -1.6 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.24916 | 0.25006 |
| E0 | 2324 | 380 | 0.24225 | 0.24683 |
| E0 | 2425 | 380 | 0.24626 | 0.24676 |
| E0 | 2526 | 380 | 0.24509 | 0.24718 |
| SP1 | 2223 | 380 | 0.24724 | 0.25010 |
| SP1 | 2324 | 380 | 0.24546 | 0.25019 |
| SP1 | 2425 | 380 | 0.24703 | 0.24966 |
| SP1 | 2526 | 380 | 0.24559 | 0.24862 |
| F1 | 2223 | 380 | 0.24648 | 0.24633 |
| F1 | 2324 | 306 | 0.24891 | 0.24865 |
| F1 | 2425 | 306 | 0.24583 | 0.24641 |
| F1 | 2526 | 306 | 0.24929 | 0.25101 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.24916 | 0.24930 | oui |
| SP1 | 0.24724 | 0.24998 | oui |
| F1 | 0.24648 | 0.25541 | oui |

## Over/Under 0,5 but

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote Over/Under 0,5 but dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ❌ Bat les baselines : +0.8 % vs fréquences, +77.5 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 0.7 pts d'écart (tolérance 5 pts)
- ➖ Anti-fuite : pas de dégradation nette sur les 3 ligues, mais limite CONNUE de la méthode sur ce marché — base réelle ≈ 94 % sur les saisons de test (over 0,5) : très peu de variance pour la permutation à détruire, le test manque de puissance sur ce marché précis. Pas une fuite prouvée, mais pas exclue non plus : la certitude anti-fuite vient des marchés où la dégradation EST nette sur les 3 ligues (ou25, btts, ou35, totaux par équipe).

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.05621 | 4338 |
| Modèle (brut, avant recalibration) | 0.05625 | 4338 |
| Fréquences (walk-forward) | 0.05667 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 80–85 % * | 8 | 83.8 % | 100.0 % | +16.2 pts |
| 85–90 % | 305 | 88.6 % | 88.2 % | -0.4 pts |
| 90–95 % | 2323 | 93.0 % | 93.5 % | +0.5 pts |
| 95–100 % | 1702 | 96.3 % | 95.6 % | -0.7 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.05696 | 0.05690 |
| E0 | 2324 | 380 | 0.02807 | 0.02904 |
| E0 | 2425 | 380 | 0.04046 | 0.04053 |
| E0 | 2526 | 380 | 0.06655 | 0.06627 |
| SP1 | 2223 | 380 | 0.06352 | 0.06408 |
| SP1 | 2324 | 380 | 0.07435 | 0.07496 |
| SP1 | 2425 | 380 | 0.05114 | 0.05293 |
| SP1 | 2526 | 380 | 0.03806 | 0.03935 |
| F1 | 2223 | 380 | 0.06320 | 0.06379 |
| F1 | 2324 | 306 | 0.08081 | 0.08072 |
| F1 | 2425 | 306 | 0.04412 | 0.04438 |
| F1 | 2526 | 306 | 0.07295 | 0.07237 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.05696 | 0.03132 | NON — FUITE PROBABLE |
| SP1 | 0.06352 | 0.06596 | oui |
| F1 | 0.06320 | 0.08552 | oui |

## Over/Under 1,5 but

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote Over/Under 1,5 but dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ❌ Bat les baselines : +1.7 % vs fréquences, +30.4 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 3.6 pts d'écart (tolérance 5 pts)
- ❌ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.17391 | 4338 |
| Modèle (brut, avant recalibration) | 0.17389 | 4338 |
| Fréquences (walk-forward) | 0.17684 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 50–55 % * | 6 | 53.2 % | 100.0 % | +46.8 pts |
| 55–60 % * | 53 | 58.5 % | 60.4 % | +1.9 pts |
| 60–65 % * | 157 | 62.9 % | 58.6 % | -4.3 pts |
| 65–70 % | 450 | 68.0 % | 71.6 % | +3.6 pts |
| 70–75 % | 827 | 72.7 % | 72.4 % | -0.2 pts |
| 75–80 % | 1160 | 77.6 % | 77.7 % | +0.1 pts |
| 80–85 % | 1136 | 82.3 % | 80.2 % | -2.2 pts |
| 85–90 % | 474 | 86.9 % | 85.9 % | -1.0 pts |
| 90–95 % * | 74 | 91.5 % | 90.5 % | -1.0 pts |
| 95–100 % * | 1 | 95.2 % | 100.0 % | +4.8 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.18639 | 0.18530 |
| E0 | 2324 | 380 | 0.12228 | 0.12671 |
| E0 | 2425 | 380 | 0.15229 | 0.15261 |
| E0 | 2526 | 380 | 0.16630 | 0.16627 |
| SP1 | 2223 | 380 | 0.20396 | 0.20704 |
| SP1 | 2324 | 380 | 0.20039 | 0.20352 |
| SP1 | 2425 | 380 | 0.19456 | 0.20007 |
| SP1 | 2526 | 380 | 0.16270 | 0.17415 |
| F1 | 2223 | 380 | 0.16766 | 0.17191 |
| F1 | 2324 | 306 | 0.18118 | 0.18513 |
| F1 | 2425 | 306 | 0.14658 | 0.14983 |
| F1 | 2526 | 306 | 0.20466 | 0.20044 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.18639 | 0.15244 | NON — FUITE PROBABLE |
| SP1 | 0.20396 | 0.20418 | oui |
| F1 | 0.16766 | 0.20535 | oui |

## Over/Under 3,5 buts

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote Over/Under 3,5 buts dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ❌ Bat les baselines : +1.7 % vs fréquences, +17.6 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ❌ Calibration : pire tranche (n ≥ 300) à 5.1 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.20591 | 4338 |
| Modèle (brut, avant recalibration) | 0.20530 | 4338 |
| Fréquences (walk-forward) | 0.20956 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 10–15 % * | 17 | 13.8 % | 29.4 % | +15.6 pts |
| 15–20 % * | 172 | 17.9 % | 15.7 % | -2.2 pts |
| 20–25 % | 540 | 22.8 % | 21.5 % | -1.4 pts |
| 25–30 % | 856 | 27.6 % | 24.2 % | -3.4 pts |
| 30–35 % | 931 | 32.5 % | 29.0 % | -3.5 pts |
| 35–40 % | 870 | 37.4 % | 33.9 % | -3.5 pts |
| 40–45 % | 551 | 42.3 % | 37.2 % | -5.1 pts |
| 45–50 % * | 250 | 47.1 % | 47.6 % | +0.5 pts |
| 50–55 % * | 103 | 52.1 % | 39.8 % | -12.3 pts |
| 55–60 % * | 34 | 57.3 % | 52.9 % | -4.4 pts |
| 60–65 % * | 11 | 61.9 % | 45.5 % | -16.5 pts |
| 65–70 % * | 3 | 66.2 % | 33.3 % | -32.9 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.21076 | 0.21748 |
| E0 | 2324 | 380 | 0.24817 | 0.26139 |
| E0 | 2425 | 380 | 0.22833 | 0.22544 |
| E0 | 2526 | 380 | 0.21005 | 0.20512 |
| SP1 | 2223 | 380 | 0.17612 | 0.17548 |
| SP1 | 2324 | 380 | 0.18507 | 0.19843 |
| SP1 | 2425 | 380 | 0.17534 | 0.18222 |
| SP1 | 2526 | 380 | 0.19139 | 0.19022 |
| F1 | 2223 | 380 | 0.20564 | 0.20687 |
| F1 | 2324 | 306 | 0.20150 | 0.20500 |
| F1 | 2425 | 306 | 0.22290 | 0.23196 |
| F1 | 2526 | 306 | 0.22103 | 0.22080 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.21076 | 0.24273 | oui |
| SP1 | 0.17612 | 0.19340 | oui |
| F1 | 0.20564 | 0.21006 | oui |

## Over/Under 4,5 buts

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote Over/Under 4,5 buts dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ❌ Bat les baselines : +1.3 % vs fréquences, +49.5 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 4.0 pts d'écart (tolérance 5 pts)
- ➖ Anti-fuite : pas de dégradation nette sur les 3 ligues, mais limite CONNUE de la méthode sur ce marché — base réelle ≈ 15 % sur les saisons de test (over 4,5) : même limite que ou05, à l'autre bout de la distribution. Pas une fuite prouvée, mais pas exclue non plus : la certitude anti-fuite vient des marchés où la dégradation EST nette sur les 3 ligues (ou25, btts, ou35, totaux par équipe).

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.12617 | 4338 |
| Modèle (brut, avant recalibration) | 0.12571 | 4338 |
| Fréquences (walk-forward) | 0.12788 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 0–5 % * | 8 | 4.3 % | 0.0 % | -4.3 pts |
| 5–10 % | 471 | 8.2 % | 10.0 % | +1.8 pts |
| 10–15 % | 1200 | 12.6 % | 10.9 % | -1.7 pts |
| 15–20 % | 1218 | 17.5 % | 13.8 % | -3.7 pts |
| 20–25 % | 839 | 22.2 % | 18.2 % | -4.0 pts |
| 25–30 % | 404 | 27.2 % | 25.0 % | -2.2 pts |
| 30–35 % * | 128 | 32.3 % | 25.0 % | -7.3 pts |
| 35–40 % * | 45 | 37.1 % | 33.3 % | -3.7 pts |
| 40–45 % * | 18 | 41.7 % | 22.2 % | -19.4 pts |
| 45–50 % * | 6 | 46.9 % | 33.3 % | -13.5 pts |
| 50–55 % * | 1 | 50.7 % | 0.0 % | -50.7 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.13731 | 0.14064 |
| E0 | 2324 | 380 | 0.17585 | 0.18027 |
| E0 | 2425 | 380 | 0.13702 | 0.13124 |
| E0 | 2526 | 380 | 0.11703 | 0.11323 |
| SP1 | 2223 | 380 | 0.09508 | 0.09431 |
| SP1 | 2324 | 380 | 0.12479 | 0.13480 |
| SP1 | 2425 | 380 | 0.10655 | 0.11442 |
| SP1 | 2526 | 380 | 0.11388 | 0.11438 |
| F1 | 2223 | 380 | 0.11748 | 0.11819 |
| F1 | 2324 | 306 | 0.10707 | 0.10896 |
| F1 | 2425 | 306 | 0.14143 | 0.14688 |
| F1 | 2526 | 306 | 0.14318 | 0.13956 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.13731 | 0.15752 | oui |
| SP1 | 0.09508 | 0.09481 | NON — FUITE PROBABLE |
| F1 | 0.11748 | 0.10394 | NON — FUITE PROBABLE |

## Domicile marque plus de 1,5 but

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote Domicile marque plus de 1,5 but dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ✅ Bat les baselines : +8.8 % vs fréquences, +9.8 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 4.7 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.22546 | 4338 |
| Modèle (brut, avant recalibration) | 0.22553 | 4338 |
| Fréquences (walk-forward) | 0.24718 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 5–10 % * | 1 | 9.4 % | 0.0 % | -9.4 pts |
| 10–15 % * | 14 | 13.4 % | 28.6 % | +15.2 pts |
| 15–20 % * | 80 | 18.2 % | 20.0 % | +1.8 pts |
| 20–25 % * | 210 | 22.8 % | 24.3 % | +1.4 pts |
| 25–30 % | 382 | 27.8 % | 27.5 % | -0.3 pts |
| 30–35 % | 525 | 32.7 % | 33.0 % | +0.2 pts |
| 35–40 % | 515 | 37.6 % | 34.2 % | -3.4 pts |
| 40–45 % | 519 | 42.5 % | 43.7 % | +1.2 pts |
| 45–50 % | 506 | 47.4 % | 42.7 % | -4.7 pts |
| 50–55 % | 416 | 52.4 % | 49.8 % | -2.6 pts |
| 55–60 % | 347 | 57.4 % | 57.6 % | +0.2 pts |
| 60–65 % * | 298 | 62.5 % | 61.7 % | -0.7 pts |
| 65–70 % * | 213 | 67.2 % | 68.5 % | +1.4 pts |
| 70–75 % * | 149 | 72.3 % | 72.5 % | +0.2 pts |
| 75–80 % * | 88 | 77.2 % | 76.1 % | -1.0 pts |
| 80–85 % * | 51 | 82.2 % | 74.5 % | -7.7 pts |
| 85–90 % * | 22 | 87.3 % | 95.5 % | +8.1 pts |
| 90–95 % * | 2 | 91.1 % | 100.0 % | +8.9 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.22288 | 0.24902 |
| E0 | 2324 | 380 | 0.22344 | 0.25726 |
| E0 | 2425 | 380 | 0.22560 | 0.24705 |
| E0 | 2526 | 380 | 0.24052 | 0.25084 |
| SP1 | 2223 | 380 | 0.22812 | 0.24559 |
| SP1 | 2324 | 380 | 0.21854 | 0.24259 |
| SP1 | 2425 | 380 | 0.20868 | 0.23723 |
| SP1 | 2526 | 380 | 0.21987 | 0.25007 |
| F1 | 2223 | 380 | 0.21499 | 0.24326 |
| F1 | 2324 | 306 | 0.23098 | 0.24537 |
| F1 | 2425 | 306 | 0.23475 | 0.24992 |
| F1 | 2526 | 306 | 0.24355 | 0.24837 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.22288 | 0.25429 | oui |
| SP1 | 0.22812 | 0.24740 | oui |
| F1 | 0.21499 | 0.25050 | oui |

## Extérieur marque plus de 1,5 but

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote Extérieur marque plus de 1,5 but dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
- ✅ Bat les baselines : +6.5 % vs fréquences, +15.0 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 2.8 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.21246 | 4338 |
| Modèle (brut, avant recalibration) | 0.21234 | 4338 |
| Fréquences (walk-forward) | 0.22721 | 4338 |
| Uniforme (0,5) | 0.25000 | 4338 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 5–10 % * | 1 | 10.0 % | 0.0 % | -10.0 pts |
| 10–15 % * | 46 | 13.4 % | 6.5 % | -6.8 pts |
| 15–20 % * | 249 | 18.0 % | 18.9 % | +0.8 pts |
| 20–25 % | 495 | 22.6 % | 21.2 % | -1.4 pts |
| 25–30 % | 700 | 27.6 % | 24.7 % | -2.8 pts |
| 30–35 % | 712 | 32.4 % | 31.5 % | -1.0 pts |
| 35–40 % | 628 | 37.4 % | 36.9 % | -0.5 pts |
| 40–45 % | 497 | 42.3 % | 42.5 % | +0.1 pts |
| 45–50 % | 377 | 47.2 % | 44.6 % | -2.7 pts |
| 50–55 % * | 295 | 52.4 % | 50.5 % | -1.9 pts |
| 55–60 % * | 168 | 57.1 % | 59.5 % | +2.4 pts |
| 60–65 % * | 93 | 62.2 % | 63.4 % | +1.2 pts |
| 65–70 % * | 50 | 67.3 % | 70.0 % | +2.7 pts |
| 70–75 % * | 20 | 72.1 % | 55.0 % | -17.1 pts |
| 75–80 % * | 5 | 77.9 % | 60.0 % | -17.9 pts |
| 80–85 % * | 1 | 81.2 % | 100.0 % | +18.8 pts |
| 85–90 % * | 1 | 85.6 % | 100.0 % | +14.4 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Fréquences |
| --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.21724 | 0.22562 |
| E0 | 2324 | 380 | 0.21992 | 0.24716 |
| E0 | 2425 | 380 | 0.22904 | 0.24624 |
| E0 | 2526 | 380 | 0.21780 | 0.22617 |
| SP1 | 2223 | 380 | 0.18116 | 0.19942 |
| SP1 | 2324 | 380 | 0.20796 | 0.22603 |
| SP1 | 2425 | 380 | 0.19979 | 0.21838 |
| SP1 | 2526 | 380 | 0.20661 | 0.21314 |
| F1 | 2223 | 380 | 0.21283 | 0.23159 |
| F1 | 2324 | 306 | 0.21787 | 0.23304 |
| F1 | 2425 | 306 | 0.22106 | 0.23896 |
| F1 | 2526 | 306 | 0.22298 | 0.22345 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.21724 | 0.23379 | oui |
| SP1 | 0.18116 | 0.21168 | oui |
| F1 | 0.21283 | 0.22591 | oui |

## Handicap asiatique (ligne du marché, domicile couvre)

4067 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire (grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée avant ce chantier — cf. backtest_derived.py). Grille de score dérivée du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

⚠ Push exclus du Brier (remboursement, ni gagnant ni perdant — possible seulement sur ligne entière). model_p/market_p sont des probabilités CONDITIONNELLES « domicile couvre sachant pas de push » : avec seulement 2 cotes de marché (pas de 3e cote « push » chez football-data.co.uk), c'est la seule quantité comparable en face. La colonne « Fréquences » vaut 0,5 par construction ici (freq_p n'est pas une baseline pertinente : la ligne est choisie par le marché précisément pour équilibrer les deux issues) — c'est le baseline uniforme (0,5) qui joue ce rôle, et il vaut donc la même chose deux fois de suite ci-dessous, volontairement.

### Verdict

- ✅ Brier modèle à -0.11 % du marché [-0.64 ; +0.42 %] (critère < +2 %), sur 4067 match(s) avec cote « Handicap asiatique (ligne du marché, domicile couvre) ».
- ❌ Bat les baselines : +0.3 % vs fréquences, +0.3 % vs uniforme (0,5) (critère ≥ 3 % chacune)
- ❌ Calibration : pire tranche (n ≥ 300) à 5.3 pts d'écart (tolérance 5 pts)
- ➖ Anti-fuite : pas de dégradation nette sur les 3 ligues, mais limite CONNUE de la méthode sur ce marché — la permutation réassigne des scores réels entre matchs de la même ligue (multiset de buts domicile/extérieur préservé, juste réassigné) : le biais domicile GLOBAL survit donc à la permutation, alors que la ligne de handicap est fixée précisément pour l'annuler — il ne reste à détruire que la dépendance fine à la paire d'équipes, sur laquelle le test a structurellement moins de prise. Pas une fuite prouvée, mais pas exclue non plus : la certitude anti-fuite vient des marchés où la dégradation EST nette sur les 3 ligues (ou25, btts, ou35, totaux par équipe).

### Résultats agrégés

| Méthode | Brier | n |
| --- | --- | --- |
| Modèle (recalibré) | 0.24920 | 4067 |
| Modèle (brut, avant recalibration) | 0.25200 | 4067 |
| Marché (démargé proportionnel) — conditionnel, push exclu | 0.24947 | 4067 |
| Fréquences (walk-forward) | 0.25000 | 4067 |
| Uniforme (0,5) | 0.25000 | 4067 |

### Calibration (tranches de 5 pts)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 30–35 % * | 5 | 34.0 % | 60.0 % | +26.0 pts |
| 35–40 % * | 40 | 38.4 % | 50.0 % | +11.6 pts |
| 40–45 % | 429 | 43.2 % | 44.5 % | +1.3 pts |
| 45–50 % | 1529 | 47.7 % | 46.2 % | -1.5 pts |
| 50–55 % | 1510 | 52.2 % | 53.0 % | +0.8 pts |
| 55–60 % | 507 | 56.7 % | 51.5 % | -5.3 pts |
| 60–65 % * | 47 | 61.3 % | 57.4 % | -3.9 pts |

\* tranches sous n = 300, hors verdict.

### Par ligue et saison de test

| Ligue | Saison | n | Modèle | Marché | Écart rel. marché | Fréquences |
| --- | --- | --- | --- | --- | --- | --- |
| E0 | 2223 | 359 | 0.24889 | 0.24881 | +0.03 % | 0.25000 |
| E0 | 2324 | 352 | 0.25066 | 0.24968 | +0.39 % | 0.25000 |
| E0 | 2425 | 362 | 0.25101 | 0.24847 | +1.02 % | 0.25000 |
| E0 | 2526 | 354 | 0.24679 | 0.25279 | -2.37 % | 0.25000 |
| SP1 | 2223 | 355 | 0.24982 | 0.24755 | +0.92 % | 0.25000 |
| SP1 | 2324 | 356 | 0.24729 | 0.24860 | -0.53 % | 0.25000 |
| SP1 | 2425 | 356 | 0.25027 | 0.25053 | -0.10 % | 0.25000 |
| SP1 | 2526 | 352 | 0.24751 | 0.24919 | -0.67 % | 0.25000 |
| F1 | 2223 | 352 | 0.24810 | 0.24783 | +0.11 % | 0.25000 |
| F1 | 2324 | 287 | 0.25205 | 0.25302 | -0.38 % | 0.25000 |
| F1 | 2425 | 292 | 0.25017 | 0.24871 | +0.59 % | 0.25000 |
| F1 | 2526 | 290 | 0.24830 | 0.24886 | -0.22 % | 0.25000 |

### Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.24889 | 0.22281 | NON — FUITE PROBABLE |
| SP1 | 0.24982 | 0.21711 | NON — FUITE PROBABLE |
| F1 | 0.24810 | 0.22211 | NON — FUITE PROBABLE |

## Top-k scores exacts (diagnostic, pas un marché coté)

football-data.co.uk ne cote aucun score exact : rien à comparer à un marché ici. Ce n'est qu'un diagnostic de la grille déjà en production (grille 7×7, `match_model.py`), sans recalibration ni paramètre à figer — pas soumis au protocole tune/validation/test.

| k | Hit-rate modèle | Hit-rate fréquence (walk-forward) |
| --- | --- | --- |
| 1 | 12.8 % | 12.0 % |
| 3 | 33.6 % | 30.7 % |
| 5 | 49.9 % | 44.1 % |

4338 matchs de test.
