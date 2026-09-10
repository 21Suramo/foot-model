# M7 (roadmap) — Validation indépendante des marchés dérivés (O/U 2,5, BTTS)

Marchés calculés depuis la MÊME grille de score que le modèle M3.5 déjà backtesté (`reports/m35_backtest.md`) — w/ξ/κ inchangés. Seule une recalibration binaire propre à chaque marché a été réglée ici, sur la même validation, avec la même interdiction de retoucher quoi que ce soit après lecture du test.

Réglages figés le 2026-09-10 : Over/Under 2,5 buts t = 0.678 (Brier validation 0.24678 -> 0.24598) ; BTTS (les deux équipes marquent) t = 0.500 (Brier validation 0.25011 -> 0.24903)

⚠ Température au bord de la plage autorisée (0.5–2.5) pour BTTS (les deux équipes marquent) : l'optimum réel est peut-être hors plage (aplatissement encore plus fort). Reste dans TEMP_BOUNDS par cohérence avec M3.5 plutôt que d'élargir la plage après avoir vu où l'optimiseur bute — à noter, pas à corriger rétroactivement.

## Over/Under 2,5 buts

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire. Grille de score du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ✅ Brier modèle à +1.81 % du marché [+1.16 ; +2.56 %] (critère < +2 %), sur 4336 match(s) avec cote O/U 2,5.
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

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire. Grille de score du modèle M3.5 déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée sur la validation 2021+2122.

### Verdict

- ➖ Comparaison au marché : **inapplicable** — aucune cote BTTS dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Pas de source, pas de critère fabriqué.
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
