"""Pipeline de données football : football-data.co.uk + Understat -> SQLite.

Usage :
    python pipeline.py --update [--league E0] [--season 2324] [--force]
"""
import argparse
import logging
import sys

import aliases
import db
import footballdata
import understat
import xgjoin

log = logging.getLogger("pipeline")


def update_league_season(conn, league, season, force=False):
    # 1. Résultats + cotes football-data
    path = footballdata.fetch(league, season, force=force)
    if path is None:
        log.error("%s %s : CSV football-data indisponible, saison ignorée", league, season)
        return
    rows = footballdata.parse_csv(path, league, season)
    for row in rows:
        db.upsert_match(conn, row)
    conn.commit()
    log.info("%s %s : %d matchs upsertés", league, season, len(rows))

    # 2. xG Understat
    data = understat.fetch(league, season, footballdata.CURRENT_SEASON, force=force)
    if data is None:
        log.warning("%s %s : xG Understat indisponibles", league, season)
        return
    us_matches = understat.parse_matches(data)
    alias_map = db.load_aliases(conn)
    matched, unmatched = xgjoin.join_xg(conn, us_matches, alias_map)
    conn.commit()
    log.info("%s %s : xG jointes sur %d/%d matchs Understat", league, season, matched, len(us_matches))
    for m in unmatched:
        log.warning("xG non appariée : %s | %s vs %s (noms Understat)", m["date"], m["home"], m["away"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="télécharge et met à jour la base")
    parser.add_argument("--league", choices=footballdata.LEAGUES, help="restreindre à une ligue")
    parser.add_argument("--season", choices=footballdata.SEASONS, help="restreindre à une saison (ex: 2324)")
    parser.add_argument("--force", action="store_true", help="ignorer le cache et re-télécharger")
    parser.add_argument("--db", default=str(db.DB_PATH), help="chemin de la base SQLite")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")

    if not args.update:
        parser.print_help()
        return 1

    leagues = [args.league] if args.league else footballdata.LEAGUES
    seasons = [args.season] if args.season else footballdata.SEASONS

    conn = db.connect(args.db)
    aliases.seed(conn)
    for league in leagues:
        for season in seasons:
            update_league_season(conn, league, season, force=args.force)
    conn.close()
    log.info("Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
