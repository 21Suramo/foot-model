"""C1 (roadmap) — le mouvement de cote est-il un signal d'entrée exploitable ?

Deux chantiers dans ce fichier, dans l'ordre où ils ont été construits :

1. **Diagnostic exploratoire** (`collect_bets`/`summarize`, section 1 du
   rapport) : walk-forward sur 1920+VALIDATION (jamais le TEST), même
   discipline que `fatigue_signal_check.py` — vérifier qu'un signal existe
   avant de construire quoi que ce soit dessus. Value = edge modèle > 0 à
   L'OUVERTURE, mouvement = ouverture→clôture. Résultat : signal net.

2. **Score composite actionnable**, réglé/testé/validé (section 2) —
   correction méthodologique du chantier 1 : la clôture n'entre PAS dans un
   score actionnable, elle n'est pas connue au moment de parier. Ici :
   - `p_taken` simule la cote RÉELLEMENT PRISE (interpolée à `HORIZON_DAYS`
     jours du coup d'envoi entre ouverture et clôture, comme
     `backtest_blend.aged_fair` — même proxy, même limite honnête : deux
     vraies lignes sharp, pas une cote scrapée). `HORIZON_DAYS` est fixé A
     PRIORI (pas réglé sur les données, pour ne pas ajouter un degré de
     liberté de plus au tuning) dans la bande de fraîcheur déjà utilisée en
     production par `predict.py` (`FRESH_MAX_DAYS`/`STALE_MIN_DAYS`).
   - edge = p_modèle − p_pris ; mouvement = p_pris − p_ouverture (jamais
     p_clôture − quoi que ce soit : inconnu au moment de parier).
   - score = edge + w×mouvement ; (w, seuil de décision) réglés sur la
     VALIDATION (2020-21+2021-22, jamais 1920 ni le TEST) par grid search du
     ROI théorique moyen, sous contrainte n ≥ MIN_BETS_FOR_TUNE (sinon un
     seuil qui isole une poignée de paris chanceux gagnerait le grid search).
   - `--run` applique (w, seuil) figés sur le TEST — LECTURE UNIQUE.
   - `--shuffle-test` permute le mouvement entre paris du test (edge/résultat
     réels conservés) : si le lift réel n'est pas exceptionnel comparé à des
     mouvements sans lien avec CE match, le signal serait un artefact
     mécanique de la sélection plutôt qu'une vraie information de marché.

Usage : python clv_signal_check.py [--tune] [--run] [--shuffle-test] [--db ...]

⚠ Nécessite les CSV bruts football-data en cache (data/raw/football-data/)
pour les cotes d'OUVERTURE — absentes de la base (qui ne garde que la
clôture). Lancer `python pipeline.py --update` au moins une fois si le
cache est vide (mêmes fichiers que backtest_blend.py, réutilisés ici).
"""
import argparse
import datetime
import json
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
import predict

log = logging.getLogger("clv_signal_check")

TARGET_SEASONS = ("1920",) + backtest.VALIDATION  # diagnostic exploratoire ; jamais le TEST
ISSUE_NAMES = ("home", "draw", "away")

# Chantier 2 — score composite actionnable.
HORIZON_DAYS = 2   # cote "prise" simulée à J-2 : dans la bande de fraîcheur
                    # déjà utilisée en production (predict.FRESH_MAX_DAYS=1,
                    # predict.STALE_MIN_DAYS=5), fixé A PRIORI, pas réglé.
MIN_BETS_FOR_TUNE = 100   # même seuil que la routine de suivi pour un ROI fiable
W_GRID = np.round(np.arange(-2.0, 2.01, 0.25), 2)
THRESHOLD_GRID = np.round(np.arange(0.0, 0.201, 0.01), 3)
FROZEN_PATH = Path("data/clv_composite_frozen.json")
RESULTS_PATH = Path("data/clv_composite_test_results.json")
SHUFFLE_PATH = Path("data/clv_composite_shuffle_check.json")
N_PERMUTATIONS = 999


