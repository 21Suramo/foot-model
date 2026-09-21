# Monitoring de production — calibration mensuelle

Journal : `data/production_journal.json` — 115 prédiction(s) réglée(s), 2 mois. Référence Brier hasard = 0.6667 (plus bas = mieux). « Δ vs marché » = écart **relatif** (Brier − Brier marché) / Brier marché, même échelle que le backtest (M3.5 : +1,78 % du marché).

## Par mois

| Mois | n | Brier | Brier marché | Δ vs marché | IC 95 % du Δ | RPS | Issue OK | Score exact | Nuls prédits/obs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08 | 57 | 0.5817 | 0.5916 | -1.68 % | [-4.13 ; +0.42 %] | 0.1946 | 51% | 9% | 24% / 26% |
| 2026-09 | 58 | 0.6723 | 0.6876 | -2.23 % | [-7.36 ; +2.13 %] | 0.2125 | 40% | 5% | 24% / 38% |
| **Total** | 115 | 0.6274 | 0.6300 | -0.42 % | [-4.57 ; +0.31 %] | 0.2036 | 45% | 7% | 24% / 32% |

L'IC 95 % est un bootstrap **apparié** (10000 rééchantillonnages de matchs, graine 20260909 pour que le rapport se régénère à l'identique) : modèle et marché sont notés sur les mêmes matchs, on rééchantillonne donc les matchs, pas deux séries indépendantes. Un intervalle qui contient 0 veut dire que le mois ne permet pas de distinguer le FINAL du marché — c'est le cas normal sur quelques dizaines de matchs, et la raison pour laquelle un Δ mensuel isolé ne justifie jamais de toucher aux réglages figés.

## Par fraîcheur des cotes

Découpage sur `meta.odds_age_days` aux seuils du pont marché/modèle (`market_weight`) : ≤ 1 j = poids de base 92%, ≥ 5 j = plancher 28%. Le Brier global mélange les deux régimes ; c'est ici que se voit une sous-performance propre aux cotes périmées.

| Fraîcheur | n | Brier | Brier marché | Δ vs marché | IC 95 % du Δ | Lecture |
| --- | --- | --- | --- | --- | --- | --- |
| Fraîches (≤ 1 j, poids marché 92%) | 9 | 0.4619 | 0.4651 | — | — | indicative (n < 15) |
| Intermédiaires (2–4 j, poids dégressif) | 66 | 0.6375 | 0.6444 | -1.08 % | [-3.42 ; +1.25 %] | exploitable |
| Périmées (≥ 5 j, poids marché 28%) | 32 | 0.6599 | 0.6836 | — | — | indicative (10 match(s) avec cotes) |
| Fraîcheur non renseignée (hors barème) | 8 | 0.6002 | — | — | — | indicative (n < 15) |

- Comparaison périmées vs fraîches indisponible : il faut n ≥ 15 avec cotes dans LES DEUX buckets.

## ROI réel (mise Kelly théorique)

- 20 pari(s) réglé(s) — mise cumulée 18.52% de bankroll (somme des mises successives, pas une exposition simultanée), P&L +18.000% de bankroll, soit un ROI de +97.2% de la mise.
- Ce ROI est **théorique** : les mises n'ont jamais été placées, elles sont recalculées depuis les cotes journalisées (Kelly 0.25 plafonné à 5 %). Ce n'est pas un P&L vérifié par un bookmaker.
- ⚠ 20 paris réglés (< 100) : échantillon insuffisant pour une lecture fiable du ROI — la variance sur des cotes 1N2 rend un tel échantillon quasi non-informatif.

## CLV (closing line value)

Écart entre la cote prise et la cote de clôture (`matches.odds_*`, posée par `sync-results`) sur chaque pari théorique réglé : `clv_pct = cote_prise / cote_clôture − 1`. Positif = la cote a raccourci après la prise (le pari devançait le marché) ; négatif = elle s'est détendue (la « value » vue au moment du pari a fondu, voire n'en était pas une). Le CLV converge plus vite que le ROI réel — c'est le premier signal à lire sur un petit échantillon.

**Clôture sharp exigée** : seuls les paris dont la clôture porte `odds_source` ∈ {`pinnacle_close`} entrent ici. Une moyenne de books (`avg_close`) ou une ouverture (`*_open`) mesurerait autre chose sous le même nom : battre une moyenne tirée par des books soft n'est pas battre le marché, et comparer à une ouverture inverse souvent le signe. Les paris écartés sont comptés ci-dessous, jamais mélangés à la moyenne.

Aucun pari réglé avec clôture sharp connue : soit aucun pari théorique n'a encore de résultat, soit la clôture sharp manquait pour ces matchs.

Paris réglés sans CLV (20) :
- `no_closing_odds` × 8 — aucune cote de clôture en base pour ce match (odds_* NULL), ou résultat saisi manuellement.
- `source_not_sharp` × 12 — cote de clôture d'une source non sharp (CLV exigé sur pinnacle_close).
  Ces paris-là n'attendent rien : la base n'a pas de clôture sharp pour ces matchs, relancer `sync-results` n'y changera rien.

## CLV provisoire (R1 — proxy book_odds, PAS le CLV sharp)

⚠️ **Ne sert à aucune décision du protocole Gate n=50/n=100** (section « Protocole de revue CLV » de CLAUDE.md) : c'est un proxy, pas une clôture. Constat à l'origine de cette section : football-data.co.uk ne publie ses cotes de clôture Pinnacle qu'avec des mois de retard, une fois la saison terminée — aucun pari théorique réglé en cours de saison n'a donc de `clv_pct` sharp, ce qui bloque structurellement le protocole Gate tant que la saison n'est pas close. `clv_pct_provisional` compare plutôt la cote prise au dernier snapshot Pinnacle capturé dans `book_odds` (roadmap A2, `odds_snapshot.py`) avant le coup d'envoi — un book réel et sharp, mais un instantané pris à un moment quelconque avant le match, pas la clôture elle-même. Sert uniquement à repérer une **dérive grossière** entre la cote prise et le marché ; ne mesure PAS un edge et ne doit jamais remplacer le CLV sharp dans une décision de mise ou de revue de protocole.

- 20 pari(s) avec snapshot Pinnacle provisoire — CLV provisoire moyen +2.76%, positif sur 30% des paris — IC 95 % [-8.39 ; +17.88 %].

## Focus 2026-09

- 58 match(s) réglé(s), Brier 0.6723, issues correctes 40%, scores exacts 5%.
- ⚠ Nuls : le modèle sous-estime les nuls (24% prédits vs 38% observés).
- FINAL vs marché (34 match(s) avec cotes) : Brier 0.6723 vs 0.6876 → le blend bat le marché seul — la couche modèle ajoute de la valeur.
