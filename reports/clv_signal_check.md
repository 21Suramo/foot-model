# C1 (roadmap) — le mouvement de cote est-il un signal d'entrée ?

## 1. Diagnostic exploratoire (le signal existe-t-il ?)

3319 paris « value » (edge modèle > 0 à l'ouverture) sur 1920+2021+2122 (jamais le TEST) : 1650 convergents (le marché a bougé vers l'issue value d'ici la clôture), 1669 divergents.

⚠ Portée : « edge > 0 » n'a AUCUN seuil de marge (contrairement à la production, qui filtre par `margin_ok`/mise Kelly) — sur deux distributions de probas différentes, une des 3 issues a presque toujours un edge strictement positif. Ce chantier mesure donc le signal de convergence sur TOUT désaccord modèle/marché, pas seulement les paris qu'on aurait réellement engagés (edge significatif). ⚠ Le mouvement utilisé ici va jusqu'à la CLÔTURE — inconnue au moment de parier, donc ce diagnostic mesure « le signal existe-t-il ? », pas un score actionnable (cf. section 2 du rapport pour la version corrigée, mouvement ouverture→cote prise).

- ROI théorique convergents : +4.3% [-4.6 ; +13.6 pts] (n=1650)
- ROI théorique divergents  : -10.7% [-18.5 ; -2.6 pts] (n=1669)
- Écart ROI convergents − divergents : +15.0% [+3.1 ; +27.0 pts]

Brier modèle (diagnostic, pas le critère de décision) : convergents 0.58551 (n=1650), divergents 0.59934 (n=1669).

### Verdict

Signal détecté : le ROI théorique des paris convergents dépasse significativement (IC excluant 0) celui des paris divergents — de quoi justifier de chercher un score composite actionnable (section 2).

## 2. Score composite actionnable (edge + mouvement, réglé sur validation)

Correction méthodologique par rapport à la section 1 : la clôture n'entre plus dans le score — elle n'est pas connue au moment de parier. La cote « prise » est simulée à J-2 du coup d'envoi (interpolation fair entre ouverture et clôture, `aged_taken`, même proxy que `backtest_blend.aged_fair` — deux vraies lignes sharp, pas une cote scrapée), marge reconstituée séparément pour ne pas gonfler le ROI avec une cote fair irréaliste. J-2 fixé A PRIORI (pas réglé sur les données) dans la bande de fraîcheur déjà utilisée en production (`predict.FRESH_MAX_DAYS`=1, `predict.STALE_MIN_DAYS`=5). Score = edge + w×mouvement (mouvement = ouverture→pris) ; (w, seuil) réglés sur la VALIDATION seule par grid search du ROI théorique, n ≥ 100 paris sélectionnés exigé.

Figé le 2026-09-10 : w = 0.25, seuil = 0.080 → ROI validation +3.72 % (n=347) vs -5.82 % sans filtre (n=2280).

### Résultat test (2223, 2324, 2425, 2526, lecture unique)

- ROI baseline (tous les paris value, sans filtre) : -8.6% [-13.5 ; -3.8 pts] (n=4338)
- ROI sélection (score composite > seuil) : -9.7% [-21.1 ; +2.2 pts] (n=693)
- Écart sélection − baseline : -1.1% [-13.4 ; +11.9 pts]

### Shuffle-test étendu (mouvement permuté)

Lift réel (sélection − baseline) : -1.06 pts. Sur 999 permutations du mouvement (graine 20260909) — edge et résultat réels conservés, seul le lien mouvement↔match est détruit — lift permuté moyen -0.87 pts (p95=+1.69 pts). p-value = 0.549 (fraction des permutations aussi bonnes ou meilleures que le réel).

### Verdict (chantier 2)

❌ Le score composite ne bat pas significativement la baseline sur le test (IC n'exclut pas 0, ou écart négatif) — ne pas construire cette modulation de mise en production, elle figerait du bruit.

## Synthèse : pourquoi la section 1 dit « signal », la section 2 dit « rien »

Ce n'est pas une contradiction, c'est la correction annoncée en tête de fichier. La section 1 classe convergent/divergent par le mouvement ouverture→CLÔTURE : un match où le marché finit par bouger vers l'issue que le modèle aimait est, presque par construction, un match où le marché a fini par se rapprocher de la vérité — la clôture contient déjà de l'information sur l'issue qui n'existe pas encore au moment où on aurait parié. Ce n'est pas un signal qu'un parieur peut exploiter EN AMONT, c'est en bonne partie un artefact de calendrier (regarder le futur pour expliquer le passé). La section 2 corrige exactement ça — mouvement ouverture→PRIS, jamais la clôture — et le signal disparaît sur le test, confirmé par la permutation. Conclusion honnête : le signal de la section 1 était probablement largement un artefact de fuite temporelle, pas un edge d'exécution réel. Ne pas construire de modulation de mise sur le mouvement de cote avec les données actuelles.
