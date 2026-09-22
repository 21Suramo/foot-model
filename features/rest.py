"""Jours de repos depuis le dernier match d'une équipe (charge de calendrier).

⚠ Limite assumée (même constat que M8/fatigue_signal_check.py, cf.
CLAUDE.md) : seuls les matchs de championnat des ligues suivies par ce
projet comptent — aucune donnée de coupe nationale ni de compétition
européenne n'est ingérée. Une équipe qui a joué un match de coupe la veille
apparaîtra donc à tort comme "bien reposée". Ce n'est pas une régression
par rapport à l'existant : fatigue_signal_check.py documente déjà cette
même limite pour le diagnostic fatigue (M8, source calendrier investiguée
mais pas construite, cf. CLAUDE.md).
"""
DEFAULT_REST_DAYS = 14  # valeur neutre pour le tout premier match d'une équipe dans le dataset


class RestTracker:
    """État mutable : date du dernier match joué par équipe."""

    def __init__(self, default_days=DEFAULT_REST_DAYS):
        self.default_days = default_days
        self.last_played = {}

    def get(self, team, match_date):
        """Jours de repos avant `match_date` (date de match strictement future
        par rapport à tout ce qui a déjà été enregistré, garanti par
        l'appelant — features/build.py traite les matchs en ordre chronologique)."""
        last = self.last_played.get(team)
        if last is None:
            return self.default_days
        return (match_date - last).days

    def update(self, team, match_date):
        self.last_played[team] = match_date