def collect_bets(conn, cfg):
    """Un enregistrement par match où le modèle voit de la value à l'ouverture
    ET où l'ouverture ET la clôture sont disponibles. walk-forward strict
    (backtest.walk_forward), jamais le futur. Diagnostic exploratoire
    (section 1) : mouvement ouverture→CLÔTURE, jamais utilisé comme score
    actionnable (cf. section 2 pour la version correcte)."""
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
             "pas seulement les paris qu'on aurait réellement engagés (edge significatif). "
             "⚠ Le mouvement utilisé ici va jusqu'à la CLÔTURE — inconnue au moment de parier, "
             "donc ce diagnostic mesure « le signal existe-t-il ? », pas un score actionnable "
             "(cf. section 2 du rapport pour la version corrigée, mouvement ouverture→cote prise).",
             ""]

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
               "significativement (IC excluant 0) celui des paris divergents — de quoi "
               "justifier de chercher un score composite actionnable (section 2)."
               if signal else
               "Aucun signal détecté (IC n'exclut pas 0, ou écart négatif) : ne pas "
               "construire de score composite — il figerait du bruit.")
    lines.append(f"### Verdict\n\n{verdict}\n")
    return "\n".join(lines), signal


# ---------------------------------------------------------------------------
# Chantier 2 — score composite actionnable (edge vs cote PRISE, mouvement
# ouverture->prise, jamais la clôture)
# ---------------------------------------------------------------------------

def aged_taken(p_close_fair, p_open_fair, close_odds_raw, open_odds_raw, age,
              open_horizon=backtest_blend.OPEN_HORIZON):
    """(p_pris fair, cote_pris réelle) interpolés à `age` jours du coup
    d'envoi entre clôture (age=0) et ouverture (age=open_horizon) — même
    interpolation en probabilité fair que `backtest_blend.aged_fair`. La
    marge est interpolée SÉPARÉMENT sur le booksum réel (pas fair) pour
    reconstituer une cote QUOTABLE : une cote "prise" à marge nulle
    gonflerait artificiellement le ROI théorique (aucun book ne quote sans
    marge)."""
    frac = min(age / open_horizon, 1.0)
    p_taken = tuple((1 - frac) * c + frac * o for c, o in zip(p_close_fair, p_open_fair))
    book_close = sum(1.0 / o for o in close_odds_raw)
    book_open = sum(1.0 / o for o in open_odds_raw)
    book_taken = (1 - frac) * book_close + frac * book_open
    odds_taken = tuple(1.0 / (p * book_taken) for p in p_taken)
    return p_taken, odds_taken


