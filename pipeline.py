"""Pipeline de données football : football-data.co.uk + Understat -> SQLite.

Usage :
    python pipeline.py --update [--league E0] [--season 2324] [--force]
"""
import argparse
import datetime
import logging
import sys

import aliases
import check
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
#
# Deux pauses sont NORMALES et ne doivent jamais déclencher cette garde
# (revue 2026-09-21) :
# - intersaison : la saison en cours est déjà complète (autant de matchs en
#   base que check.EXPECTED en attend) — aucune nouvelle date ne viendra
#   plus jamais pour ce code de saison, ce n'est pas un pipeline bloqué ;
# - pause internationale (trêves FIFA, ~10-14 jours plusieurs fois par
#   saison) : le seuil fixe de 5 jours ne les distingue pas d'une vraie
#   panne. On compare donc l'écart courant au plus grand écart déjà observé
#   EN COURS DE SAISON (jamais à cheval sur un changement de saison) sur la
#   dernière saison complète de cette ligue, et on ne s'alarme que si
#   l'écart courant dépasse aussi ce précédent historique.
STALE_DAYS_THRESHOLD = 5


def _max_date(conn, league, season):
    row = conn.execute(
        "SELECT MAX(date) FROM matches WHERE league = ? AND season = ?", (league, season)
    ).fetchone()
    return row[0] if row else None


def _season_complete(conn, league, season):
    """True si la saison a déjà tous ses matchs en base (intersaison) —
    check.EXPECTED est la même source de vérité que check.py, pas une
    deuxième liste à tenir à jour en parallèle."""
    expected = check.EXPECTED.get((league, season))
    if expected is None:
        return False
    n = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE league = ? AND season = ?", (league, season)
    ).fetchone()[0]
    return n >= expected


def _previous_season(season):
    idx = footballdata.SEASONS.index(season)
    return footballdata.SEASONS[idx - 1] if idx > 0 else None


def _historical_max_gap(conn, league, season):
    """Plus grand écart (jours) entre deux dates de matchs CONSÉCUTIVES
    d'une même saison déjà complète — jamais à cheval sur deux saisons
    (l'écart d'intersaison, plusieurs mois, n'a rien à voir avec une pause
    internationale en cours de saison). 0 si la saison est absente/trop
    courte en base pour donner un écart."""
    rows = conn.execute(
        "SELECT DISTINCT date FROM matches WHERE league = ? AND season = ? ORDER BY date",
        (league, season),
    ).fetchall()
    dates = [datetime.date.fromisoformat(r[0]) for r in rows]
    if len(dates) < 2:
        return 0
    return max((b - a).days for a, b in zip(dates, dates[1:]))


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
            if _season_complete(conn, league, footballdata.CURRENT_SEASON):
                continue  # intersaison : plus aucune date à attendre pour cette saison
            days = (today - datetime.date.fromisoformat(after)).days
            prev_season = _previous_season(footballdata.CURRENT_SEASON)
            historical_gap = _historical_max_gap(conn, league, prev_season) if prev_season else 0
            threshold = max(STALE_DAYS_THRESHOLD, historical_gap)
            if days > threshold:
                stagnant.append((league, after, days, threshold))
    conn.close()

    if stagnant:
        for league, max_date, days, threshold in stagnant:
            log.error("%s : MAX(date)=%s (%d j, seuil %d j) n'a pas avancé pour la saison en "
                      "cours %s — vérifier la source football-data.co.uk ou le pipeline",
                      league, max_date, days, threshold, footballdata.CURRENT_SEASON)
        return 1
    log.info("Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
