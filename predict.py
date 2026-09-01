"""M5 — Prédiction de production : le modèle M3.5 figé, appliqué aux matchs à venir.

Le backtest est terminé ; ce module fait tourner le modèle en conditions
réelles. Pour chaque affiche du week-end, il :

1. **refit à jour** le Dixon-Coles pseudo-buts xG (config figée dans
   data/m35_frozen.json : w, ξ, κ, température) sur TOUT l'historique joué
   strictement antérieur au lundi de la semaine visée — même mécanique
   anti-fuite que le walk-forward, mais sans horizon de fin ;
2. sort les probas 1N2 recalibrées + la grille de scores, **au format de
   match_model.py** (colonnes Marché / Modèle / FINAL, Top 7 des scores,
   meilleur score par issue) ;
3. fait le **pont avec le skill** : quand des cotes fraîches sont fournies,
   il mélange marché/modèle ; quand elles manquent ou datent, le poids du
   marché décroît et le modèle reprend la main — c'est là le vrai apport du
   modèle, servir de garde-fou contre une ligne périmée ;
4. branche le **mode concours** (--contest-points) sur les probas du modèle ;
5. **journalise automatiquement** chaque prédiction (format track.py, donc
   relisible par le skill) et produit un **rapport de calibration mensuel**
   — le monitoring continue en conditions réelles.

Usage :
    python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
        [--date 2026-08-15] [--odds 1.85,3.6,4.4 --odds 1.88,3.55,4.3] \
        [--odds-date 2026-08-14] [--contest-points 13,50,68]
    python predict.py result --match "Arsenal-Chelsea" --actual 2-1 [--ht 1-0]
    python pipeline.py --update && python predict.py sync-results
    python predict.py report [--month 2026-08]
"""
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest35
import db
import footballdata
import model

log = logging.getLogger("predict")

JOURNAL_PATH = Path("data/production_journal.json")
CAL_REPORT_PATH = Path("reports/production_calibration.md")

# Export natif du skill football-match-predictor (pont d'entrée : le skill
# rassemble les cotes web, predict.py recalcule SON propre FINAL par-dessus).
SKILL_SCHEMA = "football-match-predictor.skill-export/v1"

# Fraîcheur des cotes → poids du marché (garde-fou anti-cotes-périmées).
#
# Le marché de clôture bat le modèle (M3.5 : Brier +1,78 %), et backtest_blend.py
# montre qu'il le bat à TOUS les âges simulés. Le blend n'existe donc PAS pour
# gagner du Brier sur des cotes de book fraîches — il existe comme FILET contre
# une cote d'entrée douteuse : mal recopiée depuis le web, figée à J-3+, ou d'un
# book soft. D'où deux garde-fous :
#  - un poids de base < 100 % même à J-0 : une cote fraîche peut être mal
#    récupérée, on garde toujours une petite fraction de modèle en assurance ;
#  - une décroissance vers un PLANCHER (jamais vers 0) : même vieillie, une vraie
#    ligne reste informative (le backtest le confirme), on ne la jette pas — on
#    lui fait juste de moins en moins confiance à mesure qu'elle date.
#
# ⚠ IMPORTANT — ne pas surinterpréter le chiffre du backtest. backtest_blend.py
# mesure ce barème sur un PROXY (interpolation ouverture↔clôture, deux vraies
# lignes sharp) qui SOUS-ESTIME la péremption réelle visée : une cote scrapée
# fausse ou figée est bien pire qu'une simple ouverture de book. Le gain Brier
# qu'il chiffre (~0,9 %) est un PLANCHER de l'utilité du garde-fou, pas sa vraie
# valeur en conditions de cotes scrapées. N'en conclus pas « le modèle ne sert à
# rien » : le backtest ne peut pas voir le scénario que le garde-fou protège.
FRESH_MAX_DAYS = 1      # cotes ≤ 1 jour : poids marché = base (le plus frais)
STALE_MIN_DAYS = 5      # cotes ≥ 5 jours : poids marché = plancher (le plus périmé)
DEFAULT_BLEND = 0.92    # poids marché de base sur cotes fraîches (garde ~8 % modèle en assurance)
STALE_FLOOR = 0.28      # plancher de poids marché : on ne jette jamais une vraie ligne
MARGIN_MIN = 1.0        # marge implicite < 100 % = arbitrable donc suspecte/périmée
MARGIN_MAX = 1.12       # marge > 112 % = ligne de mauvaise qualité

ISSUES = ("home", "draw", "away")

# Raisons documentées d'une prédiction sans cote marché (`market_probs` null).
#
# Le journal n'enregistrait qu'un `market_weight: 0.0` muet : impossible, en
# relisant, de distinguer « le book n'avait pas encore ouvert la ligne à J-8 »
# (structurel, il n'y a rien à corriger) d'une erreur de mapping ou d'un alias
# manquant (bug, à corriger). On enregistre donc la raison, sans jamais la
# deviner : les deux dernières sont déduites par le code, les autres doivent
# être déclarées par l'appelant (`--no-odds-reason`, ou le champ homonyme de
# l'export du skill). Faute de déclaration, on écrit « non précisée » — pas une
# raison plausible inventée après coup.
NO_ODDS_REASONS = {
    "not_yet_published": "cotes pas encore ouvertes chez les books à cette date",
    "lookup_failed": "recherche de cotes infructueuse (affiche introuvable, source injoignable)",
    "margin_rejected": "cotes trouvées mais écartées en amont (marge implicite hors bornes)",
    "not_provided": "aucune cote passée à l'appel, raison non précisée",
    "slate_odds_ignored": "--odds ne s'applique qu'à un match unique : ignoré sur un slate",
}
# Ce que l'appelant a le droit de déclarer ; le reste est déduit du code.
DECLARABLE_NO_ODDS_REASONS = ("not_yet_published", "lookup_failed", "margin_rejected")
DEFAULT_NO_ODDS_REASON = "not_provided"

# Sources de cotes de la base qui sont de vraies lignes de CLÔTURE (cf.
# footballdata.ODDS_1X2). Un repli sur l'ouverture n'est pas une clôture : le
# comparer à la cote d'entrée ne mesure pas du CLV.
CLOSING_ODDS_SOURCES = footballdata.CLOSING_SOURCES


# ---------------------------------------------------------------------------
# Utilitaires de dates et de grille
# ---------------------------------------------------------------------------

def next_saturday(today=None):
    """Samedi à venir (aujourd'hui compris s'il tombe un samedi)."""
    today = today or datetime.date.today()
    return today + datetime.timedelta(days=(5 - today.weekday()) % 7)


def grid_to_dict(grid_np):
    """Grille numpy (MAX_GOALS+1)² -> dict {(h, a): p}, au format match_model."""
    return {(h, a): float(grid_np[h, a])
            for h in range(grid_np.shape[0]) for a in range(grid_np.shape[1])}


def fmt(x):
    return f"{x * 100:5.1f}%"


# ---------------------------------------------------------------------------
# Résolution des noms d'équipe (l'appelant peut fournir un nom Understat ou
# une variante ; on le ramène au nom canonique football-data du modèle)
# ---------------------------------------------------------------------------

def resolve_team(name, teams, alias_map):
    """(nom_canonique, trouvé?). Un nom inconnu du modèle est renvoyé tel quel :
    model.py attribue alors les forces moyennes de la ligue (comportement
    documenté), mais on le signale car c'est souvent une faute de frappe."""
    if name in teams:
        return name, True
    if name in alias_map and alias_map[name] in teams:
        return alias_map[name], True
    low = name.strip().lower()
    exact = [t for t in teams if t.lower() == low]
    if len(exact) == 1:
        return exact[0], True
    partial = [t for t in teams if low in t.lower() or t.lower() in low]
    if len(partial) == 1:
        return partial[0], True
    return name, False


# ---------------------------------------------------------------------------
# Pont marché / modèle (garde-fou anti-cotes-périmées)
# ---------------------------------------------------------------------------

def parse_odds_triples(specs):
    """Liste de 'h,d,a' -> liste de triplets float. Erreur explicite sinon."""
    out = []
    for spec in specs:
        try:
            oh, od, oa = (float(x) for x in spec.split(","))
        except ValueError:
            sys.exit(f"Format --odds invalide: '{spec}' (attendu home,nul,away, ex: 1.85,3.6,4.4)")
        out.append((oh, od, oa))
    return out


