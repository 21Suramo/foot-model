"""Seed de team_aliases : nom Understat OU The Odds API -> nom canonique
football-data (une seule table, un seul namespace — les deux sources
utilisent presque toujours des variantes complètes/officielles du même nom
que football-data.co.uk abrège, donc peu de collisions possibles).

Seuls les noms qui diffèrent entre les sources sont listés ; un nom absent
de la table est utilisé tel quel. check.py liste les matchs xG non appariés
pour compléter cette table au besoin ; pour The Odds API, la comparaison
directe des noms renvoyés par `oddsapi.fetch` aux noms `matches` (mêmes
ligues/saisons) sert le même rôle (pas encore automatisée en check.py).
"""

SEED = {
    # Premier League (Understat)
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Newcastle United": "Newcastle",
    "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest": "Nott'm Forest",
    "West Bromwich Albion": "West Brom",
    "Queens Park Rangers": "QPR",
    # Premier League (The Odds API — noms différents de ceux d'Understat
    # ci-dessus pour les mêmes équipes, vérifiés par appel réel le 2026-09-10)
    "Brighton and Hove Albion": "Brighton",
    "Coventry City": "Coventry",
    "Hull City": "Hull",
    "Ipswich Town": "Ipswich",
    "Leeds United": "Leeds",
    "Tottenham Hotspur": "Tottenham",
    # Liga (Understat)
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
    "Deportivo La Coruna": "La Coruna",
    "Racing Santander": "Santander",
    # Liga (The Odds API — accents/variantes différents de ceux d'Understat
    # ci-dessus, vérifiés par appel réel le 2026-09-10)
    "Alavés": "Alaves",
    "Athletic Bilbao": "Ath Bilbao",
    "Atlético Madrid": "Ath Madrid",
    "CA Osasuna": "Osasuna",
    "Deportivo La Coruña": "La Coruna",
    "Elche CF": "Elche",
    "Málaga": "Malaga",
    "Real Racing Club de Santander": "Santander",
    # Ligue 1 (Understat)
    "Paris Saint Germain": "Paris SG",
    "Saint-Etienne": "St Etienne",
    "Clermont Foot": "Clermont",
    # Ligue 1 (The Odds API, vérifiés par appel réel le 2026-09-10)
    "AS Monaco": "Monaco",
    "Le Mans FC": "Le Mans",
    "RC Lens": "Lens",
}


def seed(conn):
    import db
    for alias, canonical in SEED.items():
        db.upsert_alias(conn, alias, canonical)
    conn.commit()
