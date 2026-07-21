"""Rapport M3 : Brier/log-loss modèle vs marché vs baselines, calibration, verdicts.

Lit la table predictions (backtest.py --run), data/xi_frozen.json et
data/leak_check.json, écrit reports/m3_backtest.md.

Usage : python report.py [--db data/football.db]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

import backtest
import db
import footballdata

OUT_PATH = Path("reports/m3_backtest.md")
METHODS = ("model", "market", "freq", "uniform")
LABELS = {"model": "Modèle DC", "market": "Marché", "freq": "Fréquences", "uniform": "Uniforme"}
CALIB_MIN_N = 300      # effectif minimal d'une tranche pour compter dans le verdict
CALIB_TOL = 0.05       # écart max toléré prédit/observé sur ces tranches
CLEAR_MARGIN = 0.03    # "bat nettement les baselines" = Brier meilleur d'au moins 3 % relatif


def load_predictions(conn, table="predictions"):
    rows = [dict(r) for r in conn.execute(
        f"SELECT p.*, m.league, m.season, m.date, m.home, m.away, m.fthg, m.ftag "
        f"FROM {table} p JOIN matches m USING (match_id) ORDER BY m.date, p.match_id")]
    for r in rows:
        r["outcome"] = backtest.outcome_index(r["fthg"], r["ftag"])
        r["probs"] = {
            "model": (r["model_h"], r["model_d"], r["model_a"]),
            "market": (r["market_h"], r["market_d"], r["market_a"]),
            "freq": (r["freq_h"], r["freq_d"], r["freq_a"]),
            "uniform": (1 / 3, 1 / 3, 1 / 3),
        }
        if "raw_h" in r:
            r["probs"]["raw"] = (r["raw_h"], r["raw_d"], r["raw_a"])
    return rows


def metrics(rows, method):
    briers = [backtest.brier(r["probs"][method], r["outcome"]) for r in rows]
    lls = [backtest.logloss(r["probs"][method], r["outcome"]) for r in rows]
    hits = [int(np.argmax(r["probs"][method]) == r["outcome"]) for r in rows]
    return float(np.mean(briers)), float(np.mean(lls)), float(np.mean(hits))


def calibration_table(rows, method="model", width=0.05):
    """Tranches de probabilité : chaque match contribue ses 3 probas 1N2."""
    pairs = [(p, 1.0 if k == r["outcome"] else 0.0)
             for r in rows for k, p in enumerate(r["probs"][method])]
    arr = np.array(pairs)
    bins = np.floor(arr[:, 0] / width).astype(int)
    table = []
    for b in sorted(set(bins)):
        sel = arr[bins == b]
        table.append({"lo": b * width, "hi": (b + 1) * width, "n": len(sel),
                      "pred": float(sel[:, 0].mean()), "obs": float(sel[:, 1].mean())})
    return table


def fmt_row(cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def build_report(conn):
    rows = load_predictions(conn)
    if not rows:
        sys.exit("table predictions vide : lancer d'abord python backtest.py --run")
    xi_info = json.loads(Path(backtest.XI_PATH).read_text())
    leak = json.loads(Path(backtest.LEAK_PATH).read_text()) if Path(backtest.LEAK_PATH).exists() else None

    lines = ["# M3 — Backtest walk-forward Dixon-Coles vs marché", ""]
    lines += [f"{len(rows)} matchs de test (saisons {', '.join(backtest.TEST)}), "
              f"3 ligues, refit hebdomadaire, ξ = {xi_info['xi']} figé sur validation "
              f"{'+'.join(xi_info['validation_seasons'])} (critère {xi_info['criterion']}).", ""]

    # --- Verdicts ---
    b_model, ll_model, acc_model = metrics(rows, "model")
    b_market, ll_market, acc_market = metrics(rows, "market")
    b_freq, _, _ = metrics(rows, "freq")
    b_unif, _, _ = metrics(rows, "uniform")
    rel_market = (b_model - b_market) / b_market
    beats_freq = (b_freq - b_model) / b_freq
    beats_unif = (b_unif - b_model) / b_unif

    calib = calibration_table(rows)
    big_bins = [c for c in calib if c["n"] >= CALIB_MIN_N]
    worst = max(big_bins, key=lambda c: abs(c["obs"] - c["pred"]))
    calib_ok = abs(worst["obs"] - worst["pred"]) <= CALIB_TOL
    over = sum(1 for c in big_bins if c["obs"] < c["pred"] - 0.01)
    under = sum(1 for c in big_bins if c["obs"] > c["pred"] + 0.01)

    checks = [
        (rel_market < 0.02,
         f"Brier modèle à {rel_market * 100:+.2f} % du marché (critère < +2 %)"),
        (beats_freq >= CLEAR_MARGIN and beats_unif >= CLEAR_MARGIN,
         f"Bat les baselines : {beats_freq * 100:+.1f} % vs fréquences, "
         f"{beats_unif * 100:+.1f} % vs uniforme (critère ≥ {CLEAR_MARGIN * 100:.0f} % chacune)"),
        (calib_ok,
         f"Calibration : pire tranche (n ≥ {CALIB_MIN_N}) à "
         f"{abs(worst['obs'] - worst['pred']) * 100:.1f} pts d'écart (tolérance {CALIB_TOL * 100:.0f} pts)"),
    ]
    if leak:
        checks.append((leak["all_degraded"],
                       "Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés"))
    lines += ["## Verdict", ""]
    for ok, txt in checks:
        lines.append(f"- {'✅' if ok else '❌'} {txt}")
    if b_model < b_market:
        lines.append("- ⚠️ Le modèle bat le marché en Brier : traiter comme suspect, "
                     "vérifier le test anti-fuite ci-dessous avant toute conclusion.")
    lines.append("")

    # --- Tableau global par méthode ---
    lines += ["## Résultats agrégés (toutes saisons de test)", "",
              fmt_row(["Méthode", "Brier", "Log-loss", "Bonne issue (argmax)"]),
              fmt_row(["---"] * 4)]
    for m in METHODS:
        b, ll, acc = metrics(rows, m)
        lines.append(fmt_row([LABELS[m], f"{b:.5f}", f"{ll:.5f}", f"{acc * 100:.1f} %"]))
    lines.append("")

    # --- Par ligue x saison ---
    lines += ["## Brier par ligue et saison de test", "",
              fmt_row(["Ligue", "Saison", "n", "Modèle", "Marché", "Écart rel.", "Fréquences", "Uniforme"]),
              fmt_row(["---"] * 8)]
    for league in footballdata.LEAGUES:
        for season in backtest.TEST:
            sub = [r for r in rows if r["league"] == league and r["season"] == season]
            if not sub:
                continue
            bm = metrics(sub, "model")[0]
            bk = metrics(sub, "market")[0]
            lines.append(fmt_row([league, season, len(sub), f"{bm:.5f}", f"{bk:.5f}",
                                  f"{(bm - bk) / bk * 100:+.2f} %",
                                  f"{metrics(sub, 'freq')[0]:.5f}",
                                  f"{metrics(sub, 'uniform')[0]:.5f}"]))
    lines.append("")

    # --- Log-loss agrégé par ligue ---
    lines += ["## Log-loss par ligue (test agrégé)", "",
              fmt_row(["Ligue", "Modèle", "Marché", "Fréquences", "Uniforme"]),
              fmt_row(["---"] * 5)]
    for league in footballdata.LEAGUES:
        sub = [r for r in rows if r["league"] == league]
        lines.append(fmt_row([league] + [f"{metrics(sub, m)[1]:.5f}" for m in METHODS]))
    lines.append("")

    # --- Calibration ---
    lines += ["## Calibration du modèle (tranches de 5 pts, 3 probas par match)", "",
              fmt_row(["Tranche", "n", "Proba prédite moy.", "Fréquence observée", "Écart"]),
              fmt_row(["---"] * 5)]
    for c in calib:
        mark = "" if c["n"] >= CALIB_MIN_N else " *"
        lines.append(fmt_row([f"{c['lo'] * 100:.0f}–{c['hi'] * 100:.0f} %{mark}", c["n"],
                              f"{c['pred'] * 100:.1f} %", f"{c['obs'] * 100:.1f} %",
                              f"{(c['obs'] - c['pred']) * 100:+.1f} pts"]))
    lines += ["", f"\\* tranches sous n = {CALIB_MIN_N}, hors verdict. "
              f"Tranches sur-confiantes : {over}, sous-confiantes : {under} (sur {len(big_bins)}).", ""]

    # --- Tuning ξ ---
    lines += ["## Grid search ξ (validation 2020-21 + 2021-22)", "",
              fmt_row(["ξ (jours⁻¹)", "Brier validation"]), fmt_row(["---"] * 2)]
    for k, v in sorted(xi_info["grid"].items(), key=lambda kv: float(kv[0])):
        star = " ← figé" if float(k) == xi_info["xi"] else ""
        lines.append(fmt_row([k, f"{v:.5f}{star}"]))
    lines.append("")

    # --- Anti-fuite ---
    lines += ["## Test anti-fuite (labels permutés, saison 2223)", ""]
    if leak:
        lines += [fmt_row(["Ligue", "Brier réel", "Brier permuté", "Dégradé ?"]), fmt_row(["---"] * 4)]
        for league, v in leak["leagues"].items():
            lines.append(fmt_row([league, f"{v['brier_real']:.5f}", f"{v['brier_shuffled']:.5f}",
                                  "oui" if v["degraded"] else "NON — FUITE PROBABLE"]))
    else:
        lines.append("Non exécuté (`python backtest.py --shuffle-test`).")
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
