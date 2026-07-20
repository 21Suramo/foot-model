"""Modèle Dixon-Coles (1997), un modèle par ligue.

Paramètres : force d'attaque α_i et de défense β_i par équipe, avantage
domicile γ global, correction rho des scores fermés (0-0, 1-0, 0-1, 1-1).
λ_home = α_home × β_away × γ ; λ_away = α_away × β_home.

Estimation par maximum de vraisemblance pondérée (scipy L-BFGS-B, gradient
analytique) avec :
- poids temporels exp(-ξ × jours_écoulés) — ξ est réglé par validation dans
  backtest.py, jamais estimé ici ;
- pénalité ridge sur les log-forces (prior gaussien centré sur la moyenne de
  la ligue) : les équipes promues/nouvelles sont rétrécies vers la moyenne,
  le prior s'efface quand l'historique s'accumule ;
- identifiabilité : après le fit, renormalisation exacte à moyenne(α) = 1
  (transformation α/s, β×s qui laisse les λ invariants).
"""
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

MAX_GOALS = 6          # grille de scores 0-0 à 6-6, renormalisée
RHO_BOUNDS = (-0.2, 0.2)
PRIOR_WEIGHT = 2.0     # ridge κ, ~3 matchs d'évidence ; fixe, jamais réglé sur le test
_TAU_FLOOR = 1e-10


