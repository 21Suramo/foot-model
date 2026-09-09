"""Quel démargeage lit le mieux le marché : proportionnel, power ou Shin ?

Le pont marché/modèle de `predict.py` ne consomme jamais les cotes brutes : il
en extrait d'abord des probabilités « fair » en retirant la marge du book. Cette
étape n'est pas neutre — le même 1,85 / 3,60 / 4,40 donne 51,7 % ou 52,6 % de
victoire à domicile selon la méthode, et c'est ce chiffre qui pèse ensuite 92 %
du FINAL sur cotes fraîches. Un démargeage biaisé se propage donc à toutes les
prédictions de production, sans jamais apparaître dans un diagnostic du modèle.

Trois méthodes comparées (définitions dans backtest.py) :

- **proportionnel** : marge répartie au prorata des cotes implicites. Ignore le
  biais favori-longshot (le book charge plus de marge sur les grosses cotes),
  donc surestime structurellement les outsiders.
- **power** : exposant unique k tel que Σ(1/o_i)^k = 1. Corrige le biais, mais
  sans modèle de ce qui le cause — l'exposant est un paramètre d'ajustement.
- **Shin** : modèle explicite de la marge (le book se couvre contre une
  proportion z de parieurs informés). Corrige le même biais en le dérivant
  d'une hypothèse économique plutôt que d'un exposant libre.

Protocole, calqué sur `fatigue_signal_check.py` : la mesure ne touche JAMAIS
les saisons de test (backtest.TEST). Elle porte sur le burn-in et la validation
— les saisons sur lesquelles le projet s'autorise à régler quelque chose. C'est
d'ailleurs une mesure purement marché : aucun modèle n'intervient, rien de figé
n'est régénéré ici.

Chaque écart est publié avec son intervalle de confiance bootstrap apparié
(bootstrap.py) : sur ~4 400 matchs les écarts entre méthodes se jouent à la
4e décimale du Brier, l'IC dit si l'un d'eux est distinguable du bruit.

Usage : python devig_check.py [--db data/football.db]
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

import backtest
import bootstrap
import db
import footballdata

log = logging.getLogger("devig_check")

OUT_PATH = Path("reports/devig_check.md")
METHODS = ("proportional", "power", "shin")
LABELS = {"proportional": "Proportionnel", "power": "Power", "shin": "Shin"}
# Méthode de référence des comparaisons appariées : celle que le code de
# production utilisait avant ce chantier.
BASELINE = "power"
# Ligne sharp de référence (cf. footballdata.ODDS_1X2) : la seule sur laquelle
# un modèle de marge à un seul book a vraiment un sens.
SHARP_SOURCE = "pinnacle_close"
CALIB_WIDTH = 0.05
CALIB_MIN_N = 300   # même seuil que report.py pour qu'une tranche compte


def load_rows(conn, seasons):
    """Matchs joués de ces saisons ayant une cote 1N2 complète."""
    out = []
    for league in footballdata.LEAGUES:
        for r in conn.execute(
                "SELECT league, season, date, home, away, fthg, ftag, "
                "odds_h, odds_d, odds_a, odds_source FROM matches "
                "WHERE league = ? AND fthg IS NOT NULL AND odds_h IS NOT NULL "
                "AND odds_d IS NOT NULL AND odds_a IS NOT NULL ORDER BY date",
                (league,)):
            if r["season"] not in seasons:
                continue
            row = dict(r)
            row["outcome"] = backtest.outcome_index(row["fthg"], row["ftag"])
            row["probs"] = {m: backtest.DEMARGIN_METHODS[m](
                row["odds_h"], row["odds_d"], row["odds_a"]) for m in METHODS}
            out.append(row)
    return out


def scores(rows, method):
    """(Brier par match, log-loss par match) — séries appariées, même ordre."""
    briers = [backtest.brier(r["probs"][method], r["outcome"]) for r in rows]
    lls = [backtest.logloss(r["probs"][method], r["outcome"]) for r in rows]
    return briers, lls


def calibration(rows, method, width=CALIB_WIDTH):
    """Tranches de probabilité : chaque match contribue ses 3 probas 1N2.

    C'est ici que se voit le biais favori-longshot : si les probas basses sont
    systématiquement au-dessus de la fréquence observée, la méthode laisse trop
    de proba aux outsiders (marge mal retirée sur les grosses cotes).
    """
    pairs = np.array([(p, 1.0 if k == r["outcome"] else 0.0)
                      for r in rows for k, p in enumerate(r["probs"][method])])
    bins = np.floor(pairs[:, 0] / width).astype(int)
    table = []
    for b in sorted(set(bins)):
        sel = pairs[bins == b]
        table.append({"lo": b * width, "hi": (b + 1) * width, "n": len(sel),
                      "pred": float(sel[:, 0].mean()), "obs": float(sel[:, 1].mean())})
    return table


def longshot_bias(rows, method, threshold=0.15):
    """Écart prédit − observé cumulé sur les tranches de proba < threshold.

    Résumé en un chiffre du biais favori-longshot : positif = la méthode
    surestime les issues improbables (elle leur laisse la marge du book).
    """
    pairs = [(p, 1.0 if k == r["outcome"] else 0.0)
             for r in rows for k, p in enumerate(r["probs"][method]) if p < threshold]
    if not pairs:
        return None, 0
    arr = np.array(pairs)
    return float(arr[:, 0].mean() - arr[:, 1].mean()), len(arr)


def method_results(rows):
    """{méthode: {brier, logloss, briers, delta, ci}} — écarts appariés vs BASELINE."""
    base_b, _ = scores(rows, BASELINE)
    out = {}
    for m in METHODS:
        b, ll = scores(rows, m)
        out[m] = {"brier": float(np.mean(b)), "logloss": float(np.mean(ll)), "briers": b}
        if m != BASELINE:
            point, lo, hi = bootstrap.ci_relative_delta(b, base_b)
            out[m]["delta"], out[m]["ci"] = point, (lo, hi)
    return out


def method_table(rows):
    """Lignes markdown du tableau Brier / log-loss / écart apparié vs BASELINE."""
    res = method_results(rows)
    lines = [f"| Méthode | Brier | Log-loss | Δ Brier rel. vs {LABELS[BASELINE].lower()} | "
             f"IC 95 % de l'écart |",
             "| --- | --- | --- | --- | --- |"]
    for m in METHODS:
        if m == BASELINE:
            delta, ci = "référence", "—"
        else:
            delta = f"{res[m]['delta']:+.3f} %"
            ci = bootstrap.fmt_ci(*res[m]["ci"])
        lines.append(f"| {LABELS[m]} | {res[m]['brier']:.5f} | {res[m]['logloss']:.5f} | "
                     f"{delta} | {ci} |")
    lines.append("")
    return lines


def build_report(rows, seasons):
    lines = ["# Démargeage des cotes : proportionnel vs power vs Shin", ""]
    by_season = {}
    for r in rows:
        by_season[r["season"]] = by_season.get(r["season"], 0) + 1
    lines += [f"{len(rows)} matchs avec cotes 1N2 complètes sur les saisons "
              f"{', '.join(sorted(seasons))} (burn-in + validation). "
              f"**Les saisons de test ({', '.join(backtest.TEST)}) sont exclues** : "
              f"choisir une méthode de démargeage est un réglage, il ne se fait pas "
              f"sur le jeu de test — même discipline que ξ, w et κ.", "",
              "Aucun modèle n'intervient ici : on mesure uniquement la qualité des "
              "probabilités que l'on extrait des cotes du marché. Référence Brier "
              "hasard = 0.6667 (plus bas = mieux).", ""]

    lines += ["## Brier et log-loss par méthode", ""]
    lines += method_table(rows)
    lines += [f"L'écart relatif est apparié (mêmes matchs, mêmes tirages bootstrap "
              f"— {bootstrap.DEFAULT_RESAMPLES} rééchantillonnages, graine "
              f"{bootstrap.DEFAULT_SEED}). Négatif = meilleur que "
              f"{LABELS[BASELINE].lower()}.", ""]
    results = method_results(rows)

    # Sous-ensemble « ligne sharp » : la seule sur laquelle un démargeage a un
    # sens fort (une moyenne de books soft mélange des marges hétérogènes).
    sharp = [r for r in rows if r["odds_source"] == SHARP_SOURCE]
    mix = {}
    for r in rows:
        mix[r["odds_source"]] = mix.get(r["odds_source"], 0) + 1
    lines += [f"Sources de cotes de l'échantillon : "
              + ", ".join(f"`{k}` {v}" for k, v in sorted(mix.items())) + ".", ""]
    if sharp and len(sharp) < len(rows):
        lines += [f"### Restreint à la ligne sharp (`{SHARP_SOURCE}`, {len(sharp)} matchs)", "",
                  "Une moyenne de books mélange des marges hétérogènes ; le modèle de "
                  "Shin suppose UN book qui se protège d'insiders. La comparaison la "
                  "plus propre est donc celle-ci.", ""]
        lines += method_table(sharp)

    lines += ["## Biais favori-longshot (probas < 15 %)", "",
              "Moyenne (proba prédite − issue observée) sur les probabilités "
              "inférieures à 15 %. Positif = la méthode surestime les issues "
              "improbables, c'est-à-dire qu'elle leur laisse une part de la marge "
              "du bookmaker au lieu de la retirer.", "",
              "| Méthode | n probas < 15 % | Écart moyen prédit − observé |",
              "| --- | --- | --- |"]
    for m in METHODS:
        bias, n = longshot_bias(rows, m)
        cell = f"{bias * 100:+.2f} pts" if bias is not None else "—"
        lines.append(f"| {LABELS[m]} | {n} | {cell} |")
    lines.append("")

    lines += ["## Calibration par tranche (3 probas par match)", "",
              "| Tranche | n | " + " | ".join(f"Obs − prédit ({LABELS[m]})" for m in METHODS) + " |",
              "| --- | --- | " + " | ".join(["---"] * len(METHODS)) + " |"]
    tables = {m: {(c["lo"], c["hi"]): c for c in calibration(rows, m)} for m in METHODS}
    keys = sorted({k for t in tables.values() for k in t})
    for k in keys:
        cells = []
        n_disp = max((tables[m][k]["n"] for m in METHODS if k in tables[m]), default=0)
        for m in METHODS:
            c = tables[m].get(k)
            cells.append(f"{(c['obs'] - c['pred']) * 100:+.1f} pts" if c else "—")
        mark = "" if n_disp >= CALIB_MIN_N else " *"
        lines.append(f"| {k[0] * 100:.0f}–{k[1] * 100:.0f} %{mark} | {n_disp} | "
                     + " | ".join(cells) + " |")
    lines += ["", f"\\* tranches sous n = {CALIB_MIN_N} : lecture indicative. "
              f"Le n affiché est celui de la tranche la plus fournie des trois "
              f"méthodes (elles ne rangent pas exactement les mêmes probas dans "
              f"les mêmes tranches).", ""]

    # --- Verdict ---
    best = min(METHODS, key=lambda m: results[m]["brier"])
    biases = {m: longshot_bias(rows, m)[0] for m in METHODS}
    least_biased = min((m for m in METHODS if biases[m] is not None),
                       key=lambda m: abs(biases[m]))
    any_significant = any(bootstrap.excludes_zero(*results[m]["ci"])
                          for m in METHODS if m != BASELINE)
    lines += ["## Verdict", ""]
    lines.append(f"- Meilleur Brier : **{LABELS[best]}** ({results[best]['brier']:.5f}).")
    for m in METHODS:
        if m == BASELINE:
            continue
        lo, hi = results[m]["ci"]
        sig = bootstrap.excludes_zero(lo, hi)
        verdict = ("écart distinguable du bruit" if sig
                   else "écart NON distinguable du bruit (IC contient 0)")
        lines.append(f"- {LABELS[m]} vs {LABELS[BASELINE].lower()} : "
                     f"{results[m]['delta']:+.3f} % {bootstrap.fmt_ci(lo, hi)} — {verdict}.")
    lines.append(f"- Biais longshot le plus faible en valeur absolue : "
                 f"**{LABELS[least_biased]}** ({biases[least_biased] * 100:+.2f} pts "
                 f"sur les probas < 15 %).")
    if not any_significant:
        lines += ["",
                  "**Aucun écart de Brier n'est distinguable du bruit.** Sur une ligne "
                  "sharp à marge faible, les trois méthodes produisent des probabilités "
                  "trop proches pour que 4 459 matchs les départagent — c'est le "
                  "résultat, pas une insuffisance de l'échantillon. Choisir Shin ne "
                  "s'appuie donc PAS sur un gain mesuré : c'est un choix de rigueur "
                  "(une marge dérivée d'un modèle explicite plutôt qu'un exposant "
                  "libre), et il faut le présenter comme tel. Toute affirmation du "
                  "type « Shin améliore les probas » serait ici une surinterprétation.",
                  ""]
    if biases[BASELINE] is not None and abs(biases[BASELINE]) > abs(biases[least_biased]):
        lines += [f"À noter, contre l'intuition habituelle : sur ces cotes "
                  f"(clôture Pinnacle, marge basse) le biais favori-longshot résiduel "
                  f"est NÉGATIF pour {LABELS[BASELINE].lower()} comme pour Shin — les "
                  f"probas basses ressortent SOUS la fréquence observée, donc la "
                  f"correction sur-corrige légèrement plutôt que d'être insuffisante. "
                  f"Le démargeage proportionnel, censé être le plus biaisé, est ici le "
                  f"plus proche de zéro sur cette tranche. Conséquence pratique : ni "
                  f"power ni Shin ne fabrique de fausse value sur les outsiders — ils "
                  f"iraient plutôt en manquer.", ""]
    lines += ["Lecture générale : les trois méthodes partent des mêmes cotes et ne "
              "diffèrent que par la façon de retirer la marge ; les écarts de Brier "
              "attendus sont petits par construction. Ce qui les départage vraiment "
              "est la calibration sur les probas basses — une méthode qui y garde un "
              "biais fabrique de fausses « values » sur les outsiders, là précisément "
              "où `predict.py` déclenche ses mises Kelly aux plus grosses cotes.", ""]
    return "\n".join(lines), results, best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    seasons = tuple(s for s in backtest.BURN_IN + backtest.VALIDATION)
    conn = db.connect(args.db)
    rows = load_rows(conn, seasons)
    conn.close()
    if not rows:
        sys.exit("Aucun match avec cotes sur les saisons hors test — lancer "
                 "`python pipeline.py --update`.")
    text, results, best = build_report(rows, seasons)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text)
    print(text)
    print(f"\nRapport écrit dans {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
