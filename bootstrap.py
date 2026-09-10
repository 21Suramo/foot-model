"""Intervalles de confiance par bootstrap — l'incertitude autour d'un Brier.

Pourquoi ce module existe : jusqu'ici le projet publiait des points nus
(« Brier à +1,78 % du marché », « CLV moyen +0,8 % »). Un point nu ne dit pas
s'il est distinguable de zéro. Sur 4 338 matchs de test l'écart au marché est
probablement réel ; sur les quelques dizaines de matchs du monitoring de
production, un « Δ vs marché » lu au centième est du bruit qu'on prend pour un
signal. L'intervalle de confiance rend cette différence visible dans les
rapports au lieu de la laisser à l'intuition du lecteur.

Deux règles de méthode, appliquées partout ici :

1. **Rééchantillonnage APPARIÉ.** Modèle et marché sont évalués sur les MÊMES
   matchs et leurs Brier sont fortement corrélés (un match surprise pénalise
   les deux). Rééchantillonner indépendamment gonflerait l'intervalle de
   l'écart d'un facteur important. On tire donc des indices de match, et on
   relit les deux séries sur les mêmes indices.
2. **Graine figée.** Les rapports sont committés dans le repo : deux exécutions
   du même rapport sur les mêmes données doivent donner le même intervalle,
   sinon le diff git devient illisible et l'IC passe pour instable alors que
   c'est seulement le tirage qui a changé.

Bootstrap percentile simple (pas de BCa) : sur des moyennes de scores bornés,
avec n de l'ordre de la centaine ou plus, la correction de biais n'est pas ce
qui manque à ces rapports. Le module reste volontairement minimal.
"""
import numpy as np

DEFAULT_RESAMPLES = 10000
DEFAULT_CONFIDENCE = 0.95
# Graine figée : un rapport committé doit se régénérer à l'identique.
DEFAULT_SEED = 20260909


def _percentiles(confidence):
    alpha = (1.0 - confidence) / 2.0
    return 100.0 * alpha, 100.0 * (1.0 - alpha)


def _resample_indices(n, resamples, seed):
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(resamples, n))


def ci_mean(values, confidence=DEFAULT_CONFIDENCE, resamples=DEFAULT_RESAMPLES,
            seed=DEFAULT_SEED):
    """(moyenne, borne_basse, borne_haute) par bootstrap percentile.

    Renvoie (moyenne, None, None) pour n < 2 : un intervalle sur un point
    unique n'aurait aucun sens et vaut mieux dit qu'inventé.
    """
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return None, None, None
    point = float(arr.mean())
    if arr.size < 2:
        return point, None, None
    draws = arr[_resample_indices(arr.size, resamples, seed)].mean(axis=1)
    lo, hi = _percentiles(confidence)
    return point, float(np.percentile(draws, lo)), float(np.percentile(draws, hi))


def ci_relative_delta(scores, reference, confidence=DEFAULT_CONFIDENCE,
                      resamples=DEFAULT_RESAMPLES, seed=DEFAULT_SEED):
    """IC de l'écart RELATIF entre deux séries de scores appariées, en %.

    Même définition que « Écart rel. marché » (report35.py) et « Δ vs marché »
    (predict.py) : (moyenne(scores) − moyenne(reference)) / moyenne(reference)
    × 100, avec scores = Brier du modèle et reference = Brier du marché sur les
    mêmes matchs, dans le même ordre.

    Renvoie (point, bas, haut). L'appariement est le point clé : chaque tirage
    rééchantillonne des MATCHS, pas deux séries indépendantes.
    """
    a = np.asarray(list(scores), dtype=float)
    b = np.asarray(list(reference), dtype=float)
    if a.size != b.size:
        raise ValueError(f"séries non appariées : {a.size} vs {b.size} éléments")
    if a.size == 0 or b.mean() == 0:
        return None, None, None
    point = float((a.mean() - b.mean()) / b.mean() * 100.0)
    if a.size < 2:
        return point, None, None
    idx = _resample_indices(a.size, resamples, seed)
    ma, mb = a[idx].mean(axis=1), b[idx].mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = (ma - mb) / mb * 100.0
    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return point, None, None
    lo, hi = _percentiles(confidence)
    return point, float(np.percentile(draws, lo)), float(np.percentile(draws, hi))


