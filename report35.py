"""Rapport M3.5 : même format que M3 + grid (w, ξ), recalibration, diagnostic promus.

Lit predictions_m35 (backtest35.py --run), data/m35_frozen.json,
data/leak_check_m35.json et la table predictions (comparaison M3),
écrit reports/m35_backtest.md.

Usage : python report35.py [--db data/football.db]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest35
import db
import footballdata
from report import (CALIB_MIN_N, CALIB_TOL, CLEAR_MARGIN, LABELS, METHODS,
                    calibration_table, fmt_row, load_predictions, metrics)

OUT_PATH = Path("reports/m35_backtest.md")


def build_report(conn):
    rows = load_predictions(conn, table="predictions_m35")
    if not rows:
        sys.exit("table predictions_m35 vide : lancer d'abord python backtest35.py --run")
    cfg = json.loads(backtest35.FROZEN_PATH.read_text())
    leak = (json.loads(backtest35.LEAK_PATH.read_text())
            if backtest35.LEAK_PATH.exists() else None)
    m3_rows = load_predictions(conn, table="predictions")

    lines = ["# M3.5 — Dixon-Coles + pseudo-buts xG + recalibration, vs marché", ""]
    lines += [f"{len(rows)} matchs de test (saisons {', '.join(backtest.TEST)}), "
              f"3 ligues, refit hebdomadaire. Réglages figés sur validation "
              f"{'+'.join(cfg['validation_seasons'])} : w = {cfg['w']:.1f} (pseudo-buts xG), "
              f"ξ = {cfg['xi']}, κ = {cfg['kappa']}, température t = {cfg['temperature']:.3f}.", ""]

    # --- Verdicts ---
    b_model, _, _ = metrics(rows, "model")
    b_market, _, _ = metrics(rows, "market")
    b_freq, _, _ = metrics(rows, "freq")
    b_unif, _, _ = metrics(rows, "uniform")
    b_m3 = metrics(m3_rows, "model")[0] if m3_rows else None
    rel_market = (b_model - b_market) / b_market
    beats_freq = (b_freq - b_model) / b_freq
    beats_unif = (b_unif - b_model) / b_unif

    calib = calibration_table(rows)
    big = [c for c in calib if c["n"] >= CALIB_MIN_N]
    worst = max(big, key=lambda c: abs(c["obs"] - c["pred"]))
    over = sum(1 for c in big if c["obs"] < c["pred"] - 0.01)
    under = sum(1 for c in big if c["obs"] > c["pred"] + 0.01)

    checks = [
        (rel_market < 0.02,
         f"Brier modèle à {rel_market * 100:+.2f} % du marché (critère < +2 %)"),
        (beats_freq >= CLEAR_MARGIN and beats_unif >= CLEAR_MARGIN,
         f"Bat les baselines : {beats_freq * 100:+.1f} % vs fréquences, "
         f"{beats_unif * 100:+.1f} % vs uniforme (critère ≥ {CLEAR_MARGIN * 100:.0f} % chacune)"),
        (abs(worst["obs"] - worst["pred"]) <= CALIB_TOL,
         f"Calibration : pire tranche (n ≥ {CALIB_MIN_N}) à "
         f"{abs(worst['obs'] - worst['pred']) * 100:.1f} pts d'écart (tolérance {CALIB_TOL * 100:.0f} pts)"),
    ]
    if leak:
        checks.append((leak["all_degraded"],
                       "Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés"))
    lines += ["## Verdict", ""]
    for ok, txt in checks:
        lines.append(f"- {'✅' if ok else '❌'} {txt}")
    if b_m3 is not None:
        lines.append(f"- Rappel M3 (buts seuls, sans recalibration) : Brier {b_m3:.5f} ; "
                     f"M3.5 : {b_model:.5f} ({(b_model - b_m3) / b_m3 * 100:+.2f} %).")
    if b_model < b_market:
        lines.append("- ⚠️ Le modèle bat le marché en Brier : traiter comme suspect, "
                     "vérifier le test anti-fuite avant toute conclusion.")
    lines.append("")

    # --- Tableau global ---
    lines += ["## Résultats agrégés (toutes saisons de test)", "",
              fmt_row(["Méthode", "Brier", "Log-loss", "Bonne issue (argmax)"]),
              fmt_row(["---"] * 4)]
    for m in ("model", "raw") + METHODS[1:]:
        b, ll, acc = metrics(rows, m)
        label = LABELS.get(m, "Modèle avant recalibration")
        if m == "model":
            label = "Modèle M3.5 (recalibré)"
        lines.append(fmt_row([label, f"{b:.5f}", f"{ll:.5f}", f"{acc * 100:.1f} %"]))
    if m3_rows:
        b, ll, acc = metrics(m3_rows, "model")
        lines.append(fmt_row(["Modèle M3 (rappel)", f"{b:.5f}", f"{ll:.5f}", f"{acc * 100:.1f} %"]))
    lines.append("")

    # --- Par ligue x saison ---
    m3_by_id = {r["match_id"]: r for r in m3_rows}
    lines += ["## Brier par ligue et saison de test", "",
              fmt_row(["Ligue", "Saison", "n", "M3.5", "M3", "Marché", "Écart rel. marché",
                       "Fréquences"]),
              fmt_row(["---"] * 8)]
    for league in footballdata.LEAGUES:
        for season in backtest.TEST:
            sub = [r for r in rows if r["league"] == league and r["season"] == season]
            if not sub:
                continue
            sub3 = [m3_by_id[r["match_id"]] for r in sub if r["match_id"] in m3_by_id]
            bm = metrics(sub, "model")[0]
            bk = metrics(sub, "market")[0]
            lines.append(fmt_row([league, season, len(sub), f"{bm:.5f}",
                                  f"{metrics(sub3, 'model')[0]:.5f}" if sub3 else "—",
                                  f"{bk:.5f}", f"{(bm - bk) / bk * 100:+.2f} %",
                                  f"{metrics(sub, 'freq')[0]:.5f}"]))
    lines.append("")

    # --- Calibration ---
    lines += ["## Calibration du modèle recalibré (tranches de 5 pts, 3 probas par match)", "",
              fmt_row(["Tranche", "n", "Proba prédite moy.", "Fréquence observée", "Écart"]),
              fmt_row(["---"] * 5)]
    for c in calib:
        mark = "" if c["n"] >= CALIB_MIN_N else " *"
        lines.append(fmt_row([f"{c['lo'] * 100:.0f}–{c['hi'] * 100:.0f} %{mark}", c["n"],
                              f"{c['pred'] * 100:.1f} %", f"{c['obs'] * 100:.1f} %",
                              f"{(c['obs'] - c['pred']) * 100:+.1f} pts"]))
    lines += ["", f"\\* tranches sous n = {CALIB_MIN_N}, hors verdict. "
              f"Tranches sur-confiantes : {over}, sous-confiantes : {under} (sur {len(big)}).", ""]

    # --- Diagnostic promus (test, lecture seule) ---
    lines += ["## Diagnostic promus (matchs de test impliquant au moins un promu)", "",
              fmt_row(["Ligue", "Saison", "n promus", "Brier M3.5", "Brier marché",
                       "n sans promu", "Brier M3.5", "Brier marché"]),
              fmt_row(["---"] * 8)]
    for league in footballdata.LEAGUES:
        all_rows = backtest.load_league(conn, league)
        promo = backtest35.promoted_teams(all_rows)
        for season in backtest.TEST:
            sub = [r for r in rows if r["league"] == league and r["season"] == season]
            if not sub:
                continue
            teams_promo = promo.get(season, set())
            avec = [r for r in sub if r["home"] in teams_promo or r["away"] in teams_promo]
            sans = [r for r in sub if r not in avec]
            cells = [league, season]
            for group in (avec, sans):
                if group:
                    cells += [len(group), f"{metrics(group, 'model')[0]:.5f}",
                              f"{metrics(group, 'market')[0]:.5f}"]
                else:
                    cells += [0, "—", "—"]
            lines.append(fmt_row(cells))
    pv = cfg.get("promoted_validation", {})
    if pv:
        lines += ["", "Sur la validation (base du choix de κ) : " + " ; ".join(
            f"{lg} avec promu {v['brier_avec']:.4f} (n={v['n_avec']}) vs sans {v['brier_sans']:.4f}"
            for lg, v in pv.items()), ""]

    # --- Grids de réglage ---
    lines += ["## Grid 2D (w, ξ) — validation 2020-21 + 2021-22 (Brier)", "",
              fmt_row(["w \\ ξ"] + [str(x) for x in backtest.XI_GRID]),
              fmt_row(["---"] * (len(backtest.XI_GRID) + 1))]
    for w in backtest35.W_GRID:
        cells = [f"{w:.1f}"]
        for xi in backtest.XI_GRID:
            v = cfg["grid_w_xi"][f"{w},{xi}"]
            star = " ←" if (w == cfg["w"] and xi == cfg["xi"]) else ""
            cells.append(f"{v:.5f}{star}")
        lines.append(fmt_row(cells))
    lines += ["", fmt_row(["κ", "Brier validation"]), fmt_row(["---"] * 2)]
    for k, v in cfg["grid_kappa"].items():
        star = " ← figé" if float(k) == cfg["kappa"] else ""
        lines.append(fmt_row([k, f"{v:.5f}{star}"]))
    lines += ["", f"Température : t = {cfg['temperature']:.3f}, Brier validation "
              f"{cfg['brier_validation_raw']:.5f} → {cfg['brier_validation']:.5f}.", ""]

    # --- Anti-fuite ---
    lines += ["## Test anti-fuite (labels permutés, saison 2223)", ""]
    if leak:
        lines += [fmt_row(["Ligue", "Brier réel", "Brier permuté", "Dégradé ?"]),
                  fmt_row(["---"] * 4)]
        for league, v in leak["leagues"].items():
            lines.append(fmt_row([league, f"{v['brier_real']:.5f}", f"{v['brier_shuffled']:.5f}",
                                  "oui" if v["degraded"] else "NON — FUITE PROBABLE"]))
    else:
        lines.append("Non exécuté (`python backtest35.py --shuffle-test`).")
    lines.append("")
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
