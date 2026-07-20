"""Validation de la base : comptages, doublons, complétude, xG non appariées.

Usage : python check.py [--db data/football.db]
Code retour 0 si tous les critères passent, 1 sinon.
"""
import argparse
import sys

import db
import footballdata

# Nombre de matchs attendus par saison. Écarts connus :
# - Ligue 1 à 18 clubs depuis 2023-24 -> 306 matchs
# - Ligue 1 2019-20 arrêtée (COVID) après 279 matchs
EXPECTED = {}
for _s in footballdata.SEASONS:
    EXPECTED[("E0", _s)] = 380
    EXPECTED[("SP1", _s)] = 380
    EXPECTED[("F1", _s)] = 306 if _s in ("2324", "2425", "2526") else 380
EXPECTED[("F1", "1920")] = 279

CLOSING_SOURCES = ("pinnacle_close", "avg_close")
CLOSING_FROM_SEASON = "1920"  # football-data n'a pas de cotes de clôture avant 2019-20


def check(conn):
    ok = True
    current = footballdata.CURRENT_SEASON

    print("=== Comptages par ligue/saison ===")
    print(f"{'ligue':<6}{'saison':<8}{'matchs':>7}{'attendu':>9}  statut")
    counts = {(r["league"], r["season"]): r["n"] for r in conn.execute(
        "SELECT league, season, COUNT(*) n FROM matches GROUP BY league, season")}
    for league in footballdata.LEAGUES:
        for season in footballdata.SEASONS:
            n = counts.get((league, season), 0)
            exp = EXPECTED[(league, season)]
            if season == current:
                status = f"en cours ({n}/{exp})"
            elif n == exp:
                status = "OK"
            else:
                status = f"ÉCART ({n - exp:+d})"
                ok = False
            print(f"{league:<6}{season:<8}{n:>7}{exp:>9}  {status}")

    print("\n=== Doublons (date+home+away) ===")
    dups = conn.execute(
        "SELECT date, home, away, COUNT(*) n FROM matches GROUP BY date, home, away HAVING n > 1"
    ).fetchall()
    if dups:
        ok = False
        for d in dups:
            print(f"DOUBLON : {d['date']} {d['home']} vs {d['away']} x{d['n']}")
    else:
        print("aucun")

    print("\n=== Cotes de clôture 1N2 (critère ≥99%, saisons 2019-20+) ===")
    for r in conn.execute(
        "SELECT league, season, COUNT(*) n, "
        "SUM(odds_h IS NOT NULL) with_odds, "
        f"SUM(odds_source IN {CLOSING_SOURCES}) closing "
        "FROM matches GROUP BY league, season ORDER BY league, season"):
        pct_close = 100 * r["closing"] / r["n"] if r["n"] else 0
        pct_any = 100 * r["with_odds"] / r["n"] if r["n"] else 0
        if r["season"] < CLOSING_FROM_SEASON:
            note = f"(pas de clôture avant 2019-20 ; ouverture : {pct_any:.1f}%)"
            if pct_any < 99:
                note += " ÉCART"
                ok = False
        elif pct_close >= 99:
            note = "OK"
        else:
            note = "ÉCART"
            ok = False
        print(f"{r['league']:<6}{r['season']:<8}clôture {pct_close:5.1f}%  {note}")

    print("\n=== Complétude par colonne (% non NULL, tous matchs) ===")
    cols = ["odds_h", "ou25_over", "ah_line", "xg_home", "fthg", "hthg"]
    sums = ", ".join(f"SUM({c} IS NOT NULL) {c}" for c in cols)
    print(f"{'ligue':<6}{'saison':<8}" + "".join(f"{c:>11}" for c in cols))
    for r in conn.execute(
        f"SELECT league, season, COUNT(*) n, {sums} FROM matches "
        "GROUP BY league, season ORDER BY league, season"):
        line = f"{r['league']:<6}{r['season']:<8}"
        for c in cols:
            line += f"{100 * r[c] / r['n']:>10.1f}%"
        print(line)

    print("\n=== xG (critère ≥95% des matchs joués) ===")
    missing_total = 0
    for r in conn.execute(
        "SELECT league, season, COUNT(*) n, SUM(xg_home IS NOT NULL) with_xg "
        "FROM matches WHERE fthg IS NOT NULL GROUP BY league, season ORDER BY league, season"):
        pct = 100 * r["with_xg"] / r["n"] if r["n"] else 0
        status = "OK" if pct >= 95 else "ÉCART"
        if pct < 95 and r["season"] != current:
            ok = False
        missing_total += r["n"] - r["with_xg"]
        print(f"{r['league']:<6}{r['season']:<8}xG {pct:5.1f}%  {status}")

    if missing_total:
        print(f"\n--- {missing_total} matchs joués sans xG ---")
        print("Équipes les plus fréquentes (candidates pour team_aliases) :")
        for r in conn.execute(
            "SELECT team, COUNT(*) n FROM ("
            "  SELECT home team FROM matches WHERE fthg IS NOT NULL AND xg_home IS NULL"
            "  UNION ALL"
            "  SELECT away FROM matches WHERE fthg IS NOT NULL AND xg_home IS NULL"
            ") GROUP BY team ORDER BY n DESC LIMIT 15"):
            print(f"  {r['team']:<25}{r['n']:>4} matchs")
        print("Détail (20 premiers) :")
        for r in conn.execute(
            "SELECT date, league, home, away FROM matches "
            "WHERE fthg IS NOT NULL AND xg_home IS NULL ORDER BY date LIMIT 20"):
            print(f"  {r['date']} {r['league']} {r['home']} vs {r['away']}")

    print(f"\n=== Résultat global : {'OK' if ok else 'ÉCHEC'} ===")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    ok = check(conn)
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
