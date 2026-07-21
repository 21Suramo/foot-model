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
                  odds_specs, odds_age_days, blend, fit_cache):
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
    fresh_note = "aucune cote fournie — modèle seul"
    if odds_specs:
        triples = parse_odds_triples(odds_specs)
        market, best_odds = market_consensus(triples)
        all_ok = all(margin_ok(t)[0] for t in triples)
        m_weight, _, fresh_note = market_weight(blend, odds_age_days, all_ok)

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
        "odds_age_days": odds_age_days,
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
          (f" → poids marché {res['market_weight']:.0%}." if res["market"] else "."))
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


def log_prediction(path, res):
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
        "bets": [], "actual_score": None, "actual_ht": None,
        "meta": {"model": "M5", "lambda_home": round(res["lam_h"], 3),
                 "lambda_away": round(res["lam_a"], 3),
                 "market_weight": round(res["market_weight"], 3),
                 "odds_age_days": res["odds_age_days"]},
    }
    for i, e in enumerate(entries):
        if e["match"] == match and e["date"] == date_iso and e.get("actual_score") is None:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    save_journal(path, entries)
    return entry


def record_result(path, match, actual, actual_ht=None):
    entries = load_journal(path)
    for e in reversed(entries):
        if e["match"].lower() == match.lower() and e.get("actual_score") is None:
            e["actual_score"] = actual
            e["actual_ht"] = actual_ht
            save_journal(path, entries)
            return e
    sys.exit(f"Aucune prédiction non réglée pour '{match}' dans {path}.")


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
              f"{len(by_month)} mois. Référence Brier hasard = 0.6667 (plus bas = mieux).", ""]
    lines += ["## Par mois", "",
              "| Mois | n | Brier | Brier marché | Δ vs marché | RPS | Issue OK | Score exact | Nuls prédits/obs |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for month in sorted(by_month):
        m = month_metrics(by_month[month])
        bmkt = f"{m['brier_market']:.4f}" if m["brier_market"] is not None else "—"
        delta = (f"{(m['brier'] - m['brier_market']) * 100:+.2f} %"
                 if m["brier_market"] is not None else "—")
        lines.append(f"| {month} | {m['n']} | {m['brier']:.4f} | {bmkt} | {delta} | "
                     f"{m['rps']:.4f} | {m['issue_rate']:.0%} | {m['exact_rate']:.0%} | "
                     f"{m['draw_pred']:.0%} / {m['draw_obs']:.0%} |")
    overall = month_metrics(settled)
    bmkt = f"{overall['brier_market']:.4f}" if overall["brier_market"] is not None else "—"
    delta = (f"{(overall['brier'] - overall['brier_market']) * 100:+.2f} %"
             if overall["brier_market"] is not None else "—")
    lines.append(f"| **Total** | {overall['n']} | {overall['brier']:.4f} | {bmkt} | {delta} | "
                 f"{overall['rps']:.4f} | {overall['issue_rate']:.0%} | "
                 f"{overall['exact_rate']:.0%} | {overall['draw_pred']:.0%} / {overall['draw_obs']:.0%} |")
    lines.append("")

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

    Champs lus : league, home, away, match_date, odds_date, odds_1x2. Les champs
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
    return {"league": league, "home": home, "away": away, "odds_spec": odds_spec,
            "match_date": doc.get("match_date"), "odds_date": doc.get("odds_date")}


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
    if args.odds and len(fixtures) > 1:
        log.warning("--odds ne s'applique qu'à un match unique — ignoré pour un slate "
                    "(%d affiches). Passe chaque match séparément pour blender ses cotes.",
                    len(fixtures))

    fit_cache = {}
    for i, (league, home, away) in enumerate(fixtures):
        if league not in footballdata.LEAGUES:
            sys.exit(f"Ligue inconnue '{league}' (attendu {footballdata.LEAGUES}).")
        if i:
            print("\n" + "=" * 72 + "\n")
        res = predict_match(conn, cfg, league, home, away, target_date,
                            args.odds if len(fixtures) == 1 else [], odds_age,
                            args.blend, fit_cache)
        print_prediction(res, cfg, contest, args.contest_exact_bonus, args.no_stake)
        if not args.no_log:
            log_prediction(args.log, res)
    if not args.no_log:
        print(f"\n{len(fixtures)} prédiction(s) journalisée(s) dans {args.log}.")


def cmd_result(args, conn):
    e = record_result(args.log, args.match, args.actual, args.ht)
    pred_out = _outcome_index_score(e["predicted_score"])
    act_out = _outcome_index_score(args.actual)
    issue = "ISSUE OK" if pred_out == act_out else "issue ratée"
    exact = " + SCORE EXACT !" if e["predicted_score"] == args.actual else ""
    print(f"Résultat enregistré : {args.match} {args.actual} — {issue}{exact}")


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
    p.add_argument("--no-stake", action="store_true", help="désactive la section mise suggérée")
    p.add_argument("--no-log", action="store_true", help="ne pas journaliser la prédiction")
    p.set_defaults(func=cmd_match)

    r = sub.add_parser("result", parents=[common], help="enregistrer le résultat réel d'une prédiction")
    r.add_argument("--match", required=True, help="'Domicile-Extérieur' (comme journalisé)")
    r.add_argument("--actual", required=True, help="Score réel, ex: 2-1")
    r.add_argument("--ht", default=None, help="Score mi-temps 'h-a' (optionnel)")
    r.set_defaults(func=cmd_result)

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
