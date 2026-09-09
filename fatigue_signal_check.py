"""Chantier 3.2 — le signal fatigue/congestion existe-t-il ?

Avant d'investir dans une calibration (protocole tune/validation/test complet,
comme M3/M3.5), la roadmap demande de vérifier que le signal existe réellement
dans les données : les équipes à <=3 jours de repos sous-performent-elles
significativement par rapport à ce que le modèle M3.5 prédit déjà (sans
aucune notion de repos) ? Si non, ajouter une pénalité fatigue reviendrait à
figer du bruit.

Protocole : walk-forward hebdomadaire IDENTIQUE à backtest.walk_forward (même
garde anti-fuite — refit strict sur le passé), restreint à 1920+VALIDATION
(backtest.VALIDATION = 2020-21 + 2021-22 ; 1920 ajoutée pour la puissance
statistique, jamais le TEST), avec les réglages figés de production
(backtest35.frozen()). Pour chaque match, on calcule lambda_home/away du
modèle ET récupère les buts réels, puis on compare le résidu (buts réels -
lambda) par tranche de repos de l'équipe (jours écoulés depuis son match
précédent, calculé depuis matches — aucune nouvelle source).

Résultat (voir reports/fatigue_signal_check.md) : AUCUN écart significatif
détecté (|z| < 1 sur les 4 axes attaque/défense x domicile/extérieur,
~250 matchs à repos court sur 13 242 observations). Conclusion : le chantier
de calibration (pénalité offensive/défensive walk-forward) n'est PAS lancé —
il figerait du bruit. Ne pas re-lancer ce chantier sans nouvelle donnée
(davantage de saisons) ou nouvelle méthode de mesure.

Usage : python fatigue_signal_check.py [--db data/football.db]
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest35
import db
import footballdata
import model

log = logging.getLogger("fatigue_signal_check")

TARGET_SEASONS = ("1920",) + backtest.VALIDATION  # jamais le TEST ; 1920 pour la puissance
RESIDUAL_AXES = ("home_attack", "away_attack", "home_defense_conceded", "away_defense_conceded")


def rest_bucket(days):
    """<=3j = court (le seuil de la roadmap), 4-6j = normal, >=7j = long."""
    if days is None:
        return None
    if days <= 3:
        return "court (<=3j)"
    if days <= 6:
        return "normal (4-6j)"
    return "long (>=7j)"


def collect_residuals(conn, cfg):
    """Résidus (buts réels - λ) par tranche de repos, sur TARGET_SEASONS.

    Renvoie une liste de (bucket, axe, résidu). walk-forward strict : le repos
    d'une équipe pour le match de la semaine S n'utilise que des matchs déjà
    joués AVANT le lundi de S (même passé que le fit lui-même)."""
    records = []
    for league in footballdata.LEAGUES:
        rows = backtest.load_league(conn, league)
        last_match_date = {}

        weeks = {}
        for r in rows:
            if r["season"] in TARGET_SEASONS:
                weeks.setdefault(backtest.monday_of(r["date"]), []).append(r)

        fitted = None
        train = []
        i = 0
        for monday in sorted(weeks):
            cutoff = monday.isoformat()
            while i < len(rows) and rows[i]["date"] < cutoff:
                r = rows[i]
                last_match_date[r["home"]] = r["date"]
                last_match_date[r["away"]] = r["date"]
                train.append(r)
                i += 1
            fitted = model.fit(train, xi=cfg["xi"], ref_date=monday, warm_start=fitted,
                               xg_weight=cfg["w"], prior_weight=cfg["kappa"])
            for r in weeks[monday]:
                lam_h, lam_a = fitted.lambdas(r["home"], r["away"])
                d_h, d_a = last_match_date.get(r["home"]), last_match_date.get(r["away"])
                rest_h = (np.datetime64(r["date"]) - np.datetime64(d_h)).astype(int) if d_h else None
                rest_a = (np.datetime64(r["date"]) - np.datetime64(d_a)).astype(int) if d_a else None
                b_h, b_a = rest_bucket(rest_h), rest_bucket(rest_a)
                if b_h:
                    records.append((b_h, "home_attack", r["fthg"] - lam_h))
                    records.append((b_h, "home_defense_conceded", r["ftag"] - lam_a))
                if b_a:
                    records.append((b_a, "away_attack", r["ftag"] - lam_a))
                    records.append((b_a, "away_defense_conceded", r["fthg"] - lam_h))
            # les matchs de cette semaine ne comptent comme "joués" qu'APRÈS
            # avoir servi au calcul de repos ci-dessus (walk-forward strict)
            for r in weeks[monday]:
                last_match_date[r["home"]] = r["date"]
                last_match_date[r["away"]] = r["date"]
    return records


def summarize(records):
    """Moyenne/IC95%/z-test (court vs normal+long) par axe. Renvoie (texte, significatif)."""
    lines = [f"{len(records)} observations (home+away x attaque/défense) "
             f"sur {'+'.join(TARGET_SEASONS)} (jamais le TEST).\n"]
    any_significant = False
    for axis in RESIDUAL_AXES:
        lines.append(f"### {axis} (résidu = buts réels - λ modèle M3.5)\n")
        by_bucket = {}
        for b, a, resid in records:
            if a == axis:
                by_bucket.setdefault(b, []).append(resid)
        for b in ("court (<=3j)", "normal (4-6j)", "long (>=7j)"):
            vals = by_bucket.get(b, [])
            if not vals:
                continue
            arr = np.array(vals)
            se = arr.std(ddof=1) / np.sqrt(len(arr))
            lines.append(f"- {b:16s} n={len(arr):5d}  moyenne={arr.mean():+.4f}  "
                         f"±{1.96 * se:.4f} (IC95%)")
        court = np.array(by_bucket.get("court (<=3j)", []))
        reste = np.array(by_bucket.get("normal (4-6j)", []) + by_bucket.get("long (>=7j)", []))
        if len(court) > 5 and len(reste) > 5:
            diff = court.mean() - reste.mean()
            se_diff = np.sqrt(court.var(ddof=1) / len(court) + reste.var(ddof=1) / len(reste))
            z = diff / se_diff if se_diff > 0 else 0.0
            sig = abs(z) > 1.96
            any_significant = any_significant or sig
            lines.append(f"- écart court - reste = {diff:+.4f}  (z={z:+.2f}, "
                         f"{'SIGNIFICATIF' if sig else 'non significatif'})\n")
    verdict = ("Signal détecté sur au moins un axe — la calibration walk-forward "
               "peut être envisagée (tune/validation/test, jamais un patch direct)."
               if any_significant else
               "Aucun écart significatif détecté : ne pas construire la calibration "
               "fatigue — elle figerait du bruit.")
    lines.append(f"## Verdict\n\n{verdict}\n")
    return "\n".join(lines), any_significant


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    conn = db.connect(args.db)
    cfg = backtest35.frozen()
    records = collect_residuals(conn, cfg)
    conn.close()

    text, significant = summarize(records)
    print(text)
    out = Path("reports/fatigue_signal_check.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# Chantier 3.2 — signal fatigue/congestion : existe-t-il ?\n\n" + text)
    print(f"\nRapport écrit dans {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