def _nll_grad(theta, n, h_idx, a_idx, hg, ag, w, kappa):
    """NLL pondérée Dixon-Coles + ridge, et son gradient analytique.

    theta = [log α (n), log β (n), log γ, rho].
    """
    la, lb = theta[:n], theta[n:2 * n]
    lg, rho = theta[2 * n], theta[2 * n + 1]

    log_lh = la[h_idx] + lb[a_idx] + lg
    log_la = la[a_idx] + lb[h_idx]
    lam_h, lam_a = np.exp(log_lh), np.exp(log_la)

    # Correction tau sur les scores fermés et ses dérivées partielles
    tau = np.ones_like(lam_h)
    dt_dlh = np.zeros_like(lam_h)   # dtau/dλ_home
    dt_dla = np.zeros_like(lam_h)   # dtau/dλ_away
    dt_drho = np.zeros_like(lam_h)
    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)
    tau[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    dt_dlh[m00] = -lam_a[m00] * rho
    dt_dla[m00] = -lam_h[m00] * rho
    dt_drho[m00] = -lam_h[m00] * lam_a[m00]
    tau[m01] = 1.0 + lam_h[m01] * rho
    dt_dlh[m01] = rho
    dt_drho[m01] = lam_h[m01]
    tau[m10] = 1.0 + lam_a[m10] * rho
    dt_dla[m10] = rho
    dt_drho[m10] = lam_a[m10]
    tau[m11] = 1.0 - rho
    dt_drho[m11] = -1.0
    tau = np.maximum(tau, _TAU_FLOOR)

    ll = (np.log(tau)
          + hg * log_lh - lam_h - gammaln(hg + 1.0)
          + ag * log_la - lam_a - gammaln(ag + 1.0))
    nll = -(w * ll).sum() + kappa * (la @ la + lb @ lb)

    # d ll / d log λ = buts - λ + (λ/τ) dτ/dλ
    gh = w * (hg - lam_h + lam_h * dt_dlh / tau)
    ga = w * (ag - lam_a + lam_a * dt_dla / tau)
    g_la = np.zeros(n)
    g_lb = np.zeros(n)
    np.add.at(g_la, h_idx, gh)
    np.add.at(g_la, a_idx, ga)
    np.add.at(g_lb, a_idx, gh)
    np.add.at(g_lb, h_idx, ga)
    grad = np.concatenate([
        -g_la + 2.0 * kappa * la,
        -g_lb + 2.0 * kappa * lb,
        [-gh.sum()],
        [-(w * dt_drho / tau).sum()],
    ])
    return nll, grad


class DixonColes:
    """Modèle ajusté : λ, grille de scores et probas 1N2 pour un match donné.

    Une équipe inconnue du fit reçoit les forces moyennes de la ligue
    (α = moyenne(α) = 1, β = moyenne(β)) — cohérent avec le shrinkage.
    """

    def __init__(self, teams, log_attack, log_defense, log_gamma, rho):
        self.teams = list(teams)
        self.index = {t: i for i, t in enumerate(self.teams)}
        self.log_attack = log_attack
        self.log_defense = log_defense
        self.log_gamma = log_gamma
        self.rho = rho
        self._la_default = float(np.log(np.mean(np.exp(log_attack)))) if len(teams) else 0.0
        self._lb_default = float(np.log(np.mean(np.exp(log_defense)))) if len(teams) else 0.0

    @property
    def gamma(self):
        return float(np.exp(self.log_gamma))

    def _params(self, team):
        i = self.index.get(team)
        if i is None:
            return self._la_default, self._lb_default
        return self.log_attack[i], self.log_defense[i]

    def lambdas(self, home, away):
        la_h, lb_h = self._params(home)
        la_a, lb_a = self._params(away)
        return float(np.exp(la_h + lb_a + self.log_gamma)), float(np.exp(la_a + lb_h))

    def score_grid(self, home, away):
        """Grille P(score = x-y), x, y dans 0..MAX_GOALS, sommant à 1."""
        lam_h, lam_a = self.lambdas(home, away)
        goals = np.arange(MAX_GOALS + 1)
        ph = np.exp(goals * np.log(lam_h) - lam_h - gammaln(goals + 1.0))
        pa = np.exp(goals * np.log(lam_a) - lam_a - gammaln(goals + 1.0))
        grid = np.outer(ph, pa)
        grid[0, 0] *= 1.0 - lam_h * lam_a * self.rho
        grid[0, 1] *= 1.0 + lam_h * self.rho
        grid[1, 0] *= 1.0 + lam_a * self.rho
        grid[1, 1] *= 1.0 - self.rho
        grid = np.maximum(grid, 0.0)
        return grid / grid.sum()

    def probs_1x2(self, home, away):
        """(P(domicile), P(nul), P(extérieur)), somme à 1."""
        grid = self.score_grid(home, away)
        return (float(np.tril(grid, -1).sum()),
                float(np.trace(grid)),
                float(np.triu(grid, 1).sum()))


def fit(rows, xi=0.0, ref_date=None, prior_weight=PRIOR_WEIGHT, warm_start=None):
    """Ajuste le modèle sur des matchs joués.

    rows : itérable de dicts/Rows avec date (ISO), home, away, fthg, ftag.
    ref_date : datetime.date de référence des poids exp(-xi × jours) —
        typiquement le lundi de la semaine à prédire. Tout match daté de
        ref_date ou après lève ValueError (garde anti-fuite du walk-forward).
    warm_start : DixonColes précédent pour initialiser les paramètres.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("aucun match d'entraînement")
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    index = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    h_idx = np.array([index[r["home"]] for r in rows])
    a_idx = np.array([index[r["away"]] for r in rows])
    hg = np.array([r["fthg"] for r in rows], dtype=float)
    ag = np.array([r["ftag"] for r in rows], dtype=float)

    dates = np.array([r["date"] for r in rows], dtype="datetime64[D]")
    if ref_date is not None:
        ref = np.datetime64(ref_date, "D")
        if (dates >= ref).any():
            raise ValueError(f"fuite de données : match daté de {dates.max()} >= référence {ref}")
        w = np.exp(-xi * (ref - dates).astype(float))
    else:
        w = np.exp(-xi * (dates.max() - dates).astype(float))

    theta0 = np.zeros(2 * n + 2)
    if warm_start is not None:
        for t, i in index.items():
            j = warm_start.index.get(t)
            if j is not None:
                theta0[i] = warm_start.log_attack[j]
                theta0[n + i] = warm_start.log_defense[j]
        theta0[2 * n] = warm_start.log_gamma
        theta0[2 * n + 1] = warm_start.rho

    bounds = [(None, None)] * (2 * n + 1) + [RHO_BOUNDS]
    res = minimize(_nll_grad, theta0, args=(n, h_idx, a_idx, hg, ag, w, prior_weight),
                   jac=True, method="L-BFGS-B", bounds=bounds)
    la, lb = res.x[:n], res.x[n:2 * n]

    # Identifiabilité : moyenne(α) = 1 exactement (les λ sont invariants)
    shift = np.log(np.mean(np.exp(la)))
    return DixonColes(teams, la - shift, lb + shift, res.x[2 * n], res.x[2 * n + 1])
