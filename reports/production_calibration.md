# Monitoring de production — calibration mensuelle

Journal : `data/production_journal.json` — 28 prédiction(s) réglée(s), 1 mois. Référence Brier hasard = 0.6667 (plus bas = mieux). « Δ vs marché » = écart **relatif** (Brier − Brier marché) / Brier marché, même échelle que le backtest (M3.5 : +1,78 % du marché).

## Par mois

| Mois | n | Brier | Brier marché | Δ vs marché | RPS | Issue OK | Score exact | Nuls prédits/obs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08 | 28 | 0.5214 | 0.5187 | +0.54 % | 0.1632 | 61% | 7% | 24% / 29% |
| **Total** | 28 | 0.5214 | 0.5187 | +0.54 % | 0.1632 | 61% | 7% | 24% / 29% |

## Par fraîcheur des cotes

Découpage sur `meta.odds_age_days` aux seuils du pont marché/modèle (`market_weight`) : ≤ 1 j = poids de base 92%, ≥ 5 j = plancher 28%. Le Brier global mélange les deux régimes ; c'est ici que se voit une sous-performance propre aux cotes périmées.

| Fraîcheur | n | dont sans cote | Brier | Brier marché | Δ vs marché | Lecture |
| --- | --- | --- | --- | --- | --- | --- |
| Fraîches (≤ 1 j, poids marché 92%) | 5 | 0 | 0.3135 | 0.3108 | — | indicative (n < 15) |
| Intermédiaires (2–4 j, poids dégressif) | 22 | 0 | 0.5872 | 0.5837 | +0.60 % | exploitable |
| Périmées (≥ 5 j, poids marché 28%) | 1 | 0 | 0.1147 | 0.1275 | — | indicative (n < 15) |

- Comparaison périmées vs fraîches indisponible : il faut n ≥ 15 avec cotes dans LES DEUX buckets.

## CLV (closing line value)

Écart **relatif** entre la proba implicite de clôture (cote de football.db, démargée power) et celle de la cote utilisée au moment de la prédiction, sur l'issue jouée par le modèle. Même formule que « Δ vs marché » ci-dessus. **Positif = la cote prise battait la clôture** : la ligne a bougé vers notre issue.

| Ligue | n avec CLV | n réglés | CLV moyen | Lecture |
| --- | --- | --- | --- | --- |
| E0 | 10 | 10 | +2.65 % | indicative (n < 15) |
| F1 | 8 | 8 | -2.55 % | indicative (n < 15) |
| SP1 | 10 | 10 | -0.77 % | indicative (n < 15) |
| **Total** | 28 | 28 | -0.06 % | exploitable |

- CLV moyen -0.06 % sur 28 match(s) : dans la bande neutre (±1 pt) — les cotes retenues suivent la clôture, ni battue ni subie. Rien à corriger côté sourcing.

## ROI réel (mise Kelly théorique)

Aucun pari réglé sur la période : soit les prédictions n'avaient pas de cote exploitable, soit leurs résultats ne sont pas encore synchronisés (`python predict.py sync-results`).

## Focus 2026-08

- 28 match(s) réglé(s), Brier 0.5214, issues correctes 61%, scores exacts 7%.
- FINAL vs marché (28 match(s) avec cotes) : Brier 0.5214 vs 0.5187 → équivalent au marché.