def market_consensus(triples):
    """Consensus démargé (méthode power) + meilleure cote brute par issue."""
    fairs = [backtest.demargin_power(*t) for t in triples]
    market = {k: float(np.mean([f[i] for f in fairs])) for i, k in enumerate(ISSUES)}
    best_odds = {k: max(t[i] for t in triples) for i, k in enumerate(ISSUES)}
    return market, best_odds


def margin_ok(triple):
    margin = sum(1.0 / o for o in triple)
    return MARGIN_MIN - 1e-6 <= margin <= MARGIN_MAX, margin


def market_weight(base_blend, age_days, all_margins_ok):
    """(poids_marché, fraction_de_base, explication). age_days = âge des cotes vs
    coup d'envoi (None = fraîcheur non vérifiée). Décroissance linéaire du poids
    de base (cotes fraîches) vers le plancher STALE_FLOOR (cotes périmées) — on ne
    coupe jamais complètement le marché, une ligne même vieillie informe encore ;
    une marge aberrante divise en plus le poids par 2 (cote suspecte, peut passer
    sous le plancher car c'est alors la QUALITÉ de la cote qui est en cause)."""
    floor = min(STALE_FLOOR, base_blend)
    if age_days is None:
        w, why = base_blend, "fraîcheur non vérifiée (supposée fraîche)"
    elif age_days <= FRESH_MAX_DAYS:
        w, why = base_blend, f"cotes fraîches (J-{age_days})"
    elif age_days >= STALE_MIN_DAYS:
        w, why = floor, f"cotes périmées (J-{age_days}) — poids marché au plancher {floor:.0%}"
    else:
        frac = (STALE_MIN_DAYS - age_days) / (STALE_MIN_DAYS - FRESH_MAX_DAYS)
        w = floor + (base_blend - floor) * frac
        why = f"cotes à J-{age_days} — poids marché {w:.0%}"
    if not all_margins_ok:
        w *= 0.5
        why += " ; marge implicite aberrante, poids divisé par 2"
    return w, (w / base_blend if base_blend else 0.0), why


# ---------------------------------------------------------------------------
# Mise (Kelly fractionné plafonné) — repris de match_model pour la section 💰
# ---------------------------------------------------------------------------

def kelly_stake(p, decimal_odds, fraction=0.25, cap=0.05):
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    f_star = (p * b - (1.0 - p)) / b
    if f_star <= 0:
        return 0.0
    return min(f_star * fraction, cap)


def risk_label(stake_pct):
    if stake_pct <= 0:
        return "pas de mise (aucune value)"
    if stake_pct < 0.01:
        return "risque faible"
    if stake_pct < 0.025:
        return "risque modéré"
    return "risque élevé (proche du plafond)"


# ---------------------------------------------------------------------------
# Prédiction d'un match
# ---------------------------------------------------------------------------

def best_score_for_outcome(grid, outcome):
    cond = {"home": lambda h, a: h > a, "draw": lambda h, a: h == a,
            "away": lambda h, a: h < a}[outcome]
    (h, a), _ = max((kv for kv in grid.items() if cond(*kv[0])), key=lambda kv: kv[1])
    return f"{h}-{a}"


def over_prob(grid, line):
    return sum(p for (h, a), p in grid.items() if h + a > line)


def btts_prob(grid):
    return sum(p for (h, a), p in grid.items() if h > 0 and a > 0)


def predict_match(conn, cfg, league, home_in, away_in, target_date,
                  odds_specs, odds_age_days, blend, fit_cache,
                  no_odds_reason=None):
    """Calcule tout pour un match et renvoie un dict de résultats (sans imprimer)."""
    ref_monday = backtest.monday_of(target_date.isoformat())
    key = (league, ref_monday)
    if key not in fit_cache:
        rows = [r for r in backtest.load_league(conn, league)
                if r["date"] < ref_monday.isoformat()]
        if not rows:
            sys.exit(f"Aucun match d'entraînement pour {league} avant {ref_monday}.")
        fit_cache[key] = (model.fit(rows, xi=cfg["xi"], ref_date=ref_monday,
                                    xg_weight=cfg["w"], prior_weight=cfg["kappa"]),
                          len(rows), max(r["date"] for r in rows))
    fitted, n_train, last_date = fit_cache[key]

    alias_map = db.load_aliases(conn)
    home, home_ok = resolve_team(home_in, fitted.teams, alias_map)
    away, away_ok = resolve_team(away_in, fitted.teams, alias_map)

    lam_h, lam_a = fitted.lambdas(home, away)
    grid = grid_to_dict(fitted.score_grid(home, away))
    raw = fitted.probs_1x2(home, away)
    model_probs = dict(zip(ISSUES, backtest35.apply_temperature(raw, cfg["temperature"])))

    market = best_odds = None
    m_weight = 0.0
    reason = None
    fresh_note = "aucune cote fournie — modèle seul"
    if odds_specs:
        triples = parse_odds_triples(odds_specs)
        market, best_odds = market_consensus(triples)
        all_ok = all(margin_ok(t)[0] for t in triples)
        m_weight, _, fresh_note = market_weight(blend, odds_age_days, all_ok)
    else:
        # Modèle pur : on trace POURQUOI, sinon le journal ne garde qu'un
        # market_weight nul qu'on ne saura plus interpréter dans trois semaines.
        reason = no_odds_reason if no_odds_reason in NO_ODDS_REASONS else DEFAULT_NO_ODDS_REASON
        fresh_note = f"aucune cote fournie — modèle seul ({NO_ODDS_REASONS[reason]})"

    if market is not None:
        final = {k: m_weight * market[k] + (1 - m_weight) * model_probs[k] for k in ISSUES}
        s = sum(final.values())
        final = {k: v / s for k, v in final.items()}
    else:
        final = dict(model_probs)

    return {
        "league": league, "home": home, "away": away,
        "home_in": home_in, "away_in": away_in,
        "home_ok": home_ok, "away_ok": away_ok,
        "date": target_date, "ref_monday": ref_monday,
        "n_train": n_train, "last_train_date": last_date,
        "lam_h": lam_h, "lam_a": lam_a, "grid": grid,
        "model": model_probs, "market": market, "best_odds": best_odds,
        "final": final, "market_weight": m_weight, "fresh_note": fresh_note,
        "odds_age_days": odds_age_days, "no_odds_reason": reason,
    }


# ---------------------------------------------------------------------------
# Impression (format match_model.py)
# ---------------------------------------------------------------------------

