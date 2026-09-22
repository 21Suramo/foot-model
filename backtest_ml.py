"""Backtest walk-forward "purgé" du modèle GBM (roadmap B1-B4, 2026-09-22).

Même discipline que backtest.py/backtest35.py : --tune règle des
hyperparamètres sur VALIDATION uniquement (jamais retouché après), --run
teste UNE FOIS sur TEST avec les hyperparamètres figés, --shuffle-test est
le contrôle anti-fuite. Résultats écrits dans data/ml_frozen.json /
predictions_ml / data/leak_check_ml.json, lus par report_ml.py.

Deux différences ASSUMÉES et documentées avec backtest.py, pas des oublis :

1. **Refit par saison, pas par semaine.** model.fit (Dixon-Coles) est un
   L-BFGS-B sur quelques centaines de paramètres, assez rapide pour un
   refit hebdomadaire strict. Un GBM (des centaines d'arbres) coûte
   nettement plus cher : ce backtest refit une fois par FRONTIÈRE DE
   SAISON (la coupure la plus ancienne parmi toutes les ligues couvertes,
   donc strictement anti-fuite pour chacune) plutôt que chaque semaine —
   plus grossier que Dixon-Coles, choix de vitesse assumé.
2. **Portée ALL_LEAGUES (top + secondaires, roadmap A3), un seul modèle
   poolé.** Contrairement à backtest.py/backtest35.py qui restent sur
   LEAGUES (M3.5 déjà publié, jamais retouché), le GBM est entraîné sur
   l'union des 7 ligues avec 'league' en feature catégorielle — le rapport
   (report_ml.py) publie quand même le Brier PAR LIGUE séparément : un bon
   résultat agrégé ne doit jamais masquer une ligue secondaire à la traîne.

Stacking : les probas Dixon-Coles walk-forward (goals-only, même ξ figé
que M3) sont calculées pour CHAQUE ligue via backtest.walk_forward sur la
totalité de l'historique (pas seulement TEST) et injectées comme features
d'entrée du GBM — Dixon-Coles n'est donc jamais retouché, seulement
réutilisé comme générateur de features.

Usage : python backtest_ml.py --tune | --run | --shuffle-test [--db ...]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

import backtest
import db
import footballdata
import ml_model
from features.build import build_feature_table, numeric_feature_names

log = logging.getLogger("backtest_ml")

FROZEN_PATH = Path("data/ml_frozen.json")
LEAK_PATH = Path("data/leak_check_ml.json")

HYPERPARAM_GRID = [
    {"num_leaves": 7, "learning_rate": 0.08, "num_rounds": 80},
    {"num_leaves": 15, "learning_rate": 0.05, "num_rounds": 150},
    {"num_leaves": 31, "learning_rate": 0.03, "num_rounds": 200},
]

STACK_COLS = ("dc_prob_h", "dc_prob_d", "dc_prob_a")
SHUFFLE_COLS = ("fthg", "ftag", "xg_home", "xg_away",
                "shots_h", "shots_a", "sot_h", "sot_a", "corners_h", "corners_a")


def _dc_xi():
    """ξ figé M3 (data/xi_frozen.json). Sert UNIQUEMENT à générer une feature
    d'entrée du GBM (stacking) — pas une nouvelle publication du backtest M3,
    qui reste celui de backtest.py/report.py, inchangé."""
    if backtest.XI_PATH.exists():
        return json.loads(backtest.XI_PATH.read_text())["xi"]
    log.warning("%s absent : xi=0.002 utilisé par défaut pour le stacking DC "
                "(lancer python backtest.py --tune pour figer la vraie valeur).",
                backtest.XI_PATH)
    return 0.002


def dc_stacking_map(conn, leagues):
    """match_id -> {dc_prob_h, dc_prob_d, dc_prob_a}, Dixon-Coles goals-only
    walk-forward sur TOUTE la plage de saisons SAUF la toute première
    (SEASONS[0] — comme dans backtest.py, elle ne sert qu'à amorcer
    l'historique, jamais prédite elle-même, sinon la toute première semaine
    n'aurait aucun match d'entraînement). Les matchs de cette première
    saison reçoivent donc dc_prob_*=None dans la table de features — NaN
    pour LightGBM, géré nativement (cf. ml_model.py) — pas une valeur
    inventée. Sert de feature d'entrée, jamais publié comme une prédiction
    validée pour les ligues secondaires (Dixon-Coles n'y a jamais été
    backtesté au sens M3)."""
    xi = _dc_xi()
    out = {}
    predict_seasons = footballdata.SEASONS[1:]
    for league in leagues:
        rows = backtest.load_league(conn, league)
        if not rows:
            continue
        preds = backtest.walk_forward(rows, predict_seasons, xi)
        for p in preds:
            out[p["match_id"]] = {
                "dc_prob_h": p["model"][0], "dc_prob_d": p["model"][1], "dc_prob_a": p["model"][2],
            }
    return out


def _season_start_dates(table):
    starts = {}
    for r in table:
        s = r["season"]
        if s not in starts or r["date"] < starts[s]:
            starts[s] = r["date"]
    return starts


def build_stacked_table(conn, leagues):
    table = build_feature_table(conn, leagues=leagues)
    if not table:
        return [], []
    dc_map = dc_stacking_map(conn, leagues)
    for r in table:
        r.update(dc_map.get(r["match_id"], {c: None for c in STACK_COLS}))
    # numeric_feature_names(table[0]) inclut déjà dc_prob_h/d/a (ajoutées
    # ci-dessus AVANT ce calcul) — ne pas les rajouter, LightGBM refuse un
    # nom de feature dupliqué.
    feature_names = numeric_feature_names(table[0]) + ["league"]
    return table, feature_names


def purged_walk_forward(table, feature_names, target_seasons, clf_params=None,
                        reg_params=None, num_rounds=ml_model.DEFAULT_NUM_ROUNDS):
    """Un refit par saison cible, entraîné sur tout l'historique strictement
    antérieur au premier match de cette saison (toutes ligues confondues —
    frontière conservatrice, garantit l'absence de fuite pour chaque ligue)."""
    starts = _season_start_dates(table)
    by_season = {}
    for r in table:
        by_season.setdefault(r["season"], []).append(r)

    out = []
    for season in target_seasons:
        cutoff = starts.get(season)
        test_rows = by_season.get(season, [])
        if cutoff is None or not test_rows:
            continue
        train = [r for r in table if r["date"] < cutoff]
        if not train:
            continue
        model = ml_model.fit(train, feature_names, clf_params, reg_params, num_rounds)
        for r in test_rows:
            probs = model.probs_1x2(r)
            out.append({
                "match_id": r["match_id"], "league": r["league"], "season": season,
                "date": r["date"], "outcome": r["outcome"], "model": probs,
                "market": (backtest.demargin_power(r["odds_h"], r["odds_d"], r["odds_a"])
                          if r.get("odds_h") and r.get("odds_d") and r.get("odds_a") else None),
            })
    return out


def _mean_brier(preds):
    return float(np.mean([backtest.brier(p["model"], p["outcome"]) for p in preds]))


def tune(conn, leagues=None):
    leagues = leagues if leagues is not None else footballdata.ALL_LEAGUES
    if FROZEN_PATH.exists():
        frozen = json.loads(FROZEN_PATH.read_text())
        log.warning("Réglages GBM déjà figés (%s) — le protocole interdit de les re-régler. "
                    "Supprimer le fichier manuellement pour assumer un nouveau tuning.", frozen)
        return
    table, feature_names = build_stacked_table(conn, leagues)
    if not table:
        sys.exit("Aucun match disponible pour construire la table de features.")

    grid_results = {}
    best = None
    for cfg in HYPERPARAM_GRID:
        clf_p = {"num_leaves": cfg["num_leaves"], "learning_rate": cfg["learning_rate"]}
        preds = purged_walk_forward(table, feature_names, backtest.VALIDATION,
                                    clf_params=clf_p, reg_params=clf_p,
                                    num_rounds=cfg["num_rounds"])
        b = _mean_brier(preds)
        key = f"{cfg['num_leaves']},{cfg['learning_rate']},{cfg['num_rounds']}"
        grid_results[key] = b
        log.info("num_leaves=%d lr=%.2f rounds=%d : Brier validation = %.5f",
                 cfg["num_leaves"], cfg["learning_rate"], cfg["num_rounds"], b)
        if best is None or b < grid_results[best[0]]:
            best = (key, cfg)
    best_key, best_cfg = best
    log.info("Meilleure config : %s (Brier %.5f)", best_key, grid_results[best_key])

    FROZEN_PATH.write_text(json.dumps({
        "num_leaves": best_cfg["num_leaves"], "learning_rate": best_cfg["learning_rate"],
        "num_rounds": best_cfg["num_rounds"], "leagues": leagues,
        "feature_names": feature_names, "criterion": "brier",
        "validation_seasons": list(backtest.VALIDATION),
        "brier_validation": grid_results[best_key], "grid": grid_results,
        "tuned_at": __import__("datetime").date.today().isoformat(),
    }, indent=2))
    log.info("Réglages GBM figés -> %s", FROZEN_PATH)


def frozen():
    if not FROZEN_PATH.exists():
        sys.exit(f"{FROZEN_PATH} absent : lancer d'abord python backtest_ml.py --tune")
    return json.loads(FROZEN_PATH.read_text())


def run(conn):
    cfg = frozen()
    leagues = cfg["leagues"]
    table, feature_names = build_stacked_table(conn, leagues)
    clf_p = {"num_leaves": cfg["num_leaves"], "learning_rate": cfg["learning_rate"]}
    preds = purged_walk_forward(table, feature_names, backtest.TEST,
                                clf_params=clf_p, reg_params=clf_p,
                                num_rounds=cfg["num_rounds"])
    _write_predictions_ml(conn, preds, cfg)
    conn.commit()
    for league in leagues:
        lp = [p for p in preds if p["league"] == league]
        if lp:
            log.info("%s : %d prédictions de test GBM, Brier %.5f", league, len(lp), _mean_brier(lp))
    log.info("%d prédictions écrites dans predictions_ml.", len(preds))
    return preds


PREDICTIONS_ML_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions_ml (
    match_id  INTEGER PRIMARY KEY REFERENCES matches(match_id),
    model_h   REAL NOT NULL,
    model_d   REAL NOT NULL,
    model_a   REAL NOT NULL,
    market_h  REAL,
    market_d  REAL,
    market_a  REAL
);
"""


def _write_predictions_ml(conn, preds, cfg):
    conn.executescript(PREDICTIONS_ML_SCHEMA)
    for p in preds:
        market = p.get("market") or (None, None, None)
        conn.execute(
            "INSERT INTO predictions_ml (match_id, model_h, model_d, model_a, market_h, market_d, market_a) "
            "VALUES (:match_id, :model_h, :model_d, :model_a, :market_h, :market_d, :market_a) "
            "ON CONFLICT(match_id) DO UPDATE SET model_h=excluded.model_h, model_d=excluded.model_d, "
            "model_a=excluded.model_a, market_h=excluded.market_h, market_d=excluded.market_d, "
            "market_a=excluded.market_a",
            {"match_id": p["match_id"], "model_h": p["model"][0], "model_d": p["model"][1],
             "model_a": p["model"][2], "market_h": market[0], "market_d": market[1], "market_a": market[2]},
        )


def _shuffled_scratch_db(real_conn, leagues, seed):
    """Base :memory: avec, pour chaque ligue, les résultats/xG/stats permutés
    ENSEMBLE (même permutation) entre matchs — dates/équipes/cotes/ligue
    inchangées. Même principe que backtest35.shuffle_test, étendu aux
    colonnes de stats (features/form.py) : si le Brier ne se dégrade pas sur
    la saison de contrôle malgré ce brassage, une fuite existe quelque part
    dans le pipeline de features."""
    rng = np.random.default_rng(seed)
    scratch = db.connect(":memory:")
    for league in leagues:
        rows = [dict(r) for r in real_conn.execute(
            "SELECT * FROM matches WHERE league = ? AND fthg IS NOT NULL ORDER BY date, match_id",
            (league,))]
        if not rows:
            continue
        perm = rng.permutation(len(rows))
        shuffled = []
        for r, j in zip(rows, perm):
            src = rows[j]
            new_r = dict(r)
            for c in SHUFFLE_COLS:
                new_r[c] = src[c]
            shuffled.append(new_r)
        for r in shuffled:
            db.upsert_match(scratch, r)
            db.update_xg(scratch, r["date"], r["home"], r["away"], r["xg_home"], r["xg_away"])
    scratch.commit()
    return scratch


def shuffle_test(conn, seed=42):
    cfg = frozen()
    leagues = cfg["leagues"]
    clf_p = {"num_leaves": cfg["num_leaves"], "learning_rate": cfg["learning_rate"]}

    table, feature_names = build_stacked_table(conn, leagues)
    real_preds = purged_walk_forward(table, feature_names, (backtest.LEAK_SEASON,),
                                     clf_params=clf_p, reg_params=clf_p, num_rounds=cfg["num_rounds"])
    real_brier = _mean_brier(real_preds) if real_preds else None

    scratch = _shuffled_scratch_db(conn, leagues, seed)
    s_table, s_feature_names = build_stacked_table(scratch, leagues)
    shuf_preds = purged_walk_forward(s_table, s_feature_names, (backtest.LEAK_SEASON,),
                                     clf_params=clf_p, reg_params=clf_p, num_rounds=cfg["num_rounds"])
    scratch.close()
    shuf_brier = _mean_brier(shuf_preds) if shuf_preds else None

    report = {
        "season": backtest.LEAK_SEASON, "seed": seed,
        "brier_real": real_brier, "brier_shuffled": shuf_brier,
        "n_real": len(real_preds), "n_shuffled": len(shuf_preds),
        "degraded": bool(shuf_brier is not None and real_brier is not None and shuf_brier > real_brier),
    }
    LEAK_PATH.write_text(json.dumps(report, indent=2))
    log.info("GBM : Brier réel %.5f / permuté %.5f -> %s", real_brier or float("nan"),
             shuf_brier or float("nan"),
             "dégradé (attendu)" if report["degraded"] else "PAS DÉGRADÉ : FUITE PROBABLE")
    log.info("Résultat écrit dans %s", LEAK_PATH)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tune", action="store_true", help="grid search hyperparamètres GBM (validation)")
    parser.add_argument("--run", action="store_true", help="backtest de test avec hyperparamètres figés")
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
