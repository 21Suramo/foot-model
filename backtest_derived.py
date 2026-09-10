"""M7 (roadmap) : validation indépendante des marchés dérivés (O/U 2,5, BTTS).

Le skill vend ces deux marchés depuis le début (dérivés de la grille de score
de predict.py : `over_prob`, `btts_prob`) sans jamais les avoir mesurés au
même niveau de rigueur que le 1N2 — aucun tune/validation/test, aucun IC.
Ce module comble ce trou, SANS retoucher aux hyperparamètres déjà figés du
Dixon-Coles (w, ξ, κ dans data/m35_frozen.json) : la grille de score est celle
du modèle M3.5 déjà validé. Seule une recalibration binaire propre à chaque
marché (q = p^t / (p^t + (1-p)^t), symétrique de la température 1X2 de M3.5)
est réglée sur la MÊME validation 2020-21 + 2021-22, puis figée dans
data/derived_markets_frozen.json, puis testée sur 2022-23 → 2025-26 — même
protocole, même interdiction de re-régler après lecture du test.

⚠ Limite honnête : football-data.co.uk ne fournit PAS de cote de marché BTTS
(seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Le critère « vs marché »
est donc INAPPLICABLE pour BTTS tant qu'une source de cotes BTTS n'existe pas
dans le pipeline (cf. roadmap M10, cotes programmatiques). Bat-les-baselines
et calibration restent mesurables et mesurés pour les deux marchés.

Usage : python backtest_derived.py --tune | --run | --shuffle-test [--db ...]
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
import backtest35
import db
import footballdata
import model

log = logging.getLogger("backtest_derived")

MARKETS = ("ou25", "btts")
MARKET_LABELS = {"ou25": "Over/Under 2,5 buts", "btts": "BTTS (les deux équipes marquent)"}
TEMP_BOUNDS = backtest35.TEMP_BOUNDS
FROZEN_PATH = Path("data/derived_markets_frozen.json")
LEAK_PATH = Path("data/leak_check_derived.json")

# Grilles booléennes (MAX_GOALS+1)² précalculées une fois : "over 2,5" et BTTS
# dérivés de la MÊME grille de score que predict.py (over_prob/btts_prob) —
# même approximation de troncature à MAX_GOALS, déjà en production.
_GOALS = np.arange(model.MAX_GOALS + 1)
_TOTAL = np.add.outer(_GOALS, _GOALS)
_OVER25_MASK = _TOTAL > 2.5
_BTTS_MASK = np.zeros((model.MAX_GOALS + 1, model.MAX_GOALS + 1), dtype=bool)
_BTTS_MASK[1:, 1:] = True
GRID_MASKS = {"ou25": _OVER25_MASK, "btts": _BTTS_MASK}

# Cotes marché par match, ou None si la source n'en fournit pas (BTTS).
MARKET_ODDS = {
    "ou25": lambda r: (r["ou25_over"], r["ou25_under"]),
    "btts": lambda r: None,
}


def load_league(conn, league):
    """Comme backtest.load_league, avec les cotes O/U 2,5 en plus : nécessaires
    à la comparaison marché de ce module, absentes de la requête de base
    (backtest.py n'en a jamais eu besoin pour le 1N2)."""
    return [dict(r) for r in conn.execute(
        "SELECT match_id, date, season, home, away, fthg, ftag, "
        "xg_home, xg_away, odds_h, odds_d, odds_a, ou25_over, ou25_under "
        "FROM matches WHERE league = ? AND fthg IS NOT NULL ORDER BY date, match_id",
        (league,))]


def _market_outcome(market, fthg, ftag):
    total = fthg + ftag
    if market == "ou25":
        return 1 if total > 2.5 else 0
    if market == "btts":
        return 1 if fthg > 0 and ftag > 0 else 0
    raise ValueError(f"marché dérivé inconnu : {market}")


def apply_binary_temperature(p, t):
    """Recalibration binaire q = p^t / (p^t + (1-p)^t), symétrique de
    backtest35.apply_temperature mais pour une proba unique (pas un triplet
    qui somme à 1). t=1 identité, t>1 accentue la confiance du modèle."""
    p = min(max(float(p), 1e-9), 1.0 - 1e-9)
    q = p ** t
    return q / (q + (1.0 - p) ** t)


def binary_brier(p, outcome):
    return (p - outcome) ** 2


def demargin_2way(odds_pos, odds_neg):
    """Démargeage proportionnel à 2 issues : p_i = (1/o_i) / Σ(1/o_j).

    Un marché à 2 issues n'a pas le biais favori-longshot à 3 issues que Shin
    modélise (devig_check.py) ; le proportionnel est le choix standard ici et
    n'a pas été comparé à une alternative — pas de prétention de rigueur
    au-delà de ça, juste la méthode la plus simple et la plus lisible."""
    inv1, inv2 = 1.0 / odds_pos, 1.0 / odds_neg
    s = inv1 + inv2
    return inv1 / s, inv2 / s


def walk_forward_derived(rows, target_seasons, cfg):
    """Refit hebdomadaire walk-forward IDENTIQUE à backtest.walk_forward, avec
    les réglages M3.5 déjà figés (w, ξ, κ — jamais retouchés ici), mais
    renvoie la grille de score complète (pas seulement les probas 1X2) pour
    en dériver O/U 2,5 et BTTS, plus une baseline de fréquence walk-forward
    (comptage strictement antérieur, jamais le futur) pour chaque marché.
    """
    weeks = {}
    for r in rows:
        if r["season"] in target_seasons:
            weeks.setdefault(backtest.monday_of(r["date"]), []).append(r)

    out = []
    fitted = None
    train = []
    counts = {m: [0, 0] for m in MARKETS}   # [positifs, total]
    i = 0
    for monday in sorted(weeks):
        cutoff = monday.isoformat()
        while i < len(rows) and rows[i]["date"] < cutoff:
            r = rows[i]
            for m in MARKETS:
                counts[m][1] += 1
                counts[m][0] += _market_outcome(m, r["fthg"], r["ftag"])
            train.append(r)
            i += 1
        fitted = model.fit(train, xi=cfg["xi"], ref_date=monday, warm_start=fitted,
                           xg_weight=cfg["w"], prior_weight=cfg["kappa"])
        freq = {m: (counts[m][0] / counts[m][1] if counts[m][1] else 0.5) for m in MARKETS}
        for r in weeks[monday]:
            out.append({
                "match_id": r["match_id"], "season": r["season"], "date": r["date"],
                "row": r, "grid": fitted.score_grid(r["home"], r["away"]), "freq": dict(freq),
            })
    return out


def tune(conn):
    """Recalibration binaire par marché, validation uniquement, figée dans
    derived_markets_frozen.json. Ne touche jamais à data/m35_frozen.json."""
    if FROZEN_PATH.exists():
        frozen = json.loads(FROZEN_PATH.read_text())
        log.warning("Réglages des marchés dérivés déjà figés (%s) — le protocole "
                    "interdit de les re-régler. Supprimer le fichier manuellement "
                    "pour assumer un nouveau tuning.", frozen)
        return
    cfg = backtest35.frozen()
    preds = []
    for league in footballdata.LEAGUES:
        preds += walk_forward_derived(load_league(conn, league), backtest.VALIDATION, cfg)

    result = {
        "based_on_m35": {"w": cfg["w"], "xi": cfg["xi"], "kappa": cfg["kappa"]},
        "validation_seasons": list(backtest.VALIDATION),
        "markets": {},
    }
    for market in MARKETS:
        mask = GRID_MASKS[market]
        raw = [(float(p["grid"][mask].sum()),
               _market_outcome(market, p["row"]["fthg"], p["row"]["ftag"]))
               for p in preds]
        res = minimize_scalar(
            lambda t: np.mean([binary_brier(apply_binary_temperature(rp, t), o) for rp, o in raw]),
            bounds=TEMP_BOUNDS, method="bounded")
        b_raw = float(np.mean([binary_brier(rp, o) for rp, o in raw]))
        result["markets"][market] = {
            "t": float(res.x), "brier_validation_raw": b_raw, "brier_validation": float(res.fun),
        }
        log.info("%s : température figée t = %.3f (Brier validation %.5f -> %.5f)",
                 market, res.x, b_raw, res.fun)
    result["tuned_at"] = datetime.date.today().isoformat()
    FROZEN_PATH.write_text(json.dumps(result, indent=2))
    log.info("Réglages figés -> %s", FROZEN_PATH)


def frozen():
    if not FROZEN_PATH.exists():
        sys.exit(f"{FROZEN_PATH} absent : lancer d'abord python backtest_derived.py --tune")
    return json.loads(FROZEN_PATH.read_text())


def run(conn):
    """Backtest de test des marchés dérivés avec réglages figés -> predictions_derived."""
    cfg = backtest35.frozen()
    dcfg = frozen()
    total = 0
    for league in footballdata.LEAGUES:
        rows = load_league(conn, league)
        preds = walk_forward_derived(rows, backtest.TEST, cfg)
        for market in MARKETS:
            mask = GRID_MASKS[market]
            t = dcfg["markets"][market]["t"]
            briers = []
            for p in preds:
                r = p["row"]
                raw_p = float(p["grid"][mask].sum())
                model_p = apply_binary_temperature(raw_p, t)
                outcome = _market_outcome(market, r["fthg"], r["ftag"])
                pair = MARKET_ODDS[market](r)
                market_p = None
                if pair and pair[0] and pair[1]:
                    market_p = demargin_2way(*pair)[0]
                db.upsert_prediction_derived(conn, {
                    "match_id": p["match_id"], "market": market,
                    "model_p": model_p, "raw_p": raw_p, "market_p": market_p,
                    "freq_p": p["freq"][market],
                })
                briers.append(binary_brier(model_p, outcome))
            conn.commit()
            log.info("%s %s : %d prédictions de test, Brier %.5f", league, market,
                     len(briers), float(np.mean(briers)))
            total += len(briers)
    log.info("%d prédictions écrites dans predictions_derived.", total)


def shuffle_test(conn, seed=42):
    """Anti-fuite : mêmes dates/équipes/cotes, scores ET xG permutés ensemble."""
    cfg = backtest35.frozen()
    dcfg = frozen()
    rng = np.random.default_rng(seed)
    report = {"season": backtest.LEAK_SEASON, "seed": seed, "leagues": {}}

    def _briers(rows):
        preds = walk_forward_derived(rows, (backtest.LEAK_SEASON,), cfg)
        out = {}
        for market in MARKETS:
            mask = GRID_MASKS[market]
            t = dcfg["markets"][market]["t"]
            vals = [binary_brier(apply_binary_temperature(float(p["grid"][mask].sum()), t),
                                 _market_outcome(market, p["row"]["fthg"], p["row"]["ftag"]))
                   for p in preds]
            out[market] = float(np.mean(vals))
        return out

    for league in footballdata.LEAGUES:
        rows = load_league(conn, league)
        real = _briers(rows)
        perm = rng.permutation(len(rows))
        shuffled = [dict(r, fthg=rows[j]["fthg"], ftag=rows[j]["ftag"],
                        xg_home=rows[j]["xg_home"], xg_away=rows[j]["xg_away"])
                   for r, j in zip(rows, perm)]
        shuf = _briers(shuffled)
        report["leagues"][league] = {
            m: {"brier_real": real[m], "brier_shuffled": shuf[m], "degraded": bool(shuf[m] > real[m])}
            for m in MARKETS
        }
        for m in MARKETS:
            log.info("%s %s : Brier réel %.5f / permuté %.5f -> %s", league, m, real[m], shuf[m],
                     "dégradé (attendu)" if shuf[m] > real[m] else "PAS DÉGRADÉ : FUITE PROBABLE")
    report["all_degraded"] = all(v[m]["degraded"] for v in report["leagues"].values() for m in MARKETS)
    LEAK_PATH.write_text(json.dumps(report, indent=2))
    log.info("Résultat écrit dans %s", LEAK_PATH)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tune", action="store_true", help="recalibration binaire par marché (validation)")
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
