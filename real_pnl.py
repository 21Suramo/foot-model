"""Suivi du P&L réel sur 1xbet — LECTURE SEULE, jamais écrit par predict.py.

`data/real_bets.json` est un journal tenu à la main (décision du 2026-09-13,
CLAUDE.md « Suivi du P&L réel ») : le journal de production (predict.py)
reste 100 % théorique (les mises n'y sont jamais réellement placées) ;
`real_bets.json` est la seule trace de ce que l'utilisateur a RÉELLEMENT
posé sur 1xbet, ajoutée à la main après chaque pari. Ce script ne modifie
jamais ce fichier — il le lit et publie un résumé.

Ne JAMAIS mélanger ce rapport à `reports/production_calibration.md` (ROI
théorique du modèle) : les deux mesurent des choses différentes, l'un un
modèle, l'autre un P&L réel avec slippage d'exécution.

Schéma d'une entrée de `data/real_bets.json` :
{
  "date_placed": "YYYY-MM-DD", "match": "Home-Away", "competition": "E0|SP1|F1",
  "issue": "home|draw|away", "journal_odds": <cote vue dans le journal>,
  "taken_odds_1xbet": <cote réellement obtenue sur 1xbet>,
  "stake_eur": <montant réellement misé, en euros>,
  "result": "pending|win|loss|void", "notes": "optionnel"
}

Usage : python real_pnl.py [--bets data/real_bets.json] [--out reports/real_pnl.md]
"""
import argparse
import json
from pathlib import Path

DEFAULT_BETS_PATH = Path("data/real_bets.json")
DEFAULT_OUT_PATH = Path("reports/real_pnl.md")
MIN_BETS_FOR_ROI = 20  # même seuil indicatif que CLV_MIN_BETS (predict.py) —
                        # pas un critère statistique, juste un garde-fou de lecture.


def load_bets(path):
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text())


def pnl_eur(bet):
    """P&L réel d'un pari réglé, en euros. None pour un pari 'pending'."""
    result = bet.get("result", "pending")
    if result == "win":
        return bet["stake_eur"] * (bet["taken_odds_1xbet"] - 1.0)
    if result == "loss":
        return -bet["stake_eur"]
    if result == "void":
        return 0.0
    return None


def slippage(bet):
    """(cote_journal / cote_prise_1xbet − 1) : positif = la cote obtenue sur
    1xbet était MOINS bonne que celle vue dans le journal au moment du pari.
    None si l'une des deux cotes manque (ex. pari passé en pur modèle)."""
    journal_odds = bet.get("journal_odds")
    taken = bet.get("taken_odds_1xbet")
    if not journal_odds or not taken:
        return None
    return journal_odds / taken - 1.0


def summarize(bets):
    """Statistiques agrégées : P&L réel, ROI réel, slippage moyen, comptage
    par statut. Les paris 'void' (remboursés) sont comptés mais exclus du
    P&L/ROI (mise rendue, ni gain ni perte) ; les 'pending' sont exclus des
    deux (résultat inconnu)."""
    by_status = {"pending": 0, "win": 0, "loss": 0, "void": 0}
    pnl_total = 0.0
    stake_settled = 0.0
    slippages = []
    for bet in bets:
        status = bet.get("result", "pending")
        by_status[status] = by_status.get(status, 0) + 1
        if status in ("win", "loss"):
            pnl_total += pnl_eur(bet)
            stake_settled += bet["stake_eur"]
        s = slippage(bet)
        if s is not None:
            slippages.append(s)
    n_win_loss = by_status["win"] + by_status["loss"]
    roi_pct = (pnl_total / stake_settled * 100) if stake_settled else None
    avg_slippage = (sum(slippages) / len(slippages)) if slippages else None
    return {
        "n_total": len(bets), "n_win_loss": n_win_loss, "by_status": by_status,
        "pnl_eur": pnl_total, "roi_pct": roi_pct,
        "avg_slippage": avg_slippage, "n_slippage": len(slippages),
    }


def build_report(bets):
    s = summarize(bets)
    lines = ["# Suivi du P&L réel (1xbet)", "",
             "Lecture de `data/real_bets.json` (tenu à la main) — jamais mélangé "
             "au ROI théorique de `reports/production_calibration.md` (le journal "
             "de production reste 100 % théorique, séparation actée le 2026-09-13).",
             ""]
    if not bets:
        lines += ["Aucun pari réel enregistré pour l'instant — "
                  "`data/real_bets.json` est vide.", ""]
        return "\n".join(lines), s

    lines.append(
        f"- {s['n_total']} pari(s) au total — {s['by_status']['win']} gagné(s), "
        f"{s['by_status']['loss']} perdu(s), {s['by_status']['void']} annulé(s)/"
        f"remboursé(s), {s['by_status']['pending']} en attente.")

    if s["n_win_loss"]:
        lines.append(
            f"- P&L réel cumulé : {s['pnl_eur']:+.2f} € — ROI réel {s['roi_pct']:+.1f} % "
            f"sur {s['n_win_loss']} pari(s) réglé(s) gagné/perdu (annulés et en attente "
            f"exclus du calcul).")
        if s["n_win_loss"] < MIN_BETS_FOR_ROI:
            lines.append(
                f"  ⚠ {s['n_win_loss']} pari(s) (< {MIN_BETS_FOR_ROI}) : lecture indicative, "
                f"pas de conclusion sur un échantillon aussi petit.")
    else:
        lines.append("- Aucun pari gagné/perdu encore réglé : P&L et ROI pas encore calculables.")

    if s["n_slippage"]:
        lines.append(
            f"- Slippage moyen cote journal → cote 1xbet : {s['avg_slippage']:+.2%} "
            f"sur {s['n_slippage']} pari(s) (positif = la cote 1xbet obtenue était "
            f"moins bonne que celle vue dans le journal au moment du pari).")
    else:
        lines.append("- Slippage non calculable (aucun pari n'a les deux cotes renseignées).")
    lines.append("")
    return "\n".join(lines), s


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bets", default=DEFAULT_BETS_PATH, help="Journal réel (défaut : %(default)s)")
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help="Rapport markdown (défaut : %(default)s)")
    args = parser.parse_args(argv)

    bets = load_bets(args.bets)
    text, _ = build_report(bets)
    print(text)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"\nRapport écrit dans {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