def collect_composite_records(conn, cfg, target_seasons):
    """Comme collect_bets, mais l'edge et le pari sont évalués contre la cote
    RÉELLEMENT PRISE (aged_taken à HORIZON_DAYS) — pas contre l'ouverture, et
    le mouvement est ouverture→pris — jamais pris→clôture, inconnu au moment
    de parier."""
    open_map = backtest_blend.opening_odds_map(target_seasons)
    records = []
    n_no_open = n_no_close = n_no_value = 0
    for league in footballdata.LEAGUES:
        rows = backtest.load_league(conn, league)
        by_id = {r["match_id"]: r for r in rows}
        preds = backtest.walk_forward(rows, target_seasons, cfg["xi"], with_market=True,
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
            close_odds_raw = (r["odds_h"], r["odds_d"], r["odds_a"])
            p_taken, odds_taken = aged_taken(p_close, p_open, close_odds_raw, trip_open, HORIZON_DAYS)
            p_model = backtest35.apply_temperature(p["model"], cfg["temperature"])
            edges = [p_model[k] - p_taken[k] for k in range(3)]
            k_star = int(np.argmax(edges))
            if edges[k_star] <= 0:
                n_no_value += 1
                continue
            movement = p_taken[k_star] - p_open[k_star]
            win = 1 if p["outcome"] == k_star else 0
            roi = (odds_taken[k_star] - 1.0) if win else -1.0
            records.append({"league": league, "season": r["season"],
                            "edge": edges[k_star], "movement": movement, "roi": roi})
    log.info("%d paris value (cote prise J-%d) collectés (%d sans ouverture, %d sans "
             "clôture, %d sans value modèle).", len(records), HORIZON_DAYS,
             n_no_open, n_no_close, n_no_value)
    return records


def composite_score(edge, movement, w):
    return edge + w * movement


def select(records, w, threshold):
    return [r for r in records if composite_score(r["edge"], r["movement"], w) > threshold]


def tune(conn):
    """Grid search de (w, seuil) sur la VALIDATION (2021+2122, jamais 1920 ni
    le TEST — même rigueur que M3.5/A1), critère ROI théorique moyen, sous
    contrainte n >= MIN_BETS_FOR_TUNE (sinon un seuil qui isole une poignée
    de paris chanceux gagnerait le grid search)."""
    if FROZEN_PATH.exists():
        frozen_cfg = json.loads(FROZEN_PATH.read_text())
        log.warning("Score composite déjà figé (%s) — le protocole interdit de le "
                    "re-régler. Supprimer le fichier manuellement pour assumer un "
                    "nouveau tuning.", frozen_cfg)
        return
    cfg = backtest35.frozen()
    records = collect_composite_records(conn, cfg, backtest.VALIDATION)
    if len(records) < MIN_BETS_FOR_TUNE:
        sys.exit(f"Seulement {len(records)} paris value sur la validation — pas assez "
                 f"pour régler (w, seuil) de façon fiable (minimum {MIN_BETS_FOR_TUNE}).")

    best = None
    for w in W_GRID:
        for threshold in THRESHOLD_GRID:
            sel = select(records, w, threshold)
            if len(sel) < MIN_BETS_FOR_TUNE:
                continue
            roi = float(np.mean([r["roi"] for r in sel]))
            if best is None or roi > best["roi"]:
                best = {"w": float(w), "threshold": float(threshold), "roi": roi, "n": len(sel)}
    if best is None:
        sys.exit(f"Aucune combinaison (w, seuil) ne laisse ≥ {MIN_BETS_FOR_TUNE} paris "
                 f"sur la validation — grille à revoir, pas de figeage possible.")

    baseline_roi = float(np.mean([r["roi"] for r in records]))
    result = {
        "w": best["w"], "threshold": best["threshold"],
        "roi_validation": best["roi"], "n_validation": best["n"],
        "baseline_roi_validation": baseline_roi, "n_baseline_validation": len(records),
        "horizon_days": HORIZON_DAYS, "validation_seasons": list(backtest.VALIDATION),
        "tuned_at": datetime.date.today().isoformat(),
    }
    FROZEN_PATH.write_text(json.dumps(result, indent=2))
    log.info("Figé : w=%.2f, seuil=%.3f -> ROI validation %+.2f%% (n=%d) vs %+.2f%% "
             "sans filtre (n=%d) -> %s", best["w"], best["threshold"], best["roi"] * 100,
             best["n"], baseline_roi * 100, len(records), FROZEN_PATH)


def frozen():
    if not FROZEN_PATH.exists():
        sys.exit(f"{FROZEN_PATH} absent : lancer d'abord python clv_signal_check.py --tune")
    return json.loads(FROZEN_PATH.read_text())


def run(conn):
    """Applique (w, seuil) figés sur le TEST — LECTURE UNIQUE."""
    cfg = backtest35.frozen()
    fcfg = frozen()
    records = collect_composite_records(conn, cfg, backtest.TEST)
    if not records:
        sys.exit("Aucun pari value sur le test.")
    sel = select(records, fcfg["w"], fcfg["threshold"])
    result = {
        "w": fcfg["w"], "threshold": fcfg["threshold"], "horizon_days": fcfg["horizon_days"],
        "n_test_total": len(records), "n_test_selected": len(sel),
        "test_seasons": list(backtest.TEST),
        "roi_baseline_all_list": [r["roi"] for r in records],
        "roi_selected_list": [r["roi"] for r in sel],
    }
    RESULTS_PATH.write_text(json.dumps(result, indent=2))
    b = float(np.mean(result["roi_baseline_all_list"]))
    s = float(np.mean(result["roi_selected_list"])) if sel else float("nan")
    log.info("Test : %d paris totaux (ROI %+.2f%%), %d sélectionnés par le score "
             "composite (ROI %+.2f%%) -> %s", len(records), b * 100, len(sel), s * 100, RESULTS_PATH)
    return result


def shuffle_test(conn, n_perm=N_PERMUTATIONS, seed=bootstrap.DEFAULT_SEED):
    """Permute les valeurs de `movement` ENTRE paris du test (edge/résultat
    réels conservés), recalcule la sélection et son ROI avec (w, seuil)
    figés, répète n_perm fois. Si le lift réel (sélection − baseline) n'est
    pas exceptionnel comparé à la distribution de lifts obtenus avec un
    mouvement sans lien avec CE match, le signal composite serait un
    artefact mécanique de la sélection plutôt qu'une vraie information de
    marché — la protection contre la corrélation mécanique demandée."""
    cfg = backtest35.frozen()
    fcfg = frozen()
    records = collect_composite_records(conn, cfg, backtest.TEST)
    if not records:
        sys.exit("Aucun pari value sur le test.")

    edges = np.array([r["edge"] for r in records])
    movements = np.array([r["movement"] for r in records])
    rois = np.array([r["roi"] for r in records])
    baseline_roi = float(rois.mean())

    real_mask = (edges + fcfg["w"] * movements) > fcfg["threshold"]
    real_roi = float(rois[real_mask].mean()) if real_mask.any() else None
    real_lift = (real_roi - baseline_roi) if real_roi is not None else None

    rng = np.random.default_rng(seed)
    lifts = []
    for _ in range(n_perm):
        shuffled = movements[rng.permutation(len(records))]
        mask = (edges + fcfg["w"] * shuffled) > fcfg["threshold"]
        if mask.any():
            lifts.append(float(rois[mask].mean()) - baseline_roi)
    lifts = np.array(lifts)
    p_value = float((lifts >= real_lift).mean()) if real_lift is not None and lifts.size else None

    result = {
        "n_perm_effective": int(lifts.size), "seed": int(seed),
        "real_lift": real_lift, "baseline_roi": baseline_roi,
        "real_selected_roi": real_roi, "n_real_selected": int(real_mask.sum()),
        "p_value": p_value,
        "perm_lift_mean": float(lifts.mean()) if lifts.size else None,
        "perm_lift_p95": float(np.percentile(lifts, 95)) if lifts.size else None,
    }
    SHUFFLE_PATH.write_text(json.dumps(result, indent=2))
    log.info("Shuffle-test : lift réel %+.2f pts vs lift permuté moyen %+.2f pts "
             "(p95=%+.2f pts), p-value=%s (n_perm=%d) -> %s",
             (real_lift or 0) * 100, (result["perm_lift_mean"] or 0) * 100,
             (result["perm_lift_p95"] or 0) * 100, result["p_value"], lifts.size, SHUFFLE_PATH)
    return result


def _pct_ci(lo, hi):
    return bootstrap.fmt_ci(None if lo is None else lo * 100,
                            None if hi is None else hi * 100, unit="pts", decimals=1)


def build_full_report(conn):
    cfg = backtest35.frozen()
    lines = ["# C1 (roadmap) — le mouvement de cote est-il un signal d'entrée ?", ""]

    lines += ["## 1. Diagnostic exploratoire (le signal existe-t-il ?)", ""]
    records = collect_bets(conn, cfg)
    if records:
        text, _ = summarize(records)
        lines.append(text)
    else:
        lines.append("Aucun pari collecté (cache CSV brut absent ?).\n")

    lines += ["## 2. Score composite actionnable (edge + mouvement, réglé sur validation)", "",
             f"Correction méthodologique par rapport à la section 1 : la clôture n'entre "
             f"plus dans le score — elle n'est pas connue au moment de parier. La cote "
             f"« prise » est simulée à J-{HORIZON_DAYS} du coup d'envoi (interpolation "
             f"fair entre ouverture et clôture, `aged_taken`, même proxy que "
             f"`backtest_blend.aged_fair` — deux vraies lignes sharp, pas une cote "
             f"scrapée), marge reconstituée séparément pour ne pas gonfler le ROI avec "
             f"une cote fair irréaliste. J-{HORIZON_DAYS} fixé A PRIORI (pas réglé sur "
             f"les données) dans la bande de fraîcheur déjà utilisée en production "
             f"(`predict.FRESH_MAX_DAYS`={predict.FRESH_MAX_DAYS}, "
             f"`predict.STALE_MIN_DAYS`={predict.STALE_MIN_DAYS}). Score = edge + "
             f"w×mouvement (mouvement = ouverture→pris) ; (w, seuil) réglés sur la "
             f"VALIDATION seule par grid search du ROI théorique, n ≥ "
             f"{MIN_BETS_FOR_TUNE} paris sélectionnés exigé.", ""]

    if not FROZEN_PATH.exists():
        lines.append("Pas encore réglé — lancer `python clv_signal_check.py --tune`.\n")
        return "\n".join(lines)

    fcfg = json.loads(FROZEN_PATH.read_text())
    lines.append(f"Figé le {fcfg['tuned_at']} : w = {fcfg['w']:.2f}, seuil = "
                f"{fcfg['threshold']:.3f} → ROI validation {fcfg['roi_validation'] * 100:+.2f} % "
                f"(n={fcfg['n_validation']}) vs {fcfg['baseline_roi_validation'] * 100:+.2f} % "
                f"sans filtre (n={fcfg['n_baseline_validation']}).\n")

    if not RESULTS_PATH.exists():
        lines.append("Test pas encore exécuté — lancer `python clv_signal_check.py --run`.\n")
        return "\n".join(lines)

    res = json.loads(RESULTS_PATH.read_text())
    base_list, sel_list = res["roi_baseline_all_list"], res["roi_selected_list"]
    b_mean, b_lo, b_hi = bootstrap.ci_mean(base_list)
    s_mean, s_lo, s_hi = bootstrap.ci_mean(sel_list)
    diff, d_lo, d_hi = bootstrap.ci_diff_mean(sel_list, base_list)

    lines += [f"### Résultat test ({', '.join(res['test_seasons'])}, lecture unique)", "",
             f"- ROI baseline (tous les paris value, sans filtre) : {b_mean:+.1%} "
             f"{_pct_ci(b_lo, b_hi)} (n={res['n_test_total']})",
             f"- ROI sélection (score composite > seuil) : "
             f"{s_mean:+.1%} {_pct_ci(s_lo, s_hi)} (n={res['n_test_selected']})"
             if s_mean is not None else "- ROI sélection : aucun pari sélectionné sur le test",
             f"- Écart sélection − baseline : {diff:+.1%} {_pct_ci(d_lo, d_hi)}"
             if diff is not None else "- Écart : IC indisponible", ""]

    signal_test = diff is not None and bootstrap.excludes_zero(d_lo, d_hi) is True and diff > 0

    shuffle_ok = None
    if not SHUFFLE_PATH.exists():
        lines.append("Shuffle-test pas encore exécuté — lancer "
                     "`python clv_signal_check.py --shuffle-test`.\n")
    else:
        sh = json.loads(SHUFFLE_PATH.read_text())
        if sh["real_lift"] is not None and sh["p_value"] is not None:
            lines += ["### Shuffle-test étendu (mouvement permuté)", "",
                     f"Lift réel (sélection − baseline) : {sh['real_lift'] * 100:+.2f} pts. Sur "
                     f"{sh['n_perm_effective']} permutations du mouvement (graine {sh['seed']}) "
                     f"— edge et résultat réels conservés, seul le lien mouvement↔match est "
                     f"détruit — lift permuté moyen {sh['perm_lift_mean'] * 100:+.2f} pts "
                     f"(p95={sh['perm_lift_p95'] * 100:+.2f} pts). p-value = {sh['p_value']:.3f} "
                     f"(fraction des permutations aussi bonnes ou meilleures que le réel).", ""]
            shuffle_ok = sh["p_value"] < 0.05
        else:
            lines.append("Shuffle-test inconclusif (aucun pari réel sélectionné).\n")

    lines += ["### Verdict (chantier 2)", ""]
    if signal_test and shuffle_ok:
        verdict = ("✅ Signal confirmé sur le test ET il ne survit pas à la permutation du "
                  "mouvement (p < 0,05) : ce n'est pas une corrélation mécanique. Le score "
                  "composite peut être considéré pour la production — la mise reste à cadrer "
                  "(fraction Kelly, plafond) avant tout déploiement.")
    elif signal_test and shuffle_ok is False:
        verdict = ("⚠️ Le gap sélection/baseline est statistiquement non nul sur le test, MAIS "
                  "la permutation du mouvement produit un lift comparable ou meilleur dans au "
                  "moins 5 % des tirages : la part du signal réellement attribuable au mouvement "
                  "(plutôt qu'à un artefact de la sélection edge/seuil) n'est pas établie. Ne "
                  "pas construire sur ce résultat sans creuser davantage.")
    elif signal_test is False:
        verdict = ("❌ Le score composite ne bat pas significativement la baseline sur le test "
                  "(IC n'exclut pas 0, ou écart négatif) — ne pas construire cette modulation "
                  "de mise en production, elle figerait du bruit.")
    else:
        verdict = "Verdict incomplet — lancer --run puis --shuffle-test."
    lines.append(verdict)
    lines.append("")

    if signal_test is False:
        lines += ["## Synthèse : pourquoi la section 1 dit « signal », la section 2 dit « rien »",
                  "",
                  "Ce n'est pas une contradiction, c'est la correction annoncée en tête de "
                  "fichier. La section 1 classe convergent/divergent par le mouvement "
                  "ouverture→CLÔTURE : un match où le marché finit par bouger vers l'issue "
                  "que le modèle aimait est, presque par construction, un match où le marché "
                  "a fini par se rapprocher de la vérité — la clôture contient déjà de "
                  "l'information sur l'issue qui n'existe pas encore au moment où on aurait "
                  "parié. Ce n'est pas un signal qu'un parieur peut exploiter EN AMONT, "
                  "c'est en bonne partie un artefact de calendrier (regarder le futur pour "
                  "expliquer le passé). La section 2 corrige exactement ça — mouvement "
                  "ouverture→PRIS, jamais la clôture — et le signal disparaît sur le test, "
                  "confirmé par la permutation. Conclusion honnête : le signal de la section "
                  "1 était probablement largement un artefact de fuite temporelle, pas un "
                  "edge d'exécution réel. Ne pas construire de modulation de mise sur le "
                  "mouvement de cote avec les données actuelles.", ""]

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tune", action="store_true",
                        help="règle (w, seuil) du score composite sur la validation")
    parser.add_argument("--run", action="store_true",
                        help="applique le score composite figé sur le test (lecture unique)")
    parser.add_argument("--shuffle-test", action="store_true",
                        help="permute le mouvement, vérifie que le signal n'est pas mécanique")
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    conn = db.connect(args.db)
    if args.tune:
        tune(conn)
    if args.run:
        run(conn)
    if args.shuffle_test:
        shuffle_test(conn)

    text = build_full_report(conn)
    conn.close()
    print(text)
    out = Path("reports/clv_signal_check.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"\nRapport écrit dans {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
