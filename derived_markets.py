"""Marchés dérivés de la grille de score Dixon-Coles (roadmap "profit durable",
Phase A1 : "tous les marchés secondaires sont une simple somme sur cette
grille").

Fonctions pures, grille numpy en entrée : aucune dépendance sur le fit du
modèle, la base SQLite, ou les réglages figés. Utilisé par backtest_derived.py
(validation) et predict.py (production).

⚠ EXTENDED_MAX_GOALS (grille 12×12) ne touche JAMAIS model.MAX_GOALS (7×7) :
le 1N2 et sa recalibration M3.5 restent calculés sur la grille 7×7 déjà
backtestée et publiée (reports/m3_backtest.md, reports/m35_backtest.md) —
l'élargir changerait légèrement leurs probas (moins de masse renormalisée
depuis la queue tronquée) et ferait dériver des résultats déjà lus. Les
marchés dérivés qui bénéficient d'une queue plus précise (O/U hauts,
handicaps, totaux par équipe) utilisent EXTENDED_MAX_GOALS sur une grille
CALCULÉE SÉPARÉMENT (model.DixonColes.score_grid(..., max_goals=...)),
jamais testée avant ce chantier — élargir la base AVANT de lire un test
n'est pas le re-réglage que le protocole interdit.
"""
import numpy as np

EXTENDED_MAX_GOALS = 11  # grille 12×12, réservée aux marchés dérivés (jamais au 1N2)


def over_under_mask(max_goals, line):
    """Masque booléen (max_goals+1)² : total de buts > line (line typiquement
    à .5 pour éviter tout push — 0.5, 1.5, 2.5, 3.5, 4.5)."""
    goals = np.arange(max_goals + 1)
    total = np.add.outer(goals, goals)
    return total > line


def btts_mask(max_goals):
    """Masque booléen : les deux équipes marquent (h > 0 et a > 0)."""
    mask = np.zeros((max_goals + 1, max_goals + 1), dtype=bool)
    mask[1:, 1:] = True
    return mask


def team_total_mask(max_goals, side, line):
    """Masque booléen : buts de l'équipe `side` ('home' ou 'away') > line."""
    goals = np.arange(max_goals + 1)
    if side == "home":
        return np.broadcast_to(goals[:, None] > line, (max_goals + 1, max_goals + 1))
    if side == "away":
        return np.broadcast_to(goals[None, :] > line, (max_goals + 1, max_goals + 1))
    raise ValueError(f"side inconnu : {side!r} (attendu 'home' ou 'away')")


def asian_handicap_outcome(fthg, ftag, line):
    """'home' / 'away' / 'push' pour un résultat réel et une ligne de handicap
    (ajoutée au score domicile : ligne négative = domicile favori).

    Sur une ligne quart (ex. -0.25, -0.75) la marge ajustée n'est jamais
    exactement nulle : le push n'existe mathématiquement que sur les lignes
    entières (0, ±1, ±2...), conformément à la convention du marché."""
    adjusted = (fthg - ftag) + line
    if abs(adjusted) < 1e-9:
        return "push"
    return "home" if adjusted > 0 else "away"


def asian_handicap_probs(grid, line):
    """(P(domicile couvre), P(extérieur couvre), P(push)) pour une grille de
    score (n'importe quelle taille) et une ligne de handicap donnée."""
    n_h, n_a = grid.shape
    h = np.arange(n_h)[:, None]
    a = np.arange(n_a)[None, :]
    adjusted = (h - a) + line
    push = np.abs(adjusted) < 1e-9
    home_covers = (adjusted > 0) & ~push
    away_covers = (adjusted < 0) & ~push
    return (float(grid[home_covers].sum()), float(grid[away_covers].sum()),
            float(grid[push].sum()))


def topk_scores(grid, k):
    """Les k scores exacts les plus probables de la grille, par proba décroissante."""
    flat_order = np.argsort(grid, axis=None)[::-1][:k]
    return [tuple(int(x) for x in np.unravel_index(i, grid.shape)) for i in flat_order]


def topk_hit(grid, fthg, ftag, k):
    """Le score réel figure-t-il dans le top-k de la grille ? False si le score
    réel dépasse la troncature de la grille (jamais dans aucun top-k tronqué)."""
    n_h, n_a = grid.shape
    if fthg >= n_h or ftag >= n_a:
        return False
    return (fthg, ftag) in set(topk_scores(grid, k))
