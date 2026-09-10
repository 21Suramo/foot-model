"""Roadmap "profit durable" A1 : validation indépendante des marchés dérivés
de la grille de score (O/U à 5 lignes, BTTS, totaux par équipe, handicap
asiatique). Racine historique : M7 (roadmap), qui a d'abord couvert O/U 2,5
et BTTS ; ce module a été étendu depuis à la lettre du chantier A1.

Le skill vend ces marchés depuis le début (dérivés de la grille de score de
predict.py) sans jamais les avoir mesurés au même niveau de rigueur que le
1N2 — aucun tune/validation/test, aucun IC. Ce module comble ce trou, SANS
retoucher aux hyperparamètres déjà figés du Dixon-Coles (w, ξ, κ dans
data/m35_frozen.json) : la grille de score est celle du modèle M3.5 déjà
validé. Seule une recalibration binaire propre à chaque marché
(q = p^t / (p^t + (1-p)^t), symétrique de la température 1X2 de M3.5) est
réglée sur la MÊME validation 2020-21 + 2021-22, puis figée dans
data/derived_markets_frozen.json, puis testée sur 2022-23 → 2025-26 — même
protocole, même interdiction de re-régler après lecture du test. `tune()`
applique cette interdiction MARCHÉ PAR MARCHÉ (fusion, jamais d'écrasement) :
ajouter un marché ne retouche jamais la température d'un marché déjà figé et
déjà testé.

Grille 7×7 vs 12×12 — deux bases documentées séparément dans derived_markets.py :
ou25/btts restent sur la grille 7×7 exacte de M3.5 (déjà backtestée et publiée
dans reports/derived_markets_backtest.md — l'élargir changerait légèrement
leurs probas et ferait dériver un résultat déjà lu). Les marchés ajoutés
depuis (ou05/ou15/ou35/ou45, totaux par équipe, handicap asiatique) n'avaient
jamais été testés avant ce chantier : ils utilisent dès leur premier tune la
grille 12×12 (EXTENDED_MAX_GOALS), plus précise sur les queues de distribution
— élargir la base AVANT de lire un test n'est pas le re-réglage que le
protocole interdit.

⚠ Limites honnêtes :
- BTTS et les totaux par équipe : football-data.co.uk ne fournit AUCUNE cote
  pour ces marchés (seuls 1X2, O/U 2,5 et handicap asiatique y figurent). Le
  critère « vs marché » y est INAPPLICABLE (cf. roadmap M10, cotes
  programmatiques). Bat-les-baselines et calibration restent mesurés.
- Handicap asiatique : la ligne est propre à CHAQUE match (ah_line), pas un
  marché à mask fixe. Les push (remboursement, possibles seulement sur ligne
  entière — cf. derived_markets.asian_handicap_outcome) sont EXCLUS du Brier
  (ni gagnant ni perdant, pas une observation) — model_p/market_p sont alors
  des probabilités CONDITIONNELLES « domicile couvre sachant pas de push »,
  seule lecture possible avec seulement 2 cotes de marché (pas de 3e cote
  « push » chez football-data.co.uk). freq_p vaut 0,5 constant pour ce marché :
  la ligne est choisie par le marché justement pour équilibrer les deux issues,
  donc une fréquence historique inconditionnelle n'est pas un baseline
  pertinent ici (le baseline uniforme déjà calculé par report_derived.py joue
  ce rôle).

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
import derived_markets
import footballdata
import model

log = logging.getLogger("backtest_derived")

AH_MARKET = "ah"
MARKETS = ("ou25", "btts", "ou05", "ou15", "ou35", "ou45", "home_ov15", "away_ov15")
ALL_MARKETS = MARKETS + (AH_MARKET,)
MARKET_LABELS = {
    "ou25": "Over/Under 2,5 buts",
    "btts": "BTTS (les deux équipes marquent)",
    "ou05": "Over/Under 0,5 but",
    "ou15": "Over/Under 1,5 but",
    "ou35": "Over/Under 3,5 buts",
    "ou45": "Over/Under 4,5 buts",
    "home_ov15": "Domicile marque plus de 1,5 but",
    "away_ov15": "Extérieur marque plus de 1,5 but",
    AH_MARKET: "Handicap asiatique (ligne du marché, domicile couvre)",
}
TEMP_BOUNDS = backtest35.TEMP_BOUNDS
FROZEN_PATH = Path("data/derived_markets_frozen.json")
LEAK_PATH = Path("data/leak_check_derived.json")
EXTENDED_MAX_GOALS = derived_markets.EXTENDED_MAX_GOALS

# Grilles booléennes (MAX_GOALS+1)² précalculées une fois : "over 2,5" et BTTS
# dérivés de la MÊME grille de score que predict.py (over_prob/btts_prob) —
# même approximation de troncature à MAX_GOALS, déjà en production. INCHANGÉ
# depuis M7 (roadmap) : ne pas toucher, déjà backtesté et publié.
_GOALS = np.arange(model.MAX_GOALS + 1)
_TOTAL = np.add.outer(_GOALS, _GOALS)
_OVER25_MASK = _TOTAL > 2.5
_BTTS_MASK = np.zeros((model.MAX_GOALS + 1, model.MAX_GOALS + 1), dtype=bool)
_BTTS_MASK[1:, 1:] = True

# Marchés ajoutés par A1 : grille 12×12 (jamais testée avant ce chantier, cf.
# docstring), masks construits une fois via derived_markets.py.
GRID_MASKS = {
    "ou25": _OVER25_MASK,
    "btts": _BTTS_MASK,
    "ou05": derived_markets.over_under_mask(EXTENDED_MAX_GOALS, 0.5),
    "ou15": derived_markets.over_under_mask(EXTENDED_MAX_GOALS, 1.5),
    "ou35": derived_markets.over_under_mask(EXTENDED_MAX_GOALS, 3.5),
    "ou45": derived_markets.over_under_mask(EXTENDED_MAX_GOALS, 4.5),
    "home_ov15": derived_markets.team_total_mask(EXTENDED_MAX_GOALS, "home", 1.5),
    "away_ov15": derived_markets.team_total_mask(EXTENDED_MAX_GOALS, "away", 1.5),
}

# Quelle grille par-match (walk_forward_derived) chaque marché utilise —
# "grid" = 7×7 legacy (ou25/btts, inchangé), "grid_ext" = 12×12 (le reste).
MARKET_GRID_FIELD = {
    "ou25": "grid", "btts": "grid",
    "ou05": "grid_ext", "ou15": "grid_ext", "ou35": "grid_ext", "ou45": "grid_ext",
    "home_ov15": "grid_ext", "away_ov15": "grid_ext",
}

# Cotes marché par match, ou None si la source n'en fournit pas (BTTS, totaux
# par équipe — football-data.co.uk ne les cote pas).
MARKET_ODDS = {
    "ou25": lambda r: (r["ou25_over"], r["ou25_under"]),
    "btts": lambda r: None,
    "ou05": lambda r: None,
    "ou15": lambda r: None,
    "ou35": lambda r: None,
    "ou45": lambda r: None,
    "home_ov15": lambda r: None,
    "away_ov15": lambda r: None,
}


def load_league(conn, league):
    """Comme backtest.load_league, avec les cotes O/U 2,5 et handicap
    asiatique en plus : nécessaires à la comparaison marché de ce module,
    absentes de la requête de base (backtest.py n'en a jamais eu besoin pour
    le 1N2)."""
    return [dict(r) for r in conn.execute(
        "SELECT match_id, date, season, home, away, fthg, ftag, "
        "xg_home, xg_away, odds_h, odds_d, odds_a, ou25_over, ou25_under, "
        "ah_line, ah_home, ah_away "
        "FROM matches WHERE league = ? AND fthg IS NOT NULL ORDER BY date, match_id",
        (league,))]


def _market_outcome(market, fthg, ftag):
    total = fthg + ftag
    if market == "ou25":
        return 1 if total > 2.5 else 0
    if market == "btts":
        return 1 if fthg > 0 and ftag > 0 else 0
    if market == "ou05":
        return 1 if total > 0.5 else 0
    if market == "ou15":
        return 1 if total > 1.5 else 0
    if market == "ou35":
        return 1 if total > 3.5 else 0
    if market == "ou45":
        return 1 if total > 4.5 else 0
    if market == "home_ov15":
        return 1 if fthg > 1.5 else 0
    if market == "away_ov15":
        return 1 if ftag > 1.5 else 0
    raise ValueError(f"marché dérivé inconnu : {market}")


def ah_raw_prob(grid_ext, line):
    """P(domicile couvre | pas de push) — conditionnelle, cf. docstring du
    module : les 2 cotes de marché fournies par football-data.co.uk n'isolent
    pas non plus la probabilité de push, donc c'est la seule quantité
    comparable en face."""
    p_home, p_away, _ = derived_markets.asian_handicap_probs(grid_ext, line)
    denom = p_home + p_away
    return p_home / denom if denom > 0 else 0.5


def ah_home_covers(row):
    """1 si domicile couvre, 0 si extérieur couvre, None si push ou ligne
    absente — à exclure du Brier (un push rembourse la mise, ce n'est pas une
    observation gagnant/perdant)."""
    line = row.get("ah_line")
    if line is None:
        return None
    outcome = derived_markets.asian_handicap_outcome(row["fthg"], row["ftag"], line)
    if outcome == "push":
        return None
    return 1 if outcome == "home" else 0


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
    les réglages M3.5 déjà figés (w, ξ, κ — jamais retouchés ici). Renvoie DEUX
    grilles par match : "grid" (7×7, inchangée depuis M7 roadmap — ou25/btts)
    et "grid_ext" (12×12, cf. derived_markets.EXTENDED_MAX_GOALS — tous les
    marchés ajoutés depuis), plus une baseline de fréquence walk-forward
    (comptage strictement antérieur, jamais le futur) pour chaque marché de
    MARKETS, et "score_freq" : une grille 7×7 de fréquence de scores exacts
    walk-forward (lissage de Laplace), baseline du diagnostic top-k de
    report_derived.py — pas un marché coté, jamais figé/recalibré.
    """
    weeks = {}
    for r in rows:
        if r["season"] in target_seasons:
            weeks.setdefault(backtest.monday_of(r["date"]), []).append(r)

    out = []
    fitted = None
    train = []
    counts = {m: [0, 0] for m in MARKETS}   # [positifs, total]
    score_counts = np.ones((model.MAX_GOALS + 1, model.MAX_GOALS + 1))  # lissage de Laplace
    i = 0
    for monday in sorted(weeks):
        cutoff = monday.isoformat()
        while i < len(rows) and rows[i]["date"] < cutoff:
            r = rows[i]
            for m in MARKETS:
                counts[m][1] += 1
                counts[m][0] += _market_outcome(m, r["fthg"], r["ftag"])
            hh = min(r["fthg"], model.MAX_GOALS)
            aa = min(r["ftag"], model.MAX_GOALS)
            score_counts[hh, aa] += 1
            train.append(r)
            i += 1
        fitted = model.fit(train, xi=cfg["xi"], ref_date=monday, warm_start=fitted,
                           xg_weight=cfg["w"], prior_weight=cfg["kappa"])
        freq = {m: (counts[m][0] / counts[m][1] if counts[m][1] else 0.5) for m in MARKETS}
        score_freq = score_counts / score_counts.sum()
        for r in weeks[monday]:
            out.append({
                "match_id": r["match_id"], "season": r["season"], "date": r["date"],
                "row": r,
                "grid": fitted.score_grid(r["home"], r["away"]),
                "grid_ext": fitted.score_grid(r["home"], r["away"], max_goals=EXTENDED_MAX_GOALS),
                "freq": dict(freq), "score_freq": score_freq,
            })
    return out


