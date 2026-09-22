"""Rapport GBM (roadmap B1-B4) : Brier/log-loss/accuracy/AUC vs Dixon-Coles
et marché, IC bootstrap appariés, ventilé par ligue (top ET secondaires).

Lit predictions_ml (backtest_ml.py --run), data/ml_frozen.json,
data/leak_check_ml.json, la table predictions_m35 (comparaison directe à
l'existant sur les 3 ligues où M3.5 a été validé), écrit
reports/ml_backtest.md.

Usage : python report_ml.py [--db data/football.db]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

import backtest
import backtest_ml
import bootstrap
import db
import footballdata

OUT_PATH = Path("reports/ml_backtest.md")


def load_predictions_ml(conn):
    rows = conn.execute(
        "SELECT p.match_id, m.league, m.season, m.date, m.fthg, m.ftag, "
        "p.model_h, p.model_d, p.model_a, p.market_h, p.market_d, p.market_a "
        "FROM predictions_ml p JOIN matches m ON m.match_id = p.match_id "
        "ORDER BY m.date"
    ).fetchall()
    out = []
    for r in rows:
        outcome = 0 if r["fthg"] > r["ftag"] else (1 if r["fthg"] == r["ftag"] else 2)
        market = ((r["market_h"], r["market_d"], r["market_a"])
                  if r["market_h"] is not None else None)
        out.append({
            "match_id": r["match_id"], "league": r["league"], "season": r["season"],
            "date": r["date"], "outcome": outcome,
            "model": (r["model_h"], r["model_d"], r["model_a"]), "market": market,
        })
    return out


def _freq_baseline(rows):
    """Baseline fréquence : proportions H/D/A observées dans CE sous-ensemble
    (diagnostic seulement — contrairement à predictions.freq_*, ce n'est PAS
    un walk-forward causal ici, juste une baseline agrégée après coup pour
    situer le Brier du modèle, comme le fait déjà predict.py pour les
    marchés dérivés A1)."""
    counts = np.zeros(3)
    for r in rows:
        counts[r["outcome"]] += 1
    return tuple(counts / counts.sum()) if counts.sum() else (1 / 3,) * 3


def accuracy(rows, key="model"):
    correct = sum(1 for r in rows if int(np.argmax(r[key])) == r["outcome"] if r[key] is not None)
    n = sum(1 for r in rows if r[key] is not None)
    return correct / n if n else None


def logloss_mean(rows, key="model"):
    vals = [-np.log(max(r[key][r["outcome"]], 1e-12)) for r in rows if r[key] is not None]
    return float(np.mean(vals)) if vals else None


def auc_ovr(rows, key="model"):
    """AUC one-vs-rest macro-moyennée, calcul par rang (Mann-Whitney U /
    n_pos*n_neg) — pas de dépendance à scikit-learn (absent du projet)."""
    sub = [r for r in rows if r[key] is not None]
    if len(sub) < 2:
        return None
    outcomes = np.array([r["outcome"] for r in sub])
    aucs = []
    for k in range(3):
        y = (outcomes == k).astype(int)
        n_pos, n_neg = int(y.sum()), int((1 - y).sum())
        if n_pos == 0 or n_neg == 0:
            continue
        scores = np.array([r[key][k] for r in sub])
        ranks = rankdata(scores)
        auc = (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
        aucs.append(auc)
    return float(np.mean(aucs)) if aucs else None


def brier_series(rows, key):
    return [backtest.brier(r[key], r["outcome"]) for r in rows if r[key] is not None]


def _league_block(league, rows):
    lines = [f"### {league} ({len(rows)} matchs)", ""]
    b_model = brier_series(rows, "model")
    b_market = brier_series(rows, "market")
    freq = _freq_baseline(rows)
    b_freq = [backtest.brier(freq, r["outcome"]) for r in rows]
    b_unif = [backtest.brier((1 / 3, 1 / 3, 1 / 3), r["outcome"]) for r in rows]

    lines.append(f"- Brier GBM : {np.mean(b_model):.5f}")
    lines.append(f"- Brier baseline fréquence : {np.mean(b_freq):.5f}")
    lines.append(f"- Brier baseline uniforme : {np.mean(b_unif):.5f}")
    acc = accuracy(rows)
    ll = logloss_mean(rows)
    auc = auc_ovr(rows)
    lines.append(f"- Accuracy (classe la plus probable) : {acc:.1%}" if acc is not None else "- Accuracy : n/a")
    lines.append(f"- Log-loss : {ll:.5f}" if ll is not None else "- Log-loss : n/a")
    lines.append(f"- AUC one-vs-rest (macro) : {auc:.3f}" if auc is not None else "- AUC : n/a")

    if b_market:
        rows_with_market = [r for r in rows if r["market"] is not None]
        bm = brier_series(rows_with_market, "model")
        bmk = brier_series(rows_with_market, "market")
        rel, lo, hi = bootstrap.ci_relative_delta(bm, bmk)
        lines.append(f"- Δ Brier vs marché (démargé power, {len(rows_with_market)} matchs avec cote) : "
                     f"{rel:+.2f} % {bootstrap.fmt_ci(lo, hi)}")
    else:
        lines.append("- Δ vs marché : inapplicable (aucune cote de clôture disponible)")
    lines.append("")
    return lines


def build_report(conn):
    rows = load_predictions_ml(conn)
    if not rows:
        sys.exit("table predictions_ml vide : lancer d'abord python backtest_ml.py --run")
    cfg = json.loads(backtest_ml.FROZEN_PATH.read_text())
    leak = (json.loads(backtest_ml.LEAK_PATH.read_text())
            if backtest_ml.LEAK_PATH.exists() else None)

    lines = ["# GBM (LightGBM) — roadmap B1-B4, stacké sur Dixon-Coles + features/", ""]
    lines.append(
        "⚠ **Statut honnête** — chantier neuf (2026-09-22), validé par le même protocole "
        "tune/validation/test + IC bootstrap que M3.5, mais sans l'ampleur de vérification "
        "(plusieurs mois, plusieurs itérations indépendantes) qui a précédé le verdict M3.5. "
        "Ce rapport ne remplace pas reports/m35_backtest.md — il le complète. Le staking Kelly "
        "de production reste basé sur M3.5 tant que ce chantier n'a pas atteint le même niveau "
        "de confiance (cf. CLAUDE.md, section override du 2026-09-22)."
    )
    lines.append("")
    lines.append(f"{len(rows)} matchs de test (saisons {', '.join(backtest.TEST)}), "
                f"{len(cfg['leagues'])} ligues ({', '.join(cfg['leagues'])}), refit par frontière de "
                f"saison (pas hebdomadaire — cf. backtest_ml.py). Hyperparamètres figés sur validation "
                f"{'+'.join(cfg['validation_seasons'])} : num_leaves = {cfg['num_leaves']}, "
                f"learning_rate = {cfg['learning_rate']}, num_rounds = {cfg['num_rounds']} "
                f"(Brier validation {cfg['brier_validation']:.5f}).")
    lines.append("")

    # --- Verdict global (toutes ligues confondues) ---
    b_model = brier_series(rows, "model")
    freq = _freq_baseline(rows)
    b_freq = [backtest.brier(freq, r["outcome"]) for r in rows]
    b_unif = [backtest.brier((1 / 3, 1 / 3, 1 / 3), r["outcome"]) for r in rows]
    rows_with_market = [r for r in rows if r["market"] is not None]
    lines.append("## Verdict global (agrégat toutes ligues — voir détail par ligue plus bas)")
    lines.append("")
    lines.append(f"- Brier GBM (agrégat) : {np.mean(b_model):.5f}")
    lines.append(f"- Brier baseline fréquence (agrégat) : {np.mean(b_freq):.5f}")
    lines.append(f"- Brier baseline uniforme (agrégat) : {np.mean(b_unif):.5f}")
    if rows_with_market:
        bm = brier_series(rows_with_market, "model")
        bmk = brier_series(rows_with_market, "market")
        rel, lo, hi = bootstrap.ci_relative_delta(bm, bmk)
        excl = bootstrap.excludes_zero(lo, hi)
        verdict = ("IC exclut 0 — écart distinguable du bruit" if excl
                  else "IC N'exclut PAS 0 — pas distinguable du bruit sur cet échantillon")
        lines.append(f"- **Δ Brier vs marché (agrégat, {len(rows_with_market)} matchs avec cote de "
                     f"clôture) : {rel:+.2f} % {bootstrap.fmt_ci(lo, hi)} — {verdict}.**")
    lines.append("")

    # --- Comparaison directe à M3.5 sur les 3 ligues où M3.5 est validé ---
    m35_rows = {r["match_id"]: r for r in conn.execute(
        "SELECT match_id, model_h, model_d, model_a FROM predictions_m35")}
    top_rows = [r for r in rows if r["league"] in footballdata.LEAGUES and r["match_id"] in m35_rows]
    if top_rows:
        gbm_b = [backtest.brier(r["model"], r["outcome"]) for r in top_rows]
        m35_b = [backtest.brier((m35_rows[r["match_id"]]["model_h"],
                                 m35_rows[r["match_id"]]["model_d"],
                                 m35_rows[r["match_id"]]["model_a"]), r["outcome"]) for r in top_rows]
        rel, lo, hi = bootstrap.ci_relative_delta(gbm_b, m35_b)
        lines.append(f"## GBM vs M3.5 (comparaison directe, {len(top_rows)} matchs communs aux 3 "
                     f"grandes ligues déjà validées par M3.5)")
        lines.append("")
        lines.append(f"- Δ Brier GBM vs M3.5 (négatif = le GBM fait MIEUX que M3.5 sur ces matchs) : "
                     f"{rel:+.2f} % {bootstrap.fmt_ci(lo, hi)}")
        lines.append(f"- Brier GBM {np.mean(gbm_b):.5f} vs Brier M3.5 {np.mean(m35_b):.5f}")
        lines.append("")

    # --- Détail par ligue ---
    lines.append("## Détail par ligue")
    lines.append("")
    for league in cfg["leagues"]:
        lr = [r for r in rows if r["league"] == league]
        if not lr:
            continue
        is_secondary = league in footballdata.SECONDARY_LEAGUES
        lines.append(f"*(ligue {'secondaire, roadmap A3 — sans xG, jamais backtestée par M3.5' if is_secondary else 'principale'})*")
        lines += _league_block(league, lr)

    # --- Anti-fuite ---
    lines.append("## Contrôle anti-fuite (shuffle-test)")
    lines.append("")
    if leak is None:
        lines.append("⚠ Pas encore exécuté : lancer `python backtest_ml.py --shuffle-test`.")
    else:
        status = "dégradé (attendu, pas de fuite détectée)" if leak["degraded"] else "PAS DÉGRADÉ — FUITE PROBABLE"
        lines.append(f"- Saison de contrôle {leak['season']} : Brier réel {leak['brier_real']:.5f} / "
                     f"permuté {leak['brier_shuffled']:.5f} -> **{status}**")
    lines.append("")

    lines.append("## Limites assumées, pas cachées")
    lines.append("")
    lines.append("- Refit par frontière de saison (pas hebdomadaire comme Dixon-Coles) : plus grossier, "
                 "compromis de vitesse documenté dans backtest_ml.py.")
    lines.append("- Aucune xG sur les ligues secondaires (Understat ne les couvre pas) : le GBM s'appuie "
                 "sur buts/tirs/tirs cadrés/corners réels pour elles, jamais une xG simulée.")
    lines.append("- Hyperparamètres num_leaves/learning_rate/num_rounds réglés par grid search sur la "
                 "validation ; les autres (feature_fraction, bagging, min_data_in_leaf) restent des "
                 "valeurs par défaut de la littérature LightGBM, pas optimisées sur ce dataset.")
    lines.append("- La possession n'existe dans aucune feature : football-data.co.uk ne la publie pas.")
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    report = build_report(conn)
    conn.close()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(report)
    print(report)
    print(f"\nRapport écrit dans {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
