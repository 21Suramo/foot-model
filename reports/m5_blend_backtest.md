# M5 — Backtest du pont marché/modèle (blend de predict.py)

4338 matchs de test (saisons 2223, 2324, 2425, 2526) avec ouverture ET clôture disponibles ; 2280 matchs de validation. Config M3.5 figée (w = 0.6, ξ = 0.003, κ = 1.0, t = 1.077), refit hebdomadaire. Cotes vieillies par interpolation clôture↔ouverture (J-0 = clôture, J-7 = ouverture).

Poids du blend jamais réglé au préalable : predict.py fixe 65% à J-1, décroissance linéaire, 0 % à partir de J-5. Les barèmes alternatifs ci-dessous sont réglés sur la VALIDATION seule ; le test ne sert qu'à mesurer.

## Verdict

- ❌ FINAL jamais pire que le meilleur de {marché vieilli, modèle} : pire cas à J-5 (+9.5 millièmes de Brier vs le meilleur des deux).
- ❌ Le decay actuel n'est pas battu nettement par un barème réglé sur validation : Brier moyen decay 0.57948 vs meilleur poids fixe 0.57410 (w=1.00) vs oracle par âge 0.57410.
- ⚠️ Sur ce proxy, le marché vieilli bat le modèle pur à TOUS les âges testés (même l'ouverture J-7) : le blend ne peut pas améliorer le Brier, il ne fait que protéger contre une péremption plus sévère que l'ouverture — non capturée ici.

## Test : blend actuel par tranche d'âge des cotes

| Âge | Poids marché | Brier FINAL | Brier marché vieilli | Brier modèle pur | Meilleur des deux | FINAL − meilleur |
| --- | --- | --- | --- | --- | --- | --- |
| J-0 | 65% | 0.57531 | 0.57361 | 0.58384 | 0.57361 | +1.7 m‰ |
| J-1 | 65% | 0.57541 | 0.57361 | 0.58384 | 0.57361 | +1.8 m‰ |
| J-3 | 32% | 0.57902 | 0.57383 | 0.58384 | 0.57383 | +5.2 m‰ |
| J-5 | 0% | 0.58384 | 0.57433 | 0.58384 | 0.57433 | +9.5 m‰ |
| J-7 | 0% | 0.58384 | 0.57511 | 0.58384 | 0.57511 | +8.7 m‰ |

Référence marché frais (clôture, J-0) : Brier 0.57361. « m‰ » = millièmes de Brier (plus bas = mieux ; positif = FINAL moins bon que le meilleur des deux à cet âge).

## Barèmes alternatifs (réglés sur validation, mesurés sur test)

| Âge | Decay actuel (poids → Brier) | Oracle validation (poids → Brier) | Poids fixe w=1.00 → Brier |
| --- | --- | --- | --- |
| J-0 | 65% → 0.57531 | 100% → 0.57361 | 100% → 0.57361 |
| J-1 | 65% → 0.57541 | 100% → 0.57361 | 100% → 0.57361 |
| J-3 | 32% → 0.57902 | 100% → 0.57383 | 100% → 0.57383 |
| J-5 | 0% → 0.58384 | 100% → 0.57433 | 100% → 0.57433 |
| J-7 | 0% → 0.58384 | 100% → 0.57511 | 100% → 0.57511 |
| **Moyenne** | **0.57948** | **0.57410** | **0.57410** |

Marché vieilli moyen (aucun modèle) : 0.57410. Modèle pur : 0.58384.

## Grid search du poids fixe sur la validation (Brier moyen sur les âges)

| Poids marché w | 0.00 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 1.00 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Brier validation | 0.59567 | 0.59381 | 0.59212 | 0.59061 | 0.58928 | 0.58812 | 0.58713 | 0.58632 | 0.58568 | 0.58522 | 0.58493 ← |

Couverture : 0 matchs de test écartés faute d'ouverture, 0 faute de clôture ; validation 0 sans ouverture.

## Conclusion — optimal ou plausible ?

Sous ce proxy de vieillissement, **le blend actuel est PLAUSIBLE, pas Brier-optimal**. Le marché — même son ouverture (J-7) — reste plus précis que le modèle sur ces trois ligues, donc tout poids modèle > 0 dégrade légèrement le Brier quand la cote existe. Un poids fixe w=1.00 fait 0.93 % mieux en moyenne sur le test (0.57410 vs 0.57948).

Deux composantes du barème coûtent du Brier ici, et il faut les distinguer :

1. **Le poids de base 65 %** (J-0/J-1) dilue une cote fraîche encore excellente : +1.7 m‰ à J-0. Sur cotes *vérifiées* fraîches, il n'y a aucune raison Brier de descendre sous ~100 % marché.
2. **La coupure à J-5 (poids → 0, donc FINAL = modèle)** est en fait le point le PLUS coûteux du barème : +9.5 m‰, car le marché vieilli à J-5 (0.57433) bat toujours le modèle (0.58384). Sur ce proxy, couper vers le modèle n'est jamais justifié — un plancher de poids marché > 0 dominerait le 0.

Ce que le backtest NE peut PAS voir, et qui justifie malgré tout de garder un garde-fou : il compare deux vraies lignes de book (ouverture vs clôture), toutes deux sharp. Le garde-fou, lui, vise une cote **grattée sur le web, potentiellement mal recopiée, issue d'un book soft ou figée à J-3+** — strictement pire que l'ouverture, et non simulable avec football-data. Le decay échange donc un peu de Brier sur cotes fraîches contre une borne de sécurité quand la fraîcheur/qualité de la cote d'entrée n'est pas vérifiable.

**Recommandation actionnable** (sans re-toucher au test) :
- cote *vérifiée* fraîche et de book sérieux → monter le poids marché à ~90–100 % (le blend 65 % actuel laisse ~1.7 m‰ sur la table) ;
- remplacer la coupure sèche à J-5 par un **plancher** (p. ex. 20–30 % marché même au-delà de J-5) : garde le filet anti-péremption sans jeter une ligne encore informative ;
- réserver le poids modèle élevé aux cas où la cote est absente, invérifiable ou signalée douteuse (marge aberrante) — c'est là son vrai rôle, et ce proxy ne peut pas le chiffrer.