def _raw_and_outcome_pairs(market, preds):
    """(raw_p, outcome) par prédiction pour un marché donné, en excluant les
    push/lignes absentes pour le handicap asiatique (pas une observation
    gagnant/perdant)."""
    if market == AH_MARKET:
        pairs = []
        for p in preds:
            row = p["row"]
            outcome = ah_home_covers(row)
            if outcome is None:
                continue
            pairs.append((ah_raw_prob(p["grid_ext"], row["ah_line"]), outcome))
        return pairs
    mask = GRID_MASKS[market]
    field = MARKET_GRID_FIELD[market]
    return [(float(p[field][mask].sum()), _market_outcome(market, p["row"]["fthg"], p["row"]["ftag"]))
            for p in preds]


def tune(conn):
    """Recalibration binaire par marché, validation uniquement, figée dans
    derived_markets_frozen.json. Ne touche jamais à data/m35_frozen.json.

    Fusionne avec un fichier déjà figé plutôt que de tout refuser : un marché
    déjà présent (donc déjà testé et publié) n'est JAMAIS retouché — seuls les
    marchés manquants sont réglés et ajoutés. C'est la même interdiction
    « jamais re-régler après lecture du test » que la version précédente,
    appliquée marché par marché pour permettre l'extension du chantier A1
    sans remettre en cause ou/btts déjà publiés."""
    cfg = backtest35.frozen()
    if FROZEN_PATH.exists():
        result = json.loads(FROZEN_PATH.read_text())
    else:
        result = {"based_on_m35": {"w": cfg["w"], "xi": cfg["xi"], "kappa": cfg["kappa"]},
                  "validation_seasons": list(backtest.VALIDATION), "markets": {}}
    result.setdefault("markets", {})
    already = set(result["markets"])
    to_tune = [m for m in ALL_MARKETS if m not in already]
    if not to_tune:
        log.warning("Tous les marchés (%s) sont déjà figés dans %s — rien à faire. "
                    "Supprimer une entrée manuellement pour assumer un nouveau tuning.",
                    ", ".join(ALL_MARKETS), FROZEN_PATH)
        return
    if already:
        log.info("Marchés déjà figés, non retouchés : %s", ", ".join(sorted(already)))

    preds = []
    for league in footballdata.LEAGUES:
        preds += walk_forward_derived(load_league(conn, league), backtest.VALIDATION, cfg)

    for market in to_tune:
        raw = _raw_and_outcome_pairs(market, preds)
        res = minimize_scalar(
            lambda t: np.mean([binary_brier(apply_binary_temperature(rp, t), o) for rp, o in raw]),
            bounds=TEMP_BOUNDS, method="bounded")
        b_raw = float(np.mean([binary_brier(rp, o) for rp, o in raw]))
        result["markets"][market] = {
            "t": float(res.x), "brier_validation_raw": b_raw, "brier_validation": float(res.fun),
            "n_validation": len(raw),
        }
        log.info("%s : température figée t = %.3f (Brier validation %.5f -> %.5f, n=%d)",
                 market, res.x, b_raw, res.fun, len(raw))
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
    missing = [m for m in ALL_MARKETS if m not in dcfg.get("markets", {})]
    if missing:
        sys.exit(f"Marché(s) non figé(s) dans {FROZEN_PATH} : {', '.join(missing)} — "
                 f"lancer d'abord python backtest_derived.py --tune")
    total = 0
    for league in footballdata.LEAGUES:
        rows = load_league(conn, league)
        preds = walk_forward_derived(rows, backtest.TEST, cfg)
        for market in ALL_MARKETS:
            t = dcfg["markets"][market]["t"]
            briers = []
            n_pushes = 0
            for p in preds:
                row = p["row"]
                if market == AH_MARKET:
                    if row.get("ah_line") is None:
                        continue
                    outcome = ah_home_covers(row)
                    if outcome is None:
                        n_pushes += 1
                        continue
                    raw_p = ah_raw_prob(p["grid_ext"], row["ah_line"])
                    pair = (row["ah_home"], row["ah_away"]) \
                        if row.get("ah_home") and row.get("ah_away") else None
                    freq_p = 0.5
                else:
                    mask = GRID_MASKS[market]
                    field = MARKET_GRID_FIELD[market]
                    raw_p = float(p[field][mask].sum())
                    outcome = _market_outcome(market, row["fthg"], row["ftag"])
                    pair = MARKET_ODDS[market](row)
                    freq_p = p["freq"][market]
                market_p = demargin_2way(*pair)[0] if pair and pair[0] and pair[1] else None
                model_p = apply_binary_temperature(raw_p, t)
                db.upsert_prediction_derived(conn, {
                    "match_id": p["match_id"], "market": market,
                    "model_p": model_p, "raw_p": raw_p, "market_p": market_p,
                    "freq_p": freq_p,
                })
                briers.append(binary_brier(model_p, outcome))
            conn.commit()
            extra = f" ({n_pushes} push exclus)" if market == AH_MARKET else ""
            log.info("%s %s : %d prédictions de test%s, Brier %.5f", league, market,
                     len(briers), extra, float(np.mean(briers)) if briers else float("nan"))
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
        for market in ALL_MARKETS:
            t = dcfg["markets"][market]["t"]
            raw = _raw_and_outcome_pairs(market, preds)
            vals = [binary_brier(apply_binary_temperature(rp, t), o) for rp, o in raw]
            out[market] = float(np.mean(vals)) if vals else None
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
            for m in ALL_MARKETS if real[m] is not None and shuf[m] is not None
        }
        for m in ALL_MARKETS:
            if real[m] is None or shuf[m] is None:
                continue
            log.info("%s %s : Brier réel %.5f / permuté %.5f -> %s", league, m, real[m], shuf[m],
                     "dégradé (attendu)" if shuf[m] > real[m] else "PAS DÉGRADÉ : FUITE PROBABLE")
    report["all_degraded"] = all(v[m]["degraded"] for v in report["leagues"].values() for m in v)
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
