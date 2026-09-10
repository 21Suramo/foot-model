# C1 (roadmap) — le mouvement de cote est-il un signal d'entrée ?

3319 paris « value » (edge modèle > 0 à l'ouverture) sur 1920+2021+2122 (jamais le TEST) : 1650 convergents (le marché a bougé vers l'issue value d'ici la clôture), 1669 divergents.

⚠ Portée : « edge > 0 » n'a AUCUN seuil de marge (contrairement à la production, qui filtre par `margin_ok`/mise Kelly) — sur deux distributions de probas différentes, une des 3 issues a presque toujours un edge strictement positif. Ce chantier mesure donc le signal de convergence sur TOUT désaccord modèle/marché, pas seulement les paris qu'on aurait réellement engagés (edge significatif). C'est la bonne échelle pour DÉTECTER un signal (puissance statistique maximale, comme fatigue_signal_check.py) ; un seuil de marge resterait à ajouter si C1 est construit en production.

- ROI théorique convergents : +4.3% [-4.6 ; +13.6 pts] (n=1650)
- ROI théorique divergents  : -10.7% [-18.5 ; -2.6 pts] (n=1669)
- Écart ROI convergents − divergents : +15.0% [+3.1 ; +27.0 pts]

Brier modèle (diagnostic, pas le critère de décision) : convergents 0.58551 (n=1650), divergents 0.59934 (n=1669).

## Verdict

Signal détecté : le ROI théorique des paris convergents dépasse significativement (IC excluant 0) celui des paris divergents — un score composite modèle+mouvement de cote (C1 complet) peut être envisagé, à RÉGLER SUR LA VALIDATION UNIQUEMENT (jamais le test, cf. piège de confirmation cité par la roadmap elle-même).
