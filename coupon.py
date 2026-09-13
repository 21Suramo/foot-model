"""Coupon du week-end : LIT le journal de production, n'invente rien à côté.

Constat (audit 2026-09-13) : un coupon compilé séparément du journal
(outil externe, jamais identifié) a montré 7,1 % d'exposition pendant que
le journal en contenait ~18,5 % — deux calculs indépendants du même
concept qui divergent, sans qu'aucun des deux ne fasse foi. Ce script est
la source unique : il lit `data/production_journal.json` (déjà la seule
vérité pour les mises Kelly, cf. M5.1/M5.5) et n'y ajoute qu'un filtrage,
jamais un recalcul des cotes ou des mises.

Filtres appliqués (roadmap B3, décision utilisateur du 2026-09-13) :
- équipes à faible historique (shrinkage ridge insuffisant, cf. model.py) —
  liste explicite ci-dessous, à mettre à jour à la main chaque saison
  (les promotions changent, pas de détection automatique construite ici :
  ce serait un nouveau chantier statistique non demandé) ;
- `odds_age_days >= 5` (cote trop périmée pour être fiable face à 1xbet).

Les cotes du journal ne sont PAS celles de 1xbet (bridge marché/modèle sur
d'autres books, cf. M5) : ce script affiche les mises et laisse le
recoupement de chaque cote sur 1xbet à l'utilisateur avant toute mise
réelle — il ne l'automatise pas.
"""
import argparse

import predict

# Équipes à faible historique dans les 3 ligues suivies (promues récentes /
# nouvelles en 1re division) — à jour au 2026-09-13, à réviser chaque saison.
LOW_HISTORY_TEAMS = {"Leeds", "Hull", "Coventry"}

STALE_ODDS_AGE_DAYS = 5  # cf. B3 : Leeds-Newcastle à 5j était le pari le plus suspect


def eligible_and_excluded(entries):
    """(éligibles, exclus) parmi les paris théoriques non réglés du journal.

    Chaque élément est {match, date, issue, odds, stake_pct, odds_age_days}
    (+ "reasons", liste, pour les exclus — un pari peut cumuler les deux
    filtres, ex. Leeds-Newcastle : faible historique ET cote périmée, les
    deux doivent apparaître sinon la raison affichée dépend arbitrairement de
    l'ordre de vérification). N'agrège et ne recalcule aucune cote."""
    eligible, excluded = [], []
    for e in entries:
        if e.get("actual_score") is not None:
            continue
        meta = e.get("meta") or {}
        age = meta.get("odds_age_days")
        home, away = meta.get("home"), meta.get("away")
        reasons = []
        low_history_teams = {home, away} & LOW_HISTORY_TEAMS
        if low_history_teams:
            reasons.append(f"équipe à faible historique ({', '.join(sorted(low_history_teams))})")
        if age is not None and age >= STALE_ODDS_AGE_DAYS:
            reasons.append(f"cote périmée ({age} j ≥ {STALE_ODDS_AGE_DAYS})")
        for bet in e.get("bets") or []:
            row = {"match": e["match"], "date": e["date"], "issue": bet["issue"],
                  "odds": bet["odds"], "stake_pct": bet["stake_pct"], "odds_age_days": age}
            if reasons:
                excluded.append({**row, "reasons": reasons})
            else:
                eligible.append(row)
    return eligible, excluded


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", default=predict.JOURNAL_PATH, help="Journal (défaut : %(default)s)")
    args = parser.parse_args()

    entries = predict.load_journal(args.log)
    eligible, excluded = eligible_and_excluded(entries)

    print("⚠️  Cotes issues du journal (pont marché/modèle, recherche web multi-books), "
          "PAS des cotes 1xbet. Recouper CHAQUE cote sur 1xbet avant toute mise réelle — "
          "un écart de cote change la mise Kelly attendue.\n")
    print("Coupon éligible :\n")
    for r in sorted(eligible, key=lambda r: (r["date"], r["match"])):
        print(f"  {r['date']}  {r['match']:30s} {r['issue']:5s} "
              f"cote journal {r['odds']:.2f}  mise {r['stake_pct']:.2%}"
              f"  (cote âgée de {r['odds_age_days']} j)")
    total = sum(r["stake_pct"] for r in eligible)
    print(f"\n{len(eligible)} pari(s) éligible(s) — exposition cumulée {total:.2%}.")

    if excluded:
        print(f"\n{len(excluded)} pari(s) exclu(s) (filtres B3, restent dans le journal "
              f"comme suggestions théoriques du modèle — rien n'y est modifié) :")
        for r in sorted(excluded, key=lambda r: (r["date"], r["match"])):
            print(f"  EXCLU — {r['date']}  {r['match']:30s} {r['issue']:5s} "
                  f"mise {r['stake_pct']:.2%} — {' + '.join(r['reasons'])}")


if __name__ == "__main__":
    main()
