"""Pi-ratings (Constantinou & Fenwick 2008), version simplifiée.

Contrairement à l'Elo (un seul rating, mis à jour sur le résultat W/D/L),
le pi-rating garde DEUX ratings par équipe — un rating "domicile" et un
rating "extérieur" — et se met à jour sur l'ÉCART DE BUTS plutôt que le
seul résultat, avec un transfert partiel entre les deux ratings d'une même
équipe (jouer fort à domicile déteint un peu sur le rating extérieur, et
inversement).

⚠ Simplification assumée, pas une reproduction fidèle du papier original :
la mise à jour ici ne touche que les ratings des deux équipes du match
(pas de propagation vers l'adversaire au-delà de son propre match), et les
constantes (LEARNING_RATE, TRANSFER_RATE) sont des valeurs de départ
usuelles de la littérature, PAS réglées par validation sur ce dataset —
même statut que CORRELATED_EXPOSURE_MULTIPLIER dans predict.py (une
heuristique assumée, jamais présentée comme un chiffre mesuré). Si un
chantier futur veut les régler, le faire avec le même protocole
tune/validation/test que M3.5 plutôt qu'à la main.
"""
import math

LEARNING_RATE = 0.06
TRANSFER_RATE = 0.3     # part de la mise à jour qui déteint sur l'autre rating de l'équipe
DEFAULT_RATING = 0.0    # 0 = équipe de force moyenne, l'échelle est symétrique autour de 0


def _g(rating):
    """Rating -> écart de buts attendu (fonction non linéaire du papier original)."""
    sign = 1.0 if rating >= 0 else -1.0
    return sign * (10.0 ** (abs(rating) / 3.0) - 1.0)


def _g_inv(goal_diff):
    """Écart de buts -> unités de rating (inverse de _g)."""
    sign = 1.0 if goal_diff >= 0 else -1.0
    return sign * 3.0 * math.log10(1.0 + abs(goal_diff))


class PiRatings:
    """État mutable : deux ratings (home/away) par équipe."""

    def __init__(self, learning_rate=LEARNING_RATE, transfer_rate=TRANSFER_RATE,
                 default=DEFAULT_RATING):
        self.lr = learning_rate
        self.gamma = transfer_rate
        self.default = default
        self.home_rating = {}
        self.away_rating = {}

    def get(self, team):
        """(rating_domicile, rating_extérieur) de l'équipe, lecture pure."""
        return self.home_rating.get(team, self.default), self.away_rating.get(team, self.default)

    def update(self, home, away, fthg, ftag):
        """Met à jour les 4 ratings impliqués. Retourne les 4 ratings PRÉ-match
        (rh_home, ra_home, rh_away, ra_away) où rh_home = rating domicile de
        l'équipe à domicile, ra_home = son rating extérieur, etc."""
        rh_home, ra_home = self.get(home)
        rh_away, ra_away = self.get(away)

        predicted_gd = _g(rh_home) - _g(ra_away)
        actual_gd = float(fthg - ftag)
        error = _g_inv(actual_gd) - _g_inv(predicted_gd)

        self.home_rating[home] = rh_home + self.lr * error
        self.away_rating[home] = ra_home + self.lr * self.gamma * error
        self.away_rating[away] = ra_away - self.lr * error
        self.home_rating[away] = rh_away - self.lr * self.gamma * error

        return rh_home, ra_home, rh_away, ra_away
