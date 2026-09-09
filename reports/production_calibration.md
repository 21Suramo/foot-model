# Monitoring de production — calibration mensuelle

Journal : `data/production_journal.json` — 28 prédiction(s) réglée(s), 1 mois. Référence Brier hasard = 0.6667 (plus bas = mieux). « Δ vs marché » = écart **relatif** (Brier − Brier marché) / Brier marché, même échelle que le backtest (M3.5 : +1,78 % du marché).

## Par mois

| Mois | n | Brier | Brier marché | Δ vs marché | IC 95 % du Δ | RPS | Issue OK | Score exact | Nuls prédits/obs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08 | 28 | 0.5214 | 0.5187 | +0.54 % | [-2.62 ; +3.68 %] | 0.1632 | 61% | 7% | 24% / 29% |
| **Total** | 28 | 0.5214 | 0.5187 | +0.54 % | [-2.62 ; +3.68 %] | 0.1632 | 61% | 7% | 24% / 29% |

L'IC 95 % est un bootstrap **apparié** (10000 rééchantillonnages de matchs, graine 20260909 pour que le rapport se régénère à l'identique) : modèle et marché sont notés sur les mêmes matchs, on rééchantillonne donc les matchs, pas deux séries indépendantes. Un intervalle qui contient 0 veut dire que le mois ne permet pas de distinguer le FINAL du marché — c'est le cas normal sur quelques dizaines de matchs, et la raison pour laquelle un Δ mensuel isolé ne justifie jamais de toucher aux réglages figés.

## Par fraîcheur des cotes

Découpage sur `meta.odds_age_days` aux seuils du pont marché/modèle (`market_weight`) : ≤ 1 j = poids de base 92%, ≥ 5 j = plancher 28%. Le Brier global mélange les deux régimes ; c'est ici que se voit une sous-performance propre aux cotes périmées.

| Fraîcheur | n | Brier | Brier marché | Δ vs marché | IC 95 % du Δ | Lecture |
| --- | --- | --- | --- | --- | --- | --- |
| Fraîches (≤ 1 j, poids marché 92%) | 5 | 0.3135 | 0.3108 | — | — | indicative (n < 15) |
| Intermédiaires (2–4 j, poids dégressif) | 22 | 0.5872 | 0.5837 | +0.60 % | [-3.02 ; +4.22 %] | exploitable |
| Périmées (≥ 5 j, poids marché 28%) | 1 | 0.1147 | 0.1275 | — | — | indicative (n < 15) |

- Comparaison périmées vs fraîches indisponible : il faut n ≥ 15 avec cotes dans LES DEUX buckets.

## ROI réel (mise Kelly théorique)

Aucun pari réglé sur la période : soit les prédictions n'avaient pas de cote exploitable, soit leurs résultats ne sont pas encore synchronisés (`python predict.py sync-results`).

## CLV (closing line value)

Écart entre la cote prise et la cote de clôture (`matches.odds_*`, posée par `sync-results`) sur chaque pari théorique réglé : `clv_pct = cote_prise / cote_clôture − 1`. Positif = la cote a raccourci après la prise (le pari devançait le marché) ; négatif = elle s'est détendue (la « value » vue au moment du pari a fondu, voire n'en était pas une). Le CLV converge plus vite que le ROI réel — c'est le premier signal à lire sur un petit échantillon.

**Clôture sharp exigée** : seuls les paris dont la clôture porte `odds_source` ∈ {`pinnacle_close`} entrent ici. Une moyenne de books (`avg_close`) ou une ouverture (`*_open`) mesurerait autre chose sous le même nom : battre une moyenne tirée par des books soft n'est pas battre le marché, et comparer à une ouverture inverse souvent le signe. Les paris écartés sont comptés ci-dessous, jamais mélangés à la moyenne.

Aucun pari réglé avec clôture sharp connue : soit aucun pari théorique n'a encore de résultat, soit la clôture sharp manquait pour ces matchs.

## Focus 2026-08

- 28 match(s) réglé(s), Brier 0.5214, issues correctes 61%, scores exacts 7%.
- FINAL vs marché (28 match(s) avec cotes) : Brier 0.5214 vs 0.5187 → équivalent au marché.
