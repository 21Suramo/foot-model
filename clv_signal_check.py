"""C1 (roadmap) — le mouvement de cote ouverture→clôture est-il un signal
d'entrée exploitable, avant d'y indexer une mise ?

C1 propose : quand le modèle voit de la value ET que le marché bouge dans le
même sens (un sharp a la même info), c'est un signal renforcé ; quand ils
divergent, c'est un signal d'alerte. Avant de coder cette logique dans
predict.py (mise modulée par la convergence), la roadmap elle-même prévient :
« à valider sur validation, pas sur test » — exactement le protocole déjà
suivi pour la fatigue (fatigue_signal_check.py) : vérifier que le signal
existe avant de construire quoi que ce soit dessus.

Protocole : walk-forward hebdomadaire IDENTIQUE à backtest.walk_forward (même
garde anti-fuite), restreint à 1920+VALIDATION (jamais le TEST), réglages
figés de production (backtest35.frozen()). Pour chaque match :

- p_open = probas fair à l'OUVERTURE (démargeage power, cohérent avec le
  reste du projet — cf. backtest.py), depuis les CSV bruts football-data
  (mêmes colonnes que backtest_blend.py, réutilisées telles quelles) ;
- p_close = probas fair à la CLÔTURE (le "market" que backtest.walk_forward
  calcule déjà, colonne odds_* de la base) ;
- p_model = probas du modèle M3.5 recalibré (backtest35.apply_temperature).

Le "pari value" du match est l'issue à l'edge modèle-vs-ouverture le plus
élevé, s'il est positif (edge = p_model − p_open) — c'est la seule issue
qu'on aurait effectivement misée à l'ouverture, donc la seule pertinente ici.
Il est classé CONVERGENT si le marché a bougé vers cette issue d'ici la
clôture (p_close > p_open sur cette issue), DIVERGENT sinon. On compare le
ROI théorique (mise plate 1 unité à la cote d'ouverture réelle) des deux
groupes, avec IC bootstrap non apparié (groupes disjoints) — cf.
bootstrap.ci_diff_mean.

Usage : python clv_signal_check.py [--db data/football.db]

⚠ Nécessite les CSV bruts football-data en cache (data/raw/football-data/)
pour les cotes d'OUVERTURE — absentes de la base (qui ne garde que la
clôture). Lancer `python pipeline.py --update` au moins une fois si le
cache est vide (mêmes fichiers que backtest_blend.py, réutilisés ici).
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest35
import backtest_blend
import bootstrap
import db
import footballdata

log = logging.getLogger("clv_signal_check")

TARGET_SEASONS = ("1920",) + backtest.VALIDATION  # jamais le TEST ; 1920 pour la puissance
ISSUE_NAMES = ("home", "draw", "away")


def collect_bets(conn, cfg):
    """Un enregistrement par match où le modèle voit de la value à l'ouverture
    ET où l'ouverture ET la clôture sont disponibles. walk-forward strict
    (backtest.walk_forward), jamais le futur."""
    open_map = backtest_blend.opening_odds_map(TARGET_SEASONS)
    records = []
    n_no_open = n_no_close = n_no_value = 0
    for league in footballdata.LEAGUES:
        rows = backtest.load_league(conn, league)
        by_id = {r["match_id"]: r for r in rows}
        preds = backtest.walk_forward(rows, TARGET_SEASONS, cfg["xi"], with_market=True,
                                      xg_weight=cfg["w"], prior_weight=cfg["kappa"])
        for p in preds:
            r = by_id[p["match_id"]]
            if "market" not in p:
                n_no_close += 1
                continue
            trip_open = open_map.get((r["date"], r["home"], r["away"]))
            if trip_open is None:
                n_no_open += 1
                continue
            p_open = backtest.demargin_power(*trip_open)
            p_close = p["market"]
            p_model = backtest35.apply_temperature(p["model"], cfg["temperature"])
            edges = [p_model[k] - p_open[k] for k in range(3)]
            k_star = int(np.argmax(edges))
            if edges[k_star] <= 0:
                n_no_value += 1
                continue
            movement = p_close[k_star] - p_open[k_star]
            odds_open = trip_open[k_star]
            win = 1 if p["outcome"] == k_star else 0
            roi = (odds_open - 1.0) if win else -1.0
            records.append({
                "league": league, "season": r["season"], "issue": ISSUE_NAMES[k_star],
                "edge": edges[k_star], "movement": movement,
                "convergent": movement > 0.0,
                "roi": roi, "outcome": p["outcome"],
                "model_brier": backtest.brier(p_model, p["outcome"]),
            })
    log.info("%d paris value collectés (%d sans ouverture, %d sans clôture, "
             "%d sans value modèle).", len(records), n_no_open, n_no_close, n_no_value)
    return records


def summarize(records):
    conv = [r for r in records if r["convergent"]]
    div = [r for r in records if not r["convergent"]]
    lines = [f"{len(records)} paris « value » (edge modèle > 0 à l'ouverture) sur "
             f"{'+'.join(TARGET_SEASONS)} (jamais le TEST) : {len(conv)} convergents "
             f"(le marché a bougé vers l'issue value d'ici la clôture), {len(div)} divergents.", "",
             "⚠ Portée : « edge > 0 » n'a AUCUN seuil de marge (contrairement à la production, "
             "qui filtre par `margin_ok`/mise Kelly) — sur deux distributions de probas "
             "différentes, une des 3 issues a presque toujours un edge strictement positif. "
             "Ce chantier mesure donc le signal de convergence sur TOUT désaccord modèle/marché, "
             "pas seulement les paris qu'on aurait réellement engagés (edge significatif). C'est "
             "la bonne échelle pour DÉTECTER un signal (puissance statistique maximale, comme "
             "fatigue_signal_check.py) ; un seuil de marge resterait à ajouter si C1 est construit "
             "en production.", ""]

    def _pct_ci(lo, hi):
        # ci_mean/ci_diff_mean renvoient des fractions (ROI = 0.043) ; fmt_ci
        # attend déjà des points de pourcentage (×100), comme le point affiché
        # juste à côté via {:+.1%} — sans ce ×100 les deux étaient sur deux
        # échelles différentes dans le même message.
        return bootstrap.fmt_ci(None if lo is None else lo * 100,
                                None if hi is None else hi * 100, unit="pts", decimals=1)

    roi_conv, roi_div = [r["roi"] for r in conv], [r["roi"] for r in div]
    p_conv, lo_c, hi_c = bootstrap.ci_mean(roi_conv)
    p_div, lo_d, hi_d = bootstrap.ci_mean(roi_div)
    lines.append(f"- ROI théorique convergents : {p_conv:+.1%} {_pct_ci(lo_c, hi_c)} (n={len(conv)})"
                 if p_conv is not None else "- ROI convergents : aucune observation")
    lines.append(f"- ROI théorique divergents  : {p_div:+.1%} {_pct_ci(lo_d, hi_d)} (n={len(div)})"
                 if p_div is not None else "- ROI divergents : aucune observation")

    diff, lo, hi = bootstrap.ci_diff_mean(roi_conv, roi_div)
    signal = False
    if diff is not None:
        signal = bootstrap.excludes_zero(lo, hi) is True and diff > 0
        lines.append(f"- Écart ROI convergents − divergents : {diff:+.1%} {_pct_ci(lo, hi)}")
    else:
        lines.append("- Écart ROI : IC indisponible (groupe trop petit)")
    lines.append("")

    b_conv = [r["model_brier"] for r in conv]
    b_div = [r["model_brier"] for r in div]
    if b_conv and b_div:
        lines.append(f"Brier modèle (diagnostic, pas le critère de décision) : "
                     f"convergents {np.mean(b_conv):.5f} (n={len(b_conv)}), "
                     f"divergents {np.mean(b_div):.5f} (n={len(b_div)}).")
        lines.append("")

    verdict = ("Signal détecté : le ROI théorique des paris convergents dépasse "
               "significativement (IC excluant 0) celui des paris divergents — "
               "un score composite modèle+mouvement de cote (C1 complet) peut être "
               "envisagé, à RÉGLER SUR LA VALIDATION UNIQUEMENT (jamais le test, "
               "cf. piège de confirmation cité par la roadmap elle-même)."
               if signal else
               "Aucun signal détecté (IC n'exclut pas 0, ou écart négatif) : ne pas "
               "construire de modulation de mise par convergence de cote — elle "
               "figerait du bruit. C1 reste investigué, pas construit.")
    lines.append(f"## Verdict\n\n{verdict}\n")
    return "\n".join(lines), signal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    conn = db.connect(args.db)
    cfg = backtest35.frozen()
    records = collect_bets(conn, cfg)
    conn.close()

    if not records:
        sys.exit("Aucun pari value collecté — cache CSV brut absent ? "
                 "Lancer `python pipeline.py --update`.")

    text, signal = summarize(records)
    print(text)
    out = Path("reports/clv_signal_check.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# C1 (roadmap) — le mouvement de cote est-il un signal d'entrée ?\n\n" + text)
    print(f"\nRapport écrit dans {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
