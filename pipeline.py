"""Pipeline de données football : football-data.co.uk + Understat -> SQLite.

Usage :
    python pipeline.py --update [--league E0] [--season 2324] [--force]
"""
import argparse
import datetime
import logging
import sys

import aliases
import db
import footballdata
import understat
import xgjoin

log = logging.getLogger("pipeline")

# Garde anti-stagnation (audit 2026-09-21) : si la saison en cours n'a pas la
# moindre nouvelle date en base après un run, alors que la dernière date
# connue remonte à plus de STALE_DAYS_THRESHOLD jours, quelque chose ne
# tourne pas rond (source sans nouvelle donnée depuis longtemps, ou
# régression du pipeline) — un cas qu'on veut voir, pas laisser filer en
# silence semaine après semaine. Ne se déclenche PAS si le run a fait
# avancer MAX(date) ne serait-ce que d'un jour, même si le résultat reste
# encore vieux de plus de 5 jours (retard de publication connu de la
# source, déjà documenté ailleurs) : ce n'est pas de la stagnation, c'est
# une progression normale contrainte par la source.
STALE_DAYS_THRESHOLD = 5


def _max_date(conn, league, season):
    row = conn.execute(
        "SELECT MAX(date) FROM matches WHERE league = ? AND season = ?", (league, season)
    ).fetchone()
    return row[0] if row else None


def update_league_season(conn, league, season, force=False):
    """Met à jour une (ligue, saison) : résultats/cotes football-data puis xG.

    Retourne un dict de statistiques d'observabilité (lignes lues/insérées/
    mises à jour/ignorées, MAX(date) de la saison après update), ou None si
    le CSV football-data est indisponible (aucune tentative de mise à jour)."""
    # 1. Résultats + cotes football-data
    path = footballdata.fetch(league, season, force=force)
    if path is None:
        log.error("%s %s : CSV football-data indisponible, saison ignorée", league, season)
        return None
    rows = footballdata.parse_csv(path, league, season)
    upsert_cols = [c for c in db.MATCH_COLS if c not in ("date", "home", "away")]
    inserted = updated = ignored = 0
    for row in rows:
        existing = conn.execute(
            f"SELECT {', '.join(upsert_cols)} FROM matches "
            "WHERE date = :date AND home = :home AND away = :away",
            row,
        ).fetchone()
        if existing is None:
            inserted += 1
        elif all(existing[c] == row.get(c) for c in upsert_cols):
            ignored += 1
        else:
            updated += 1
        db.upsert_match(conn, row)
    conn.commit()
    max_date = _max_date(conn, league, season)
    stats = {"read": len(rows), "inserted": inserted, "updated": updated,
              "ignored": ignored, "max_date": max_date}
    log.info("%s %s : %d lignes lues, %d insérées, %d mises à jour, %d ignorées, MAX(date)=%s",
              league, season, stats["read"], inserted, updated, ignored, max_date)

    # 2. xG Understat
    data = understat.fetch(league, season, footballdata.CURRENT_SEASON, force=force)
    if data is None:
        log.warning("%s %s : xG Understat indisponibles", league, season)
        return stats
    us_matches = understat.parse_matches(data)
    alias_map = db.load_aliases(conn)
    matched, unmatched = xgjoin.join_xg(conn, us_matches, alias_map)
    conn.commit()
    log.info("%s %s : xG jointes sur %d/%d matchs Understat", league, season, matched, len(us_matches))
    for m in unmatched:
        log.warning("xG non appariée : %s | %s vs %s (noms Understat)", m["date"], m["home"], m["away"])
    return stats


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
    max_date_before = {
        league: _max_date(conn, league, footballdata.CURRENT_SEASON)
        for league in leagues if footballdata.CURRENT_SEASON in seasons
    }
    for league in leagues:
        for season in seasons:
            update_league_season(conn, league, season, force=args.force)

    stagnant = []
    if footballdata.CURRENT_SEASON in seasons:
        today = datetime.date.today()
        for league in leagues:
            after = _max_date(conn, league, footballdata.CURRENT_SEASON)
            if after is None or after != max_date_before.get(league):
                continue  # pas de donnée, ou ça a avancé : rien à signaler
            days = (today - datetime.date.fromisoformat(after)).days
            if days > STALE_DAYS_THRESHOLD:
                stagnant.append((league, after, days))
    conn.close()

    if stagnant:
        for league, max_date, days in stagnant:
            log.error("%s : MAX(date)=%s (%d j) n'a pas avancé pour la saison en cours %s — "
                      "vérifier la source football-data.co.uk ou le pipeline",
                      league, max_date, days, footballdata.CURRENT_SEASON)
        return 1
    log.info("Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