def print_prediction(res, cfg, contest=None, exact_bonus=0.0, no_stake=False):
    home, away = res["home"], res["away"]
    print(f"=== {home} vs {away} — modèle M5 production "
          f"(w={cfg['w']:.1f}, ξ={cfg['xi']}, κ={cfg['kappa']}, t={cfg['temperature']:.3f}) ===\n")
    if not res["home_ok"]:
        print(f"⚠ '{res['home_in']}' inconnu du modèle — forces moyennes de la ligue appliquées "
              f"(vérifie l'orthographe).")
    if not res["away_ok"]:
        print(f"⚠ '{res['away_in']}' inconnu du modèle — forces moyennes de la ligue appliquées "
              f"(vérifie l'orthographe).")
    print(f"Refit sur {res['n_train']} matchs joués {res['league']} jusqu'au "
          f"{res['last_train_date']} (réf. {res['ref_monday']}, match prévu {res['date']}).")
    print(f"Lambdas : {home} λ={res['lam_h']:.2f} | {away} λ={res['lam_a']:.2f}")
    print(f"Pont marché/modèle : {res['fresh_note']}" +
          (f" → poids marché {res['market_weight']:.0%}." if res["market"]
           else f" [{res.get('no_odds_reason') or DEFAULT_NO_ODDS_REASON}]."))
    print()

    market, model_probs, final = res["market"], res["model"], res["final"]
    header = f"{'':22}"
    if market:
        header += f"{'Marché (fair)':>15}"
    header += f"{'Modèle':>12}{'FINAL':>12}"
    print(header)
    for key, label in (("home", f"Victoire {home}"), ("draw", "Match nul"),
                       ("away", f"Victoire {away}")):
        line = f"{label:<22}"
        if market:
            line += f"{fmt(market[key]):>15}"
        line += f"{fmt(model_probs[key]):>12}{fmt(final[key]):>12}"
        print(line)

    grid = res["grid"]
    print()
    print(f"BTTS : {fmt(btts_prob(grid))}   |   Over 2.5 : {fmt(over_prob(grid, 2.5))}")
    print(f"Double chance 1X : {fmt(final['home'] + final['draw'])}   |   "
          f"X2 : {fmt(final['away'] + final['draw'])}")

    print("\nTop 7 des scores exacts (grille du modèle) :")
    for (h, a), p in sorted(grid.items(), key=lambda x: -x[1])[:7]:
        print(f"  {home} {h}-{a} {away}  :  {fmt(p)}")

    print("\nMeilleur score PAR ISSUE (cohérent avec un pronostic d'issue déjà fixé) :")
    for outcome, label in (("home", f"si victoire {home}"), ("draw", "si match nul"),
                           ("away", f"si victoire {away}")):
        sub = [((h, a), p) for (h, a), p in grid.items()
               if (outcome == "home" and h > a) or (outcome == "draw" and h == a)
               or (outcome == "away" and h < a)]
        (bh, ba), bp = max(sub, key=lambda x: x[1])
        mass = sum(p for _, p in sub)
        print(f"  {label:<24}: {bh}-{ba}  ({fmt(bp)} absolu, {fmt(bp / mass)} conditionnel)")

    if market is not None:
        print("\n--- Détection de value (modèle recalibré vs marché) ---")
        any_value = False
        for key, label in (("home", f"Victoire {home}"), ("draw", "Nul"),
                           ("away", f"Victoire {away}")):
            gap = (model_probs[key] - market[key]) * 100
            if abs(gap) >= 8:
                print(f"  VALUE CLAIRE   {label}: modèle {fmt(model_probs[key])} vs "
                      f"marché {fmt(market[key])} ({gap:+.1f} pts)")
                any_value = True
            elif abs(gap) >= 4:
                print(f"  Value modérée  {label}: modèle {fmt(model_probs[key])} vs "
                      f"marché {fmt(market[key])} ({gap:+.1f} pts)")
                any_value = True
        if not any_value:
            print("  Aucune value ≥ 4 pts sur le 1N2 — le modèle confirme le marché.")

        if not no_stake and res["best_odds"] is not None:
            print("\n--- Mise suggérée (Kelly 0.25, plafond 5% de bankroll) ---")
            any_stake = False
            for key, label in (("home", f"Victoire {home}"), ("draw", "Match nul"),
                               ("away", f"Victoire {away}")):
                odds = res["best_odds"][key]
                stake = kelly_stake(final[key], odds)
                if stake > 0:
                    print(f"  {label:<22}: cote {odds:.2f}  |  p={fmt(final[key])}  |  "
                          f"mise conseillée {stake:.1%} de bankroll  ({risk_label(stake)})")
                    any_stake = True
            if not any_stake:
                print("  Aucune issue ne présente de value suffisante — pas de mise.")
            print("  (Estimation mathématique, pas un conseil financier.)")

    if contest is not None:
        run_contest_mode(grid, final, contest, exact_bonus, home, away)


def no_odds_recap(without_odds, total):
    """Récapitulatif des matchs partis sans cote marché, à la fin d'un run.

    Un slate de 29 affiches défile trop vite pour qu'on remarque, ligne à ligne,
    que 22 d'entre elles tournent en modèle pur. Sans ce bloc, l'information ne
    ressort qu'en relisant le journal — c'est-à-dire jamais."""
    if not without_odds:
        return ""
    by_reason = {}
    for res in without_odds:
        by_reason.setdefault(res.get("no_odds_reason") or DEFAULT_NO_ODDS_REASON,
                             []).append(res)
    lines = [f"⚠ {len(without_odds)}/{total} match(s) sans cote marché : modèle pur, "
             f"garde-fou marché désactivé (poids marché 0 %)."]
    for reason in sorted(by_reason):
        lines.append(f"  [{reason}] {NO_ODDS_REASONS[reason]}")
        for res in by_reason[reason]:
            lines.append(f"    - {res['league']} {res['home']}-{res['away']} "
                         f"({res['date']})")
    if DEFAULT_NO_ODDS_REASON in by_reason:
        lines.append(f"  (précise la cause avec --no-odds-reason "
                     f"{{{','.join(DECLARABLE_NO_ODDS_REASONS)}}} pour que le journal "
                     f"garde la trace de la vraie raison.)")
    return "\n".join(lines)


def run_contest_mode(grid, final, pts, bonus, home, away):
    """Mode concours : maximise l'espérance de POINTS, pas la probabilité brute.
    EP(issue) = pts[issue]·P(issue) + bonus·P(meilleur score conditionnel)."""
    cond = {"home": lambda h, a: h > a, "draw": lambda h, a: h == a,
            "away": lambda h, a: h < a}
    labels = {"home": f"Victoire {home}", "draw": "Nul", "away": f"Victoire {away}"}
    rows = []
    for o in ISSUES:
        scores = sorted(((s, p) for s, p in grid.items() if cond[o](*s)), key=lambda x: -x[1])
        best_s, best_p = scores[0]
        rows.append({"o": o, "ep": pts[o] * final[o] + bonus * best_p,
                     "ep_outcome": pts[o] * final[o], "score": best_s, "p_score": best_p})
    rows.sort(key=lambda r: -r["ep"])
    fav = max(final, key=final.get)

    print("\n--- MODE CONCOURS (espérance de points, pas probabilité) ---")
    print(f"Barème : {labels['home']}={pts['home']:g} | Nul={pts['draw']:g} | "
          f"{labels['away']}={pts['away']:g} | bonus score exact={bonus:g}")
    print(f"{'Pick':<24}{'P(issue)':>10}{'EP issue':>10}{'Score':>8}{'P(score)':>10}{'EP TOTAL':>10}")
    for r in rows:
        s = f"{r['score'][0]}-{r['score'][1]}"
        print(f"{labels[r['o']]:<24}{fmt(final[r['o']]):>10}{r['ep_outcome']:>10.2f}"
              f"{s:>8}{fmt(r['p_score']):>10}{r['ep']:>10.2f}")
    top = rows[0]
    ts = f"{top['score'][0]}-{top['score'][1]}"
    print(f"\n>>> PICK CONCOURS : {labels[top['o']]}, score {ts} (espérance {top['ep']:.2f} pts)")
    if top["o"] != fav:
        naive = next(r for r in rows if r["o"] == fav)
        print(f"    ⚠ Le pick EP diverge du favori probabiliste ({labels[fav]}, "
              f"EP {naive['ep']:.2f}) : le barème paye l'écart (+{top['ep'] - naive['ep']:.2f} pts).")


# ---------------------------------------------------------------------------
# Journal (format track.py, relisible par le skill)
# ---------------------------------------------------------------------------

def load_journal(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []


def save_journal(path, entries):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=2))


def prediction_bets(res, no_stake=False):
    """Paris théoriques d'une prédiction : une entrée par issue dont la mise
    Kelly est strictement positive, aux cotes effectivement utilisées.

    Purement descriptif — c'est la trace de ce que la section 💰 a affiché, pour
    pouvoir en mesurer le P&L a posteriori. Aucune mise n'est placée et rien
    ici ne décide de parier : la décision reste humaine."""
    if no_stake or not res.get("best_odds"):
        return []
    bets = []
    for issue in ISSUES:
        odds = float(res["best_odds"][issue])
        stake = kelly_stake(res["final"][issue], odds)
        if stake > 0:
            bets.append({"issue": issue, "odds": odds, "stake_pct": round(stake, 6)})
    return bets


def log_prediction(path, res, no_stake=False):
    """Journalise (ou met à jour) la prédiction. Idempotent : un ré-run du même
    match/date écrase l'entrée non réglée au lieu d'en créer une seconde."""
    entries = load_journal(path)
    match = f"{res['home']}-{res['away']}"
    date_iso = res["date"].isoformat()
    entry = {
        "match": match, "date": date_iso, "competition": res["league"],
        "probs": res["final"],
        "market_probs": res["market"],
        "predicted_score": best_score_for_outcome(res["grid"], max(res["final"], key=res["final"].get)),
        "bets": prediction_bets(res, no_stake),
        "actual_score": None, "actual_ht": None,
        "meta": {"model": "M5", "home": res["home"], "away": res["away"],
                 "lambda_home": round(res["lam_h"], 3),
                 "lambda_away": round(res["lam_a"], 3),
                 "market_weight": round(res["market_weight"], 3),
                 "odds_age_days": res["odds_age_days"],
                 "no_odds_reason": res.get("no_odds_reason")},
    }
    for i, e in enumerate(entries):
        if e["match"] == match and e["date"] == date_iso and e.get("actual_score") is None:
            # Une re-prédiction remplace la prévision, pas les faits déjà
            # constatés : un CLV posé par sync-results survit à la réécriture.
            for k in ("closing_probs", "clv_pct"):
                if e.get(k) is not None:
                    entry[k] = e[k]
            if (e.get("meta") or {}).get("closing_odds_source"):
                entry["meta"]["closing_odds_source"] = e["meta"]["closing_odds_source"]
            entries[i] = entry
            break
    else:
        entries.append(entry)
    save_journal(path, entries)
    return entry


