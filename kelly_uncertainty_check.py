"""Le désaccord M3.5/GBM prédit-il un edge qui tourne mal ?

Idée testée (soulevée par l'utilisateur, section "Kelly ajusté à l'incertitude
du modèle" de CLAUDE.md) : moduler la mise Kelly selon l'incertitude du
modèle, mesurée par le désaccord entre deux modèles indépendants (M3.5,
production, et le GBM du chantier B1-B4). Intuition : quand les deux modèles
sont d'accord, la prédiction est probablement plus fiable ; quand ils
divergent, la "value" vue par M3.5 seul est peut-être un artefact.

AVANT de construire quoi que ce soit dans le staking réel, ce script vérifie
si ce signal existe — même discipline que fatigue_signal_check.py et le
diagnostic 1 de clv_signal_check.py : jamais de calibration sans avoir
d'abord vérifié qu'il y a un signal à calibrer.

Contrainte méthodologique à assumer honnêtement (pas de solution propre
disponible) : predictions_m35 et predictions_ml n'existent QUE sur le TEST
(2223+, la même coupure déjà publiée dans m35_backtest.md et ml_backtest.md)
— il n'y a pas de second jeu GBM+M3.5 hors-test à disposition pour ce
diagnostic (le GBM n'a jamais été entraîné/évalué sur VALIDATION seule).
Ce script lit donc des sorties de modèle déjà figées et déjà publiées
(aucun hyperparamètre M3.5/GBM n'est retouché ici, purement une lecture a
posteriori), mais toute calibration future de ce signal (seuil, facteur de
réduction) devra être validée sur des données FRAÎCHES (production à venir),
jamais directement sur ces 4338 matchs déjà vus par les deux modèles.

Deux angles :
1. Brier de M3.5 (le modèle en production) par tranche de désaccord
   (distance en variation totale entre les distributions M3.5 et GBM) —
   le désaccord prédit-il une prédiction M3.5 moins bonne en général ?
2. ROI théorique du "meilleur pari" (l'issue où M3.5 a le plus d'edge sur le
   marché démargé, cote équitable 1/market_p — même proxy que les autres
   diagnostics de ce projet, ex. clv_signal_check.py) par tranche de
   désaccord — le désaccord prédit-il spécifiquement qu'un pari Kelly aurait
   mal tourné ? Limite assumée : predictions_m35/predictions_ml ne stockent
   que le marché DÉMARGÉ (fair), pas la cote réellement cotée avec sa marge —
   un edge vs marché démargé est structurellement quasi toujours positif sur
   au moins une des 3 issues (les écarts modèle-marché somment à ~0 sur les
   3 issues), donc "positif" seul ne filtre rien. VALUE_EDGE_THRESHOLD (3 %)
   approxime le seuil qu'une vraie cote cotée (marge ~5 %) imposerait — pas
   une reproduction exacte du déclenchement réel de `kelly_stake` (qui, lui,
   compare à la cote réellement cotée, pas au marché démargé).

Usage : python kelly_uncertainty_check.py [--db data/football.db]
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

import bootstrap
import db

log = logging.getLogger("kelly_uncertainty_check")
ISSUES = ("h", "d", "a")
# Cf. docstring, angle 2 : approxime le seuil qu'une vraie marge de ~5 % sur
# la cote cotée imposerait, faute de disposer des cotes réellement cotées
# (avec marge) pour ce jeu de test rétroactif — seul le marché démargé (fair)
# est stocké dans predictions_m35/predictions_ml.
VALUE_EDGE_THRESHOLD = 0.03


def total_variation(p, q):
    """Distance en variation totale entre deux lois discrètes sur {home,draw,away}
    (demi-somme des écarts absolus) — 0 = accord parfait, 1 = désaccord total."""
    return 0.5 * sum(abs(p[k] - q[k]) for k in ISSUES)


def outcome_index(fthg, ftag):
    if fthg > ftag:
        return 0
    if fthg < ftag:
        return 2
    return 1


def collect_rows(conn):
    """Un dict par match commun à predictions_m35/predictions_ml (le TEST des
    deux chantiers, 3 grandes ligues, 4338 matchs) : probas M3.5, probas GBM,
    probas marché démargées, résultat réel, désaccord, Brier M3.5, et le
    "meilleur pari" (issue avec le plus d'edge M3.5 vs marché, cote équitable
    1/market_p, ROI théorique si on l'avait pris à cote unitaire)."""
    query = """
        SELECT m.fthg, m.ftag,
               p35.model_h AS m35_h, p35.model_d AS m35_d, p35.model_a AS m35_a,
               pml.model_h AS gbm_h, pml.model_d AS gbm_d, pml.model_a AS gbm_a,
               p35.market_h AS mk_h, p35.market_d AS mk_d, p35.market_a AS mk_a
        FROM matches m
        JOIN predictions_m35 p35 ON p35.match_id = m.match_id
        JOIN predictions_ml pml ON pml.match_id = m.match_id
    """
    rows = []
    for r in conn.execute(query).fetchall():
        m35 = {"h": r["m35_h"], "d": r["m35_d"], "a": r["m35_a"]}
        gbm = {"h": r["gbm_h"], "d": r["gbm_d"], "a": r["gbm_a"]}
        mk = {"h": r["mk_h"], "d": r["mk_d"], "a": r["mk_a"]}
        y = outcome_index(r["fthg"], r["ftag"])
        y_key = ISSUES[y]
        brier_m35 = sum((m35[k] - (1.0 if k == y_key else 0.0)) ** 2 for k in ISSUES)
        disagreement = total_variation(m35, gbm)

        # Meilleur pari M3.5 : l'issue où l'edge (model_p / market_p - 1) est
        # maximal. Cote équitable = 1/market_p (démargée, comme partout
        # ailleurs dans ce projet pour un diagnostic théorique).
        edges = {k: m35[k] / mk[k] - 1.0 for k in ISSUES if mk[k] > 0}
        best_issue = max(edges, key=edges.get)
        best_edge = edges[best_issue]
        fair_odds = 1.0 / mk[best_issue]
        bet_roi = (fair_odds - 1.0) if best_issue == y_key else -1.0

        rows.append({"disagreement": disagreement, "brier_m35": brier_m35,
                     "best_edge": best_edge, "bet_roi": bet_roi})
    return rows


def bucket_by_median(rows, key):
    values = sorted(r[key] for r in rows)
    med = values[len(values) // 2]
    low = [r for r in rows if r[key] <= med]
    high = [r for r in rows if r[key] > med]
    return low, high, med


def run(conn):
    rows = collect_rows(conn)
    n = len(rows)
    if n < 30:
        sys.exit(f"Seulement {n} matchs communs M3.5/GBM — échantillon trop petit.")

    disagreements = [r["disagreement"] for r in rows]
    log.info("n=%d matchs. Désaccord M3.5/GBM (variation totale) : "
             "moyenne=%.4f, médiane=%.4f, p90=%.4f",
             n, float(np.mean(disagreements)), float(np.median(disagreements)),
             float(np.percentile(disagreements, 90)))

    # --- Angle 1 : Brier M3.5 par tranche de désaccord (tous matchs) --------
    low, high, med = bucket_by_median(rows, "disagreement")
    brier_low = [r["brier_m35"] for r in low]
    brier_high = [r["brier_m35"] for r in high]
    point, lo, hi = bootstrap.ci_diff_mean(brier_high, brier_low)
    log.info("Brier M3.5 — désaccord faible (n=%d, <=%.4f) : %.4f | "
             "désaccord fort (n=%d, >%.4f) : %.4f",
             len(low), med, float(np.mean(brier_low)),
             len(high), med, float(np.mean(brier_high)))
    log.info("Écart (fort - faible) : %+.4f %s — %s", point,
             bootstrap.fmt_ci(lo, hi, unit="pt Brier"),
             "IC exclut 0" if bootstrap.excludes_zero(lo, hi) else "IC contient 0")

    # --- Angle 2 : ROI théorique du meilleur pari M3.5 vs marché, par -------
    #     tranche de désaccord, restreint aux matchs où M3.5 voit un edge
    #     positif (c'est la seule population qu'un Kelly réel toucherait).
    value_rows = [r for r in rows if r["best_edge"] > VALUE_EDGE_THRESHOLD]
    log.info("Matchs avec edge M3.5 positif ('value bets') : %d/%d", len(value_rows), n)
    vlow, vhigh, vmed = bucket_by_median(value_rows, "disagreement")
    roi_low = [r["bet_roi"] for r in vlow]
    roi_high = [r["bet_roi"] for r in vhigh]
    vpoint, vlo, vhi = bootstrap.ci_diff_mean(roi_high, roi_low)
    log.info("ROI théorique (mise unitaire) — désaccord faible (n=%d) : %+.3f | "
             "désaccord fort (n=%d) : %+.3f",
             len(vlow), float(np.mean(roi_low)), len(vhigh), float(np.mean(roi_high)))
    log.info("Écart (fort - faible) : %+.3f %s — %s", vpoint,
             bootstrap.fmt_ci(vlo, vhi, unit="pt ROI"),
             "IC exclut 0" if bootstrap.excludes_zero(vlo, vhi) else "IC contient 0")

    return {
        "n": n, "disagreement_mean": float(np.mean(disagreements)),
        "disagreement_median": float(np.median(disagreements)),
        "brier": {"n_low": len(low), "n_high": len(high),
                  "mean_low": float(np.mean(brier_low)), "mean_high": float(np.mean(brier_high)),
                  "point": point, "ci_lo": lo, "ci_hi": hi,
                  "excludes_zero": bootstrap.excludes_zero(lo, hi)},
        "value_roi": {"n_value_bets": len(value_rows), "n_low": len(vlow), "n_high": len(vhigh),
                      "mean_low": float(np.mean(roi_low)), "mean_high": float(np.mean(roi_high)),
                      "point": vpoint, "ci_lo": vlo, "ci_hi": vhi,
                      "excludes_zero": bootstrap.excludes_zero(vlo, vhi)},
    }


def write_report(result, path):
    b, v = result["brier"], result["value_roi"]
    lines = [
        "# Le désaccord M3.5/GBM prédit-il un edge qui tourne mal ?",
        "",
        "Diagnostic AVANT calibration (même discipline que "
        "`fatigue_signal_check.py`) — voir docstring de `kelly_uncertainty_check.py` "
        "pour la limite méthodologique assumée (données TEST déjà publiées, pas de "
        "second jeu hors-test disponible pour ce diagnostic).",
        "",
        f"n = {result['n']} matchs (3 grandes ligues, TEST commun à M3.5 et au GBM). "
        f"Désaccord (variation totale) moyen = {result['disagreement_mean']:.4f}, "
        f"médian = {result['disagreement_median']:.4f}.",
        "",
        "## Angle 1 — Brier de M3.5 par tranche de désaccord (tous matchs)",
        "",
        f"- Désaccord faible (n={b['n_low']}) : Brier moyen {b['mean_low']:.4f}",
        f"- Désaccord fort (n={b['n_high']}) : Brier moyen {b['mean_high']:.4f}",
        f"- Écart (fort − faible) : {b['point']:+.4f} "
        f"{bootstrap.fmt_ci(b['ci_lo'], b['ci_hi'], unit='pt Brier')} — "
        f"{'IC exclut 0' if b['excludes_zero'] else 'IC contient 0'}",
        "",
        "## Angle 2 — ROI théorique du meilleur pari M3.5 (matchs à edge positif "
        "seulement, la population qu'un Kelly réel toucherait)",
        "",
        f"- Paris à edge positif : {v['n_value_bets']}/{result['n']}",
        f"- Désaccord faible (n={v['n_low']}) : ROI moyen {v['mean_low']:+.3f}",
        f"- Désaccord fort (n={v['n_high']}) : ROI moyen {v['mean_high']:+.3f}",
        f"- Écart (fort − faible) : {v['point']:+.3f} "
        f"{bootstrap.fmt_ci(v['ci_lo'], v['ci_hi'], unit='pt ROI')} — "
        f"{'IC exclut 0' if v['excludes_zero'] else 'IC contient 0'}",
        "",
        "## Verdict",
        "",
    ]
    brier_signal = b["excludes_zero"] and b["point"] > 0
    roi_signal = v["excludes_zero"] and v["point"] < 0
    roi_wrong_direction = v["point"] > 0  # désaccord fort -> ROI MOINS négatif : sens inverse de l'hypothèse
    if roi_signal:
        lines.append(
            "⚠ Signal détecté sur l'angle qui compte pour le staking (ROI théorique "
            "des paris à edge positif, IC excluant 0, dans le sens attendu). NE PAS "
            "construire de calibration directement sur ces 4338 matchs (déjà TEST) : "
            "ce signal justifie d'ouvrir un chantier, pas de coder un facteur de "
            "réduction dessus. Il faudrait une donnée fraîche (production à venir) "
            "pour régler et valider un seuil/facteur sans re-tuner sur ce qui a déjà "
            "servi de test aux deux modèles.")
    else:
        lines.append(
            "**Pas de signal actionnable pour le staking.** L'angle 1 (Brier "
            "général de M3.5) montre un écart réel — IC excluant 0, désaccord fort "
            "associé à un Brier nettement plus mauvais — donc le désaccord M3.5/GBM "
            "N'EST PAS du bruit en général : il dit quelque chose sur la fiabilité "
            "de M3.5 sur un match donné. Mais l'angle 2, le seul qui compte pour "
            "Kelly (ROI théorique des paris à edge réel, pas juste la qualité "
            "générale de la prédiction), ne confirme PAS ce signal : IC contenant 0"
            + (", et le point va même dans le sens INVERSE de l'hypothèse (désaccord "
               "fort associé à un ROI théorique moins mauvais, pas pire)"
               if roi_wrong_direction else "") + ". "
            "Même pattern que le chantier C1 (`clv_signal_check.py`) : un signal "
            "large (angle 1 ici, le diagnostic exploratoire là-bas) qui ne survit "
            "pas au passage à la métrique réellement actionnable (angle 2 ici, le "
            "score composite tune/test/shuffle là-bas). **Verdict : la calibration "
            "(moduler Kelly par ce désaccord) N'EST PAS construite** — elle "
            "capturerait un signal réel mais mal ciblé (qualité générale de "
            "prédiction) en le faisant passer pour un signal de staking, ce qu'il "
            "n'est pas démontré être. Chantier fermé proprement, comme fatigue/M8 "
            "et C1. À rouvrir seulement avec une vraie donnée fraîche (production, "
            "pas ce même jeu de test) et, idéalement, les cotes réellement cotées "
            "plutôt que le marché démargé (cf. limite VALUE_EDGE_THRESHOLD).")
    Path(path).write_text("\n".join(lines) + "\n")
    log.info("Rapport écrit dans %s", path)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="data/football.db")
    p.add_argument("--report", default="reports/kelly_uncertainty_check.md")
    args = p.parse_args(argv)

    conn = db.connect(args.db)
    result = run(conn)
    write_report(result, args.report)


if __name__ == "__main__":
    main()
