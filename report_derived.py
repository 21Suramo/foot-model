"""Rapport M7 (roadmap) : validation indépendante des marchés dérivés (O/U 2,5, BTTS).

Lit predictions_derived (backtest_derived.py --run), data/derived_markets_frozen.json,
data/leak_check_derived.json, écrit reports/derived_markets_backtest.md.

Usage : python report_derived.py [--db data/football.db]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest_derived as bd
import bootstrap
import db
import footballdata
from report import CALIB_MIN_N, CALIB_TOL, CLEAR_MARGIN, fmt_row

OUT_PATH = Path("reports/derived_markets_backtest.md")

# Le marché BTTS n'a pas de cote dans football-data.co.uk (seuls 1X2, O/U 2,5
# et handicap asiatique y figurent) : le critère « vs marché » y est donc
# structurellement inapplicable, pas un trou de mesure à corriger ici.
MARKETS_WITH_MARKET_ODDS = {"ou25"}


def load_predictions(conn, market):
    rows = [dict(r) for r in conn.execute(
        "SELECT p.*, m.league, m.season, m.date, m.home, m.away, m.fthg, m.ftag "
        "FROM predictions_derived p JOIN matches m USING (match_id) "
        "WHERE p.market = ? ORDER BY m.date, p.match_id", (market,))]
    for r in rows:
        r["outcome"] = bd._market_outcome(market, r["fthg"], r["ftag"])
    return rows


def brier_series(rows, key):
    return [bd.binary_brier(r[key], r["outcome"]) for r in rows if r.get(key) is not None]


def mean_brier(rows, key):
    s = brier_series(rows, key)
    return float(np.mean(s)) if s else None


def calibration_table(rows, key="model_p", width=0.05):
    pairs = [(r[key], 1.0 if r["outcome"] else 0.0) for r in rows if r.get(key) is not None]
    arr = np.array(pairs)
    bins = np.floor(arr[:, 0] / width).astype(int)
    table = []
    for b in sorted(set(bins)):
        sel = arr[bins == b]
        table.append({"lo": b * width, "hi": (b + 1) * width, "n": len(sel),
                      "pred": float(sel[:, 0].mean()), "obs": float(sel[:, 1].mean())})
    return table


def market_section(conn, market, leak):
    rows = load_predictions(conn, market)
    if not rows:
        return [f"## {bd.MARKET_LABELS[market]}", "",
                "Aucune prédiction de test — lancer `python backtest_derived.py --run`.", ""]

    lines = [f"## {bd.MARKET_LABELS[market]}", "",
             f"{len(rows)} matchs de test (saisons {', '.join(backtest.TEST)}), 3 ligues, "
             f"refit hebdomadaire. Grille de score du modèle M3.5 déjà figé (w/ξ/κ inchangés), "
             f"recalibration binaire propre à ce marché réglée sur la validation "
             f"{'+'.join(backtest.VALIDATION)}.", ""]

    b_model = mean_brier(rows, "model_p")
    b_raw = mean_brier(rows, "raw_p")
    b_freq = mean_brier(rows, "freq_p")
    b_unif = float(np.mean([bd.binary_brier(0.5, r["outcome"]) for r in rows]))
    beats_freq = (b_freq - b_model) / b_freq
    beats_unif = (b_unif - b_model) / b_unif

    calib = calibration_table(rows)
    big = [c for c in calib if c["n"] >= CALIB_MIN_N]
    worst = max(big, key=lambda c: abs(c["obs"] - c["pred"])) if big else None

    checks = []
    has_market = market in MARKETS_WITH_MARKET_ODDS
    n_with_market = sum(1 for r in rows if r.get("market_p") is not None)
    if has_market and n_with_market:
        model_b = brier_series(rows, "model_p")
        market_b_full = [bd.binary_brier(r["market_p"], r["outcome"]) for r in rows if r.get("market_p") is not None]
        # Séries appariées : ne garder que les matchs où le modèle ET le marché existent (toujours vrai ici).
        rel_market = (mean_brier(rows, "model_p") - mean_brier(rows, "market_p")) / mean_brier(rows, "market_p")
        model_b_paired = [bd.binary_brier(r["model_p"], r["outcome"]) for r in rows if r.get("market_p") is not None]
        _, lo, hi = bootstrap.ci_relative_delta(model_b_paired, market_b_full)
        checks.append((rel_market < 0.02,
                       f"Brier modèle à {rel_market * 100:+.2f} % du marché "
                       f"{bootstrap.fmt_ci(lo, hi)} (critère < +2 %), sur {n_with_market} "
                       f"match(s) avec cote O/U 2,5."))
    else:
        checks.append((None, "Comparaison au marché : **inapplicable** — aucune cote BTTS "
                             "dans football-data.co.uk (seuls 1X2, O/U 2,5 et handicap "
                             "asiatique y figurent). Pas de source, pas de critère fabriqué."))
    checks.append((beats_freq >= CLEAR_MARGIN and beats_unif >= CLEAR_MARGIN,
                   f"Bat les baselines : {beats_freq * 100:+.1f} % vs fréquences, "
                   f"{beats_unif * 100:+.1f} % vs uniforme (0,5) (critère ≥ {CLEAR_MARGIN * 100:.0f} % chacune)"))
    if worst is not None:
        checks.append((abs(worst["obs"] - worst["pred"]) <= CALIB_TOL,
                       f"Calibration : pire tranche (n ≥ {CALIB_MIN_N}) à "
                       f"{abs(worst['obs'] - worst['pred']) * 100:.1f} pts d'écart "
                       f"(tolérance {CALIB_TOL * 100:.0f} pts)"))
    leak_m = (leak or {}).get("leagues", {})
    if leak_m:
        all_degraded = all(v[market]["degraded"] for v in leak_m.values())
        checks.append((all_degraded, "Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés"))

    lines += ["### Verdict", ""]
    for ok, txt in checks:
        mark = "➖" if ok is None else ("✅" if ok else "❌")
        lines.append(f"- {mark} {txt}")
    lines.append("")

    lines += ["### Résultats agrégés", "",
              fmt_row(["Méthode", "Brier", "n"]), fmt_row(["---"] * 3)]
    lines.append(fmt_row(["Modèle (recalibré)", f"{b_model:.5f}", len(rows)]))
    lines.append(fmt_row(["Modèle (brut, avant recalibration)", f"{b_raw:.5f}", len(rows)]))
    if has_market and n_with_market:
        lines.append(fmt_row(["Marché (démargé proportionnel)",
                              f"{mean_brier(rows, 'market_p'):.5f}", n_with_market]))
    lines.append(fmt_row(["Fréquences (walk-forward)", f"{b_freq:.5f}", len(rows)]))
    lines.append(fmt_row(["Uniforme (0,5)", f"{b_unif:.5f}", len(rows)]))
    lines.append("")

    lines += ["### Calibration (tranches de 5 pts)", "",
              fmt_row(["Tranche", "n", "Proba prédite moy.", "Fréquence observée", "Écart"]),
              fmt_row(["---"] * 5)]
    for c in calib:
        mark = "" if c["n"] >= CALIB_MIN_N else " *"
        lines.append(fmt_row([f"{c['lo'] * 100:.0f}–{c['hi'] * 100:.0f} %{mark}", c["n"],
                              f"{c['pred'] * 100:.1f} %", f"{c['obs'] * 100:.1f} %",
                              f"{(c['obs'] - c['pred']) * 100:+.1f} pts"]))
    lines += ["", f"\\* tranches sous n = {CALIB_MIN_N}, hors verdict.", ""]

    lines += ["### Par ligue et saison de test", "",
              fmt_row(["Ligue", "Saison", "n", "Modèle"] +
                     (["Marché", "Écart rel. marché"] if has_market else []) + ["Fréquences"]),
              fmt_row(["---"] * (4 + (2 if has_market else 0) + 1))]
    for league in footballdata.LEAGUES:
        for season in backtest.TEST:
            sub = [r for r in rows if r["league"] == league and r["season"] == season]
            if not sub:
                continue
            cells = [league, season, len(sub), f"{mean_brier(sub, 'model_p'):.5f}"]
            if has_market:
                sub_mkt = [r for r in sub if r.get("market_p") is not None]
                if sub_mkt:
                    bm, bk = mean_brier(sub, "model_p"), mean_brier(sub_mkt, "market_p")
                    cells += [f"{bk:.5f}", f"{(bm - bk) / bk * 100:+.2f} %"]
                else:
                    cells += ["—", "—"]
            cells.append(f"{mean_brier(sub, 'freq_p'):.5f}")
            lines.append(fmt_row(cells))
    lines.append("")

    if leak_m:
        lines += ["### Test anti-fuite (labels permutés, saison 2223)", "",
                  fmt_row(["Ligue", "Brier réel", "Brier permuté", "Dégradé ?"]), fmt_row(["---"] * 4)]
        for league, v in leak_m.items():
            e = v[market]
            lines.append(fmt_row([league, f"{e['brier_real']:.5f}", f"{e['brier_shuffled']:.5f}",
                                  "oui" if e["degraded"] else "NON — FUITE PROBABLE"]))
        lines.append("")
    else:
        lines += ["Anti-fuite non exécuté (`python backtest_derived.py --shuffle-test`).", ""]

    return lines


def build_report(conn):
    if not bd.FROZEN_PATH.exists():
        sys.exit(f"{bd.FROZEN_PATH} absent : lancer d'abord "
                 f"python backtest_derived.py --tune puis --run")
    cfg = json.loads(bd.FROZEN_PATH.read_text())
    leak = json.loads(bd.LEAK_PATH.read_text()) if bd.LEAK_PATH.exists() else None

    lines = ["# M7 (roadmap) — Validation indépendante des marchés dérivés (O/U 2,5, BTTS)", "",
             "Marchés calculés depuis la MÊME grille de score que le modèle M3.5 déjà "
             "backtesté (`reports/m35_backtest.md`) — w/ξ/κ inchangés. Seule une "
             "recalibration binaire propre à chaque marché a été réglée ici, sur la "
             "même validation, avec la même interdiction de retoucher quoi que ce soit "
             "après lecture du test.", "",
             f"Réglages figés le {cfg.get('tuned_at', '?')} : " +
             " ; ".join(f"{bd.MARKET_LABELS[m]} t = {cfg['markets'][m]['t']:.3f} "
                       f"(Brier validation {cfg['markets'][m]['brier_validation_raw']:.5f} -> "
                       f"{cfg['markets'][m]['brier_validation']:.5f})"
                       for m in bd.MARKETS if m in cfg.get("markets", {})), ""]

    bound_lo, bound_hi = bd.TEMP_BOUNDS
    at_bound = [m for m in bd.MARKETS if m in cfg.get("markets", {})
               and (abs(cfg["markets"][m]["t"] - bound_lo) < 1e-3
                    or abs(cfg["markets"][m]["t"] - bound_hi) < 1e-3)]
    if at_bound:
        lines += [f"⚠ Température au bord de la plage autorisée ({bound_lo}–{bound_hi}) pour "
                  f"{', '.join(bd.MARKET_LABELS[m] for m in at_bound)} : l'optimum réel est "
                  f"peut-être hors plage (aplatissement encore plus fort). Reste dans "
                  f"TEMP_BOUNDS par cohérence avec M3.5 plutôt que d'élargir la plage après "
                  f"avoir vu où l'optimiseur bute — à noter, pas à corriger rétroactivement.",
                  ""]

    for market in bd.MARKETS:
        lines += market_section(conn, market, leak)

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    text = build_report(conn)
    conn.close()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text)
    print(text)
    print(f"\nRapport écrit dans {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
