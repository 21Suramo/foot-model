"""Backtest walk-forward du modèle Dixon-Coles contre le marché.

Protocole (verrouillé par construction) :
- pour prédire la semaine S (lundi→dimanche), fit uniquement sur les matchs
  antérieurs au lundi de S — model.fit lève ValueError sinon ; refit hebdomadaire ;
- burn-in : 1819 et 1920 servent d'historique, jamais prédites ;
- --tune : grid search de ξ sur 2021+2122 uniquement, critère Brier,
  résultat figé dans data/xi_frozen.json — interdiction d'y retoucher ensuite ;
- --run : test 2223→2526 avec ξ figé, prédictions upsertées dans la table
  predictions (modèle + marché démargé power + baseline fréquences) ;
- --shuffle-test : contrôle anti-fuite — scores permutés entre matchs
  (dates/équipes/cotes fixes), le Brier doit se dégrader nettement.

Usage : python backtest.py --tune | --run | --shuffle-test [--db ...]
"""
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

import db
import footballdata
import model

log = logging.getLogger("backtest")

BURN_IN = ("1819", "1920")
VALIDATION = ("2021", "2122")
TEST = ("2223", "2324", "2425", "2526")
XI_GRID = (0.0, 0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005, 0.008)  # jours^-1
XI_PATH = Path("data/xi_frozen.json")
LEAK_PATH = Path("data/leak_check.json")
LEAK_SEASON = "2223"  # saison de test rejouée avec labels permutés


def outcome_index(fthg, ftag):
    """0 = domicile, 1 = nul, 2 = extérieur."""
    return 0 if fthg > ftag else (1 if fthg == ftag else 2)


def brier(probs, outcome):
    """Brier multi-classe : somme des (p_k - 1{k=outcome})^2."""
    return sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in enumerate(probs))


def logloss(probs, outcome):
    return -np.log(max(probs[outcome], 1e-12))


def demargin_power(odds_h, odds_d, odds_a):
    """Probas implicites démargées par la méthode power : p_i = (1/o_i)^k, Σp = 1."""
    inv = np.array([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a])
    f = lambda k: (inv ** k).sum() - 1.0
    if abs(f(1.0)) < 1e-12:
        k = 1.0
    else:
        k = brentq(f, 1e-3, 20.0)
    p = inv ** k
    return float(p[0]), float(p[1]), float(p[2])


def load_league(conn, league):
    """Matchs joués d'une ligue, triés par date."""
    return [dict(r) for r in conn.execute(
        "SELECT match_id, date, season, home, away, fthg, ftag, odds_h, odds_d, odds_a "
        "FROM matches WHERE league = ? AND fthg IS NOT NULL ORDER BY date, match_id",
        (league,))]


def monday_of(date_str):
    d = datetime.date.fromisoformat(date_str)
    return d - datetime.timedelta(days=d.weekday())


def walk_forward(rows, target_seasons, xi, with_market=False):
    """Prédit les matchs des saisons cibles, refit hebdomadaire sur le passé strict.

    Retourne une liste de dicts {match_id, season, date, outcome, model, freq
    [, market]} avec des triplets de probas (H, D, A).
    """
    weeks = {}
    for r in rows:
        if r["season"] in target_seasons:
            weeks.setdefault(monday_of(r["date"]), []).append(r)

    out = []
    fitted = None
    train = []
    counts = np.zeros(3)
    i = 0
    for monday in sorted(weeks):
        cutoff = monday.isoformat()
        while i < len(rows) and rows[i]["date"] < cutoff:
            train.append(rows[i])
            counts[outcome_index(rows[i]["fthg"], rows[i]["ftag"])] += 1
            i += 1
        fitted = model.fit(train, xi=xi, ref_date=monday, warm_start=fitted)
        freq = tuple(counts / counts.sum()) if counts.sum() else (1 / 3,) * 3
        for r in weeks[monday]:
            rec = {"match_id": r["match_id"], "season": r["season"], "date": r["date"],
                   "outcome": outcome_index(r["fthg"], r["ftag"]),
                   "model": fitted.probs_1x2(r["home"], r["away"]),
                   "freq": freq}
            if with_market and r["odds_h"] and r["odds_d"] and r["odds_a"]:
                rec["market"] = demargin_power(r["odds_h"], r["odds_d"], r["odds_a"])
            out.append(rec)
    return out


