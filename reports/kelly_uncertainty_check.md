# Le désaccord M3.5/GBM prédit-il un edge qui tourne mal ?

Diagnostic AVANT calibration (même discipline que `fatigue_signal_check.py`) — voir docstring de `kelly_uncertainty_check.py` pour la limite méthodologique assumée (données TEST déjà publiées, pas de second jeu hors-test disponible pour ce diagnostic).

n = 4338 matchs (3 grandes ligues, TEST commun à M3.5 et au GBM). Désaccord (variation totale) moyen = 0.0647, médian = 0.0563.

## Angle 1 — Brier de M3.5 par tranche de désaccord (tous matchs)

- Désaccord faible (n=2170) : Brier moyen 0.5615
- Désaccord fort (n=2168) : Brier moyen 0.6062
- Écart (fort − faible) : +0.0447 [+0.03 ; +0.06 pt Brier] — IC exclut 0

## Angle 2 — ROI théorique du meilleur pari M3.5 (matchs à edge positif seulement, la population qu'un Kelly réel toucherait)

- Paris à edge positif : 4136/4338
- Désaccord faible (n=2069) : ROI moyen -0.054
- Désaccord fort (n=2067) : ROI moyen -0.022
- Écart (fort − faible) : +0.032 [-0.08 ; +0.15 pt ROI] — IC contient 0

## Verdict

**Pas de signal actionnable pour le staking.** L'angle 1 (Brier général de M3.5) montre un écart réel — IC excluant 0, désaccord fort associé à un Brier nettement plus mauvais — donc le désaccord M3.5/GBM N'EST PAS du bruit en général : il dit quelque chose sur la fiabilité de M3.5 sur un match donné. Mais l'angle 2, le seul qui compte pour Kelly (ROI théorique des paris à edge réel, pas juste la qualité générale de la prédiction), ne confirme PAS ce signal : IC contenant 0, et le point va même dans le sens INVERSE de l'hypothèse (désaccord fort associé à un ROI théorique moins mauvais, pas pire). Même pattern que le chantier C1 (`clv_signal_check.py`) : un signal large (angle 1 ici, le diagnostic exploratoire là-bas) qui ne survit pas au passage à la métrique réellement actionnable (angle 2 ici, le score composite tune/test/shuffle là-bas). **Verdict : la calibration (moduler Kelly par ce désaccord) N'EST PAS construite** — elle capturerait un signal réel mais mal ciblé (qualité générale de prédiction) en le faisant passer pour un signal de staking, ce qu'il n'est pas démontré être. Chantier fermé proprement, comme fatigue/M8 et C1. À rouvrir seulement avec une vraie donnée fraîche (production, pas ce même jeu de test) et, idéalement, les cotes réellement cotées plutôt que le marché démargé (cf. limite VALUE_EDGE_THRESHOLD).