def settle_entry(entry, actual, actual_ht=None):
    """Pose le résultat réel sur une entrée et règle ses paris théoriques.

    Un pari déjà réglé (champ `realized_pct` présent) n'est jamais recalculé —
    le P&L d'un match est figé une fois posé."""
    entry["actual_score"] = actual
    entry["actual_ht"] = actual_ht
    winner = ISSUES[_outcome_index_score(actual)]
    for bet in entry.get("bets") or []:
        if "realized_pct" in bet:
            continue
        stake, odds = float(bet["stake_pct"]), float(bet["odds"])
        gain = stake * (odds - 1.0) if bet["issue"] == winner else -stake
        bet["realized_pct"] = round(gain, 6)
    return entry


def record_result(path, match, actual, actual_ht=None):
    entries = load_journal(path)
    for e in reversed(entries):
        if e["match"].lower() == match.lower() and e.get("actual_score") is None:
            settle_entry(e, actual, actual_ht)
            save_journal(path, entries)
            return e
    sys.exit(f"Aucune prédiction non réglée pour '{match}' dans {path}.")


# ---------------------------------------------------------------------------
# Synchronisation des résultats depuis football.db (fermeture de la boucle)
#
# `result` demande une commande manuelle par match — en pratique le journal
# reste vide et le monitoring ne mesure rien. `sync-results` va chercher les
# scores dans la base déjà alimentée par pipeline.py --update. Règle absolue :
# on ne remplit que ce que la source contient, jamais un score deviné.
# ---------------------------------------------------------------------------

SYNC_TOLERANCE_DAYS = 2   # report de calendrier toléré (même convention que xgjoin)


def league_teams(conn, league):
    """Noms canoniques des équipes vues dans la base pour cette ligue."""
    if not league:
        return []
    rows = conn.execute(
        "SELECT DISTINCT home AS t FROM matches WHERE league = ? "
        "UNION SELECT DISTINCT away AS t FROM matches WHERE league = ?",
        (league, league))
    return [r["t"] for r in rows]


def split_match_key(match, teams, alias_map):
    """'Domicile-Extérieur' -> (domicile, extérieur) canoniques, ou None.

    Un nom d'équipe peut contenir un tiret : on essaie chaque coupure et on ne
    retient que celles dont les DEUX moitiés se résolvent. Si zéro ou plusieurs
    coupures conviennent, on renvoie None — mieux vaut laisser l'entrée en
    attente que de régler le mauvais match."""
    cands = []
    for i, ch in enumerate(match):
        if ch != "-" or i == 0 or i == len(match) - 1:
            continue
        home, home_ok = resolve_team(match[:i], teams, alias_map)
        away, away_ok = resolve_team(match[i + 1:], teams, alias_map)
        if home_ok and away_ok and home != away:
            cands.append((home, away))
    return cands[0] if len(cands) == 1 else None


def entry_teams(entry, teams, alias_map):
    """(domicile, extérieur) d'une entrée : depuis meta si présent (entrées
    récentes), sinon en redécoupant la clé 'match' (entrées historiques)."""
    meta = entry.get("meta") or {}
    if meta.get("home") and meta.get("away"):
        return meta["home"], meta["away"]
    return split_match_key(entry["match"], teams, alias_map)


def find_actual_result(conn, league, home, away, date_iso, tolerance=SYNC_TOLERANCE_DAYS):
    """(ligne, décalage_en_jours) du match joué correspondant, ou (None, None).

    Fenêtre de ±tolerance jours autour de la date prévue : un match reporté
    garde le même couple d'équipes, et deux fois la même affiche en 5 jours
    n'existe pas. Une ligne sans score (fthg NULL) n'est jamais renvoyée."""
    target = datetime.date.fromisoformat(date_iso)
    lo = (target - datetime.timedelta(days=tolerance)).isoformat()
    hi = (target + datetime.timedelta(days=tolerance)).isoformat()
    rows = conn.execute(
        "SELECT date, fthg, ftag, hthg, htag, odds_h, odds_d, odds_a, odds_source "
        "FROM matches "
        "WHERE league = ? AND home = ? AND away = ? AND fthg IS NOT NULL "
        "AND ftag IS NOT NULL AND date BETWEEN ? AND ?",
        (league, home, away, lo, hi)).fetchall()
    if not rows:
        return None, None
    shift = lambda r: (datetime.date.fromisoformat(r["date"]) - target).days
    best = min(rows, key=lambda r: abs(shift(r)))
    return best, shift(best)


# --- CLV (closing line value) ---------------------------------------------
#
# Le Brier a besoin de beaucoup de matchs pour départager un modèle d'un autre ;
# le CLV, lui, est lisible bien plus tôt : il ne demande pas de savoir qui a
# gagné, seulement si la ligne a bougé vers l'issue jouée entre notre cote
# d'entrée et la clôture. C'est le seul signal exploitable sur quelques dizaines
# de matchs, donc celui qui rend le monitoring utile dès la première saison.
#
# On compare deux probas DÉMARGÉES par la même méthode power (backtest.demargin_
# power) : la cote d'entrée journalisée dans `market_probs` d'un côté, la cote de
# clôture de football.db de l'autre. Comparer des cotes brutes mesurerait surtout
# une différence de marge entre books.

def closing_market_probs(row):
    """(probas démargées de clôture, source) d'une ligne `matches`, ou (None, source).

    Renvoie None si la ligne n'a pas de cotes 1N2 exploitables, ou si
    `odds_source` n'est pas une VRAIE source de clôture : football-data se rabat
    sur l'ouverture pour les saisons d'avant 2019-20, et comparer une cote
    d'entrée à une ouverture ne mesure pas du CLV. Aucun repli, aucune
    substitution — sans clôture, pas de CLV."""
    keys = row.keys() if hasattr(row, "keys") else row
    src = row["odds_source"] if "odds_source" in keys else None
    if src not in CLOSING_ODDS_SOURCES:
        return None, src
    odds = [row[c] if c in keys else None for c in ("odds_h", "odds_d", "odds_a")]
    if any(o is None or float(o) <= 1.0 for o in odds):
        return None, src
    return dict(zip(ISSUES, backtest.demargin_power(*(float(o) for o in odds)))), src


def model_issue(entry):
    """Issue jouée par le modèle : l'argmax de ses probas FINAL — la même que
    celle dont `predicted_score` donne le score le plus probable."""
    probs = entry.get("probs") or {}
    return max(ISSUES, key=lambda k: probs.get(k, 0.0)) if probs else None


def clv_pct(market_probs, closing_probs, issue):
    """CLV en %, sur une issue, ou None si l'un des deux côtés manque.

    Écart RELATIF entre la proba implicite de clôture et celle payée à l'entrée
    — même formule que le « Δ vs marché » du rapport (relative_delta), donc même
    échelle de lecture. Convention de signe usuelle du CLV : **positif = on a
    battu la ligne** (la clôture juge l'issue plus probable que la cote prise, on
    a donc encaissé un meilleur prix que le marché final)."""
    if not market_probs or not closing_probs or issue is None:
        return None
    p_in, p_close = market_probs.get(issue), closing_probs.get(issue)
    if p_in is None or p_close is None or p_in <= 0:
        return None
    return relative_delta(p_close, p_in)


def apply_clv(entry, row):
    """Pose `closing_probs` / `clv_pct` sur une entrée depuis la ligne source.

    Additif et idempotent : les champs existants du journal ne sont pas touchés.
    Une clôture introuvable laisse les deux champs à `null` — jamais une cote de
    substitution. Retourne True si un CLV a été calculé."""
    closing, src = closing_market_probs(row)
    entry["closing_probs"] = closing
    entry.setdefault("meta", {})["closing_odds_source"] = src
    issue = model_issue(entry)
    entry["clv_pct"] = None if closing is None else clv_pct(
        entry.get("market_probs"), closing, issue)
    if entry["clv_pct"] is not None:
        entry["clv_pct"] = round(entry["clv_pct"], 4)
        entry["meta"]["clv_issue"] = issue
    return entry["clv_pct"] is not None


