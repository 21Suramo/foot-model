"""Roadmap A2 — cotes multi-books via The Odds API (the-odds-api.com).

⚠ Clé API lue UNIQUEMENT depuis la variable d'environnement ODDS_API_KEY —
jamais en dur dans le code, jamais dans un fichier committé (secret
personnel, quota mensuel limité). Voir CLAUDE.md pour comment la définir.

Couverture vérifiée par appel réel le 2026-09-10 (pas une donnée de blog) :
`regions=eu` seul suffit à couvrir Pinnacle (référence sharp du projet, cf.
`backtest.demargin_shin`/CLV) ET plusieurs books retail pertinents pour un
utilisateur français (winamax_fr, betclic_fr, pmu_fr), sur les 3 ligues du
projet (E0/SP1/F1). Coût observé : 1 crédit par (ligue × région × marché
demandé) — ex. 3 ligues × regions=eu × markets=h2h = 3 crédits par
snapshot. Free tier = 500 crédits/mois -> ~160 snapshots/mois en h2h seul,
~80 en h2h+totals. `/v4/sports/` (liste des ligues) est gratuit.

Ce module ne fait QUE parler à l'API et parser sa réponse — la capture
planifiée (cadence, budget de crédits) est le rôle de `odds_snapshot.py`.
"""
import logging
import os

import requests

log = logging.getLogger("oddsapi")

BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
API_KEY_ENV = "ODDS_API_KEY"

# Ligue interne (convention du projet, cf. footballdata.LEAGUES) -> sport_key
# de l'API, vérifié par appel réel à /v4/sports/ le 2026-09-10.
SPORT_KEYS = {
    "E0": "soccer_epl",
    "SP1": "soccer_spain_la_liga",
    "F1": "soccer_france_ligue_one",
}

DEFAULT_REGIONS = "eu"     # Pinnacle + winamax_fr + betclic_fr + pmu_fr déjà couverts ici
DEFAULT_MARKETS = "h2h"    # ajouter "totals" double le coût en crédits


def api_key():
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(f"{API_KEY_ENV} non défini dans l'environnement — la clé ne "
                           f"doit JAMAIS être écrite en dur dans le code ni committée. "
                           f"Voir CLAUDE.md pour comment la définir.")
    return key


def fetch(league, regions=DEFAULT_REGIONS, markets=DEFAULT_MARKETS, timeout=30):
    """Snapshot brut (JSON de l'API, liste d'événements) pour une ligue interne."""
    sport = SPORT_KEYS[league]
    resp = requests.get(BASE_URL.format(sport=sport), params={
        "apiKey": api_key(), "regions": regions, "markets": markets, "oddsFormat": "decimal",
    }, timeout=timeout)
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        log.info("%s : %s crédits API restants ce mois-ci.", league, remaining)
    return resp.json()


def parse_snapshot(league, raw, fetched_at):
    """JSON brut (liste d'événements) -> lignes prêtes pour db.insert_book_odds.

    h2h : le nom d'issue de l'API est le nom d'équipe ou "Draw" — remappé en
    home/draw/away par comparaison littérale à home_team/away_team de
    l'événement (les deux viennent du même appel, donc du même référentiel de
    noms — pas de résolution d'alias nécessaire ICI, seulement au moment de
    croiser avec football-data.co.uk plus tard).
    totals : l'issue est "Over"/"Under", avec une ligne (`point`) explicite.
    """
    rows = []
    for event in raw:
        home, away = event["home_team"], event["away_team"]
        for bk in event.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for oc in mk.get("outcomes", []):
                    if mk["key"] == "h2h":
                        if oc["name"] == home:
                            outcome = "home"
                        elif oc["name"] == away:
                            outcome = "away"
                        else:
                            outcome = "draw"
                        point = None
                    else:
                        outcome = oc["name"]
                        point = oc.get("point")
                    rows.append({
                        "fetched_at": fetched_at, "league": league,
                        "commence_time": event["commence_time"],
                        "home": home, "away": away,
                        "book": bk["key"], "market": mk["key"],
                        "outcome": outcome, "point": point, "price": oc["price"],
                    })
    return rows
