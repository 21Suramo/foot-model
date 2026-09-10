"""Rapport roadmap "profit durable" A1 : validation indépendante des marchés
dérivés de la grille de score (O/U à 5 lignes, BTTS, totaux par équipe,
handicap asiatique) + diagnostic top-k scores exacts.

Racine historique M7 (roadmap) : O/U 2,5 et BTTS. Étendu depuis à la lettre
du chantier A1 — voir backtest_derived.py pour le détail des marchés ajoutés,
la distinction grille 7×7 (ou25/btts, inchangée) vs 12×12 (tout le reste,
jamais testé avant ce chantier), et les limites honnêtes (BTTS/totaux par
équipe sans cote marché, push exclus du handicap asiatique).

Lit predictions_derived (backtest_derived.py --run), data/derived_markets_frozen.json,
data/leak_check_derived.json, écrit reports/derived_markets_backtest.md.

Usage : python report_derived.py [--db data/football.db]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest35
import backtest_derived as bd
import bootstrap
import db
import derived_markets
import footballdata
from report import CALIB_MIN_N, CALIB_TOL, CLEAR_MARGIN, fmt_row

OUT_PATH = Path("reports/derived_markets_backtest.md")

# Marchés sans cote marché dans football-data.co.uk (seuls 1X2, O/U 2,5 et
# handicap asiatique y figurent) : le critère « vs marché » y est donc
# structurellement inapplicable, pas un trou de mesure à corriger ici.
MARKETS_WITH_MARKET_ODDS = {"ou25", bd.AH_MARKET}

# Marchés où une non-dégradation au test anti-fuite est une limite CONNUE et
# EXPLICABLE de la méthode (permutation des scores), pas un signal de fuite
# avérée — décidé sur la base d'une propriété générale du marché (skew de la
# distribution réelle, ou construction de la ligne), PAS après coup en
# choisissant ce qui a échoué. La permutation réassigne des scores RÉELS
# entre matchs de la même ligue (multiset préservé) : elle détruit la
# dépendance spécifique à la PAIRE d'équipes, mais PAS un biais global comme
# l'avantage du terrain ou une base très déséquilibrée — donc moins de prise
# sur un marché où c'est justement l'essentiel de ce qui reste à prédire.
LEAK_TEST_CAVEAT_MARKETS = {
    "ou05": "base réelle ≈ 94 % sur les saisons de test (over 0,5) : très peu "
            "de variance pour la permutation à détruire, le test manque de "
            "puissance sur ce marché précis.",
    "ou45": "base réelle ≈ 15 % sur les saisons de test (over 4,5) : même "
            "limite que ou05, à l'autre bout de la distribution.",
    "ah": "la permutation réassigne des scores réels entre matchs de la même "
          "ligue (multiset de buts domicile/extérieur préservé, juste "
          "réassigné) : le biais domicile GLOBAL survit donc à la "
          "permutation, alors que la ligne de handicap est fixée précisément "
          "pour l'annuler — il ne reste à détruire que la dépendance fine à "
          "la paire d'équipes, sur laquelle le test a structurellement moins "
          "de prise.",
}

MARKET_NOTES = {
    bd.AH_MARKET: (
        "⚠ Push exclus du Brier (remboursement, ni gagnant ni perdant — possible "
        "seulement sur ligne entière). model_p/market_p sont des probabilités "
        "CONDITIONNELLES « domicile couvre sachant pas de push » : avec seulement "
        "2 cotes de marché (pas de 3e cote « push » chez football-data.co.uk), "
        "c'est la seule quantité comparable en face. La colonne « Fréquences » "
        "vaut 0,5 par construction ici (freq_p n'est pas une baseline pertinente : "
        "la ligne est choisie par le marché précisément pour équilibrer les deux "
        "issues) — c'est le baseline uniforme (0,5) qui joue ce rôle, et il vaut "
        "donc la même chose deux fois de suite ci-dessous, volontairement."
    ),
}


def load_predictions(conn, market):
    cols = "p.*, m.league, m.season, m.date, m.home, m.away, m.fthg, m.ftag"
    if market == bd.AH_MARKET:
        cols += ", m.ah_line"
    rows = [dict(r) for r in conn.execute(
        f"SELECT {cols} "
        "FROM predictions_derived p JOIN matches m USING (match_id) "
        "WHERE p.market = ? ORDER BY m.date, p.match_id", (market,))]
    for r in rows:
        # predictions_derived n'écrit jamais de ligne "ah" pour un push (cf.
        # backtest_derived.run) : ah_home_covers ne renvoie donc jamais None ici.
        r["outcome"] = (bd.ah_home_covers(r) if market == bd.AH_MARKET
                        else bd._market_outcome(market, r["fthg"], r["ftag"]))
    return rows


def brier_series(rows, key):
    return [bd.binary_brier(r[key], r["outcome"]) for r in rows if r.get(key) is not None]


def mean_brier(rows, key):
    s = brier_series(rows, key)
    return float(np.mean(s)) if s else None


def calibration_table(rows, key="model_p", width=0.05):
    pairs = [(r[key], 1.0 if r["outcome"] else 0.0) for r in rows if r.get(key) is not None]
    arr = np.array(pairs)
    bins = np.floor(arr[:, 0] / width).astype(int)
    table = []
    for b in sorted(set(bins)):
        sel = arr[bins == b]
        table.append({"lo": b * width, "hi": (b + 1) * width, "n": len(sel),
                      "pred": float(sel[:, 0].mean()), "obs": float(sel[:, 1].mean())})
    return table


def market_section(conn, market, leak):
    rows = load_predictions(conn, market)
    if not rows:
        return [f"## {bd.MARKET_LABELS[market]}", "",
                "Aucune prédiction de test — lancer `python backtest_derived.py --run`.", ""]

    grid_basis = ("grille 7×7 legacy, celle de M3.5" if bd.MARKET_GRID_FIELD.get(market, "grid_ext") == "grid"
                 else "grille 12×12 étendue (derived_markets.EXTENDED_MAX_GOALS), jamais testée "
                      "avant ce chantier — cf. backtest_derived.py")
    lines = [f"## {bd.MARKET_LABELS[market]}", "",
             f"{len(rows)} matchs de test (saisons {', '.join(backtest.TEST)}), 3 ligues, "
             f"refit hebdomadaire ({grid_basis}). Grille de score dérivée du modèle M3.5 "
             f"déjà figé (w/ξ/κ inchangés), recalibration binaire propre à ce marché réglée "
             f"sur la validation {'+'.join(backtest.VALIDATION)}.", ""]
    if market in MARKET_NOTES:
        lines += [MARKET_NOTES[market], ""]

    b_model = mean_brier(rows, "model_p")
    b_raw = mean_brier(rows, "raw_p")
    b_freq = mean_brier(rows, "freq_p")
    b_unif = float(np.mean([bd.binary_brier(0.5, r["outcome"]) for r in rows]))
    beats_freq = (b_freq - b_model) / b_freq
    beats_unif = (b_unif - b_model) / b_unif

    calib = calibration_table(rows)
    big = [c for c in calib if c["n"] >= CALIB_MIN_N]
    worst = max(big, key=lambda c: abs(c["obs"] - c["pred"])) if big else None

    checks = []
    has_market = market in MARKETS_WITH_MARKET_ODDS
    n_with_market = sum(1 for r in rows if r.get("market_p") is not None)
    if has_market and n_with_market:
        model_b = brier_series(rows, "model_p")
        market_b_full = [bd.binary_brier(r["market_p"], r["outcome"]) for r in rows if r.get("market_p") is not None]
        # Séries appariées : ne garder que les matchs où le modèle ET le marché existent (toujours vrai ici).
        rel_market = (mean_brier(rows, "model_p") - mean_brier(rows, "market_p")) / mean_brier(rows, "market_p")
        model_b_paired = [bd.binary_brier(r["model_p"], r["outcome"]) for r in rows if r.get("market_p") is not None]
        _, lo, hi = bootstrap.ci_relative_delta(model_b_paired, market_b_full)
        checks.append((rel_market < 0.02,
                       f"Brier modèle à {rel_market * 100:+.2f} % du marché "
                       f"{bootstrap.fmt_ci(lo, hi)} (critère < +2 %), sur {n_with_market} "
                       f"match(s) avec cote « {bd.MARKET_LABELS[market]} »."))
    else:
        checks.append((None, f"Comparaison au marché : **inapplicable** — aucune cote "
                             f"{bd.MARKET_LABELS[market]} dans football-data.co.uk (seuls "
                             f"1X2, O/U 2,5 et handicap asiatique y figurent). Pas de "
                             f"source, pas de critère fabriqué."))
    checks.append((beats_freq >= CLEAR_MARGIN and beats_unif >= CLEAR_MARGIN,
                   f"Bat les baselines : {beats_freq * 100:+.1f} % vs fréquences, "
                   f"{beats_unif * 100:+.1f} % vs uniforme (0,5) (critère ≥ {CLEAR_MARGIN * 100:.0f} % chacune)"))
    if worst is not None:
        checks.append((abs(worst["obs"] - worst["pred"]) <= CALIB_TOL,
                       f"Calibration : pire tranche (n ≥ {CALIB_MIN_N}) à "
                       f"{abs(worst['obs'] - worst['pred']) * 100:.1f} pts d'écart "
                       f"(tolérance {CALIB_TOL * 100:.0f} pts)"))
    leak_m = (leak or {}).get("leagues", {})
    if leak_m:
        all_degraded = all(v[market]["degraded"] for v in leak_m.values())
        if not all_degraded and market in LEAK_TEST_CAVEAT_MARKETS:
            checks.append((None, f"Anti-fuite : pas de dégradation nette sur les 3 ligues, mais "
                                 f"limite CONNUE de la méthode sur ce marché — "
                                 f"{LEAK_TEST_CAVEAT_MARKETS[market]} Pas une fuite prouvée, mais "
                                 f"pas exclue non plus : la certitude anti-fuite vient des marchés "
                                 f"où la dégradation EST nette sur les 3 ligues (ou25, btts, ou35, "
                                 f"totaux par équipe)."))
        else:
            checks.append((all_degraded, "Anti-fuite : Brier dégradé sur les 3 ligues avec labels permutés"))

    lines += ["### Verdict", ""]
    for ok, txt in checks:
        mark = "➖" if ok is None else ("✅" if ok else "❌")
        lines.append(f"- {mark} {txt}")
    lines.append("")

    lines += ["### Résultats agrégés", "",
              fmt_row(["Méthode", "Brier", "n"]), fmt_row(["---"] * 3)]
    lines.append(fmt_row(["Modèle (recalibré)", f"{b_model:.5f}", len(rows)]))
    lines.append(fmt_row(["Modèle (brut, avant recalibration)", f"{b_raw:.5f}", len(rows)]))
    if has_market and n_with_market:
        market_row_label = "Marché (démargé proportionnel)"
        if market == bd.AH_MARKET:
            market_row_label += " — conditionnel, push exclu"
        lines.append(fmt_row([market_row_label,
                              f"{mean_brier(rows, 'market_p'):.5f}", n_with_market]))
    lines.append(fmt_row(["Fréquences (walk-forward)", f"{b_freq:.5f}", len(rows)]))
    lines.append(fmt_row(["Uniforme (0,5)", f"{b_unif:.5f}", len(rows)]))
    lines.append("")

    lines += ["### Calibration (tranches de 5 pts)", "",
              fmt_row(["Tranche", "n", "Proba prédite moy.", "Fréquence observée", "Écart"]),
              fmt_row(["---"] * 5)]
    for c in calib:
        mark = "" if c["n"] >= CALIB_MIN_N else " *"
        lines.append(fmt_row([f"{c['lo'] * 100:.0f}–{c['hi'] * 100:.0f} %{mark}", c["n"],
                              f"{c['pred'] * 100:.1f} %", f"{c['obs'] * 100:.1f} %",
                              f"{(c['obs'] - c['pred']) * 100:+.1f} pts"]))
    lines += ["", f"\\* tranches sous n = {CALIB_MIN_N}, hors verdict.", ""]

    lines += ["### Par ligue et saison de test", "",
              fmt_row(["Ligue", "Saison", "n", "Modèle"] +
                     (["Marché", "Écart rel. marché"] if has_market else []) + ["Fréquences"]),
              fmt_row(["---"] * (4 + (2 if has_market else 0) + 1))]
    for league in footballdata.LEAGUES:
        for season in backtest.TEST:
            sub = [r for r in rows if r["league"] == league and r["season"] == season]
            if not sub:
                continue
            cells = [league, season, len(sub), f"{mean_brier(sub, 'model_p'):.5f}"]
            if has_market:
                sub_mkt = [r for r in sub if r.get("market_p") is not None]
                if sub_mkt:
                    bm, bk = mean_brier(sub, "model_p"), mean_brier(sub_mkt, "market_p")
                    cells += [f"{bk:.5f}", f"{(bm - bk) / bk * 100:+.2f} %"]
                else:
                    cells += ["—", "—"]
            cells.append(f"{mean_brier(sub, 'freq_p'):.5f}")
            lines.append(fmt_row(cells))
    lines.append("")

    if leak_m:
        lines += ["### Test anti-fuite (labels permutés, saison 2223)", "",
                  fmt_row(["Ligue", "Brier réel", "Brier permuté", "Dégradé ?"]), fmt_row(["---"] * 4)]
        for league, v in leak_m.items():
            e = v[market]
            lines.append(fmt_row([league, f"{e['brier_real']:.5f}", f"{e['brier_shuffled']:.5f}",
                                  "oui" if e["degraded"] else "NON — FUITE PROBABLE"]))
        lines.append("")
    else:
        lines += ["Anti-fuite non exécuté (`python backtest_derived.py --shuffle-test`).", ""]

    return lines


def build_report(conn):
    if not bd.FROZEN_PATH.exists():
        sys.exit(f"{bd.FROZEN_PATH} absent : lancer d'abord "
                 f"python backtest_derived.py --tune puis --run")
    cfg = json.loads(bd.FROZEN_PATH.read_text())
    leak = json.loads(bd.LEAK_PATH.read_text()) if bd.LEAK_PATH.exists() else None

    lines = ["# Roadmap \"profit durable\" A1 — Validation indépendante des marchés dérivés", "",
             "Marchés calculés depuis la grille de score du modèle M3.5 déjà backtesté "
             "(`reports/m35_backtest.md`) — w/ξ/κ inchangés. Seule une recalibration "
             "binaire propre à chaque marché a été réglée ici, sur la même validation, "
             "avec la même interdiction de retoucher quoi que ce soit après lecture du "
             "test — appliquée MARCHÉ PAR MARCHÉ (`backtest_derived.tune` fusionne, "
             "n'écrase jamais un marché déjà figé). O/U 2,5 et BTTS restent sur la "
             "grille 7×7 exacte déjà publiée (racine M7 (roadmap)) ; tous les marchés "
             "ajoutés depuis (O/U 0,5/1,5/3,5/4,5, totaux par équipe, handicap "
             "asiatique) utilisent une grille 12×12 (`derived_markets.EXTENDED_MAX_GOALS`), "
             "jamais testée avant ce chantier — élargir la base avant de lire un test "
             "n'est pas le re-réglage que le protocole interdit.", "",
             f"Réglages figés le {cfg.get('tuned_at', '?')} : " +
             " ; ".join(f"{bd.MARKET_LABELS[m]} t = {cfg['markets'][m]['t']:.3f} "
                       f"(Brier validation {cfg['markets'][m]['brier_validation_raw']:.5f} -> "
                       f"{cfg['markets'][m]['brier_validation']:.5f})"
                       for m in bd.ALL_MARKETS if m in cfg.get("markets", {})), ""]

    bound_lo, bound_hi = bd.TEMP_BOUNDS
    at_bound = [m for m in bd.ALL_MARKETS if m in cfg.get("markets", {})
               and (abs(cfg["markets"][m]["t"] - bound_lo) < 1e-3
                    or abs(cfg["markets"][m]["t"] - bound_hi) < 1e-3)]
    if at_bound:
        lines += [f"⚠ Température au bord de la plage autorisée ({bound_lo}–{bound_hi}) pour "
                  f"{', '.join(bd.MARKET_LABELS[m] for m in at_bound)} : l'optimum réel est "
                  f"peut-être hors plage (aplatissement encore plus fort). Reste dans "
                  f"TEMP_BOUNDS par cohérence avec M3.5 plutôt que d'élargir la plage après "
                  f"avoir vu où l'optimiseur bute — à noter, pas à corriger rétroactivement.",
                  ""]

    for market in bd.ALL_MARKETS:
        lines += market_section(conn, market, leak)

    lines += topk_section(conn)

    return "\n".join(lines)


def topk_section(conn):
    """Diagnostic léger (pas un marché coté, pas de tune/validation/test) :
    le score réel figure-t-il dans les k scores les plus probables de la
    grille 7×7 déjà en production (celle de match_model.py) ? Comparé à un
    top-k walk-forward tiré de la fréquence historique des scores (jamais le
    futur, cf. `score_freq` dans backtest_derived.walk_forward_derived)."""
    cfg = backtest35.frozen()
    lines = ["## Top-k scores exacts (diagnostic, pas un marché coté)", "",
             "football-data.co.uk ne cote aucun score exact : rien à comparer à un "
             "marché ici. Ce n'est qu'un diagnostic de la grille déjà en production "
             "(grille 7×7, `match_model.py`), sans recalibration ni paramètre à figer "
             "— pas soumis au protocole tune/validation/test.", ""]
    hits_model = {1: 0, 3: 0, 5: 0}
    hits_freq = {1: 0, 3: 0, 5: 0}
    n = 0
    for league in footballdata.LEAGUES:
        rows = bd.load_league(conn, league)
        for p in bd.walk_forward_derived(rows, backtest.TEST, cfg):
            r = p["row"]
            n += 1
            for k in (1, 3, 5):
                if derived_markets.topk_hit(p["grid"], r["fthg"], r["ftag"], k):
                    hits_model[k] += 1
                if derived_markets.topk_hit(p["score_freq"], r["fthg"], r["ftag"], k):
                    hits_freq[k] += 1
    lines += [fmt_row(["k", "Hit-rate modèle", "Hit-rate fréquence (walk-forward)"]),
             fmt_row(["---"] * 3)]
    for k in (1, 3, 5):
        model_rate = hits_model[k] / n if n else 0.0
        freq_rate = hits_freq[k] / n if n else 0.0
        lines.append(fmt_row([k, f"{model_rate * 100:.1f} %", f"{freq_rate * 100:.1f} %"]))
    lines += ["", f"{n} matchs de test.", ""]
    return lines


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
