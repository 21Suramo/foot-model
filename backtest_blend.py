"""M5 — backtest du pont marché/modèle de predict.py (même walk-forward que M3.5).

Question : le blend de predict.py (poids marché 65 % sur cotes fraîches,
décroissant jusqu'à 0 à J-5) est-il OPTIMAL, ou juste plausible ?

Difficulté : la base ne stocke qu'un jeu de cotes par match (la clôture). Pour
simuler des cotes « vieillies » on n'invente rien — on exploite le SEUL
mouvement de ligne réellement disponible dans les CSV football-data : l'écart
entre la cote d'OUVERTURE (publiée ~1 semaine avant, la plus « périmée ») et la
cote de CLÔTURE (au coup d'envoi, la plus « fraîche », référence de M3.5). Les
cotes à J-N sont interpolées linéairement clôture↔ouverture (frac = N/7 en
espace de probabilité fair), avec :
- J-0  = clôture pure (marché frais, référence)
- J-7  = ouverture pure (marché le plus périmé qu'on puisse observer)

⚠ Ce proxy SOUS-ESTIME la vraie péremption visée par le garde-fou : une cote
grattée sur le web à J-3 peut être bien plus mauvaise que l'ouverture d'un book
sharp (mauvaise recopie, book soft, ligne figée). Le backtest borne donc par le
BAS l'intérêt du modèle — s'il aide déjà ici, il aide a fortiori en vrai.

Protocole : refit hebdomadaire strict (backtest.walk_forward) avec la config
M3.5 figée. Le poids du blend n'a JAMAIS été réglé (predict.py fixe 65 %/J-5 a
priori) ; la recherche d'un meilleur barème se fait sur la VALIDATION seule
(2020-21 + 2021-22), le test 2223→2526 ne sert qu'à mesurer.

Usage : python backtest_blend.py [--db data/football.db]
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import backtest
import backtest35
import db
import footballdata
import predict

log = logging.getLogger("backtest_blend")

OUT_PATH = Path("reports/m5_blend_backtest.md")
AGES = (0, 1, 3, 5, 7)          # J-N testés
OPEN_HORIZON = 7                # l'ouverture ≈ J-7 ; interpolation linéaire jusque-là
W_GRID = np.round(np.linspace(0.0, 1.0, 21), 2)   # sweep du poids marché (grid search)
BLEND_BASE = 0.65              # poids de base du code (predict.py --blend)
VIOL_TOL = 1e-4                # tolérance « FINAL pas pire que le meilleur des deux »

# Cotes d'ouverture 1N2 dans les CSV bruts (mêmes priorités que footballdata,
# mais versions SANS « C » = ouverture au lieu de clôture).
OPEN_CHAIN = [
    ("PSH", "PSD", "PSA"),
    ("AvgH", "AvgD", "AvgA"),
    ("B365H", "B365D", "B365A"),
    ("BbAvH", "BbAvD", "BbAvA"),
]


# ---------------------------------------------------------------------------
# Cotes d'ouverture depuis les CSV bruts
# ---------------------------------------------------------------------------

def _pick_open(row):
    for ch, cd, ca in OPEN_CHAIN:
        h, d, a = footballdata._num(row.get(ch)), footballdata._num(row.get(cd)), footballdata._num(row.get(ca))
        if h and d and a:
            return h, d, a
    return None


def opening_odds_map(seasons):
    """(date_iso, home, away) -> triplet de cotes d'ouverture, depuis data/raw."""
    out = {}
    for league in footballdata.LEAGUES:
        for season in seasons:
            path = footballdata.raw_path(league, season)
            if not path.exists():
                continue
            df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
            df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
            dates = pd.to_datetime(df["Date"], format="mixed", dayfirst=True)
            for (_, row), date in zip(df.iterrows(), dates):
                trip = _pick_open(row)
                if trip:
                    out[(date.strftime("%Y-%m-%d"),
                         str(row["HomeTeam"]).strip(), str(row["AwayTeam"]).strip())] = trip
    return out


# ---------------------------------------------------------------------------
# Vieillissement des cotes et blend
# ---------------------------------------------------------------------------

def aged_fair(close, open_, age):
    """Probas fair « à J-age » : interpolation clôture↔ouverture (frac = age/7)."""
    frac = min(age / OPEN_HORIZON, 1.0)
    p = [(1 - frac) * c + frac * o for c, o in zip(close, open_)]
    s = sum(p)
    return tuple(x / s for x in p)


