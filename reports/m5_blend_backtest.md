# M5 — Backtest du pont marché/modèle (blend de predict.py)

4338 matchs de test (saisons 2223, 2324, 2425, 2526) avec ouverture ET clôture disponibles ; 2280 matchs de validation. Config M3.5 figée (w = 0.6, ξ = 0.003, κ = 1.0, t = 1.077), refit hebdomadaire. Cotes vieillies par interpolation clôture↔ouverture (J-0 = clôture, J-7 = ouverture).

**Barème actuel (après fix M5)** : poids marché de base 92% sur cotes fraîches, décroissance linéaire vers un plancher de 28% à partir de J-5. **Ancien barème** (colonne « legacy ») : 65% puis coupure sèche à 0 % dès J-5. Les barèmes oracle/fixe sont réglés sur la VALIDATION seule ; le test ne sert qu'à mesurer.

> ⚠️ **Ne pas surinterpréter ces chiffres.** Le proxy vieillit les cotes par interpolation ouverture↔clôture : deux vraies lignes de book, toutes deux *sharp*. Il SOUS-ESTIME donc la péremption réelle que le garde-fou vise (cote scrapée sur le web, mal recopiée, figée à J-3+, book soft), qui est strictement pire qu'une ouverture et non simulable avec football-data. Le gain Brier mesuré ici est un **plancher** de l'utilité du modèle, pas sa vraie valeur en conditions réelles — n'en conclus pas « le modèle ne sert à rien ».

## Verdict

- ✅ Le fix améliore strictement l'ancien barème : Brier moyen 0.57673 vs 0.57948 → **+0.47 %**, et aucune régression aux 5 âges testés.
- ✅ C'est **~51 % du gain théorique maximal** (0.93 % jusqu'au 100 % marché pur, 0.57410). Les ~49 % restants (+0.46 %) sont l'**assurance conservée volontairement** (base 92% < 100 %, plancher 28%) contre une cote fraîche mal récupérée. Le « ~0,9 % » n'est atteignable qu'en supprimant TOUTE assurance (100 % marché à tout âge) — ce que le cahier des charges exclut.
- ⚠️ Sur ce proxy, le marché vieilli bat le modèle pur (Brier 0.58384) à TOUS les âges, même l'ouverture J-7 : le blend ne peut donc pas *améliorer* le Brier vs le marché seul, il ne fait qu'en garder l'essentiel tout en assurant contre une péremption plus sévère — non capturée ici (voir l'avertissement).

## Test : nouveau barème vs ancien, par tranche d'âge des cotes

| Âge | Poids (new→legacy) | Brier FINAL | Brier legacy | Brier marché vieilli | Brier modèle pur | FINAL − meilleur des deux |
| --- | --- | --- | --- | --- | --- | --- |
| J-0 | 92% → 65% | 0.57382 | 0.57531 | 0.57361 | 0.58384 | +0.2 m‰ |
| J-1 | 92% → 65% | 0.57385 | 0.57541 | 0.57361 | 0.58384 | +0.2 m‰ |
| J-3 | 60% → 32% | 0.57612 | 0.57902 | 0.57383 | 0.58384 | +2.3 m‰ |
| J-5 | 28% → 0% | 0.57982 | 0.58384 | 0.57433 | 0.58384 | +5.5 m‰ |
| J-7 | 28% → 0% | 0.58006 | 0.58384 | 0.57511 | 0.58384 | +5.0 m‰ |
| **Moyenne** |  | **0.57673** | **0.57948** | **0.57410** | **0.58384** |  |

Référence marché frais (clôture, J-0) : Brier 0.57361. « m‰ » = millièmes de Brier (plus bas = mieux ; « FINAL − meilleur des deux » positif = FINAL moins bon que min(marché vieilli, modèle) à cet âge — attendu tant que le marché domine le modèle, c'est le prix de l'assurance).

## Barèmes alternatifs (réglés sur validation, mesurés sur test)

| Âge | Decay actuel (poids → Brier) | Oracle validation (poids → Brier) | Poids fixe w=1.00 → Brier |
| --- | --- | --- | --- |
| J-0 | 92% → 0.57382 | 100% → 0.57361 | 100% → 0.57361 |
| J-1 | 92% → 0.57385 | 100% → 0.57361 | 100% → 0.57361 |
| J-3 | 60% → 0.57612 | 100% → 0.57383 | 100% → 0.57383 |
| J-5 | 28% → 0.57982 | 100% → 0.57433 | 100% → 0.57433 |
| J-7 | 28% → 0.58006 | 100% → 0.57511 | 100% → 0.57511 |
| **Moyenne** | **0.57673** | **0.57410** | **0.57410** |

Marché vieilli moyen (aucun modèle) : 0.57410. Modèle pur : 0.58384.

## Grid search du poids fixe sur la validation (Brier moyen sur les âges)

| Poids marché w | 0.00 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 1.00 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Brier validation | 0.59567 | 0.59381 | 0.59212 | 0.59061 | 0.58928 | 0.58812 | 0.58713 | 0.58632 | 0.58568 | 0.58522 | 0.58493 ← |

Couverture : 0 matchs de test écartés faute d'ouverture, 0 faute de clôture ; validation 0 sans ouverture.

## Conclusion — le fix M5 est-il confirmé ?

**Oui, le gain attendu est confirmé sans régression.** Le nouveau barème (base 92%, plancher 28%) fait **+0.47 %** de Brier sur le test vs l'ancien (0.57673 vs 0.57948), et il ne régresse à aucun âge. Les deux corrections identifiées au tour précédent sont validées :

1. **Base < 100 % au lieu de 65 %** : monter de 65 % à 92% récupère l'essentiel de la dilution inutile sur cotes fraîches (J-0 : 0.57531 → 0.57382). On garde ~8% de modèle même à J-0 comme assurance contre une cote fraîche mal récupérée — d'où le +0.46 % résiduel vs le 100 % marché pur, un coût volontaire.
2. **Plancher 28% au lieu de la coupure à 0 %** : c'était le point le plus coûteux de l'ancien barème (J-5 legacy 0.58384, FINAL = modèle), car le marché vieilli à J-5 (0.57433) bat toujours le modèle (0.58384). Le plancher garde cette ligne encore informative (J-5 : 0.58384 → 0.57982) sans jeter le filet anti-péremption.

**Ce que ce chiffre n'est PAS.** Sur ce proxy, le marché bat le modèle à tous les âges : le blend ne *bat* donc jamais le marché seul en Brier, il en garde l'essentiel. Sa vraie justification — la seule qui fasse pencher vers le modèle — est le risque HORS-MODÈLE d'une cote scrapée fausse/périmée, strictement pire qu'une ouverture de book et **invisible pour ce backtest**. Le garde-fou reste donc un choix de gestion du risque : ici on mesure qu'il coûte très peu (~0.46 % vs marché pur) quand les cotes sont bonnes, et le fix M5 a réduit ce coût d'un facteur ~2 vs l'ancien barème. Sa valeur réelle en conditions de cotes scrapées est plus élevée que ce que ces données peuvent montrer.
