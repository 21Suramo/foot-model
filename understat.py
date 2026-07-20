"""Téléchargement (avec cache) et parsing des xG Understat.

Understat sert les matchs via l'endpoint JSON getLeagueData/{ligue}/{année}
(clé "dates"), qui exige l'en-tête X-Requested-With: XMLHttpRequest. En repli,
les anciennes pages ligue/saison embarquaient un `var datesData =
JSON.parse('...')` échappé en \\xNN qu'on sait toujours extraire. Le JSON est
mis en cache dans data/raw/understat/.
"""
import json
import logging
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("pipeline")

RAW_DIR = Path("data/raw/understat")
API_URL = "https://understat.com/getLeagueData/{league}/{year}"
PAGE_URL = "https://understat.com/league/{league}/{year}"
LEAGUE_MAP = {"E0": "EPL", "SP1": "La_liga", "F1": "Ligue_1"}
REQUEST_DELAY = 1.5
STALE_AFTER = 24 * 3600
USER_AGENT = "foot-model-pipeline/0.1 (usage personnel et non commercial)"

_DATES_DATA_RE = re.compile(r"datesData\s*=\s*JSON\.parse\('(.*?)'\)", re.DOTALL)

_last_request_ts = 0.0


def _throttle():
    global _last_request_ts
    wait = _last_request_ts + REQUEST_DELAY - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def season_to_year(season):
    """'1819' -> 2018 (Understat indexe la saison par son année de départ)."""
    return 2000 + int(season[:2])


def is_current(season, current_season):
    return season == current_season


def extract_dates_data(html):
    """Extrait et décode le JSON datesData d'une page ligue Understat."""
    m = _DATES_DATA_RE.search(html)
    if not m:
        raise ValueError("datesData introuvable dans la page")
    raw = m.group(1)
    # Les \xNN sont des octets UTF-8 échappés : unicode_escape puis
    # réinterprétation latin-1 -> utf-8 restitue les accents.
    decoded = raw.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    return json.loads(decoded)


def raw_path(understat_league, year):
    return RAW_DIR / f"{understat_league}_{year}.json"


def _download(us_league, year):
    """Télécharge les matchs d'une ligue/saison : endpoint JSON, puis page HTML en repli."""
    url = API_URL.format(league=us_league, year=year)
    _throttle()
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",  # sans lui, l'endpoint répond 404
    })
    if resp.ok:
        payload = resp.json()
        dates = payload.get("dates")
        if not isinstance(dates, list):
            raise ValueError(f"clé 'dates' absente de la réponse {url}")
        return dates

    log.warning("[ERR] %s : HTTP %d — repli sur la page HTML", url, resp.status_code)
    url = PAGE_URL.format(league=us_league, year=year)
    _throttle()
    resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return extract_dates_data(resp.text)


def fetch(league, season, current_season, force=False):
    """Récupère le JSON des matchs Understat pour une ligue/saison.

    Retourne la liste des matchs (dicts Understat), ou None si
    indisponible (ni réseau ni cache).
    """
    us_league = LEAGUE_MAP[league]
    year = season_to_year(season)
    path = raw_path(us_league, year)

    if path.exists() and not force:
        fresh = not is_current(season, current_season) or time.time() - path.stat().st_mtime < STALE_AFTER
        if fresh:
            log.info("[SKIP] xG %s %s : cache %s", league, season, path)
            return json.loads(path.read_text())

    url = API_URL.format(league=us_league, year=year)
    try:
        data = _download(us_league, year)
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        if path.exists():
            log.warning("[ERR] %s : %s — on garde le cache existant", url, exc)
            return json.loads(path.read_text())
        log.error("[ERR] %s : %s", url, exc)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    log.info("[DL] %s -> %s (%d matchs)", url, path, len(data))
    return data


def parse_matches(data):
    """Aplati le JSON Understat en dicts {date, home, away, xg_home, xg_away}.

    Ne garde que les matchs joués (isResult) avec des xG renseignés.
    """
    out = []
    for m in data:
        if not m.get("isResult"):
            continue
        xg = m.get("xG") or {}
        if xg.get("h") is None or xg.get("a") is None:
            continue
        out.append({
            "date": m["datetime"][:10],
            "home": m["h"]["title"],
            "away": m["a"]["title"],
            "xg_home": float(xg["h"]),
            "xg_away": float(xg["a"]),
        })
    return out
