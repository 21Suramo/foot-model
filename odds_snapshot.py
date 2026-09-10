"""Roadmap A2 — capture un snapshot de cotes multi-books -> table book_odds.

Nécessite ODDS_API_KEY dans l'environnement (jamais dans un fichier committé,
cf. oddsapi.py et CLAUDE.md). Coût : 1 crédit par (ligue × région × marché) —
défaut 3 ligues × regions=eu × markets=h2h = 3 crédits/snapshot. Free tier =
500 crédits/mois -> ~160 snapshots/mois en h2h seul.

Ne planifie rien lui-même (pas de cron dans ce script) : à lancer à la main,
ou câblé dans .github/workflows/weekly.yml si l'utilisateur ajoute
ODDS_API_KEY comme secret du dépôt — décision volontairement pas prise ici.

Usage : python odds_snapshot.py [--markets h2h,totals] [--regions eu,uk] [--db data/football.db]
"""
import argparse
import datetime
import logging
import sys

import db
import oddsapi

log = logging.getLogger("odds_snapshot")


def run(conn, markets=oddsapi.DEFAULT_MARKETS, regions=oddsapi.DEFAULT_REGIONS):
    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    total = 0
    for league in oddsapi.SPORT_KEYS:
        raw = oddsapi.fetch(league, regions=regions, markets=markets)
        rows = oddsapi.parse_snapshot(league, raw, fetched_at)
        for row in rows:
            db.insert_book_odds(conn, row)
        conn.commit()
        log.info("%s : %d matchs à venir, %d lignes de cotes capturées.",
                 league, len(raw), len(rows))
        total += len(rows)
    log.info("%d lignes écrites dans book_odds (fetched_at=%s).", total, fetched_at)
    return total


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markets", default=oddsapi.DEFAULT_MARKETS,
                        help="marchés API séparés par virgule (ex. h2h,totals) — "
                             "coût = nb marchés x nb régions x 3 ligues")
    parser.add_argument("--regions", default=oddsapi.DEFAULT_REGIONS)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    try:
        oddsapi.api_key()
    except RuntimeError as e:
        sys.exit(str(e))
    conn = db.connect(args.db)
    run(conn, args.markets, args.regions)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
