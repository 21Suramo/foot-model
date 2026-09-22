"""Moyennes glissantes de forme (buts, xG, tirs, tirs cadrés, corners).

⚠ football-data.co.uk ne publie PAS la possession (vérifié sur les CSV top
ET secondaires, cf. footballdata.STAT_COLS) — ce n'est pas un oubli de ce
module, la possession est absente de toutes les features de forme.

FormTracker garde, par équipe, l'historique chronologique COMPLET des
matchs déjà traités (perspective de cette équipe : gf/ga = buts pour/contre,
etc.), jamais le match en cours. `get(team)` est une lecture pure sur cet
historique ; `update(team, record)` doit être appelé APRÈS avoir lu les
deux équipes du match courant (même discipline que EloRatings/PiRatings/
RestTracker — orchestrée par features/build.py).
"""
from collections import defaultdict

import numpy as np

WINDOWS = (3, 5, 10)
STATS = ("gf", "ga", "xgf", "xga", "sf", "sa", "sotf", "sota", "cf", "ca")


class FormTracker:
    def __init__(self, windows=WINDOWS):
        self.windows = windows
        self.history = defaultdict(list)  # team -> [ {is_home, gf, ga, xgf, xga, sf, sa, sotf, sota, cf, ca}, ... ]

    def update(self, team, record):
        self.history[team].append(record)

    def get(self, team):
        """dict de features pour cette équipe, calculées sur son historique
        PRÉ-match (déjà garanti par l'ordre d'appel). Clés :
        form_{stat}_{window}_overall, form_{stat}_{window}_home,
        form_{stat}_{window}_away, form_{stat}_{window}_diff_home_away
        (NaN si l'un des deux membres du diff manque de données)."""
        hist = self.history.get(team, [])
        out = {}
        for w in self.windows:
            recent = hist[-w:]
            home_recent = [r for r in hist if r["is_home"]][-w:]
            away_recent = [r for r in hist if not r["is_home"]][-w:]
            for stat in STATS:
                out[f"form_{stat}_{w}_overall"] = _nanmean(recent, stat)
                h = _nanmean(home_recent, stat)
                a = _nanmean(away_recent, stat)
                out[f"form_{stat}_{w}_home"] = h
                out[f"form_{stat}_{w}_away"] = a
                out[f"form_{stat}_{w}_diff_home_away"] = (
                    h - a if not (np.isnan(h) or np.isnan(a)) else np.nan
                )
        return out


def _nanmean(records, stat):
    vals = [v for v in (r.get(stat) for r in records) if v is not None and not np.isnan(v)]
    if not vals:
        return np.nan
    return float(np.mean(vals))


def make_record(is_home, gf, ga, xgf, xga, sf, sa, sotf, sota, cf, ca):
    return {"is_home": is_home, "gf": gf, "ga": ga, "xgf": xgf, "xga": xga,
            "sf": sf, "sa": sa, "sotf": sotf, "sota": sota, "cf": cf, "ca": ca}