def ci_gap_relative_delta(scores_a, reference_a, scores_b, reference_b,
                          confidence=DEFAULT_CONFIDENCE, resamples=DEFAULT_RESAMPLES,
                          seed=DEFAULT_SEED):
    """IC de l'écart entre DEUX écarts relatifs, en points.

    Sert au rapport de production : « les cotes périmées font +5,1 % du marché
    quand les fraîches font +1,2 %, soit 3,9 pts d'écart » — cet écart-là est
    une différence entre deux groupes DISJOINTS de matchs. Chaque groupe est
    donc rééchantillonné indépendamment (l'appariement modèle/marché reste, lui,
    strict à l'intérieur de chaque groupe), et l'IC dit si les 3,9 pts survivent
    au bruit d'échantillonnage. Sans lui, une alerte peut se déclencher sur
    quinze matchs de bruit.

    Renvoie (point, bas, haut) avec point = delta_a − delta_b.
    """
    a, ra = np.asarray(list(scores_a), float), np.asarray(list(reference_a), float)
    b, rb = np.asarray(list(scores_b), float), np.asarray(list(reference_b), float)
    if a.size != ra.size or b.size != rb.size:
        raise ValueError("séries non appariées à l'intérieur d'un groupe")
    if min(a.size, b.size) < 2 or ra.mean() == 0 or rb.mean() == 0:
        return None, None, None
    delta = lambda s, r: (s.mean() - r.mean()) / r.mean() * 100.0
    point = float(delta(a, ra) - delta(b, rb))
    ia = _resample_indices(a.size, resamples, seed)
    ib = _resample_indices(b.size, resamples, seed + 1)   # tirages indépendants
    with np.errstate(divide="ignore", invalid="ignore"):
        da = (a[ia].mean(axis=1) - ra[ia].mean(axis=1)) / ra[ia].mean(axis=1) * 100.0
        dbb = (b[ib].mean(axis=1) - rb[ib].mean(axis=1)) / rb[ib].mean(axis=1) * 100.0
    draws = (da - dbb)[np.isfinite(da - dbb)]
    if draws.size == 0:
        return point, None, None
    lo, hi = _percentiles(confidence)
    return point, float(np.percentile(draws, lo)), float(np.percentile(draws, hi))


def ci_diff_mean(values_a, values_b, confidence=DEFAULT_CONFIDENCE,
                 resamples=DEFAULT_RESAMPLES, seed=DEFAULT_SEED):
    """IC de la différence de moyenne (moyenne(a) − moyenne(b)) entre DEUX
    groupes DISJOINTS et INDÉPENDANTS (pas de matchs communs, pas de
    référence partagée à l'intérieur d'un groupe) — contrairement à
    `ci_relative_delta` (séries appariées sur les mêmes matchs) et
    `ci_gap_relative_delta` (différence entre deux écarts relatifs, chacun
    avec sa propre référence). Sert par exemple à comparer le ROI théorique
    de deux sous-populations de paris (ex. clv_signal_check.py : paris
    "convergents" vs "divergents", des matchs différents dans chaque groupe).
    Chaque groupe est rééchantillonné indépendamment.

    Renvoie (point, bas, haut), point = moyenne(a) − moyenne(b)."""
    a = np.asarray(list(values_a), dtype=float)
    b = np.asarray(list(values_b), dtype=float)
    if a.size == 0 or b.size == 0:
        return None, None, None
    point = float(a.mean() - b.mean())
    if a.size < 2 or b.size < 2:
        return point, None, None
    da = a[_resample_indices(a.size, resamples, seed)].mean(axis=1)
    db = b[_resample_indices(b.size, resamples, seed + 1)].mean(axis=1)   # tirage indépendant
    draws = da - db
    lo, hi = _percentiles(confidence)
    return point, float(np.percentile(draws, lo)), float(np.percentile(draws, hi))


def fmt_ci(lo, hi, unit="%", decimals=2):
    """« [+0,91 ; +2,64 %] » — ou une mention explicite si l'IC est indisponible."""
    if lo is None or hi is None:
        return "IC indisponible (n < 2)"
    return f"[{lo:+.{decimals}f} ; {hi:+.{decimals}f} {unit}]"


def excludes_zero(lo, hi):
    """L'intervalle exclut-il 0 ? None si l'intervalle n'a pas pu être calculé."""
    if lo is None or hi is None:
        return None
    return lo > 0 or hi < 0