def sync_results(conn, path, as_of=None):
    """Remplit actual_score (et le CLV) des matchs passés depuis football.db.

    Renvoie (synchronisés, en_attente, clv_ajoutés). Un match passé absent de la
    base (source en retard, alias manquant) reste `null` et ressort en attente :
    on n'invente jamais un score. Même règle pour le CLV : sans cote de clôture
    dans la source, `closing_probs`/`clv_pct` restent `null`. Suppose que
    `pipeline.py --update` a déjà tourné.

    Le CLV est aussi rattrapé sur les entrées DÉJÀ réglées qui n'en ont pas
    encore : la boucle des résultats s'est fermée avant que ce champ n'existe, et
    la clôture d'un match passé ne bouge plus."""
    as_of = as_of or datetime.date.today()
    entries = load_journal(path)
    alias_map = db.load_aliases(conn)
    teams_cache = {}
    synced, pending, clv_added = [], [], []
    touched = False
    for e in entries:
        if e["date"] >= as_of.isoformat():
            continue
        needs_result = e.get("actual_score") is None
        # Une tentative infructueuse laisse closing_probs à null : on réessaie au
        # run suivant, la source peut avoir rattrapé son retard entre-temps.
        needs_clv = e.get("closing_probs") is None
        if not needs_result and not needs_clv:
            continue
        league = e.get("competition")
        if league not in teams_cache:
            teams_cache[league] = league_teams(conn, league)
        pair = entry_teams(e, teams_cache[league], alias_map)
        if pair is None:
            if needs_result:
                pending.append({"match": e["match"], "date": e["date"],
                                "reason": "équipes non résolues (alias manquant ?)"})
            continue
        row, shift = find_actual_result(conn, league, pair[0], pair[1], e["date"])
        if row is None:
            if needs_result:
                pending.append({"match": e["match"], "date": e["date"],
                                "reason": "absent de football.db (source en retard ?)"})
            continue
        if needs_clv and apply_clv(e, row):
            clv_added.append({"match": e["match"], "date": e["date"],
                              "clv_pct": e["clv_pct"], "issue": e["meta"]["clv_issue"]})
        touched = touched or needs_clv
        if needs_result:
            actual_ht = (f"{row['hthg']}-{row['htag']}"
                         if row["hthg"] is not None and row["htag"] is not None else None)
            settle_entry(e, f"{row['fthg']}-{row['ftag']}", actual_ht)
            synced.append({"match": e["match"], "date": e["date"],
                           "actual": f"{row['fthg']}-{row['ftag']}", "shift": shift,
                           "clv_pct": e.get("clv_pct")})
            touched = True
    if touched:
        save_journal(path, entries)
    return synced, pending, clv_added


# ---------------------------------------------------------------------------
# Rapport de calibration mensuel
# ---------------------------------------------------------------------------

def _outcome_index_score(score):
    h, a = (int(x) for x in score.split("-"))
    return backtest.outcome_index(h, a)


def _probs_tuple(d):
    return (d["home"], d["draw"], d["away"])


def rps(probs, outcome):
    o = [1.0 if k == outcome else 0.0 for k in range(3)]
    return ((probs[0] - o[0]) ** 2 + ((probs[0] + probs[1]) - (o[0] + o[1])) ** 2) / 2.0


def relative_delta(brier, brier_market):
    """Écart au marché en %, RELATIF — même formule que « Écart rel. marché »
    dans report35.py : (Brier − Brier marché) / Brier marché × 100.

    C'est la seule échelle directement comparable au +1,78 % du backtest M3.5,
    que la routine de suivi mensuel demande de confronter au chiffre du mois.
    L'écart absolu qui figurait ici valait ~1,75× moins sur les mêmes données."""
    return (brier - brier_market) / brier_market * 100


def month_metrics(entries):
    n = len(entries)
    b_final = b_mkt = rps_sum = 0.0
    issue_hits = exact_hits = draw_pred = draw_obs = 0.0
    n_mkt = 0
    for e in entries:
        outcome = _outcome_index_score(e["actual_score"])
        pf = _probs_tuple(e["probs"])
        b_final += backtest.brier(pf, outcome)
        rps_sum += rps(pf, outcome)
        draw_pred += e["probs"]["draw"]
        draw_obs += 1.0 if outcome == 1 else 0.0
        if _outcome_index_score(e["predicted_score"]) == outcome:
            issue_hits += 1
        if e["predicted_score"] == e["actual_score"]:
            exact_hits += 1
        if e.get("market_probs"):
            b_mkt += backtest.brier(_probs_tuple(e["market_probs"]), outcome)
            n_mkt += 1
    return {
        "n": n, "brier": b_final / n, "rps": rps_sum / n,
        "issue_rate": issue_hits / n, "exact_rate": exact_hits / n,
        "draw_pred": draw_pred / n, "draw_obs": draw_obs / n,
        "brier_market": (b_mkt / n_mkt) if n_mkt else None, "n_market": n_mkt,
    }


# --- Découpage par fraîcheur des cotes ------------------------------------
#
# Le Brier global agrège des prédictions faites à 92 % de marché et d'autres à
# 28 % : un bon chiffre d'ensemble peut masquer une sous-performance propre aux
# cotes périmées. C'est précisément la zone que backtest_blend.py reconnaît ne
# pas savoir simuler (son proxy sous-estime la péremption réelle), donc la seule
# mesure possible est celle-ci, en production.

BUCKET_MIN_N = 15   # sous ce seuil, lecture indicative et aucun delta

# Seuil d'alerte du bucket « périmées », en points de la colonne « Δ vs marché »
# (écart RELATIF, cf. relative_delta). L'ancien seuil valait 2 pts sur l'échelle
# absolue ; rapporté à un Brier de marché de l'ordre de 0,60 sur du 1N2, cela
# correspond à ~3,3 pts relatifs. Arrondi à 3, donc légèrement plus sensible que
# l'équivalent exact, et lisible face à la référence du backtest (+1,78 % du
# marché) : au-delà, l'écart périmées/fraîches dépasse à lui seul tout l'écart
# modèle/marché mesuré en backtest.
STALE_ALERT_GAP_PCT = 3.0

BUCKET_ORDER = ("fraiches", "intermediaires", "perimees", "inconnue")


def bucket_labels():
    """Libellés des buckets, dérivés des seuils de market_weight() (pas de
    duplication : si un seuil bouge, le rapport suit)."""
    return {
        "fraiches": f"Fraîches (≤ {FRESH_MAX_DAYS} j, poids marché {DEFAULT_BLEND:.0%})",
        "intermediaires": f"Intermédiaires ({FRESH_MAX_DAYS + 1}–{STALE_MIN_DAYS - 1} j, "
                          f"poids dégressif)",
        "perimees": f"Périmées (≥ {STALE_MIN_DAYS} j, poids marché {STALE_FLOOR:.0%})",
        "inconnue": "Fraîcheur non renseignée (hors barème)",
    }


def freshness_bucket(entry):
    """Bucket de fraîcheur d'une entrée, aux seuils exacts de market_weight()."""
    age = (entry.get("meta") or {}).get("odds_age_days")
    if age is None:
        return "inconnue"
    if age <= FRESH_MAX_DAYS:
        return "fraiches"
    if age >= STALE_MIN_DAYS:
        return "perimees"
    return "intermediaires"


