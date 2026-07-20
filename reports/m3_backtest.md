# M3 — Backtest walk-forward Dixon-Coles vs marché

4338 matchs de test (saisons 2223, 2324, 2425, 2526), 3 ligues, refit hebdomadaire, ξ = 0.002 figé sur validation 2021+2122 (critère brier).

## Verdict

- ❌ Brier modèle à +2.47 % du marché (critère < +2 %)
- ✅ Bat les baselines : +8.9 % vs fréquences, +11.8 % vs uniforme (critère ≥ 3 % chacune)
- ✅ Calibration : pire tranche (n ≥ 300) à 4.1 pts d'écart (tolérance 5 pts)
- ✅ Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés

## Résultats agrégés (toutes saisons de test)

| Méthode | Brier | Log-loss | Bonne issue (argmax) |
| --- | --- | --- | --- |
| Modèle DC | 0.58781 | 0.98683 | 52.7 % |
| Marché | 0.57361 | 0.96501 | 54.4 % |
| Fréquences | 0.64535 | 1.06749 | 44.9 % |
| Uniforme | 0.66667 | 1.09861 | 44.9 % |

## Brier par ligue et saison de test

| Ligue | Saison | n | Modèle | Marché | Écart rel. | Fréquences | Uniforme |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E0 | 2223 | 380 | 0.59525 | 0.57106 | +4.24 % | 0.63627 | 0.66667 |
| E0 | 2324 | 380 | 0.54753 | 0.52494 | +4.30 % | 0.63724 | 0.66667 |
| E0 | 2425 | 380 | 0.59136 | 0.57543 | +2.77 % | 0.65534 | 0.66667 |
| E0 | 2526 | 380 | 0.61510 | 0.61037 | +0.77 % | 0.65701 | 0.66667 |
| SP1 | 2223 | 380 | 0.59407 | 0.58073 | +2.30 % | 0.63743 | 0.66667 |
| SP1 | 2324 | 380 | 0.57466 | 0.56513 | +1.69 % | 0.65016 | 0.66667 |
| SP1 | 2425 | 380 | 0.57789 | 0.55852 | +3.47 % | 0.64816 | 0.66667 |
| SP1 | 2526 | 380 | 0.58016 | 0.57017 | +1.75 % | 0.63276 | 0.66667 |
| F1 | 2223 | 380 | 0.58681 | 0.57574 | +1.92 % | 0.65038 | 0.66667 |
| F1 | 2324 | 306 | 0.62368 | 0.61103 | +2.07 % | 0.66037 | 0.66667 |
| F1 | 2425 | 306 | 0.57702 | 0.56382 | +2.34 % | 0.63693 | 0.66667 |
| F1 | 2526 | 306 | 0.59677 | 0.58374 | +2.23 % | 0.64302 | 0.66667 |

## Log-loss par ligue (test agrégé)

| Ligue | Modèle | Marché | Fréquences | Uniforme |
| --- | --- | --- | --- | --- |
| E0 | 0.98565 | 0.96046 | 1.06886 | 1.09861 |
| SP1 | 0.97865 | 0.95820 | 1.06358 | 1.09861 |
| F1 | 0.99780 | 0.97831 | 1.07047 | 1.09861 |

## Calibration du modèle (tranches de 5 pts, 3 probas par match)

| Tranche | n | Proba prédite moy. | Fréquence observée | Écart |
| --- | --- | --- | --- | --- |
| 0–5 % * | 22 | 3.9 % | 0.0 % | -3.9 pts |
| 5–10 % * | 260 | 8.0 % | 6.9 % | -1.0 pts |
| 10–15 % | 560 | 12.9 % | 9.8 % | -3.1 pts |
| 15–20 % | 1099 | 17.8 % | 17.2 % | -0.6 pts |
| 20–25 % | 2315 | 22.8 % | 23.3 % | +0.5 pts |
| 25–30 % | 2841 | 27.3 % | 27.1 % | -0.2 pts |
| 30–35 % | 1368 | 32.1 % | 29.7 % | -2.5 pts |
| 35–40 % | 937 | 37.5 % | 37.8 % | +0.2 pts |
| 40–45 % | 803 | 42.4 % | 44.8 % | +2.5 pts |
| 45–50 % | 796 | 47.4 % | 46.6 % | -0.8 pts |
| 50–55 % | 578 | 52.4 % | 54.3 % | +1.9 pts |
| 55–60 % | 463 | 57.4 % | 56.6 % | -0.8 pts |
| 60–65 % | 364 | 62.4 % | 66.5 % | +4.1 pts |
| 65–70 % * | 257 | 67.2 % | 68.9 % | +1.6 pts |
| 70–75 % * | 160 | 72.5 % | 73.1 % | +0.6 pts |
| 75–80 % * | 106 | 77.0 % | 84.0 % | +6.9 pts |
| 80–85 % * | 67 | 81.7 % | 85.1 % | +3.3 pts |
| 85–90 % * | 17 | 87.3 % | 100.0 % | +12.7 pts |
| 90–95 % * | 1 | 91.9 % | 100.0 % | +8.1 pts |

\* tranches sous n = 300, hors verdict. Tranches sur-confiantes : 2, sous-confiantes : 3 (sur 11).

## Grid search ξ (validation 2020-21 + 2021-22)

| ξ (jours⁻¹) | Brier validation |
| --- | --- |
| 0.0 | 0.60158 |
| 0.0005 | 0.60048 |
| 0.001 | 0.59969 |
| 0.0015 | 0.59922 |
| 0.002 | 0.59904 ← figé |
| 0.003 | 0.59935 |
| 0.005 | 0.60150 |
| 0.008 | 0.60573 |

## Test anti-fuite (labels permutés, saison 2223)

| Ligue | Brier réel | Brier permuté | Dégradé ? |
| --- | --- | --- | --- |
| E0 | 0.59525 | 0.63898 | oui |
| SP1 | 0.59407 | 0.66764 | oui |
| F1 | 0.58681 | 0.65103 | oui |
