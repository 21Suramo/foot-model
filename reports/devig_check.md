# Démargeage des cotes : proportionnel vs power vs Shin

4459 matchs avec cotes 1N2 complètes sur les saisons 1819, 1920, 2021, 2122 (burn-in + validation). **Les saisons de test (2223, 2324, 2425, 2526) sont exclues** : choisir une méthode de démargeage est un réglage, il ne se fait pas sur le jeu de test — même discipline que ξ, w et κ.

Aucun modèle n'intervient ici : on mesure uniquement la qualité des probabilités que l'on extrait des cotes du marché. Référence Brier hasard = 0.6667 (plus bas = mieux).

## Brier et log-loss par méthode

| Méthode | Brier | Log-loss | Δ Brier rel. vs power | IC 95 % de l'écart |
| --- | --- | --- | --- | --- |
| Proportionnel | 0.58077 | 0.97702 | +0.000 % | [-0.05 ; +0.05 %] |
| Power | 0.58077 | 0.97716 | référence | — |
| Shin | 0.58077 | 0.97715 | -0.001 % | [-0.02 ; +0.02 %] |

L'écart relatif est apparié (mêmes matchs, mêmes tirages bootstrap — 10000 rééchantillonnages, graine 20260909). Négatif = meilleur que power.

Sources de cotes de l'échantillon : `pinnacle_close` 4459.

## Biais favori-longshot (probas < 15 %)

Moyenne (proba prédite − issue observée) sur les probabilités inférieures à 15 %. Positif = la méthode surestime les issues improbables, c'est-à-dire qu'elle leur laisse une part de la marge du bookmaker au lieu de la retirer.

| Méthode | n probas < 15 % | Écart moyen prédit − observé |
| --- | --- | --- |
| Proportionnel | 1365 | -0.31 pts |
| Power | 1459 | -1.22 pts |
| Shin | 1440 | -1.11 pts |

## Calibration par tranche (3 probas par match)

| Tranche | n | Obs − prédit (Proportionnel) | Obs − prédit (Power) | Obs − prédit (Shin) |
| --- | --- | --- | --- | --- |
| 0–5 % * | 128 | +2.8 pts | +1.8 pts | +2.0 pts |
| 5–10 % | 494 | +0.6 pts | +2.2 pts | +2.1 pts |
| 10–15 % | 852 | -0.1 pts | +0.5 pts | +0.4 pts |
| 15–20 % | 1130 | -0.1 pts | -0.7 pts | -0.6 pts |
| 20–25 % | 1668 | -1.1 pts | -0.4 pts | -0.9 pts |
| 25–30 % | 2978 | -0.1 pts | -0.0 pts | -0.0 pts |
| 30–35 % | 1803 | +0.7 pts | +0.9 pts | +1.0 pts |
| 35–40 % | 870 | -1.5 pts | -1.6 pts | -1.5 pts |
| 40–45 % | 763 | +1.3 pts | +1.0 pts | +1.0 pts |
| 45–50 % | 647 | -0.8 pts | -0.7 pts | -0.6 pts |
| 50–55 % | 520 | +1.1 pts | -0.3 pts | +0.1 pts |
| 55–60 % | 471 | -3.2 pts | -3.1 pts | -3.5 pts |
| 60–65 % | 334 | +2.8 pts | +3.6 pts | +3.7 pts |
| 65–70 % * | 295 | +5.6 pts | +1.2 pts | +1.8 pts |
| 70–75 % * | 239 | +0.4 pts | +3.3 pts | +2.0 pts |
| 75–80 % * | 157 | -3.6 pts | -9.3 pts | -7.0 pts |
| 80–85 % * | 125 | +0.6 pts | -0.1 pts | +1.9 pts |
| 85–90 % * | 78 | +0.3 pts | -1.4 pts | -0.2 pts |
| 90–95 % * | 26 | +8.9 pts | -2.9 pts | -11.6 pts |
| 95–100 % * | 1 | — | +4.5 pts | — |

\* tranches sous n = 300 : lecture indicative. Le n affiché est celui de la tranche la plus fournie des trois méthodes (elles ne rangent pas exactement les mêmes probas dans les mêmes tranches).

## Verdict

- Meilleur Brier : **Shin** (0.58077).
- Proportionnel vs power : +0.000 % [-0.05 ; +0.05 %] — écart NON distinguable du bruit (IC contient 0).
- Shin vs power : -0.001 % [-0.02 ; +0.02 %] — écart NON distinguable du bruit (IC contient 0).
- Biais longshot le plus faible en valeur absolue : **Proportionnel** (-0.31 pts sur les probas < 15 %).

**Aucun écart de Brier n'est distinguable du bruit.** Sur une ligne sharp à marge faible, les trois méthodes produisent des probabilités trop proches pour que 4 459 matchs les départagent — c'est le résultat, pas une insuffisance de l'échantillon. Choisir Shin ne s'appuie donc PAS sur un gain mesuré : c'est un choix de rigueur (une marge dérivée d'un modèle explicite plutôt qu'un exposant libre), et il faut le présenter comme tel. Toute affirmation du type « Shin améliore les probas » serait ici une surinterprétation.

À noter, contre l'intuition habituelle : sur ces cotes (clôture Pinnacle, marge basse) le biais favori-longshot résiduel est NÉGATIF pour power comme pour Shin — les probas basses ressortent SOUS la fréquence observée, donc la correction sur-corrige légèrement plutôt que d'être insuffisante. Le démargeage proportionnel, censé être le plus biaisé, est ici le plus proche de zéro sur cette tranche. Conséquence pratique : ni power ni Shin ne fabrique de fausse value sur les outsiders — ils iraient plutôt en manquer.

Lecture générale : les trois méthodes partent des mêmes cotes et ne diffèrent que par la façon de retirer la marge ; les écarts de Brier attendus sont petits par construction. Ce qui les départage vraiment est la calibration sur les probas basses — une méthode qui y garde un biais fabrique de fausses « values » sur les outsiders, là précisément où `predict.py` déclenche ses mises Kelly aux plus grosses cotes.