def freshness_section(settled):
    """Lignes markdown de la section « Par fraîcheur des cotes »."""
    labels = bucket_labels()
    by_bucket = {}
    for e in settled:
        by_bucket.setdefault(freshness_bucket(e), []).append(e)

    lines = ["## Par fraîcheur des cotes", "",
             f"Découpage sur `meta.odds_age_days` aux seuils du pont marché/modèle "
             f"(`market_weight`) : ≤ {FRESH_MAX_DAYS} j = poids de base "
             f"{DEFAULT_BLEND:.0%}, ≥ {STALE_MIN_DAYS} j = plancher {STALE_FLOOR:.0%}. "
             f"Le Brier global mélange les deux régimes ; c'est ici que se voit une "
             f"sous-performance propre aux cotes périmées.", "",
             "| Fraîcheur | n | dont sans cote | Brier | Brier marché | Δ vs marché | Lecture |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    deltas = {}
    for key in BUCKET_ORDER:
        rows = by_bucket.get(key)
        if not rows:
            continue
        m = month_metrics(rows)
        bmkt = f"{m['brier_market']:.4f}" if m["brier_market"] is not None else "—"
        if m["n"] < BUCKET_MIN_N:
            delta, read = "—", f"indicative (n < {BUCKET_MIN_N})"
        elif m["brier_market"] is None:
            delta, read = "—", "aucune cote journalisée"
        elif m["n_market"] < BUCKET_MIN_N:
            delta, read = "—", f"indicative ({m['n_market']} match(s) avec cotes)"
        else:
            d = relative_delta(m["brier"], m["brier_market"])
            deltas[key] = d
            delta, read = f"{d:+.2f} %", "exploitable"
        # `odds_age_days` est renseigné même quand aucune cote n'a servi (le
        # skill connaît la date des cotes qu'il a cherchées sans les trouver) :
        # ces matchs tournent en modèle pur et pèsent sur le Brier du bucket
        # sans peser sur son Brier marché. La colonne le rend visible.
        no_odds = m["n"] - m["n_market"]
        lines.append(f"| {labels[key]} | {m['n']} | {no_odds} | {m['brier']:.4f} | {bmkt} | "
                     f"{delta} | {read} |")
    lines.append("")

    stale, fresh = deltas.get("perimees"), deltas.get("fraiches")
    if stale is not None and fresh is not None:
        gap = stale - fresh
        if gap > STALE_ALERT_GAP_PCT:
            lines += [f"⚠ Les cotes périmées performent moins bien que prévu par le "
                      f"backtest — le garde-fou mérite d'être revu.",
                      "",
                      f"  (Δ vs marché : {stale:+.2f} % sur cotes périmées contre "
                      f"{fresh:+.2f} % sur cotes fraîches, soit {gap:+.2f} pts d'écart, "
                      f"au-delà du seuil de {STALE_ALERT_GAP_PCT:.0f} pts. À relire sur un "
                      f"trimestre complet avant de toucher au barème.)", ""]
        else:
            lines += [f"- Écart périmées − fraîches : {gap:+.2f} pt(s) de Δ vs marché "
                      f"(seuil d'alerte {STALE_ALERT_GAP_PCT:.0f} pts) — le barème tient.", ""]
    elif "perimees" in by_bucket:
        lines += [f"- Comparaison périmées vs fraîches indisponible : il faut "
                  f"n ≥ {BUCKET_MIN_N} avec cotes dans LES DEUX buckets.", ""]
    return lines


# --- CLV (closing line value) ----------------------------------------------
#
# Le Brier a besoin de centaines de matchs pour départager deux jeux de probas ;
# le CLV se lit sur quelques dizaines, parce qu'il ne dépend pas de qui a gagné.
# C'est donc le premier signal utilisable du monitoring, à condition de ne le
# trancher qu'au-delà du même seuil de significativité que le reste du rapport.

CLV_MIN_N = BUCKET_MIN_N   # même garde-fou de significativité que les buckets

# Bande neutre autour de zéro, en points de CLV relatif. Le CLV d'un match se
# compte en dizaines de points (une ligne qui bouge un peu déplace la proba
# implicite de plusieurs %) : une moyenne à quelques dixièmes de point n'est pas
# un « sourcing perdant », c'est du bruit. En dessous, on constate l'alignement
# sur la clôture au lieu de crier au loup.
CLV_FLAT_PCT = 1.0


def clv_stats(entries):
    """(n_avec_clv, n_total, moyenne) sur une liste d'entrées."""
    vals = [e["clv_pct"] for e in entries if e.get("clv_pct") is not None]
    return len(vals), len(entries), (sum(vals) / len(vals) if vals else None)


def clv_section(settled):
    """Lignes markdown de la section « CLV (closing line value) »."""
    lines = ["## CLV (closing line value)", "",
             "Écart **relatif** entre la proba implicite de clôture (cote de "
             "football.db, démargée power) et celle de la cote utilisée au moment "
             "de la prédiction, sur l'issue jouée par le modèle. Même formule que "
             "« Δ vs marché » ci-dessus. **Positif = la cote prise battait la "
             "clôture** : la ligne a bougé vers notre issue.", ""]
    n_all, total, avg_all = clv_stats(settled)
    if not n_all:
        lines += ["Aucun CLV disponible : il faut à la fois une cote journalisée à "
                  "la prédiction (`market_probs`) et une cote de clôture en base "
                  "(`python pipeline.py --update` puis `predict.py sync-results`). "
                  "Sans l'une des deux, le champ reste `null` — jamais estimé.", ""]
        return lines

    by_league = {}
    for e in settled:
        by_league.setdefault(e.get("competition") or "?", []).append(e)
    lines += ["| Ligue | n avec CLV | n réglés | CLV moyen | Lecture |",
              "| --- | --- | --- | --- | --- |"]
    for league in sorted(by_league):
        n, tot, avg = clv_stats(by_league[league])
        if not n:
            lines.append(f"| {league} | 0 | {tot} | — | aucune clôture appariée |")
            continue
        read = "exploitable" if n >= CLV_MIN_N else f"indicative (n < {CLV_MIN_N})"
        lines.append(f"| {league} | {n} | {tot} | {avg:+.2f} % | {read} |")
    read = "exploitable" if n_all >= CLV_MIN_N else f"indicative (n < {CLV_MIN_N})"
    lines += [f"| **Total** | {n_all} | {total} | {avg_all:+.2f} % | {read} |", ""]

    if n_all < CLV_MIN_N:
        lines += [f"- ⚠ {n_all} match(s) avec CLV (< {CLV_MIN_N}) : lecture indicative, "
                  f"aucun verdict de tendance.", ""]
    elif abs(avg_all) <= CLV_FLAT_PCT:
        lines += [f"- CLV moyen {avg_all:+.2f} % sur {n_all} match(s) : dans la bande "
                  f"neutre (±{CLV_FLAT_PCT:.0f} pt) — les cotes retenues suivent la "
                  f"clôture, ni battue ni subie. Rien à corriger côté sourcing.", ""]
    elif avg_all > 0:
        lines += [f"- CLV moyen {avg_all:+.2f} % sur {n_all} match(s) : les cotes "
                  f"retenues battent la clôture en moyenne — le sourcing des cotes "
                  f"prend la ligne du bon côté.", ""]
    else:
        lines += [f"- ⚠ CLV moyen {avg_all:+.2f} % sur {n_all} match(s) : les cotes "
                  f"retenues sont en moyenne moins bonnes que la clôture. À creuser "
                  f"côté sourcing (books mous, cotes recopiées trop tard) avant toute "
                  f"lecture du ROI.", ""]
    missing = total - n_all
    if missing:
        lines += [f"- {missing} match(s) réglé(s) sans CLV : pas de cote à la "
                  f"prédiction, ou pas de cote de clôture en base pour cette affiche. "
                  f"Laissés à `null`, jamais estimés.", ""]
    return lines


# --- ROI réalisé des mises Kelly théoriques --------------------------------

ROI_MIN_BETS = 100   # sous ce seuil, la variance des cotes 1N2 rend le ROI non informatif


def roi_summary(settled):
    """(nb_paris_réglés, mise_totale, p_and_l) en fractions de bankroll."""
    n = 0
    staked = pnl = 0.0
    for e in settled:
        for b in e.get("bets") or []:
            if "realized_pct" not in b:
                continue
            n += 1
            staked += float(b["stake_pct"])
            pnl += float(b["realized_pct"])
    return n, staked, pnl


def roi_section(settled):
    """Lignes markdown de la section « ROI réel (mise Kelly théorique) »."""
    n, staked, pnl = roi_summary(settled)
    lines = ["## ROI réel (mise Kelly théorique)", ""]
    if not n:
        lines += ["Aucun pari réglé sur la période : soit les prédictions n'avaient "
                  "pas de cote exploitable, soit leurs résultats ne sont pas encore "
                  "synchronisés (`python predict.py sync-results`).", ""]
        return lines
    lines += [f"- {n} pari(s) réglé(s) — mise cumulée {staked:.2%} de bankroll "
              f"(somme des mises successives, pas une exposition simultanée), "
              f"P&L {pnl:+.3%} de bankroll"
              + (f", soit un ROI de {pnl / staked:+.1%} de la mise." if staked else "."),
              "- Ce ROI est **théorique** : les mises n'ont jamais été placées, elles "
              "sont recalculées depuis les cotes journalisées (Kelly 0.25 plafonné à "
              "5 %). Ce n'est pas un P&L vérifié par un bookmaker."]
    if n < ROI_MIN_BETS:
        lines.append(f"- ⚠ {n} paris réglés (< {ROI_MIN_BETS}) : échantillon insuffisant "
                     f"pour une lecture fiable du ROI — la variance sur des cotes 1N2 "
                     f"rend un tel échantillon quasi non-informatif.")
    lines.append("")
    return lines


def build_calibration_report(path, month_filter=None):
    settled = [e for e in load_journal(path) if e.get("actual_score")]
    if month_filter:
        settled = [e for e in settled if e["date"].startswith(month_filter)]
    if not settled:
        sys.exit("Aucune prédiction réglée dans le journal (avec ce filtre) — rien à mesurer.")

    by_month = {}
    for e in settled:
        by_month.setdefault(e["date"][:7], []).append(e)

    lines = ["# Monitoring de production — calibration mensuelle", ""]
    lines += [f"Journal : `{path}` — {len(settled)} prédiction(s) réglée(s), "
              f"{len(by_month)} mois. Référence Brier hasard = 0.6667 (plus bas = mieux). "
              f"« Δ vs marché » = écart **relatif** (Brier − Brier marché) / Brier marché, "
              f"même échelle que le backtest (M3.5 : +1,78 % du marché).", ""]
    lines += ["## Par mois", "",
              "| Mois | n | Brier | Brier marché | Δ vs marché | RPS | Issue OK | Score exact | Nuls prédits/obs |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for month in sorted(by_month):
        m = month_metrics(by_month[month])
        bmkt = f"{m['brier_market']:.4f}" if m["brier_market"] is not None else "—"
        delta = (f"{relative_delta(m['brier'], m['brier_market']):+.2f} %"
                 if m["brier_market"] is not None else "—")
        lines.append(f"| {month} | {m['n']} | {m['brier']:.4f} | {bmkt} | {delta} | "
                     f"{m['rps']:.4f} | {m['issue_rate']:.0%} | {m['exact_rate']:.0%} | "
                     f"{m['draw_pred']:.0%} / {m['draw_obs']:.0%} |")
    overall = month_metrics(settled)
    bmkt = f"{overall['brier_market']:.4f}" if overall["brier_market"] is not None else "—"
    delta = (f"{relative_delta(overall['brier'], overall['brier_market']):+.2f} %"
             if overall["brier_market"] is not None else "—")
    lines.append(f"| **Total** | {overall['n']} | {overall['brier']:.4f} | {bmkt} | {delta} | "
                 f"{overall['rps']:.4f} | {overall['issue_rate']:.0%} | "
                 f"{overall['exact_rate']:.0%} | {overall['draw_pred']:.0%} / {overall['draw_obs']:.0%} |")
    lines.append("")

    lines += freshness_section(settled)
    lines += clv_section(settled)
    lines += roi_section(settled)

    # Focus sur le dernier mois (ou le mois filtré)
    focus = month_filter or sorted(by_month)[-1]
    if focus in by_month:
        m = month_metrics(by_month[focus])
        lines += [f"## Focus {focus}", "",
                  f"- {m['n']} match(s) réglé(s), Brier {m['brier']:.4f}, issues correctes "
                  f"{m['issue_rate']:.0%}, scores exacts {m['exact_rate']:.0%}."]
        draw_gap = m["draw_obs"] - m["draw_pred"]
        if abs(draw_gap) > 0.07:
            sens = "sous-estime" if draw_gap > 0 else "surestime"
            lines.append(f"- ⚠ Nuls : le modèle {sens} les nuls ({m['draw_pred']:.0%} prédits "
                         f"vs {m['draw_obs']:.0%} observés).")
        if m["brier_market"] is not None:
            gap = m["brier"] - m["brier_market"]
            if m["n_market"] < 15:
                verdict = "échantillon trop petit pour trancher"
            elif gap < -0.01:
                verdict = "le blend bat le marché seul — la couche modèle ajoute de la valeur"
            elif gap > 0.01:
                verdict = "le marché seul fait mieux — laisser le poids marché élevé sur cotes fraîches"
            else:
                verdict = "équivalent au marché"
            lines.append(f"- FINAL vs marché ({m['n_market']} match(s) avec cotes) : "
                         f"Brier {m['brier']:.4f} vs {m['brier_market']:.4f} → {verdict}.")
        if m["n"] < 15:
            lines.append("- ⚠ Moins de 15 matchs : lecture indicative, pas de conclusion structurelle.")
    lines.append("")
    return "\n".join(lines), overall


# ---------------------------------------------------------------------------
# Export natif du skill football-match-predictor
# ---------------------------------------------------------------------------

def load_skill_json(source):
    """Charge l'export JSON du skill. source='-' lit stdin (coller sans fichier)."""
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text()
    except OSError as e:
        sys.exit(f"--from-skill-json : lecture impossible ({e}).")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"--from-skill-json : JSON malformé ({e}).")
    if not isinstance(doc, dict):
        sys.exit("--from-skill-json : la racine JSON doit être un objet.")
    if doc.get("schema") != SKILL_SCHEMA:
        sys.exit(f"--from-skill-json : schéma '{doc.get('schema')}' inattendu "
                 f"(attendu '{SKILL_SCHEMA}').")
    return doc


def skill_json_to_fixture(doc):
    """Extrait de l'export les champs mappables sur les arguments de `match`.

    Champs lus : league, home, away, match_date, odds_date, odds_1x2 et
    `no_odds_reason` (optionnel : pourquoi l'export ne porte pas de cote — c'est
    le skill qui le sait, pas predict.py). Les champs
    `ou` (le modèle de production price les scores depuis sa propre grille, il ne
    se cale pas sur les cotes O/U) et `final_probs_1x2` (predict.py recalcule son
    propre FINAL) sont ignorés — voir la note émise à l'appel."""
    league = doc.get("league")
    if league not in footballdata.LEAGUES:
        sys.exit(f"--from-skill-json : league '{league}' absente ou invalide "
                 f"(attendu l'une de {footballdata.LEAGUES}) — on ne devine pas.")
    home, away = doc.get("home"), doc.get("away")
    if not home or not away:
        sys.exit("--from-skill-json : champs 'home' et 'away' requis.")
    odds_spec = None
    o = doc.get("odds_1x2")
    if o is not None:
        try:
            odds_spec = f"{float(o['home'])},{float(o['draw'])},{float(o['away'])}"
        except (KeyError, TypeError, ValueError):
            sys.exit("--from-skill-json : 'odds_1x2' doit contenir home, draw, away numériques.")
    reason = doc.get("no_odds_reason")
    if reason is not None and reason not in DECLARABLE_NO_ODDS_REASONS:
        sys.exit(f"--from-skill-json : 'no_odds_reason' vaut '{reason}', attendu l'une de "
                 f"{list(DECLARABLE_NO_ODDS_REASONS)} — on ne devine pas une raison.")
    return {"league": league, "home": home, "away": away, "odds_spec": odds_spec,
            "match_date": doc.get("match_date"), "odds_date": doc.get("odds_date"),
            "no_odds_reason": reason}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_match(args, conn):
    if args.from_skill_json:
        if (args.home or args.away or args.fixture or args.odds or args.date
                or args.odds_date or args.odds_age_days is not None):
            sys.exit("--from-skill-json fournit déjà league/home/away/odds/dates — "
                     "ne les repasse pas aussi en arguments individuels.")
        doc = load_skill_json(args.from_skill_json)
        fx = skill_json_to_fixture(doc)
        args.league, args.home, args.away = fx["league"], fx["home"], fx["away"]
        if fx["odds_spec"]:
            args.odds = [fx["odds_spec"]]
        args.date, args.odds_date = fx["match_date"], fx["odds_date"]
        # L'export sait pourquoi il n'a pas de cote ; le CLI reste prioritaire
        # s'il en déclare une aussi (l'opérateur a le dernier mot).
        if fx["no_odds_reason"] and not args.no_odds_reason:
            args.no_odds_reason = fx["no_odds_reason"]
        if doc.get("ou") is not None:
            log.info("Export skill : champ 'ou' présent mais non consommé — le modèle de "
                     "production price les scores depuis sa propre grille entraînée, il ne se "
                     "cale pas sur les cotes O/U du marché.")


    cfg = backtest35.frozen()
    target_date = (datetime.date.fromisoformat(args.date) if args.date else next_saturday())

    odds_age = None
    if args.odds_date:
        odds_age = max(0, (target_date - datetime.date.fromisoformat(args.odds_date)).days)
    elif args.odds_age_days is not None:
        odds_age = max(0, args.odds_age_days)

    contest = None
    if args.contest_points is not None:
        try:
            ph, pn, pa = (float(x) for x in args.contest_points.split(","))
        except ValueError:
            sys.exit("--contest-points attend 'H,N,A' (ex: 13,50,68)")
        contest = {"home": ph, "draw": pn, "away": pa}

    fixtures = []
    if args.home and args.away:
        if not args.league:
            sys.exit("--home/--away nécessitent --league (ou utilise --fixture LIGUE,Dom,Ext).")
        fixtures.append((args.league, args.home, args.away))
    for spec in args.fixture:
        parts = [p.strip() for p in spec.split(",")]
        if len(parts) != 3:
            sys.exit(f"--fixture invalide: '{spec}' (attendu LIGUE,Domicile,Extérieur)")
        fixtures.append(tuple(parts))
    if not fixtures:
        sys.exit("Fournis --home/--away (avec --league) ou au moins un --fixture LIGUE,Dom,Ext.")
    no_odds_reason = args.no_odds_reason
    if args.odds and len(fixtures) > 1:
        log.warning("--odds ne s'applique qu'à un match unique — ignoré pour un slate "
                    "(%d affiches). Passe chaque match séparément pour blender ses cotes.",
                    len(fixtures))
        # Cette perte était jusqu'ici purement verbale : le journal n'en gardait
        # qu'un market_weight nul. On la nomme dans chaque entrée.
        no_odds_reason = "slate_odds_ignored"

    fit_cache = {}
    without_odds = []
    for i, (league, home, away) in enumerate(fixtures):
        if league not in footballdata.LEAGUES:
            sys.exit(f"Ligue inconnue '{league}' (attendu {footballdata.LEAGUES}).")
        if i:
            print("\n" + "=" * 72 + "\n")
        res = predict_match(conn, cfg, league, home, away, target_date,
                            args.odds if len(fixtures) == 1 else [], odds_age,
                            args.blend, fit_cache, no_odds_reason)
        print_prediction(res, cfg, contest, args.contest_exact_bonus, args.no_stake)
        if res["market"] is None:
            without_odds.append(res)
        if not args.no_log:
            log_prediction(args.log, res, args.no_stake)
    recap = no_odds_recap(without_odds, len(fixtures))
    if recap:
        print("\n" + recap)
    if not args.no_log:
        print(f"\n{len(fixtures)} prédiction(s) journalisée(s) dans {args.log}.")


def cmd_result(args, conn):
    e = record_result(args.log, args.match, args.actual, args.ht)
    pred_out = _outcome_index_score(e["predicted_score"])
    act_out = _outcome_index_score(args.actual)
    issue = "ISSUE OK" if pred_out == act_out else "issue ratée"
    exact = " + SCORE EXACT !" if e["predicted_score"] == args.actual else ""
    print(f"Résultat enregistré : {args.match} {args.actual} — {issue}{exact}")


def cmd_sync_results(args, conn):
    as_of = datetime.date.fromisoformat(args.as_of) if args.as_of else None
    synced, pending, clv_added = sync_results(conn, args.log, as_of)
    for s in synced:
        note = f"  (joué à {s['shift']:+d} j de la date prévue)" if s["shift"] else ""
        clv = f"  CLV {s['clv_pct']:+.2f} %" if s["clv_pct"] is not None else ""
        print(f"  OK  {s['date']}  {s['match']} : {s['actual']}{note}{clv}")
    if clv_added:
        avg = sum(c["clv_pct"] for c in clv_added) / len(clv_added)
        print(f"\nCLV posé sur {len(clv_added)} match(s) — moyenne {avg:+.2f} % "
              f"(positif = cote d'entrée meilleure que la clôture).")
    if pending:
        print("\nEn attente de données source :")
        for p in pending:
            print(f"  ..  {p['date']}  {p['match']} — {p['reason']}")
        print("  (relance `python pipeline.py --update` puis cette commande ; si "
              "l'attente persiste, creuse la source football-data ou l'alias manquant.)")
    print(f"\n{len(synced)} résultat(s) synchronisé(s), {len(pending)} encore en attente.")


def cmd_report(args, conn):
    text, overall = build_calibration_report(args.log, args.month)
    CAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAL_REPORT_PATH.write_text(text)
    print(text)
    print(f"Rapport écrit dans {CAL_REPORT_PATH}", file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=str(db.DB_PATH))
    common.add_argument("--log", default=str(JOURNAL_PATH), help="journal de production (JSON)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("match", parents=[common], help="prédire un ou plusieurs matchs à venir")
    p.add_argument("--from-skill-json", default=None, metavar="FICHIER",
                   help=f"Lit un export JSON du skill football-match-predictor (schéma "
                        f"{SKILL_SCHEMA}) et le mappe sur league/home/away/odds/dates ; "
                        f"'-' = stdin. Exclusif des arguments individuels correspondants.")
    p.add_argument("--league", choices=footballdata.LEAGUES)
    p.add_argument("--home")
    p.add_argument("--away")
    p.add_argument("--fixture", action="append", default=[],
                   help="Affiche 'LIGUE,Domicile,Extérieur' (répétable, pour un slate de week-end)")
    p.add_argument("--date", default=None, help="Date du match (défaut : samedi à venir)")
    p.add_argument("--odds", action="append", default=[],
                   help="Cotes 1N2 'home,nul,away' (répétable, un par bookmaker)")
    p.add_argument("--odds-date", default=None,
                   help="Date de publication des cotes (fixe la fraîcheur du pont marché)")
    p.add_argument("--odds-age-days", type=int, default=None,
                   help="Âge des cotes en jours (alternative à --odds-date)")
    p.add_argument("--blend", type=float, default=DEFAULT_BLEND,
                   help=f"Poids marché de base sur cotes fraîches (défaut {DEFAULT_BLEND:g} ; "
                        f"décroît vers un plancher de {STALE_FLOOR:.0%} si périmées)")
    p.add_argument("--contest-points", default=None, metavar="H,N,A",
                   help="MODE CONCOURS : points si l'issue est correcte (ex: 13,50,68)")
    p.add_argument("--contest-exact-bonus", type=float, default=0.0, metavar="B",
                   help="Points bonus si le score exact est correct (défaut 0)")
    p.add_argument("--no-odds-reason", choices=DECLARABLE_NO_ODDS_REASONS, default=None,
                   help="Pourquoi aucune cote n'est fournie, journalisé dans "
                        "meta.no_odds_reason. Sans ce drapeau l'entrée est marquée "
                        f"'{DEFAULT_NO_ODDS_REASON}' — jamais une raison devinée.")
    p.add_argument("--no-stake", action="store_true", help="désactive la section mise suggérée")
    p.add_argument("--no-log", action="store_true", help="ne pas journaliser la prédiction")
    p.set_defaults(func=cmd_match)

    r = sub.add_parser("result", parents=[common], help="enregistrer le résultat réel d'une prédiction")
    r.add_argument("--match", required=True, help="'Domicile-Extérieur' (comme journalisé)")
    r.add_argument("--actual", required=True, help="Score réel, ex: 2-1")
    r.add_argument("--ht", default=None, help="Score mi-temps 'h-a' (optionnel)")
    r.set_defaults(func=cmd_result)

    s = sub.add_parser("sync-results", parents=[common],
                       help="remplir les résultats réels depuis football.db "
                            "(après `pipeline.py --update`)")
    s.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                   help="Date de référence : seuls les matchs antérieurs sont "
                        "synchronisés (défaut : aujourd'hui)")
    s.set_defaults(func=cmd_sync_results)

    rep = sub.add_parser("report", parents=[common], help="rapport de calibration mensuel du monitoring")
    rep.add_argument("--month", default=None, help="Filtrer sur un mois 'YYYY-MM'")
    rep.set_defaults(func=cmd_report)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    conn = db.connect(args.db)
    try:
        args.func(args, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