def blended(market, model, w):
    p = [w * mk + (1 - w) * md for mk, md in zip(market, model)]
    s = sum(p)
    return tuple(x / s for x in p)


def decay_weight(age, blend=BLEND_BASE):
    """Poids marché du code réel de predict.py pour un âge donné."""
    return predict.market_weight(blend, age, True)[0]


# ---------------------------------------------------------------------------
# Collecte walk-forward (modèle recalibré + ouverture + clôture par match)
# ---------------------------------------------------------------------------

def collect(conn, cfg, target_seasons):
    open_map = opening_odds_map(target_seasons)
    recs = []
    n_no_open = n_no_close = 0
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
            trip = open_map.get((r["date"], r["home"], r["away"]))
            if trip is None:
                n_no_open += 1
                continue
            recs.append({
                "outcome": p["outcome"], "league": league, "season": r["season"],
                "model": backtest35.apply_temperature(p["model"], cfg["temperature"]),
                "close": p["market"],                      # clôture démargée (référence J-0)
                "open": backtest.demargin_power(*trip),    # ouverture démargée (J-7)
            })
    return recs, n_no_open, n_no_close


# ---------------------------------------------------------------------------
# Métriques
# ---------------------------------------------------------------------------

def mean_brier_blend(recs, age, w):
    return float(np.mean([backtest.brier(blended(aged_fair(r["close"], r["open"], age), r["model"], w),
                                         r["outcome"]) for r in recs]))


def mean_brier_market(recs, age):
    return float(np.mean([backtest.brier(aged_fair(r["close"], r["open"], age), r["outcome"])
                          for r in recs]))


def mean_brier_model(recs):
    return float(np.mean([backtest.brier(r["model"], r["outcome"]) for r in recs]))


def oracle_weights(recs):
    """Poids marché optimal par âge (argmin Brier), calé sur `recs` (validation)."""
    return {age: float(min(W_GRID, key=lambda w: mean_brier_blend(recs, age, w))) for age in AGES}