def tune(conn):
    """Grid search de ξ sur les saisons de validation, écrit xi_frozen.json."""
    if XI_PATH.exists():
        frozen = json.loads(XI_PATH.read_text())
        log.warning("ξ déjà figé à %s dans %s — le protocole interdit de le re-régler. "
                    "Supprimer le fichier manuellement pour assumer un nouveau tuning.",
                    frozen["xi"], XI_PATH)
        return
    rows_by_league = {lg: load_league(conn, lg) for lg in footballdata.LEAGUES}
    grid = {}
    for xi in XI_GRID:
        scores = []
        for league, rows in rows_by_league.items():
            preds = walk_forward(rows, VALIDATION, xi)
            scores += [brier(p["model"], p["outcome"]) for p in preds]
        grid[xi] = float(np.mean(scores))
        log.info("ξ = %-7g Brier validation = %.5f (%d matchs)", xi, grid[xi], len(scores))
    best = min(grid, key=grid.get)
    XI_PATH.write_text(json.dumps({
        "xi": best, "criterion": "brier", "validation_seasons": VALIDATION,
        "grid": {str(k): v for k, v in grid.items()},
        "tuned_at": datetime.date.today().isoformat(),
    }, indent=2))
    log.info("ξ figé : %g (Brier %.5f) -> %s", best, grid[best], XI_PATH)


def frozen_xi():
    if not XI_PATH.exists():
        sys.exit(f"{XI_PATH} absent : lancer d'abord python backtest.py --tune")
    return json.loads(XI_PATH.read_text())["xi"]


def run(conn):
    """Backtest de test avec ξ figé, prédictions upsertées en base."""
    xi = frozen_xi()
    total = 0
    for league in footballdata.LEAGUES:
        preds = walk_forward(load_league(conn, league), TEST, xi, with_market=True)
        for p in preds:
            market = p.get("market", (None, None, None))
            db.upsert_prediction(conn, {
                "match_id": p["match_id"], "xi": xi,
                "model_h": p["model"][0], "model_d": p["model"][1], "model_a": p["model"][2],
                "market_h": market[0], "market_d": market[1], "market_a": market[2],
                "freq_h": p["freq"][0], "freq_d": p["freq"][1], "freq_a": p["freq"][2],
            })
        conn.commit()
        b = np.mean([brier(p["model"], p["outcome"]) for p in preds])
        log.info("%s : %d prédictions de test (ξ = %g), Brier modèle %.5f", league, len(preds), xi, b)
        total += len(preds)
    log.info("%d prédictions écrites dans la table predictions.", total)


def shuffle_test(conn, seed=42):
    """Anti-fuite : mêmes dates/équipes/cotes, scores permutés entre matchs.

    Le lien équipes→résultat est détruit ; sans fuite, le Brier sur %s doit se
    dégrader nettement. S'il tient ou s'améliore, une fuite existe quelque part.
    """ % LEAK_SEASON
    xi = frozen_xi()
    rng = np.random.default_rng(seed)
    report = {"season": LEAK_SEASON, "xi": xi, "seed": seed, "leagues": {}}
    for league in footballdata.LEAGUES:
        rows = load_league(conn, league)
        real = np.mean([brier(p["model"], p["outcome"])
                        for p in walk_forward(rows, (LEAK_SEASON,), xi)])
        perm = rng.permutation(len(rows))
        shuffled = [dict(r, fthg=rows[j]["fthg"], ftag=rows[j]["ftag"])
                    for r, j in zip(rows, perm)]
        shuf = np.mean([brier(p["model"], p["outcome"])
                        for p in walk_forward(shuffled, (LEAK_SEASON,), xi)])
        report["leagues"][league] = {"brier_real": float(real), "brier_shuffled": float(shuf),
                                     "degraded": bool(shuf > real)}
        log.info("%s : Brier réel %.5f / permuté %.5f -> %s", league, real, shuf,
                 "dégradé (attendu)" if shuf > real else "PAS DÉGRADÉ : FUITE PROBABLE")
    report["all_degraded"] = all(v["degraded"] for v in report["leagues"].values())
    LEAK_PATH.write_text(json.dumps(report, indent=2))
    log.info("Résultat écrit dans %s", LEAK_PATH)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tune", action="store_true", help="grid search de ξ (validation)")
    parser.add_argument("--run", action="store_true", help="backtest de test avec ξ figé")
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
