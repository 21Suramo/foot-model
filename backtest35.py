"""M3.5 : trois améliorations ciblées du Dixon-Coles, même protocole que M3.

1. Pseudo-buts xG : cible d'entraînement w×xG + (1-w)×buts réels, (w, ξ)
   re-réglés conjointement par grid 2D sur les saisons de validation de M3.
2. Recalibration : température monotone q_i ∝ p_i^t (t > 1 accentue les
   favoris), t ajusté uniquement sur la validation.
3. Diagnostic promus : Brier avec/sans promu ; le shrinkage κ est re-testé
   sur la validation (pas de données de division inférieure disponibles).

Tout est réglé sur 2020-21 + 2021-22 puis figé dans data/m35_frozen.json ;
le jeu de test 2223→2526 ne sert à AUCUN réglage.

Usage : python backtest35.py --tune | --run | --shuffle-test [--db ...]
"""
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

import backtest
import db
import footballdata
import model

log = logging.getLogger("backtest35")

W_GRID = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
XI_GRID = backtest.XI_GRID
KAPPA_GRID = (0.5, 1.0, 2.0, 4.0, 8.0)
KAPPA_DEFAULT = model.PRIOR_WEIGHT
KAPPA_MIN_GAIN = 1e-4   # gain Brier validation minimal pour dévier du défaut
TEMP_BOUNDS = (0.5, 2.5)
FROZEN_PATH = Path("data/m35_frozen.json")
LEAK_PATH = Path("data/leak_check_m35.json")


def apply_temperature(probs, t):
    """Recalibration monotone : q_i ∝ p_i^t. t=1 identité, t>1 accentue les favoris."""
    q = np.asarray(probs, dtype=float) ** t
    q /= q.sum()
    return float(q[0]), float(q[1]), float(q[2])


def promoted_teams(rows):
    """Par saison, équipes absentes de la saison précédente de la ligue."""
    teams_by = {}
    for r in rows:
        teams_by.setdefault(r["season"], set()).update((r["home"], r["away"]))
    seasons = sorted(teams_by)
    return {cur: teams_by[cur] - teams_by[prev]
            for prev, cur in zip(seasons, seasons[1:])}


def _validation_brier(rows_by_league, xi, xg_weight, prior_weight=KAPPA_DEFAULT):
    preds = []
    for rows in rows_by_league.values():
        preds += backtest.walk_forward(rows, backtest.VALIDATION, xi,
                                       xg_weight=xg_weight, prior_weight=prior_weight)
    return float(np.mean([backtest.brier(p["model"], p["outcome"]) for p in preds])), preds


def tune(conn):
    """Réglages M3.5, validation uniquement, figés dans m35_frozen.json."""
    if FROZEN_PATH.exists():
        frozen = json.loads(FROZEN_PATH.read_text())
        log.warning("Réglages M3.5 déjà figés (%s) — le protocole interdit de les re-régler. "
                    "Supprimer le fichier manuellement pour assumer un nouveau tuning.", frozen)
        return
    rows_by_league = {lg: backtest.load_league(conn, lg) for lg in footballdata.LEAGUES}

    # 1. Grid 2D (w, ξ)
    grid = {}
    best = None
    for w in W_GRID:
        for xi in XI_GRID:
            b, _ = _validation_brier(rows_by_league, xi, w)
            grid[f"{w},{xi}"] = b
            if best is None or b < grid[f"{best[0]},{best[1]}"]:
                best = (w, xi)
        log.info("w = %.1f : meilleur Brier de la ligne %.5f", w,
                 min(v for k, v in grid.items() if k.startswith(f"{w},")))
    w_best, xi_best = best
    log.info("Grid 2D : w = %.1f, ξ = %g (Brier %.5f)", w_best, xi_best, grid[f"{w_best},{xi_best}"])

    # 2. Shrinkage κ (diagnostic promus sur la validation d'abord)
    kappa_grid = {}
    for kappa in KAPPA_GRID:
        kappa_grid[kappa], _ = _validation_brier(rows_by_league, xi_best, w_best, kappa)
        log.info("κ = %-4g Brier validation = %.5f", kappa, kappa_grid[kappa])
    kappa_best = min(kappa_grid, key=kappa_grid.get)
    if kappa_grid[KAPPA_DEFAULT] - kappa_grid[kappa_best] < KAPPA_MIN_GAIN:
        kappa_best = KAPPA_DEFAULT  # gain sous le bruit : on garde le défaut
    log.info("κ figé : %g", kappa_best)

    # 3. Température sur les prédictions de validation de la config finale
    _, preds = _validation_brier(rows_by_league, xi_best, w_best, kappa_best)
    raw = [(p["model"], p["outcome"]) for p in preds]
    res = minimize_scalar(
        lambda t: np.mean([backtest.brier(apply_temperature(pr, t), o) for pr, o in raw]),
        bounds=TEMP_BOUNDS, method="bounded")
    t_best = float(res.x)
    b_raw = float(np.mean([backtest.brier(pr, o) for pr, o in raw]))
    log.info("Température figée : t = %.3f (Brier validation %.5f -> %.5f)",
             t_best, b_raw, float(res.fun))

    # Diagnostic promus sur la validation (contexte du choix de κ)
    promo_diag = {}
    for lg, rows in rows_by_league.items():
        promo = promoted_teams(rows)
        preds_lg = backtest.walk_forward(rows, backtest.VALIDATION, xi_best,
                                         xg_weight=w_best, prior_weight=kappa_best)
        by_id = {r["match_id"]: r for r in rows}
        with_p, without_p = [], []
        for p in preds_lg:
            r = by_id[p["match_id"]]
            teams_promo = promo.get(r["season"], set())
            bucket = with_p if (r["home"] in teams_promo or r["away"] in teams_promo) else without_p
            bucket.append(backtest.brier(apply_temperature(p["model"], t_best), p["outcome"]))
        promo_diag[lg] = {"n_avec": len(with_p), "brier_avec": float(np.mean(with_p)),
                          "n_sans": len(without_p), "brier_sans": float(np.mean(without_p))}

    FROZEN_PATH.write_text(json.dumps({
        "w": w_best, "xi": xi_best, "kappa": kappa_best, "temperature": t_best,
        "criterion": "brier", "validation_seasons": backtest.VALIDATION,
        "brier_validation": float(res.fun), "brier_validation_raw": b_raw,
        "grid_w_xi": grid, "grid_kappa": {str(k): v for k, v in kappa_grid.items()},
        "promoted_validation": promo_diag,
        "tuned_at": datetime.date.today().isoformat(),
    }, indent=2))
    log.info("Réglages figés -> %s", FROZEN_PATH)


