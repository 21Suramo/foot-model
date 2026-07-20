"""Jointure des xG Understat sur la table matches."""
import datetime
import logging

import db

log = logging.getLogger("pipeline")


def _shift(date_str, days):
    d = datetime.date.fromisoformat(date_str) + datetime.timedelta(days=days)
    return d.isoformat()


def join_xg(conn, us_matches, aliases):
    """Pose les xG sur les matchs existants.

    Appariement sur (date, home, away) après résolution d'alias, avec
    tolérance ±1 jour (les deux sources divergent parfois d'un jour).
    Retourne (nb_apparies, liste_non_apparies).
    """
    matched = 0
    unmatched = []
    for m in us_matches:
        home = aliases.get(m["home"], m["home"])
        away = aliases.get(m["away"], m["away"])
        for delta in (0, -1, 1):
            date = _shift(m["date"], delta) if delta else m["date"]
            if db.update_xg(conn, date, home, away, m["xg_home"], m["xg_away"]):
                matched += 1
                break
        else:
            unmatched.append(m)
    return matched, unmatched