def best_fixed_weight(recs):
    """Meilleur poids marché UNIQUE (même à tous les âges), calé sur `recs`."""
    return float(min(W_GRID, key=lambda w: float(np.mean([mean_brier_blend(recs, age, w) for age in AGES]))))


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def fmt_row(cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def build_report(conn):
    cfg = backtest35.frozen()
    log.info("Collecte validation…")
    val_recs, val_no, _ = collect(conn, cfg, backtest.VALIDATION)
    log.info("Collecte test…")
    test_recs, test_no, test_noc = collect(conn, cfg, backtest.TEST)
    if not test_recs:
        sys.exit("Aucun match de test avec ouverture ET clôture — base incomplète ?")

    b_model_test = mean_brier_model(test_recs)
    b_fresh_test = mean_brier_market(test_recs, 0)   # clôture = marché frais (réf. J-0)

    # Barèmes alternatifs réglés sur la VALIDATION uniquement
    oracle = oracle_weights(val_recs)
    w_fixed = best_fixed_weight(val_recs)

    # --- Table test : decay actuel, par âge ---
    per_age = []
    for age in AGES:
        w_cur = decay_weight(age)
        b_final = mean_brier_blend(test_recs, age, w_cur)
        b_mkt = mean_brier_market(test_recs, age)
        best2 = min(b_mkt, b_model_test)
        per_age.append({
            "age": age, "w": w_cur, "final": b_final, "market": b_mkt,
            "best2": best2, "viol": b_final - best2,
            "oracle_w": oracle[age], "oracle_final": mean_brier_blend(test_recs, age, oracle[age]),
            "fixed_final": mean_brier_blend(test_recs, age, w_fixed),
        })

    avg_final = float(np.mean([a["final"] for a in per_age]))
    avg_oracle = float(np.mean([a["oracle_final"] for a in per_age]))
    avg_fixed = float(np.mean([a["fixed_final"] for a in per_age]))
    avg_market = float(np.mean([a["market"] for a in per_age]))
    max_viol = max(per_age, key=lambda a: a["viol"])
    market_always_beats_model = all(a["market"] < b_model_test for a in per_age)

    # --- Rédaction ---
    lines = ["# M5 — Backtest du pont marché/modèle (blend de predict.py)", ""]
    lines += [f"{len(test_recs)} matchs de test (saisons {', '.join(backtest.TEST)}) avec ouverture "
              f"ET clôture disponibles ; {len(val_recs)} matchs de validation. Config M3.5 figée "
              f"(w = {cfg['w']:.1f}, ξ = {cfg['xi']}, κ = {cfg['kappa']}, t = {cfg['temperature']:.3f}), "
              f"refit hebdomadaire. Cotes vieillies par interpolation clôture↔ouverture "
              f"(J-0 = clôture, J-7 = ouverture).", ""]
    lines += [f"Poids du blend jamais réglé au préalable : predict.py fixe {BLEND_BASE:.0%} à J-1, "
              f"décroissance linéaire, 0 % à partir de J-5. Les barèmes alternatifs ci-dessous sont "
              f"réglés sur la VALIDATION seule ; le test ne sert qu'à mesurer.", ""]

    # Verdict
    lines += ["## Verdict", ""]
    c1 = max_viol["viol"] <= VIOL_TOL
    lines.append(f"- {'✅' if c1 else '❌'} FINAL jamais pire que le meilleur de "
                 f"{{marché vieilli, modèle}} : pire cas à J-{max_viol['age']} "
                 f"({max_viol['viol'] * 1000:+.1f} millièmes de Brier vs le meilleur des deux).")
    c2 = avg_final <= avg_fixed + VIOL_TOL and avg_final <= avg_oracle + 2e-3
    lines.append(f"- {'✅' if c2 else '❌'} Le decay actuel n'est pas battu nettement par un barème "
                 f"réglé sur validation : Brier moyen decay {avg_final:.5f} vs meilleur poids fixe "
                 f"{avg_fixed:.5f} (w={w_fixed:.2f}) vs oracle par âge {avg_oracle:.5f}.")
    if market_always_beats_model:
        lines.append("- ⚠️ Sur ce proxy, le marché vieilli bat le modèle pur à TOUS les âges testés "
                     "(même l'ouverture J-7) : le blend ne peut pas améliorer le Brier, il ne fait que "
                     "protéger contre une péremption plus sévère que l'ouverture — non capturée ici.")
    else:
        cross = next((a["age"] for a in per_age if a["market"] >= b_model_test), None)
        lines.append(f"- Le modèle pur (Brier {b_model_test:.5f}) devient meilleur que le marché "
                     f"vieilli à partir de J-{cross} : au-delà, couper le marché est justifié.")
    lines.append("")

    # Table principale
    lines += ["## Test : blend actuel par tranche d'âge des cotes", "",
              fmt_row(["Âge", "Poids marché", "Brier FINAL", "Brier marché vieilli",
                       "Brier modèle pur", "Meilleur des deux", "FINAL − meilleur"]),
              fmt_row(["---"] * 7)]
    for a in per_age:
        lines.append(fmt_row([f"J-{a['age']}", f"{a['w']:.0%}", f"{a['final']:.5f}",
                              f"{a['market']:.5f}", f"{b_model_test:.5f}", f"{a['best2']:.5f}",
                              f"{a['viol'] * 1000:+.1f} m‰"]))
    lines += ["", f"Référence marché frais (clôture, J-0) : Brier {b_fresh_test:.5f}. "
              f"« m‰ » = millièmes de Brier (plus bas = mieux ; positif = FINAL moins bon "
              f"que le meilleur des deux à cet âge).", ""]

    # Barèmes alternatifs
    lines += ["## Barèmes alternatifs (réglés sur validation, mesurés sur test)", "",
              fmt_row(["Âge", "Decay actuel (poids → Brier)", "Oracle validation (poids → Brier)",
                       f"Poids fixe w={w_fixed:.2f} → Brier"]),
              fmt_row(["---"] * 4)]
    for a in per_age:
        lines.append(fmt_row([f"J-{a['age']}",
                              f"{a['w']:.0%} → {a['final']:.5f}",
                              f"{a['oracle_w']:.0%} → {a['oracle_final']:.5f}",
                              f"{w_fixed:.0%} → {a['fixed_final']:.5f}"]))
    lines.append(fmt_row(["**Moyenne**", f"**{avg_final:.5f}**", f"**{avg_oracle:.5f}**",
                          f"**{avg_fixed:.5f}**"]))
    lines += ["", f"Marché vieilli moyen (aucun modèle) : {avg_market:.5f}. Modèle pur : "
              f"{b_model_test:.5f}.", ""]

    # Grid de validation (poids fixe)
    lines += ["## Grid search du poids fixe sur la validation (Brier moyen sur les âges)", "",
              fmt_row(["Poids marché w"] + [f"{w:.2f}" for w in W_GRID[::2]]),
              fmt_row(["---"] * (len(W_GRID[::2]) + 1))]
    grid_cells = ["Brier validation"]
    for w in W_GRID[::2]:
        v = float(np.mean([mean_brier_blend(val_recs, age, w) for age in AGES]))
        star = " ←" if abs(w - w_fixed) < 1e-9 else ""
        grid_cells.append(f"{v:.5f}{star}")
    lines.append(fmt_row(grid_cells))
    lines += ["", f"Couverture : {test_no} matchs de test écartés faute d'ouverture, "
              f"{test_noc} faute de clôture ; validation {val_no} sans ouverture.", ""]

    # Conclusion
    lines += ["## Conclusion — optimal ou plausible ?", ""]
    if market_always_beats_model:
        gain_fixed = (avg_final - avg_fixed) / avg_final * 100
        j5 = next(a for a in per_age if a["age"] == 5)
        lines += [
            f"Sous ce proxy de vieillissement, **le blend actuel est PLAUSIBLE, pas Brier-optimal**. "
            f"Le marché — même son ouverture (J-7) — reste plus précis que le modèle sur ces trois "
            f"ligues, donc tout poids modèle > 0 dégrade légèrement le Brier quand la cote existe. "
            f"Un poids fixe w={w_fixed:.2f} fait {abs(gain_fixed):.2f} % "
            f"{'mieux' if gain_fixed > 0 else 'moins bien'} en moyenne sur le test "
            f"({avg_fixed:.5f} vs {avg_final:.5f}).",
            "",
            "Deux composantes du barème coûtent du Brier ici, et il faut les distinguer :",
            "",
            f"1. **Le poids de base 65 %** (J-0/J-1) dilue une cote fraîche encore excellente : "
            f"+{per_age[0]['viol'] * 1000:.1f} m‰ à J-0. Sur cotes *vérifiées* fraîches, "
            f"il n'y a aucune raison Brier de descendre sous ~100 % marché.",
            f"2. **La coupure à J-5 (poids → 0, donc FINAL = modèle)** est en fait le point le PLUS "
            f"coûteux du barème : +{j5['viol'] * 1000:.1f} m‰, car le marché vieilli à J-5 "
            f"({j5['market']:.5f}) bat toujours le modèle ({b_model_test:.5f}). Sur ce proxy, couper "
            f"vers le modèle n'est jamais justifié — un plancher de poids marché > 0 dominerait le 0.",
            "",
            "Ce que le backtest NE peut PAS voir, et qui justifie malgré tout de garder un garde-fou : "
            "il compare deux vraies lignes de book (ouverture vs clôture), toutes deux sharp. Le "
            "garde-fou, lui, vise une cote **grattée sur le web, potentiellement mal recopiée, issue "
            "d'un book soft ou figée à J-3+** — strictement pire que l'ouverture, et non simulable "
            "avec football-data. Le decay échange donc un peu de Brier sur cotes fraîches contre une "
            "borne de sécurité quand la fraîcheur/qualité de la cote d'entrée n'est pas vérifiable.",
            "",
            "**Recommandation actionnable** (sans re-toucher au test) :",
            f"- cote *vérifiée* fraîche et de book sérieux → monter le poids marché à ~90–100 % "
            f"(le blend 65 % actuel laisse ~{per_age[0]['viol'] * 1000:.1f} m‰ sur la table) ;",
            "- remplacer la coupure sèche à J-5 par un **plancher** (p. ex. 20–30 % marché même "
            "au-delà de J-5) : garde le filet anti-péremption sans jeter une ligne encore informative ;",
            "- réserver le poids modèle élevé aux cas où la cote est absente, invérifiable ou "
            "signalée douteuse (marge aberrante) — c'est là son vrai rôle, et ce proxy ne peut pas "
            "le chiffrer.",
        ]
    else:
        lines += [
            f"Le decay est **cohérent avec les données** : le modèle dépasse le marché vieilli aux "
            f"âges élevés, et le blend suit ce croisement. Brier moyen decay {avg_final:.5f} vs "
            f"meilleur poids fixe {avg_fixed:.5f} — écart "
            f"{(avg_final - avg_fixed) / avg_final * 100:+.2f} %.",
        ]
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
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