def frozen():
    if not FROZEN_PATH.exists():
        sys.exit(f"{FROZEN_PATH} absent : lancer d'abord python backtest35.py --tune")
    return json.loads(FROZEN_PATH.read_text())


def run(conn):
    """Backtest de test M3.5 avec réglages figés -> table predictions_m35."""
    cfg = frozen()
    total = 0
    for league in footballdata.LEAGUES:
        preds = backtest.walk_forward(backtest.load_league(conn, league), backtest.TEST,
                                      cfg["xi"], with_market=True,
                                      xg_weight=cfg["w"], prior_weight=cfg["kappa"])
        for p in preds:
            cal = apply_temperature(p["model"], cfg["temperature"])
            market = p.get("market", (None, None, None))
            db.upsert_prediction_m35(conn, {
                "match_id": p["match_id"], "xi": cfg["xi"], "xg_w": cfg["w"],
                "kappa": cfg["kappa"], "temperature": cfg["temperature"],
                "model_h": cal[0], "model_d": cal[1], "model_a": cal[2],
                "raw_h": p["model"][0], "raw_d": p["model"][1], "raw_a": p["model"][2],
                "market_h": market[0], "market_d": market[1], "market_a": market[2],
                "freq_h": p["freq"][0], "freq_d": p["freq"][1], "freq_a": p["freq"][2],
            })
        conn.commit()
        b = np.mean([backtest.brier(apply_temperature(p["model"], cfg["temperature"]),
                                    p["outcome"]) for p in preds])
        log.info("%s : %d prédictions de test M3.5, Brier %.5f", league, len(preds), b)
        total += len(preds)
    log.info("%d prédictions écrites dans predictions_m35.", total)


def shuffle_test(conn, seed=42):
    """Anti-fuite M3.5 : scores ET xG permutés ensemble entre matchs."""
    cfg = frozen()
    rng = np.random.default_rng(seed)
    report = {"season": backtest.LEAK_SEASON, "config": {k: cfg[k] for k in
                                                         ("w", "xi", "kappa", "temperature")},
              "seed": seed, "leagues": {}}
    for league in footballdata.LEAGUES:
        rows = backtest.load_league(conn, league)

        def _brier(rs):
            preds = backtest.walk_forward(rs, (backtest.LEAK_SEASON,), cfg["xi"],
                                          xg_weight=cfg["w"], prior_weight=cfg["kappa"])
            return float(np.mean([backtest.brier(
                apply_temperature(p["model"], cfg["temperature"]), p["outcome"])
                for p in preds]))

        real = _brier(rows)
        perm = rng.permutation(len(rows))
        shuffled = [dict(r, fthg=rows[j]["fthg"], ftag=rows[j]["ftag"],
                         xg_home=rows[j]["xg_home"], xg_away=rows[j]["xg_away"])
                    for r, j in zip(rows, perm)]
        shuf = _brier(shuffled)
        report["leagues"][league] = {"brier_real": real, "brier_shuffled": shuf,
                                     "degraded": bool(shuf > real)}
        log.info("%s : Brier réel %.5f / permuté %.5f -> %s", league, real, shuf,
                 "dégradé (attendu)" if shuf > real else "PAS DÉGRADÉ : FUITE PROBABLE")
    report["all_degraded"] = all(v["degraded"] for v in report["leagues"].values())
    LEAK_PATH.write_text(json.dumps(report, indent=2))
    log.info("Résultat écrit dans %s", LEAK_PATH)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tune", action="store_true", help="grid 2D (w, ξ) + κ + température")
    parser.add_argument("--run", action="store_true", help="backtest de test avec réglages figés")
    parser.add_argument("--shuffle-test", action="store_true", help="contrôle anti-fuite")
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    if not (args.tune or args.run or args.shuffle_test):
        parser.print_help()
        return 1
    conn = db.connect(args.db)
    if args.tune:
        tune(conn)
    if args.run:
        run(conn)
    if args.shuffle_test:
        shuffle_test(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
