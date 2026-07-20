"""Schéma SQLite et upserts idempotents pour le pipeline."""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/football.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    league      TEXT NOT NULL,
    season      TEXT NOT NULL,
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    fthg        INTEGER,
    ftag        INTEGER,
    hthg        INTEGER,
    htag        INTEGER,
    odds_h      REAL,
    odds_d      REAL,
    odds_a      REAL,
    odds_source TEXT,
    ou25_over   REAL,
    ou25_under  REAL,
    ah_line     REAL,
    ah_home     REAL,
    ah_away     REAL,
    xg_home     REAL,
    xg_away     REAL,
    UNIQUE (date, home, away)
);

CREATE INDEX IF NOT EXISTS idx_matches_league_season ON matches(league, season);

CREATE TABLE IF NOT EXISTS team_aliases (
    alias     TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);
"""

MATCH_COLS = [
    "date", "league", "season", "home", "away",
    "fthg", "ftag", "hthg", "htag",
    "odds_h", "odds_d", "odds_a", "odds_source",
    "ou25_over", "ou25_under", "ah_line", "ah_home", "ah_away",
]


def connect(db_path=DB_PATH):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_match(conn, row):
    """Insère ou met à jour un match (clé date+home+away).

    Ne touche pas aux colonnes xG : elles sont posées par update_xg
    et un refresh des CSV ne doit pas les effacer.
    """
    values = {c: row.get(c) for c in MATCH_COLS}
    updates = ", ".join(f"{c}=excluded.{c}" for c in MATCH_COLS if c not in ("date", "home", "away"))
    sql = (
        f"INSERT INTO matches ({', '.join(MATCH_COLS)}) "
        f"VALUES ({', '.join(':' + c for c in MATCH_COLS)}) "
        f"ON CONFLICT(date, home, away) DO UPDATE SET {updates}"
    )
    conn.execute(sql, values)


def update_xg(conn, date, home, away, xg_home, xg_away):
    """Pose les xG sur un match existant. Retourne True si un match a été touché."""
    cur = conn.execute(
        "UPDATE matches SET xg_home = ?, xg_away = ? WHERE date = ? AND home = ? AND away = ?",
        (xg_home, xg_away, date, home, away),
    )
    return cur.rowcount > 0


def upsert_alias(conn, alias, canonical):
    conn.execute(
        "INSERT INTO team_aliases (alias, canonical) VALUES (?, ?) "
        "ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical",
        (alias, canonical),
    )


def load_aliases(conn):
    return {r["alias"]: r["canonical"] for r in conn.execute("SELECT alias, canonical FROM team_aliases")}
