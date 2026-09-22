"""Rating Elo dynamique par équipe, mis à jour match par match.

Pool GLOBAL (pas un pool par ligue) : une équipe reléguée/promue garde son
rating en changeant de championnat plutôt que de repartir de zéro — utile
puisque le projet n'a aucune donnée de coupe/Europe pour calibrer un écart
de niveau entre divisions autrement. C'est un choix délibéré, pas un oubli :
voir features/build.py pour le tri chronologique global qui le permet.

Sans donnée d'avant le début du dataset, une équipe inconnue démarre à
DEFAULT_RATING (1500, convention Elo standard).
"""
K_FACTOR = 20.0
HOME_ADVANTAGE = 60.0  # points Elo ajoutés à l'équipe à domicile pour l'espérance
DEFAULT_RATING = 1500.0


def expected_score(rating_a, rating_b):
    """Probabilité que 'a' batte 'b' (échelle logistique Elo standard)."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


class EloRatings:
    """État mutable des ratings Elo, mis à jour match après match.

    get(team) ne modifie jamais l'état (lecture pure) : à appeler AVANT
    update() pour obtenir les ratings pré-match (garde anti-fuite).
    """

    def __init__(self, k=K_FACTOR, home_advantage=HOME_ADVANTAGE, default=DEFAULT_RATING):
        self.k = k
        self.home_advantage = home_advantage
        self.default = default
        self.ratings = {}

    def get(self, team):
        return self.ratings.get(team, self.default)

    def update(self, home, away, fthg, ftag):
        """Met à jour les deux ratings après un match. Retourne (elo_home_pre, elo_away_pre)."""
        r_home, r_away = self.get(home), self.get(away)
        if fthg > ftag:
            s_home = 1.0
        elif fthg == ftag:
            s_home = 0.5
        else:
            s_home = 0.0
        e_home = expected_score(r_home + self.home_advantage, r_away)
        delta = self.k * (s_home - e_home)
        self.ratings[home] = r_home + delta
        self.ratings[away] = r_away - delta
        return r_home, r_away
