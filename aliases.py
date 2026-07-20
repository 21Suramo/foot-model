"""Seed de team_aliases : nom Understat -> nom canonique football-data.

Seuls les noms qui diffèrent entre les deux sources sont listés ; un nom
absent de la table est utilisé tel quel. check.py liste les matchs xG non
appariés pour compléter cette table au besoin.
"""

SEED = {
    # Premier League
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Newcastle United": "Newcastle",
    "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest": "Nott'm Forest",
    "West Bromwich Albion": "West Brom",
    "Queens Park Rangers": "QPR",
    # Liga
    "Atletico Madrid": "Ath Madrid",
    "Athletic Club": "Ath Bilbao",
    "Real Sociedad": "Sociedad",
    "Real Betis": "Betis",
    "Celta Vigo": "Celta",
    "Espanyol": "Espanol",
    "Rayo Vallecano": "Vallecano",
    "SD Huesca": "Huesca",
    "Real Valladolid": "Valladolid",
    "Real Oviedo": "Oviedo",
    # Ligue 1
    "Paris Saint Germain": "Paris SG",
    "Saint-Etienne": "St Etienne",
    "Clermont Foot": "Clermont",
}


def seed(conn):
    import db
    for alias, canonical in SEED.items():
        db.upsert_alias(conn, alias, canonical)
    conn.commit()
