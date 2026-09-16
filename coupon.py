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

Filtres de contexte sportif (Niveau 2 de la couche d'analyse sportive,
CLAUDE.md section « Analyse sportive — roadmap ») : lisent
`data/context_flags.json`, tenu À LA MAIN (recherche web — compos
probables, actus, comme le repricing M6), et excluent les paris dont le
match porte un flag de risque. Un match absent de `context_flags.json` est
GARDÉ (comportement par défaut inchangé) — coupon.py avertit juste en fin
de run que ce match n'a pas été documenté, plutôt que de deviner.

Les cotes du journal ne sont PAS celles de 1xbet (bridge marché/modèle sur
d'autres books, cf. M5) : ce script affiche les mises et laisse le
recoupement de chaque cote sur 1xbet à l'utilisateur avant toute mise
réelle — il ne l'automatise pas.
"""
import argparse
import json
from pathlib import Path

import predict

# Équipes à faible historique dans les 3 ligues suivies (promues récentes /
# nouvelles en 1re division) — à jour au 2026-09-13, à réviser chaque saison.
LOW_HISTORY_TEAMS = {"Leeds", "Hull", "Coventry"}

STALE_ODDS_AGE_DAYS = 5  # cf. B3 : Leeds-Newcastle à 5j était le pari le plus suspect

# Niveau 2 — filtres de contexte sportif, tous configurables ici.
DERBY_ODDS_THRESHOLD = 4.0  # derby + cote au-delà = l'outsider est un piège, pas une value
CONTEXT_FLAGS_PATH = "data/context_flags.json"

# Libellés affichés pour chaque flag vrai d'un pari RETENU (section « Contexte »).
CONTEXT_FLAG_LABELS = {
    "post_european_home": "domicile sort d'un match européen (< 72h)",
    "post_european_away": "extérieur sort d'un match européen (< 72h)",
    "derby": "derby local",
    "coach_change_home": "changement d'entraîneur récent (domicile)",
    "coach_change_away": "changement d'entraîneur récent (extérieur)",
    "weather_extreme": "météo extrême annoncée",
    "low_stakes_home": "enjeu faible pour le domicile (déjà qualifié/éliminé)",
    "low_stakes_away": "enjeu faible pour l'extérieur (déjà qualifié/éliminé)",
}


def load_context_flags(path=CONTEXT_FLAGS_PATH):
    """{match: flags} depuis context_flags.json (liste plate, indexée sur
    "match"). Fichier absent ou vide -> {} (aucun match documenté, rien
    n'est exclu par ce filtre — comportement par défaut inchangé)."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} : JSON malformé ({e}).")
    return {r["match"]: r for r in rows if r.get("match")}


def context_reasons(flags, odds):
    """Raisons d'exclusion Niveau 2 pour UN pari (une cote) d'un match donné.

    `flags` peut être None/{} (match non documenté) -> aucune raison, le
    match reste gardé par défaut (cf. docstring du module)."""
    flags = flags or {}
    reasons = []
    if flags.get("post_european_home") or flags.get("post_european_away"):
        reasons.append("rotation probable (match européen < 72h)")
    if flags.get("coach_change_home") or flags.get("coach_change_away"):
        reasons.append("changement d'entraîneur récent (variance anormale)")
    if flags.get("derby") and odds > DERBY_ODDS_THRESHOLD:
        reasons.append(f"derby + cote outsider ({odds:.2f} > {DERBY_ODDS_THRESHOLD:.1f})")
    if flags.get("weather_extreme"):
        reasons.append("météo extrême (variance anormale)")
    if flags.get("low_stakes_home") or flags.get("low_stakes_away"):
        reasons.append("enjeu faible (motivation douteuse)")
    return reasons


def context_notes(flags):
    """Flags vrais + notes d'un match RETENU, pour la section « Contexte » —
    None si rien à signaler (pas de bruit sur un match sans aucun flag)."""
    flags = flags or {}
    true_flags = [CONTEXT_FLAG_LABELS[k] for k in CONTEXT_FLAG_LABELS if flags.get(k)]
    notes = flags.get("notes")
    if not true_flags and not notes:
        return None
    return {"flags": true_flags, "notes": notes}


def eligible_and_excluded(entries, context_flags=None):
    """(éligibles, exclus) parmi les paris théoriques non réglés du journal.

    Chaque élément est {match, date, issue, odds, stake_pct, odds_age_days}
    (+ "reasons", liste, pour les exclus — un pari peut cumuler plusieurs
    filtres B3/Niveau 2, ex. Leeds-Newcastle : faible historique ET cote
    périmée, les deux doivent apparaître sinon la raison affichée dépend
    arbitrairement de l'ordre de vérification ; + "context" pour les
    éligibles, cf. context_notes). N'agrège et ne recalcule aucune cote.

    `context_flags` : {match: flags} (cf. load_context_flags). None ou {}
    -> aucun match documenté, le filtre Niveau 2 ne retire rien (comportement
    par défaut inchangé, comme avant son introduction)."""
    context_flags = context_flags or {}
    eligible, excluded = [], []
    for e in entries:
        if e.get("actual_score") is not None:
            continue
        meta = e.get("meta") or {}
        age = meta.get("odds_age_days")
        home, away = meta.get("home"), meta.get("away")
        flags = context_flags.get(e["match"])
        b3_reasons = []
        low_history_teams = {home, away} & LOW_HISTORY_TEAMS
        if low_history_teams:
            b3_reasons.append(f"équipe à faible historique ({', '.join(sorted(low_history_teams))})")
        if age is not None and age >= STALE_ODDS_AGE_DAYS:
            b3_reasons.append(f"cote périmée ({age} j ≥ {STALE_ODDS_AGE_DAYS})")
        for bet in e.get("bets") or []:
            row = {"match": e["match"], "date": e["date"], "issue": bet["issue"],
                  "odds": bet["odds"], "stake_pct": bet["stake_pct"], "odds_age_days": age}
            reasons = b3_reasons + context_reasons(flags, bet["odds"])
            if reasons:
                excluded.append({**row, "reasons": reasons})
            else:
                context = context_notes(flags)
                if context:
                    row = {**row, "context": context}
                eligible.append(row)
    return eligible, excluded


def undocumented_matches(entries, context_flags):
    """Matchs (non réglés, avec au moins un pari) absents de
    context_flags.json — informationnel seulement, ne change aucune
    exclusion (cf. docstring du module : absence = gardé)."""
    context_flags = context_flags or {}
    matches = set()
    for e in entries:
        if e.get("actual_score") is not None:
            continue
        if not e.get("bets"):
            continue
        if e["match"] not in context_flags:
            matches.add(e["match"])
    return sorted(matches)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", default=predict.JOURNAL_PATH, help="Journal (défaut : %(default)s)")
    parser.add_argument("--context-flags", default=CONTEXT_FLAGS_PATH,
                        help="Flags de contexte sportif Niveau 2 (défaut : %(default)s)")
    args = parser.parse_args()

    entries = predict.load_journal(args.log)
    context_flags = load_context_flags(args.context_flags)
    eligible, excluded = eligible_and_excluded(entries, context_flags)

    print("⚠️  Cotes issues du journal (pont marché/modèle, recherche web multi-books), "
          "PAS des cotes 1xbet. Recouper CHAQUE cote sur 1xbet avant toute mise réelle — "
          "un écart de cote change la mise Kelly attendue.\n")
    print("Coupon éligible :\n")
    for r in sorted(eligible, key=lambda r: (r["date"], r["match"])):
        print(f"  {r['date']}  {r['match']:30s} {r['issue']:5s} "
              f"cote journal {r['odds']:.2f}  mise {r['stake_pct']:.2%}"
              f"  (cote âgée de {r['odds_age_days']} j)")
        ctx = r.get("context")
        if ctx:
            if ctx["flags"]:
                print(f"      contexte : {' ; '.join(ctx['flags'])}")
            if ctx["notes"]:
                print(f"      note : {ctx['notes']}")
        print("      → vérifier compos officielles 1h avant (M6, --lineup-adjustment si besoin).")
    total = sum(r["stake_pct"] for r in eligible)
    print(f"\n{len(eligible)} pari(s) éligible(s) — exposition cumulée {total:.2%}.")

    if excluded:
        print(f"\n{len(excluded)} pari(s) exclu(s) (filtres B3 + Niveau 2, restent dans le "
              f"journal comme suggestions théoriques du modèle — rien n'y est modifié) :")
        excluded_stake = sum(r["stake_pct"] for r in excluded)
        for r in sorted(excluded, key=lambda r: (r["date"], r["match"])):
            print(f"  EXCLU — {r['date']}  {r['match']:30s} {r['issue']:5s} "
                  f"mise {r['stake_pct']:.2%} (qui aurait été misée) — {' + '.join(r['reasons'])}")
        print(f"\n{excluded_stake:.2%} de bankroll (mise théorique) exclus au total.")

    undocumented = undocumented_matches(entries, context_flags)
    if undocumented:
        print(f"\n⚠ {len(undocumented)} match(s) sans context_flags, à vérifier manuellement : "
              f"{', '.join(undocumented)}.")


if __name__ == "__main__":
    main()
