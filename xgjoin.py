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
    tolérance ±2 jours (les deux sources divergent parfois de un à deux
    jours ; pas de collision possible, une même affiche ne se rejoue
    jamais à 2 jours d'écart).

    Repli sur l'orientation home/away INVERSÉE (xg_home/xg_away permutés en
    conséquence) si l'orientation directe échoue sur les ±2 jours : les deux
    sources désignent parfois différemment le « domicile » d'une affiche
    relocalisée (terrain indisponible — vu en pratique sur Rennes-Paris SG
    du 2026-08-23, joué à Roazhon Park mais Understat le liste comme
    Paris Saint Germain à domicile). Pas une supposition sur l'identité des
    équipes (les alias restent la seule source de vérité pour les noms) :
    seule l'étiquette domicile/extérieur diffère, et `matches.home`/`away`
    (autorité football-data.co.uk) n'est jamais modifiée — seules les
    colonnes xg_home/xg_away le sont, donc chaque équipe reçoit bien SON xG
    quelle que soit l'orientation retenue côté Understat. Un même couple
    d'équipes ne se rejouant jamais deux fois à ±2 jours d'écart, aucune
    ambiguïté possible entre l'orientation directe et l'inversée.

    Retourne (nb_apparies, liste_non_apparies) ; nb_apparies inclut les
    appariements par orientation inversée (comptés séparément dans les logs).
    """
    matched = 0
    unmatched = []
    for m in us_matches:
        home = aliases.get(m["home"], m["home"])
        away = aliases.get(m["away"], m["away"])
        found = False
        for delta in (0, -1, 1, -2, 2):
            date = _shift(m["date"], delta) if delta else m["date"]
            if db.update_xg(conn, date, home, away, m["xg_home"], m["xg_away"]):
                matched += 1
                found = True
                break
        if found:
            continue
        for delta in (0, -1, 1, -2, 2):
            date = _shift(m["date"], delta) if delta else m["date"]
            if db.update_xg(conn, date, away, home, m["xg_away"], m["xg_home"]):
                log.info("xG appariée par orientation domicile/extérieur inversée : "
                         "%s | %s vs %s (Understat) -> %s vs %s (matches)",
                         date, m["home"], m["away"], away, home)
                matched += 1
                found = True
                break
        if not found:
            unmatched.append(m)
    return matched, unmatched
