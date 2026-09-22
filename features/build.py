"""Assemble la table de features ML en UNE SEULE passe chronologique.

Garde anti-fuite : pour chaque match, les features sont extraites de l'état
des trackers (Elo, pi-rating, repos, forme) AVANT qu'ils ne soient mis à
jour avec le résultat de ce match. C'est la même discipline que
model.fit(ref_date=...) dans model.py — ici appliquée à la main plutôt que
déléguée à scipy, puisque les trackers sont des mises à jour incrémentales
et non un ré-ajustement global.

Pool GLOBAL inter-ligues (cf. features/elo.py) : les matchs de TOUTES les
ligues demandées (par défaut footballdata.ALL_LEAGUES, top + secondaires)
sont traités dans un seul ordre chronologique (date, puis match_id en
départage), pas ligue par ligue — une équipe reléguée/promue garde ainsi
son rating en changeant de championnat.
"""
import datetime

import numpy as np

import footballdata
from features.elo import EloRatings
from features.form import FormTracker, make_record
from features.pi_rating import PiRatings
from features.rest import RestTracker


def _load_rows(conn, leagues=None, upto_date=None):
    leagues = leagues if leagues is not None else footballdata.ALL_LEAGUES
    placeholders = ",".join("?" * len(leagues))
    sql = (f"SELECT match_id, date, league, season, home, away, fthg, ftag, "
           f"xg_home, xg_away, shots_h, shots_a, sot_h, sot_a, corners_h, corners_a, "
           f"odds_h, odds_d, odds_a "
           f"FROM matches WHERE league IN ({placeholders}) AND fthg IS NOT NULL")
    params = list(leagues)
    if upto_date is not None:
        sql += " AND date < ?"
        params.append(upto_date)
    sql += " ORDER BY date, match_id"
    return [dict(r) for r in conn.execute(sql, params)]


def _outcome(fthg, ftag):
    return 0 if fthg > ftag else (1 if fthg == ftag else 2)


def _extract_features(home, away, match_date_str, elo, pi, rest, form):
    match_date = datetime.date.fromisoformat(match_date_str)
    feats = {}

    elo_h, elo_a = elo.get(home), elo.get(away)
    feats["elo_home"] = elo_h
    feats["elo_away"] = elo_a
    feats["elo_diff"] = elo_h - elo_a

    rh_home, ra_home = pi.get(home)
    rh_away, ra_away = pi.get(away)
    feats["pi_home_attack"] = rh_home    # rating "domicile" de l'équipe à domicile
    feats["pi_home_defend"] = ra_home    # rating "extérieur" de l'équipe à domicile (transfert)
    feats["pi_away_attack"] = ra_away    # rating "extérieur" de l'équipe à l'extérieur
    feats["pi_away_defend"] = rh_away    # rating "domicile" de l'équipe à l'extérieur (transfert)
    feats["pi_diff"] = rh_home - ra_away

    rest_h = rest.get(home, match_date)
    rest_a = rest.get(away, match_date)
    feats["rest_days_home"] = rest_h
    feats["rest_days_away"] = rest_a
    feats["rest_diff"] = rest_h - rest_a

    for k, v in form.get(home).items():
        feats[f"home_{k}"] = v
    for k, v in form.get(away).items():
        feats[f"away_{k}"] = v

    return feats


def _update_state(r, elo, pi, rest, form):
    home, away = r["home"], r["away"]
    fthg, ftag = r["fthg"], r["ftag"]
    match_date = datetime.date.fromisoformat(r["date"])

    elo.update(home, away, fthg, ftag)
    pi.update(home, away, fthg, ftag)
    rest.update(home, match_date)
    rest.update(away, match_date)
    form.update(home, make_record(
        True, fthg, ftag, r.get("xg_home"), r.get("xg_away"),
        r.get("shots_h"), r.get("shots_a"), r.get("sot_h"), r.get("sot_a"),
        r.get("corners_h"), r.get("corners_a")))
    form.update(away, make_record(
        False, ftag, fthg, r.get("xg_away"), r.get("xg_home"),
        r.get("shots_a"), r.get("shots_h"), r.get("sot_a"), r.get("sot_h"),
        r.get("corners_a"), r.get("corners_h")))


def _new_trackers():
    return EloRatings(), PiRatings(), RestTracker(), FormTracker()


def build_feature_table(conn, leagues=None, upto_date=None):
    """Table complète, une ligne par match joué, features + cible.

    Colonnes de cible/métadonnées : match_id, league, season, date, home,
    away, fthg, ftag, outcome (0=domicile,1=nul,2=extérieur), xg_home,
    xg_away (None si absent), odds_h/d/a (None si absent).
    """
    rows = _load_rows(conn, leagues, upto_date)
    elo, pi, rest, form = _new_trackers()
    out = []
    for r in rows:
        feats = _extract_features(r["home"], r["away"], r["date"], elo, pi, rest, form)
        feats.update({
            "match_id": r["match_id"], "league": r["league"], "season": r["season"],
            "date": r["date"], "home": r["home"], "away": r["away"],
            "fthg": r["fthg"], "ftag": r["ftag"],
            "outcome": _outcome(r["fthg"], r["ftag"]),
            "xg_home": r.get("xg_home"), "xg_away": r.get("xg_away"),
            "odds_h": r.get("odds_h"), "odds_d": r.get("odds_d"), "odds_a": r.get("odds_a"),
        })
        out.append(feats)
        _update_state(r, elo, pi, rest, form)
    return out


def state_asof(conn, ref_date, leagues=None):
    """Rejoue l'historique STRICTEMENT antérieur à ref_date (ISO). Retourne
    les trackers pour lire des features d'un match pas encore joué via
    features_for_fixture()."""
    rows = _load_rows(conn, leagues, upto_date=ref_date)
    elo, pi, rest, form = _new_trackers()
    for r in rows:
        _update_state(r, elo, pi, rest, form)
    return elo, pi, rest, form


def features_for_fixture(trackers, home, away, match_date_str):
    """Features d'un match pas encore joué, à partir de trackers déjà
    rejoués jusqu'à (strictement avant) sa date via state_asof()."""
    elo, pi, rest, form = trackers
    return _extract_features(home, away, match_date_str, elo, pi, rest, form)


FEATURE_COLUMNS = None  # calculé paresseusement par ml_model.py (dépend des WINDOWS de form.py)


def numeric_feature_names(sample_row):
    """Noms des colonnes de features (tout sauf les métadonnées/cible),
    déterminés à partir d'une ligne de build_feature_table (toutes les
    lignes ont les mêmes clés)."""
    meta = {"match_id", "league", "season", "date", "home", "away", "fthg", "ftag",
            "outcome", "xg_home", "xg_away", "odds_h", "odds_d", "odds_a"}
    return [k for k in sample_row if k not in meta]
