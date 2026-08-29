"""Téléchargement (avec cache) et parsing des CSV football-data.co.uk."""
import logging
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("pipeline")

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
RAW_DIR = Path("data/raw/football-data")
LEAGUES = ["E0", "SP1", "F1"]
SEASONS = ["1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526", "2627"]
CURRENT_SEASON = "2627"
REQUEST_DELAY = 1.5  # secondes entre deux téléchargements
STALE_AFTER = 24 * 3600  # saison en cours : re-télécharger si le cache a plus de 24 h
USER_AGENT = "foot-model-pipeline/0.1 (usage personnel et non commercial)"

# Chaînes de priorité par ligne : Pinnacle clôture > moyenne clôture > ouverture.
# Les fichiers d'avant 2019-20 n'ont pas de cotes de clôture et nomment
# la moyenne du marché "BbAv*" au lieu de "Avg*".
ODDS_1X2 = [
    (("PSCH", "PSCD", "PSCA"), "pinnacle_close"),
    (("AvgCH", "AvgCD", "AvgCA"), "avg_close"),
    (("PSH", "PSD", "PSA"), "pinnacle_open"),
    (("AvgH", "AvgD", "AvgA"), "avg_open"),
    (("BbAvH", "BbAvD", "BbAvA"), "avg_open"),
]
ODDS_OU25 = [
    ("PC>2.5", "PC<2.5"),
    ("AvgC>2.5", "AvgC<2.5"),
    ("P>2.5", "P<2.5"),
    ("Avg>2.5", "Avg<2.5"),
    ("BbAv>2.5", "BbAv<2.5"),
]
AH_LINE = ["AHCh", "AHh", "BbAHh"]
ODDS_AH = [
    ("PCAHH", "PCAHA"),
    ("AvgCAHH", "AvgCAHA"),
    ("PAHH", "PAHA"),
    ("AvgAHH", "AvgAHA"),
    ("BbAvAHH", "BbAvAHA"),
]

_last_request_ts = 0.0


def _throttle():
    global _last_request_ts
    wait = _last_request_ts + REQUEST_DELAY - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def raw_path(league, season):
    return RAW_DIR / f"{season}_{league}.csv"


def fetch(league, season, force=False):
    """Télécharge le CSV si absent (ou périmé pour la saison en cours).

    Retourne le chemin du fichier local, ou None si le téléchargement échoue
    et qu'aucun cache n'existe.
    """
    path = raw_path(league, season)
    if path.exists() and not force:
        fresh = season != CURRENT_SEASON or time.time() - path.stat().st_mtime < STALE_AFTER
        if fresh:
            log.info("[SKIP] %s %s : cache %s", league, season, path)
            return path

    url = BASE_URL.format(season=season, league=league)
    _throttle()
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as exc:
        if path.exists():
            log.warning("[ERR] %s : %s — on garde le cache existant", url, exc)
            return path
        log.error("[ERR] %s : %s", url, exc)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    log.info("[DL] %s -> %s (%d octets)", url, path, len(resp.content))
    return path


def _num(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_1x2(row):
    for (ch, cd, ca), source in ODDS_1X2:
        h, d, a = _num(row.get(ch)), _num(row.get(cd)), _num(row.get(ca))
        if h is not None and d is not None and a is not None:
            return h, d, a, source
    return None, None, None, None


def _pick_pair(row, chain):
    for c1, c2 in chain:
        v1, v2 = _num(row.get(c1)), _num(row.get(c2))
        if v1 is not None and v2 is not None:
            return v1, v2
    return None, None


def _pick_line(row, cols):
    for c in cols:
        v = _num(row.get(c))
        if v is not None:
            return v
    return None


def parse_csv(path, league, season):
    """Parse un CSV football-data en liste de dicts prêts pour db.upsert_match."""
    df = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
    dates = pd.to_datetime(df["Date"], format="mixed", dayfirst=True)

    rows = []
    for (_, row), date in zip(df.iterrows(), dates):
        odds_h, odds_d, odds_a, odds_source = _pick_1x2(row)
        ou_over, ou_under = _pick_pair(row, ODDS_OU25)
        ah_home, ah_away = _pick_pair(row, ODDS_AH)
        fthg = _num(row.get("FTHG"))
        ftag = _num(row.get("FTAG"))
        hthg = _num(row.get("HTHG"))
        htag = _num(row.get("HTAG"))
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "league": league,
            "season": season,
            "home": str(row["HomeTeam"]).strip(),
            "away": str(row["AwayTeam"]).strip(),
            "fthg": int(fthg) if fthg is not None else None,
            "ftag": int(ftag) if ftag is not None else None,
            "hthg": int(hthg) if hthg is not None else None,
            "htag": int(htag) if htag is not None else None,
            "odds_h": odds_h,
            "odds_d": odds_d,
            "odds_a": odds_a,
            "odds_source": odds_source,
            "ou25_over": ou_over,
            "ou25_under": ou_under,
            "ah_line": _pick_line(row, AH_LINE),
            "ah_home": ah_home,
            "ah_away": ah_away,
        })
    return rows
