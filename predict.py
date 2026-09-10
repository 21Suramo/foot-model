"""M5 — Prédiction de production : le modèle M3.5 figé, appliqué aux matchs à venir.

Le backtest est terminé ; ce module fait tourner le modèle en conditions
réelles. Pour chaque affiche du week-end, il :

1. **refit à jour** le Dixon-Coles pseudo-buts xG (config figée dans
   data/m35_frozen.json : w, ξ, κ, température) sur TOUT l'historique joué
   strictement antérieur au lundi de la semaine visée — même mécanique
   anti-fuite que le walk-forward, mais sans horizon de fin ;
2. sort les probas 1N2 recalibrées + la grille de scores, **au format de
   match_model.py** (colonnes Marché / Modèle / FINAL, Top 7 des scores,
   meilleur score par issue) ;
3. fait le **pont avec le skill** : quand des cotes fraîches sont fournies,
   il mélange marché/modèle ; quand elles manquent ou datent, le poids du
   marché décroît et le modèle reprend la main — c'est là le vrai apport du
   modèle, servir de garde-fou contre une ligne périmée ;
4. branche le **mode concours** (--contest-points) sur les probas du modèle ;
5. **journalise automatiquement** chaque prédiction (format track.py, donc
   relisible par le skill) et produit un **rapport de calibration mensuel**
   — le monitoring continue en conditions réelles.

`sync-results` pose aussi, sur chaque pari théorique réglé, son **CLV**
(closing line value) : l'écart entre la cote prise et la cote de clôture
retrouvée dans `matches.odds_*`, **et seulement si cette clôture est une ligne
sharp** (`CLV_SHARP_SOURCES`) — contre une moyenne de books ou une ouverture,
ce serait une autre grandeur publiée sous le nom de CLV. Le ROI a besoin de
~100 paris pour dire quelque chose (variance du foot) ; le CLV converge
beaucoup plus vite et dit si un pari isolé — encore trop tôt pour le ROI —
avait une vraie value ou une cote simplement mauvaise.

Les écarts publiés (ici comme dans les rapports de backtest) portent leur
intervalle de confiance bootstrap (`bootstrap.py`) : un Δ vs marché dont l'IC
contient 0 ne dit rien, et c'est le cas normal sur quelques dizaines de matchs.

Usage :
    python predict.py match --league E0 --home "Arsenal" --away "Chelsea" \
        [--date 2026-08-15] [--odds 1.85,3.6,4.4 --odds 1.88,3.55,4.3] \
        [--odds-date 2026-08-14] [--contest-points 13,50,68]
    python predict.py result --match "Arsenal-Chelsea" --actual 2-1 [--ht 1-0]
    python pipeline.py --update && python predict.py sync-results
    python predict.py report [--month 2026-08]
"""
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

import numpy as np

import backtest
import backtest35
import backtest_derived
import bootstrap
import db
import derived_markets
import footballdata
import model

log = logging.getLogger("predict")

JOURNAL_PATH = Path("data/production_journal.json")
CAL_REPORT_PATH = Path("reports/production_calibration.md")

# Export natif du skill football-match-predictor (pont d'entrée : le skill
# rassemble les cotes web, predict.py recalcule SON propre FINAL par-dessus).
SKILL_SCHEMA = "football-match-predictor.skill-export/v1"

# Fraîcheur des cotes → poids du marché (garde-fou anti-cotes-périmées).
#
# Le marché de clôture bat le modèle (M3.5 : Brier +1,78 %), et backtest_blend.py
# montre qu'il le bat à TOUS les âges simulés. Le blend n'existe donc PAS pour
# gagner du Brier sur des cotes de book fraîches — il existe comme FILET contre
# une cote d'entrée douteuse : mal recopiée depuis le web, figée à J-3+, ou d'un
# book soft. D'où deux garde-fous :
#  - un poids de base < 100 % même à J-0 : une cote fraîche peut être mal
#    récupérée, on garde toujours une petite fraction de modèle en assurance ;
#  - une décroissance vers un PLANCHER (jamais vers 0) : même vieillie, une vraie
#    ligne reste informative (le backtest le confirme), on ne la jette pas — on
#    lui fait juste de moins en moins confiance à mesure qu'elle date.
#
# ⚠ IMPORTANT — ne pas surinterpréter le chiffre du backtest. backtest_blend.py
# mesure ce barème sur un PROXY (interpolation ouverture↔clôture, deux vraies
# lignes sharp) qui SOUS-ESTIME la péremption réelle visée : une cote scrapée
# fausse ou figée est bien pire qu'une simple ouverture de book. Le gain Brier
# qu'il chiffre (~0,9 %) est un PLANCHER de l'utilité du garde-fou, pas sa vraie
# valeur en conditions de cotes scrapées. N'en conclus pas « le modèle ne sert à
# rien » : le backtest ne peut pas voir le scénario que le garde-fou protège.
FRESH_MAX_DAYS = 1      # cotes ≤ 1 jour : poids marché = base (le plus frais)
STALE_MIN_DAYS = 5      # cotes ≥ 5 jours : poids marché = plancher (le plus périmé)
DEFAULT_BLEND = 0.92    # poids marché de base sur cotes fraîches (garde ~8 % modèle en assurance)
STALE_FLOOR = 0.28      # plancher de poids marché : on ne jette jamais une vraie ligne
MARGIN_MIN = 1.0        # marge implicite < 100 % = arbitrable donc suspecte/périmée
MARGIN_MAX = 1.12       # marge > 112 % = ligne de mauvaise qualité

ISSUES = ("home", "draw", "away")

# Démargeage des cotes : comment on passe des cotes brutes du book à des
# probabilités « fair ». Ce n'est pas un détail de plomberie — sur cotes
# fraîches ces probas pèsent 92 % du FINAL, donc la méthode se propage à toutes
# les prédictions. Shin (backtest.demargin_shin) modélise explicitement la marge
# — le book se couvre contre une proportion z de parieurs informés, ce qui le
# pousse à charger les issues improbables — au lieu de la retirer au prorata
# (proportionnel) ou via un exposant libre (power).
#
# ⚠ Honnêteté sur ce choix : devig_check.py mesure les trois méthodes sur les
# saisons hors test et NE TROUVE AUCUN écart de Brier distinguable du bruit
# (IC 95 % à ±0,02 % sur 4 459 matchs de clôture Pinnacle). Shin est donc un
# choix de RIGUEUR (marge dérivée d'un modèle, pas d'un exposant d'ajustement),
# pas un gain mesuré. Ne pas écrire ailleurs que « Shin améliore les probas ».
DEVIG_METHODS = tuple(backtest.DEMARGIN_METHODS)
DEVIG_METHOD = "shin"

# Raisons documentées d'une prédiction sans cote marché (`market_probs` null).
#
# Sans ce champ, le journal ne garde qu'un `market_weight: 0.0` muet : impossible,
# en relisant, de distinguer « le book n'avait pas encore ouvert la ligne à J-8 »
# (structurel, rien à corriger) d'une erreur de mapping ou d'un alias manquant
# (bug, à corriger). On enregistre donc la raison, sans jamais la deviner : les
# deux dernières valeurs sont déduites par le code, les autres doivent être
# déclarées par l'appelant (`--no-odds-reason`, ou le champ homonyme de l'export
# du skill). Faute de déclaration, on écrit « non précisée » — jamais une raison
# plausible inventée après coup.
NO_ODDS_REASONS = {
    "not_yet_published": "cotes pas encore ouvertes chez les books à cette date",
    "lookup_failed": "recherche de cotes infructueuse (affiche introuvable, source injoignable)",
    "margin_rejected": "cotes trouvées mais écartées en amont (marge implicite hors bornes)",
    "not_provided": "aucune cote passée à l'appel, raison non précisée",
    "slate_odds_ignored": "--odds ne s'applique qu'à un match unique : ignoré sur un slate",
}
# Ce que l'appelant a le droit de déclarer ; le reste est déduit du code.
DECLARABLE_NO_ODDS_REASONS = ("not_yet_published", "lookup_failed", "margin_rejected")
DEFAULT_NO_ODDS_REASON = "not_provided"


# ---------------------------------------------------------------------------
# Utilitaires de dates et de grille
# ---------------------------------------------------------------------------

def next_saturday(today=None):
    """Samedi à venir (aujourd'hui compris s'il tombe un samedi)."""
    today = today or datetime.date.today()
    return today + datetime.timedelta(days=(5 - today.weekday()) % 7)


def grid_to_dict(grid_np):
    """Grille numpy (MAX_GOALS+1)² -> dict {(h, a): p}, au format match_model."""
    return {(h, a): float(grid_np[h, a])
            for h in range(grid_np.shape[0]) for a in range(grid_np.shape[1])}


def fmt(x):
    return f"{x * 100:5.1f}%"


# ---------------------------------------------------------------------------
# Résolution des noms d'équipe (l'appelant peut fournir un nom Understat ou
# une variante ; on le ramène au nom canonique football-data du modèle)
# ---------------------------------------------------------------------------

def resolve_team(name, teams, alias_map):
    """(nom_canonique, trouvé?). Un nom inconnu du modèle est renvoyé tel quel :
    model.py attribue alors les forces moyennes de la ligue (comportement
    documenté), mais on le signale car c'est souvent une faute de frappe."""
    if name in teams:
        return name, True
    if name in alias_map and alias_map[name] in teams:
        return alias_map[name], True
    low = name.strip().lower()
    exact = [t for t in teams if t.lower() == low]
    if len(exact) == 1:
        return exact[0], True
    partial = [t for t in teams if low in t.lower() or t.lower() in low]
    if len(partial) == 1:
        return partial[0], True
    return name, False


# ---------------------------------------------------------------------------
# Pont marché / modèle (garde-fou anti-cotes-périmées)
# ---------------------------------------------------------------------------

def parse_odds_triples(specs):
    """Liste de 'h,d,a' -> liste de triplets float. Erreur explicite sinon."""
    out = []
    for spec in specs:
        try:
            oh, od, oa = (float(x) for x in spec.split(","))
        except ValueError:
            sys.exit(f"Format --odds invalide: '{spec}' (attendu home,nul,away, ex: 1.85,3.6,4.4)")
        out.append((oh, od, oa))
    return out


def market_consensus(triples, method=DEVIG_METHOD):
    """Consensus démargé + meilleure cote brute par issue.

    `method` ∈ DEVIG_METHODS ; chaque book est démargé SÉPARÉMENT avant la
    moyenne (démarger la moyenne des cotes mélangerait des marges hétérogènes).
    """
    if method not in backtest.DEMARGIN_METHODS:
        sys.exit(f"Méthode de démargeage inconnue '{method}' "
                 f"(attendu l'une de {list(DEVIG_METHODS)}).")
    demargin = backtest.DEMARGIN_METHODS[method]
    fairs = [demargin(*t) for t in triples]
    market = {k: float(np.mean([f[i] for f in fairs])) for i, k in enumerate(ISSUES)}
    best_odds = {k: max(t[i] for t in triples) for i, k in enumerate(ISSUES)}
    return market, best_odds


def margin_ok(triple):
    margin = sum(1.0 / o for o in triple)
    return MARGIN_MIN - 1e-6 <= margin <= MARGIN_MAX, margin


def market_weight(base_blend, age_days, all_margins_ok):
    """(poids_marché, fraction_de_base, explication). age_days = âge des cotes vs
    coup d'envoi (None = fraîcheur non vérifiée). Décroissance linéaire du poids
    de base (cotes fraîches) vers le plancher STALE_FLOOR (cotes périmées) — on ne
    coupe jamais complètement le marché, une ligne même vieillie informe encore ;
    une marge aberrante divise en plus le poids par 2 (cote suspecte, peut passer
    sous le plancher car c'est alors la QUALITÉ de la cote qui est en cause)."""
    floor = min(STALE_FLOOR, base_blend)
    if age_days is None:
        w, why = base_blend, "fraîcheur non vérifiée (supposée fraîche)"
    elif age_days <= FRESH_MAX_DAYS:
        w, why = base_blend, f"cotes fraîches (J-{age_days})"
    elif age_days >= STALE_MIN_DAYS:
        w, why = floor, f"cotes périmées (J-{age_days}) — poids marché au plancher {floor:.0%}"
    else:
        frac = (STALE_MIN_DAYS - age_days) / (STALE_MIN_DAYS - FRESH_MAX_DAYS)
        w = floor + (base_blend - floor) * frac
        why = f"cotes à J-{age_days} — poids marché {w:.0%}"
    if not all_margins_ok:
        w *= 0.5
        why += " ; marge implicite aberrante, poids divisé par 2"
    return w, (w / base_blend if base_blend else 0.0), why


# ---------------------------------------------------------------------------
# Ajustement post-fit sur compositions confirmées (--lineup-adjustment, M6)
#
# Distinct du pont marché/modèle ci-dessus : celui-ci ajuste les λ AVANT le
# blend, à partir d'une composition officielle (typiquement connue ~1h avant
# coup d'envoi), plutôt que de rester sur le refit figé du lundi. N'existe
# jamais dans model.py (qui reste le fit walk-forward pur) : c'est un
# ajustement en aval du modèle M3.5 figé, pas un re-tuning de ses
# hyperparamètres — les fichiers figés ne sont jamais touchés ici.
#
# Entrée manuelle (pas de scraping) : pour chaque équipe et chaque axe
# (attack/defense), la somme des contributions xG/90 (attack) ou xG concédé/90
# (defense) des titulaires CONFIRMÉS vs d'une composition de RÉFÉRENCE
# (typique/attendue) — le ratio en est dérivé, jamais saisi directement, pour
# éviter un multiplicateur choisi au pif.
# ---------------------------------------------------------------------------

LINEUP_RATIO_BOUNDS = (0.5, 1.75)  # borne un ratio aberrant (saisie fautive) ;
                                    # une absence réaliste ne divise pas l'attaque par plus de 2


def compute_lineup_ratio(confirmed, reference):
    """Ratio (somme confirmed / somme reference), clampé à LINEUP_RATIO_BOUNDS.

    Chaque élément de confirmed/reference est un nombre ou {"value": nombre,
    "name": ...} (le nom n'est là que pour la lisibilité du fichier d'entrée).
    Une référence vide ou nulle renvoie 1.0 (rien à comparer) plutôt qu'une
    division par zéro."""
    def total(entries):
        return sum(e["value"] if isinstance(e, dict) else e for e in entries)
    ref_sum = total(reference)
    if ref_sum <= 0:
        return 1.0
    ratio = total(confirmed) / ref_sum
    lo, hi = LINEUP_RATIO_BOUNDS
    return max(lo, min(hi, ratio))


def _side_ratios(side):
    side = side or {}
    def axis(name):
        spec = side.get(name)
        if not spec:
            return 1.0
        return compute_lineup_ratio(spec.get("confirmed", []), spec.get("reference", []))
    return axis("attack"), axis("defense")


def apply_lineup_adjustment(lam_h, lam_a, rho, adjustment):
    """Ajuste λ_domicile/λ_extérieur post-fit selon les compositions confirmées.

    adjustment : dict optionnel {"home": {...}, "away": {...}} (voir
    load_lineup_adjustment) ou None/{} pour aucun ajustement. Le ratio
    d'ATTAQUE d'une équipe multiplie SON PROPRE λ ; son ratio de DÉFENSE
    multiplie le λ ADVERSE — une défense affaiblie ou renforcée change les
    buts attendus EN FACE, pas les siens.

    Renvoie (lam_h_ajusté, lam_a_ajusté, meta) ; meta contient toujours
    "applied" (bool) pour rester traçable dans le journal même quand
    l'ajustement n'est pas demandé."""
    if not adjustment:
        return lam_h, lam_a, {"applied": False}
    atk_h, def_h = _side_ratios(adjustment.get("home"))
    atk_a, def_a = _side_ratios(adjustment.get("away"))
    new_lam_h = lam_h * atk_h * def_a
    new_lam_a = lam_a * atk_a * def_h
    meta = {
        "applied": True,
        "home": {"attack_ratio": round(atk_h, 4), "defense_ratio": round(def_h, 4)},
        "away": {"attack_ratio": round(atk_a, 4), "defense_ratio": round(def_a, 4)},
        "lam_h_before": round(lam_h, 4), "lam_h_after": round(new_lam_h, 4),
        "lam_a_before": round(lam_a, 4), "lam_a_after": round(new_lam_a, 4),
    }
    return new_lam_h, new_lam_a, meta


def grid_and_probs_from_lambdas(lam_h, lam_a, rho, max_goals=None):
    """Grille de scores + probas 1N2 pour des λ arbitraires (post-ajustement).

    Réutilise model.DixonColes tel quel (jamais modifié) via une instance à
    deux équipes fictives dont les log-forces d'attaque encodent directement
    log(λ_h)/log(λ_a) (défenses et gamma neutres, log=0) : même calcul de la
    correction tau/rho que le fit normal, sans dupliquer la logique de
    model.py dans predict.py. max_goals suit model.score_grid (None = 7×7)."""
    mini = model.DixonColes(["_h", "_a"], np.log([lam_h, lam_a]), np.log([1.0, 1.0]), 0.0, rho)
    grid = mini.score_grid("_h", "_a", max_goals=max_goals)
    return grid, mini.probs_1x2("_h", "_a")


def load_lineup_adjustment(source):
    """Charge un ajustement de composition (JSON, fichier ou '-' pour stdin).

    Schéma : {"home": {"attack": {"confirmed": [...], "reference": [...]},
                        "defense": {"confirmed": [...], "reference": [...]}},
              "away": {...}} — "home"/"away" et "attack"/"defense" sont tous
    optionnels ; un axe absent = pas d'ajustement sur cet axe (ratio 1.0)."""
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text()
    except OSError as e:
        sys.exit(f"--lineup-adjustment : lecture impossible ({e}).")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"--lineup-adjustment : JSON malformé ({e}).")
    if not isinstance(doc, dict) or not (doc.get("home") or doc.get("away")):
        sys.exit("--lineup-adjustment : objet JSON avec au moins 'home' ou 'away'.")
    return doc


# ---------------------------------------------------------------------------
# Mise (Kelly fractionné plafonné) — repris de match_model pour la section 💰
# ---------------------------------------------------------------------------

def kelly_stake(p, decimal_odds, fraction=0.25, cap=0.05):
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    f_star = (p * b - (1.0 - p)) / b
    if f_star <= 0:
        return 0.0
    return min(f_star * fraction, cap)


def risk_label(stake_pct):
    if stake_pct <= 0:
        return "pas de mise (aucune value)"
    if stake_pct < 0.01:
        return "risque faible"
    if stake_pct < 0.025:
        return "risque modéré"
    return "risque élevé (proche du plafond)"


# ---------------------------------------------------------------------------
# Plafond d'exposition sur un slate (mises SIMULTANÉES)
#
# `kelly_stake` plafonne chaque pari à 5 % de bankroll. Ce plafond est
# individuel, et c'est son angle mort : un slate de week-end sort 10 à 30
# affiches dont les matchs se jouent dans la même après-midi. Dix paris à 3 %
# ne sont pas « dix fois un risque de 3 % » étalés dans le temps comme le
# suppose Kelly — ce sont 30 % de bankroll exposés EN MÊME TEMPS, sur des
# résultats qu'aucun réglage ne rend indépendants (même journée, même météo de
# marché, mêmes erreurs de modèle corrélées entre ligues). Kelly fractionné
# suppose des paris séquentiels et une re-mesure de la bankroll entre deux ;
# un slate viole les deux hypothèses.
#
# D'où un second plafond, sur la SOMME des mises exposées en même temps :
# au-delà, toutes les mises sont réduites par un facteur commun (jamais par
# troncature des dernières affiches, ce qui reviendrait à parier sur l'ordre des
# fixtures). Réduire proportionnellement préserve les rapports de mise entre
# paris, donc la hiérarchie de value du modèle.
#
# ⚠ Le périmètre du plafond n'est PAS le run courant. En pratique un slate se
# génère un match à la fois (`--odds` ne s'applique qu'à un match unique, donc
# un run `--fixture` répété ne produit aucune mise) : plafonner le run seul
# n'aurait jamais rien plafonné. Le cumul se lit donc dans le JOURNAL, sur
# toutes les mises non encore réglées de la même semaine de matchs — le lundi
# de référence déjà utilisé partout ailleurs (backtest.monday_of). C'est bien
# la définition du risque : des paris posés avant qu'aucun ne soit résolu.
#
# Valeur : 15 % de bankroll. C'est exactement 3 × le plafond individuel de 5 %,
# donc un match seul (3 issues au maximum) n'est JAMAIS réduit — le plafond ne
# mord que sur ce pour quoi il est fait, le cumul multi-matchs. Sur une
# bankroll de 100 dhs cela borne la perte d'une mauvaise semaine à 15 dhs.
# Verrouillé par tests/test_predict.py::TestRiskParameters.
SLATE_EXPOSURE_CAP = 0.15


# ---------------------------------------------------------------------------
# Corrélation entre paris (M9) — le plafond ci-dessus somme les mises brutes
# comme si chaque match était un tirage indépendant. ÇA NE L'EST PAS quand
# deux affiches de la même semaine partagent une équipe (report de calendrier,
# double confrontation coupe+championnat) : une même blessure, une même
# actualité, une même sortie de forme affecte les deux paris à la fois. Un
# pari sur cette équipe dans les deux matchs n'est donc pas "deux fois moins
# risqué" que ne le suppose la simple somme des mises — il est plus concentré
# que la somme ne le dit.
#
# CORRELATED_EXPOSURE_MULTIPLIER n'est PAS un coefficient mesuré : aucune
# donnée de paris multi-matchs corrélés n'existe pour l'estimer (même limite
# que la division par 2 sur marge aberrante dans market_weight — un choix de
# rigueur explicite, pas un chiffre calibré sur un backtest). Il ne change
# JAMAIS les mises elles-mêmes (toujours réduites par le même facteur commun,
# cf. apply_exposure_cap) : il gonfle uniquement la comptabilité interne du
# plafond, pour qu'un groupe corrélé consomme le budget plus vite et réduise
# donc davantage TOUTES les mises du run. Verrouillé, comme les autres
# paramètres de risque, par tests/test_predict.py::TestRiskParameters.
#
# Portée volontairement limitée à l'équipe partagée entre MATCHS DISTINCTS :
# plusieurs paris (1N2) sur un même match sont déjà comptés correctement par
# la simple somme (la perte simultanée maximale d'un match est déjà la somme
# de ses mises, cf. commentaire de SLATE_EXPOSURE_CAP) — rien à corriger là.
CORRELATED_EXPOSURE_MULTIPLIER = 1.5


def _teams_of(d):
    return (d.get("home"), d.get("away"))


def correlated_indices(matches):
    """Indices de `matches` (dicts avec home/away) dont une équipe apparaît
    dans un AUTRE élément de la liste — paris corrélés par équipe partagée."""
    counts = {}
    for h, a in map(_teams_of, matches):
        for t in (h, a):
            if t:
                counts[t] = counts.get(t, 0) + 1
    return {i for i, (h, a) in enumerate(map(_teams_of, matches))
            if (h and counts.get(h, 0) > 1) or (a and counts.get(a, 0) > 1)}


def match_stakes(res, no_stake=False):
    """Mises Kelly BRUTES d'un match (avant plafond de slate), par issue."""
    if no_stake or not res.get("best_odds"):
        return {}
    out = {}
    for issue in ISSUES:
        stake = kelly_stake(res["final"][issue], float(res["best_odds"][issue]))
        if stake > 0:
            out[issue] = stake
    return out


def pending_bet_matches(path, target_date, exclude_matches=()):
    """Paris non réglés de la même semaine que target_date, groupés par match
    (clé 'Domicile-Extérieur' du journal), avec équipes et mise cumulée.

    « Même semaine » = même lundi de référence (backtest.monday_of), la maille
    déjà utilisée par le walk-forward et le refit : c'est le week-end de matchs,
    donc l'ensemble des paris posés avant qu'aucun ne soit résolu.

    `exclude_matches` contient les clés (match, date) que le run courant va
    RÉÉCRIRE dans le journal — sans quoi un simple ré-run du même match
    compterait sa propre mise deux fois et se plafonnerait tout seul.

    Une seule lecture du journal sert deux besoins : le total simple
    (pending_exposure) et la détection des paris corrélés par équipe partagée
    avec le run courant (M9, apply_exposure_cap)."""
    week = backtest.monday_of(target_date.isoformat())
    out = {}
    for e in load_journal(path):
        if e.get("actual_score") is not None:
            continue
        if (e.get("match"), e.get("date")) in set(exclude_matches):
            continue
        try:
            if backtest.monday_of(e["date"]) != week:
                continue
        except (KeyError, ValueError):
            continue
        bets = e.get("bets") or []
        if not bets:
            continue
        meta = e.get("meta") or {}
        out[e["match"]] = {
            "home": meta.get("home"), "away": meta.get("away"),
            "stake": sum(float(b.get("stake_pct") or 0.0) for b in bets),
            "n_bets": len(bets),
        }
    return out


def pending_exposure(path, target_date, exclude_matches=()):
    """Mise cumulée et nombre de paris déjà engagés, non réglés, même semaine."""
    matches = pending_bet_matches(path, target_date, exclude_matches)
    return (sum(m["stake"] for m in matches.values()),
            sum(m["n_bets"] for m in matches.values()))


def apply_exposure_cap(results, cap=SLATE_EXPOSURE_CAP, no_stake=False,
                       prior=0.0, prior_bets=0, pending_matches=None):
    """Pose `stakes` (brutes) et `exposure_factor` sur chaque résultat du run.

    Doit être appelée sur TOUS les matchs du run avant d'imprimer ou de
    journaliser : le facteur dépend du cumul, il ne peut pas se décider match
    par match. `prior` est l'exposition déjà engagée sur la même semaine
    (pending_exposure) — c'est elle qui rend le plafond opérant dans le vrai
    flux, où les matchs sont générés un par un.

    `pending_matches` (pending_bet_matches) sert à détecter les paris
    CORRÉLÉS par équipe partagée (M9) entre le run courant et les paris déjà
    engagés cette semaine — voir CORRELATED_EXPOSURE_MULTIPLIER. None ou {}
    désactive la détection (comportement d'avant M9, inchangé).

    Renvoie le récapitulatif du run."""
    stakes = [match_stakes(r, no_stake) for r in results]
    totals = [sum(s.values()) for s in stakes]
    gross = sum(totals)

    pending_list = list((pending_matches or {}).values())
    pool = [{"home": r.get("home"), "away": r.get("away")} for r in results] + pending_list
    corr = correlated_indices(pool)
    n = len(results)
    run_corr = {i for i in corr if i < n}
    pending_corr_extra = sum(pending_list[i - n]["stake"] * (CORRELATED_EXPOSURE_MULTIPLIER - 1.0)
                             for i in corr if i >= n)

    effective_gross = sum(t * (CORRELATED_EXPOSURE_MULTIPLIER if i in run_corr else 1.0)
                          for i, t in enumerate(totals))
    effective_prior = prior + pending_corr_extra
    budget = max(0.0, cap - effective_prior)
    # Tolérance : 3 × 0,05 vaut 0,15000000000000002 en binaire. Sans elle, un
    # match seul dont les trois issues touchent le plafond individuel serait
    # réduit d'un cheveu, ce qui contredirait l'invariant documenté.
    factor = (1.0 if effective_gross <= budget * (1.0 + 1e-9)
              else (budget / effective_gross if effective_gross else 1.0))
    for i, (r, s) in enumerate(zip(results, stakes)):
        r["stakes"] = s
        r["exposure_factor"] = factor
        r["exposure_cap"] = cap
        r["correlated"] = i in run_corr
    return {"gross": gross, "cap": cap, "factor": factor, "net": gross * factor,
            "prior": prior, "prior_bets": prior_bets, "budget": budget,
            "total": prior + gross * factor,
            "n_bets": sum(len(s) for s in stakes), "n_matches": len(results),
            "n_correlated": len(run_corr)}


def final_stakes(res, no_stake=False):
    """Mises effectives d'un match : brutes × facteur d'exposition.

    Une mise réduite à zéro (plafond déjà saturé) disparaît : journaliser un
    pari à 0 % de bankroll n'aurait aucun sens dans le ROI ni dans le CLV.

    Retombe sur les mises brutes si `apply_exposure_cap` n'a pas été appelée
    (appel unitaire depuis un test ou un autre script)."""
    stakes = res.get("stakes")
    if stakes is None:
        stakes = match_stakes(res, no_stake)
    factor = res.get("exposure_factor", 1.0)
    out = {issue: stake * factor for issue, stake in stakes.items()}
    return {k: v for k, v in out.items() if round(v, 6) > 0}


def exposure_recap(summary):
    """Récapitulatif d'exposition affiché en fin de run (chaîne vide si sans objet)."""
    if not summary or not (summary["n_bets"] or summary["prior_bets"]):
        return ""
    cap, total = summary["cap"], summary["total"]
    saturated = total > cap * (1.0 + 1e-9)
    prior_txt = ""
    if summary["prior_bets"]:
        prior_txt = (f"\n  Déjà engagé cette semaine (journal, paris non réglés) : "
                     f"{summary['prior']:.1%} sur {summary['prior_bets']} pari(s) — "
                     f"budget restant {summary['budget']:.1%}.")
    if summary.get("n_correlated"):
        prior_txt += (f"\n  ⚠ {summary['n_correlated']} affiche(s) de ce run partagent une "
                      f"équipe avec un autre match de la semaine (report de calendrier, "
                      f"double confrontation) : comptées ×{CORRELATED_EXPOSURE_MULTIPLIER:g} "
                      f"dans le plafond (heuristique non calibrée, cf. CLAUDE.md M9).")
    head = (f"Exposition simultanée : {summary['n_bets']} pari(s) ajouté(s) sur "
            f"{summary['n_matches']} affiche(s), {summary['gross']:.1%} de bankroll "
            f"en mises brutes")
    if summary["factor"] <= 0.0 and summary["n_bets"]:
        return (f"⚠ {head}. Plafond d'exposition de {cap:.0%} DÉJÀ ATTEINT par les "
                f"paris de la semaine : aucune mise supplémentaire.{prior_txt}\n"
                f"  (Les probabilités et la value restent affichées ; c'est la mise "
                f"qui est bloquée, pas l'analyse.)")
    if summary["factor"] < 1.0:
        return (f"⚠ {head}, au-dessus du budget restant de {summary['budget']:.1%} "
                f"(plafond {cap:.0%}).{prior_txt}\n"
                f"  Toutes les mises sont réduites du même facteur "
                f"×{summary['factor']:.2f} → {summary['net']:.1%} ajoutés, "
                f"{total:.1%} exposés au total.\n"
                f"  (Kelly plafonne chaque pari isolément ; sur un week-end les matchs "
                f"se jouent en même temps, l'exposition s'additionne sans que la "
                f"bankroll ait le temps d'être re-mesurée entre deux.)")
    if saturated:
        # Rien à réduire dans ce run (aucune value), mais la semaine dépasse déjà
        # le plafond : le dire, plutôt qu'annoncer « sous le plafond ».
        return (f"⚠ {head} — total semaine {total:.1%}, AU-DESSUS du plafond de "
                f"{cap:.0%} (engagé par des runs précédents).{prior_txt}")
    return (f"{head} — total semaine {total:.1%}, sous le plafond de {cap:.0%} : "
            f"mises inchangées.{prior_txt}")


# ---------------------------------------------------------------------------
# Prédiction d'un match
# ---------------------------------------------------------------------------

def best_score_for_outcome(grid, outcome):
    cond = {"home": lambda h, a: h > a, "draw": lambda h, a: h == a,
            "away": lambda h, a: h < a}[outcome]
    (h, a), _ = max((kv for kv in grid.items() if cond(*kv[0])), key=lambda kv: kv[1])
    return f"{h}-{a}"


def over_prob(grid, line):
    return sum(p for (h, a), p in grid.items() if h + a > line)


def btts_prob(grid):
    return sum(p for (h, a), p in grid.items() if h > 0 and a > 0)


def team_over_prob(grid, side, line):
    idx = 0 if side == "home" else 1
    return sum(p for score, p in grid.items() if score[idx] > line)


_DERIVED_CFG_CACHE = None


def derived_markets_cfg():
    """data/derived_markets_frozen.json (roadmap A1), en cache — absent tant que
    personne n'a lancé `backtest_derived.py --tune` : predict.py doit rester
    utilisable sans (affiche alors les probas brutes, non calibrées, avec une
    note), pas un sys.exit comme backtest_derived.frozen()."""
    global _DERIVED_CFG_CACHE
    if _DERIVED_CFG_CACHE is None:
        _DERIVED_CFG_CACHE = (json.loads(backtest_derived.FROZEN_PATH.read_text())
                              if backtest_derived.FROZEN_PATH.exists() else {})
    return _DERIVED_CFG_CACHE


def calibrated_derived_prob(market, raw_p):
    """Proba calibrée si backtest_derived.py --tune a figé ce marché, sinon la
    proba brute (non calibrée — signalé par le "*" en affichage)."""
    t = derived_markets_cfg().get("markets", {}).get(market, {}).get("t")
    return (backtest_derived.apply_binary_temperature(raw_p, t), True) if t is not None \
        else (raw_p, False)


def derived_markets_probs(grid, grid_ext):
    """Toutes les probas de marchés dérivés validées par le chantier A1
    (backtest_derived.py/reports/derived_markets_backtest.md), calibrées si
    figées. ou25/btts restent sur `grid` (7×7, base inchangée depuis M7
    roadmap) ; le reste sur `grid_ext` (12×12, cf. derived_markets.py) —
    jamais mélangés, cf. backtest_derived.py pour pourquoi."""
    raw = {
        "ou05": over_prob(grid_ext, 0.5), "ou15": over_prob(grid_ext, 1.5),
        "ou25": over_prob(grid, 2.5), "ou35": over_prob(grid_ext, 3.5),
        "ou45": over_prob(grid_ext, 4.5), "btts": btts_prob(grid),
        "home_ov15": team_over_prob(grid_ext, "home", 1.5),
        "away_ov15": team_over_prob(grid_ext, "away", 1.5),
    }
    return {m: calibrated_derived_prob(m, p) for m, p in raw.items()}


def predict_match(conn, cfg, league, home_in, away_in, target_date,
                  odds_specs, odds_age_days, blend, fit_cache,
                  no_odds_reason=None, lineup_adjustment=None,
                  devig=DEVIG_METHOD):
    """Calcule tout pour un match et renvoie un dict de résultats (sans imprimer)."""
    ref_monday = backtest.monday_of(target_date.isoformat())
    key = (league, ref_monday)
    if key not in fit_cache:
        rows = [r for r in backtest.load_league(conn, league)
                if r["date"] < ref_monday.isoformat()]
        if not rows:
            sys.exit(f"Aucun match d'entraînement pour {league} avant {ref_monday}.")
        fit_cache[key] = (model.fit(rows, xi=cfg["xi"], ref_date=ref_monday,
                                    xg_weight=cfg["w"], prior_weight=cfg["kappa"]),
                          len(rows), max(r["date"] for r in rows))
    fitted, n_train, last_date = fit_cache[key]

    alias_map = db.load_aliases(conn)
    home, home_ok = resolve_team(home_in, fitted.teams, alias_map)
    away, away_ok = resolve_team(away_in, fitted.teams, alias_map)

    lam_h, lam_a = fitted.lambdas(home, away)
    lam_h, lam_a, lineup_meta = apply_lineup_adjustment(lam_h, lam_a, fitted.rho, lineup_adjustment)
    if lineup_meta["applied"]:
        grid_arr, raw = grid_and_probs_from_lambdas(lam_h, lam_a, fitted.rho)
        grid = grid_to_dict(grid_arr)
    else:
        grid = grid_to_dict(fitted.score_grid(home, away))
        raw = fitted.probs_1x2(home, away)
    model_probs = dict(zip(ISSUES, backtest35.apply_temperature(raw, cfg["temperature"])))

    # Grille 12×12 (roadmap A1) : mêmes λ/rho (ajustés composition compris) que
    # ci-dessus, juste moins tronquée — cf. derived_markets.py pour pourquoi
    # elle ne remplace jamais la grille 7×7 du 1N2/ou25/btts.
    grid_ext_arr, _ = grid_and_probs_from_lambdas(lam_h, lam_a, fitted.rho,
                                                  max_goals=derived_markets.EXTENDED_MAX_GOALS)
    grid_ext = grid_to_dict(grid_ext_arr)
    derived = derived_markets_probs(grid, grid_ext)

    market = best_odds = None
    m_weight = 0.0
    reason = None
    fresh_note = "aucune cote fournie — modèle seul"
    if odds_specs:
        triples = parse_odds_triples(odds_specs)
        market, best_odds = market_consensus(triples, devig)
        all_ok = all(margin_ok(t)[0] for t in triples)
        m_weight, _, fresh_note = market_weight(blend, odds_age_days, all_ok)
    else:
        # Modèle pur : on trace POURQUOI, sinon le journal ne garde qu'un
        # market_weight nul qu'on ne saura plus interpréter dans trois semaines.
        reason = no_odds_reason if no_odds_reason in NO_ODDS_REASONS else DEFAULT_NO_ODDS_REASON
        fresh_note = f"aucune cote fournie — modèle seul ({NO_ODDS_REASONS[reason]})"

    if market is not None:
        final = {k: m_weight * market[k] + (1 - m_weight) * model_probs[k] for k in ISSUES}
        s = sum(final.values())
        final = {k: v / s for k, v in final.items()}
    else:
        final = dict(model_probs)

    return {
        "league": league, "home": home, "away": away,
        "home_in": home_in, "away_in": away_in,
        "home_ok": home_ok, "away_ok": away_ok,
        "date": target_date, "ref_monday": ref_monday,
        "n_train": n_train, "last_train_date": last_date,
        "lam_h": lam_h, "lam_a": lam_a, "grid": grid,
        "model": model_probs, "market": market, "best_odds": best_odds,
        "final": final, "market_weight": m_weight, "fresh_note": fresh_note,
        "odds_age_days": odds_age_days, "no_odds_reason": reason,
        "devig": devig if market is not None else None,
        "lineup_adjustment": lineup_meta,
        "derived_markets": derived,
    }


# ---------------------------------------------------------------------------
# Impression (format match_model.py)
# ---------------------------------------------------------------------------

def print_prediction(res, cfg, contest=None, exact_bonus=0.0, no_stake=False):
    home, away = res["home"], res["away"]
    print(f"=== {home} vs {away} — modèle M5 production "
          f"(w={cfg['w']:.1f}, ξ={cfg['xi']}, κ={cfg['kappa']}, t={cfg['temperature']:.3f}) ===\n")
    if not res["home_ok"]:
        print(f"⚠ '{res['home_in']}' inconnu du modèle — forces moyennes de la ligue appliquées "
              f"(vérifie l'orthographe).")
    if not res["away_ok"]:
        print(f"⚠ '{res['away_in']}' inconnu du modèle — forces moyennes de la ligue appliquées "
              f"(vérifie l'orthographe).")
    print(f"Refit sur {res['n_train']} matchs joués {res['league']} jusqu'au "
          f"{res['last_train_date']} (réf. {res['ref_monday']}, match prévu {res['date']}).")
    print(f"Lambdas : {home} λ={res['lam_h']:.2f} | {away} λ={res['lam_a']:.2f}")
    la = res.get("lineup_adjustment")
    if la and la["applied"]:
        print(f"Ajustement composition (H-1) : {home} attaque×{la['home']['attack_ratio']:.2f} "
              f"déf×{la['home']['defense_ratio']:.2f} | {away} attaque×{la['away']['attack_ratio']:.2f} "
              f"déf×{la['away']['defense_ratio']:.2f} → λ {la['lam_h_before']:.2f}→{la['lam_h_after']:.2f} / "
              f"{la['lam_a_before']:.2f}→{la['lam_a_after']:.2f}")
    print(f"Pont marché/modèle : {res['fresh_note']}" +
          (f" → poids marché {res['market_weight']:.0%} "
           f"(démargeage {res.get('devig') or DEVIG_METHOD})." if res["market"]
           else f" [{res.get('no_odds_reason') or DEFAULT_NO_ODDS_REASON}]."))
    print()

    market, model_probs, final = res["market"], res["model"], res["final"]
    header = f"{'':22}"
    if market:
        header += f"{'Marché (fair)':>15}"
    header += f"{'Modèle':>12}{'FINAL':>12}"
    print(header)
    for key, label in (("home", f"Victoire {home}"), ("draw", "Match nul"),
                       ("away", f"Victoire {away}")):
        line = f"{label:<22}"
        if market:
            line += f"{fmt(market[key]):>15}"
        line += f"{fmt(model_probs[key]):>12}{fmt(final[key]):>12}"
        print(line)

    grid = res["grid"]
    print()
    dm = res.get("derived_markets", {})

    def _fmt_dm(key):
        if key not in dm:
            return "n/d"
        p, calibrated = dm[key]
        return fmt(p) + ("" if calibrated else "*")

    print(f"BTTS : {_fmt_dm('btts')}   |   Over 0.5 : {_fmt_dm('ou05')}   |   "
          f"Over 1.5 : {_fmt_dm('ou15')}")
    print(f"Over 2.5 : {_fmt_dm('ou25')}   |   Over 3.5 : {_fmt_dm('ou35')}   |   "
          f"Over 4.5 : {_fmt_dm('ou45')}")
    print(f"{home} +1,5 but(s) : {_fmt_dm('home_ov15')}   |   "
          f"{away} +1,5 but(s) : {_fmt_dm('away_ov15')}")
    if dm and not all(calibrated for _, calibrated in dm.values()):
        print("(* non calibré — lancer `python backtest_derived.py --tune` puis `--run` "
              "pour figer/valider ce marché, cf. reports/derived_markets_backtest.md)")
    print(f"Double chance 1X : {fmt(final['home'] + final['draw'])}   |   "
          f"X2 : {fmt(final['away'] + final['draw'])}")

    print("\nTop 7 des scores exacts (grille du modèle) :")
    for (h, a), p in sorted(grid.items(), key=lambda x: -x[1])[:7]:
        print(f"  {home} {h}-{a} {away}  :  {fmt(p)}")

    print("\nMeilleur score PAR ISSUE (cohérent avec un pronostic d'issue déjà fixé) :")
    for outcome, label in (("home", f"si victoire {home}"), ("draw", "si match nul"),
                           ("away", f"si victoire {away}")):
        sub = [((h, a), p) for (h, a), p in grid.items()
               if (outcome == "home" and h > a) or (outcome == "draw" and h == a)
               or (outcome == "away" and h < a)]
        (bh, ba), bp = max(sub, key=lambda x: x[1])
        mass = sum(p for _, p in sub)
        print(f"  {label:<24}: {bh}-{ba}  ({fmt(bp)} absolu, {fmt(bp / mass)} conditionnel)")

    if market is not None:
        print("\n--- Détection de value (modèle recalibré vs marché) ---")
        any_value = False
        for key, label in (("home", f"Victoire {home}"), ("draw", "Nul"),
                           ("away", f"Victoire {away}")):
            gap = (model_probs[key] - market[key]) * 100
            if abs(gap) >= 8:
                print(f"  VALUE CLAIRE   {label}: modèle {fmt(model_probs[key])} vs "
                      f"marché {fmt(market[key])} ({gap:+.1f} pts)")
                any_value = True
            elif abs(gap) >= 4:
                print(f"  Value modérée  {label}: modèle {fmt(model_probs[key])} vs "
                      f"marché {fmt(market[key])} ({gap:+.1f} pts)")
                any_value = True
        if not any_value:
            print("  Aucune value ≥ 4 pts sur le 1N2 — le modèle confirme le marché.")

        if not no_stake and res["best_odds"] is not None:
            factor = res.get("exposure_factor", 1.0)
            capped = (" — RÉDUITES par le plafond d'exposition de la semaine"
                      if factor < 1 else "")
            cap = res.get("exposure_cap", SLATE_EXPOSURE_CAP)
            print(f"\n--- Mise suggérée (Kelly 0.25, plafond 5% par pari, exposition "
                  f"simultanée de la semaine ≤ {cap:.0%}){capped} ---")
            stakes = final_stakes(res, no_stake)
            raw = res.get("stakes") or match_stakes(res, no_stake)
            for key, label in (("home", f"Victoire {home}"), ("draw", "Match nul"),
                               ("away", f"Victoire {away}")):
                stake = stakes.get(key, 0.0)
                if stake <= 0:
                    continue
                odds = res["best_odds"][key]
                suffix = (f"  [brut {raw[key]:.1%} ×{factor:.2f}]" if factor < 1 else "")
                print(f"  {label:<22}: cote {odds:.2f}  |  p={fmt(final[key])}  |  "
                      f"mise conseillée {stake:.1%} de bankroll  "
                      f"({risk_label(stake)}){suffix}")
            if not stakes:
                print("  Aucune mise : le plafond d'exposition de la semaine est déjà "
                      "atteint (la value ci-dessus reste valable)."
                      if raw else
                      "  Aucune issue ne présente de value suffisante — pas de mise.")
            print("  (Estimation mathématique, pas un conseil financier.)")

    if contest is not None:
        run_contest_mode(grid, final, contest, exact_bonus, home, away)


def no_odds_recap(without_odds, total):
    """Récapitulatif des matchs partis sans cote marché, à la fin d'un run.

    Un slate de 29 affiches défile trop vite pour qu'on remarque, ligne à ligne,
    que 22 d'entre elles tournent en modèle pur. Sans ce bloc, l'information ne
    ressort qu'en relisant le journal — c'est-à-dire jamais."""
    if not without_odds:
        return ""
    by_reason = {}
    for res in without_odds:
        by_reason.setdefault(res.get("no_odds_reason") or DEFAULT_NO_ODDS_REASON,
                             []).append(res)
    lines = [f"⚠ {len(without_odds)}/{total} match(s) sans cote marché : modèle pur, "
             f"garde-fou marché désactivé (poids marché 0 %)."]
    for reason in sorted(by_reason):
        lines.append(f"  [{reason}] {NO_ODDS_REASONS[reason]}")
        for res in by_reason[reason]:
            lines.append(f"    - {res['league']} {res['home']}-{res['away']} "
                         f"({res['date']})")
    if DEFAULT_NO_ODDS_REASON in by_reason:
        lines.append(f"  (précise la cause avec --no-odds-reason "
                     f"{{{','.join(DECLARABLE_NO_ODDS_REASONS)}}} pour que le journal "
                     f"garde la trace de la vraie raison.)")
    return "\n".join(lines)


def run_contest_mode(grid, final, pts, bonus, home, away):
    """Mode concours : maximise l'espérance de POINTS, pas la probabilité brute.
    EP(issue) = pts[issue]·P(issue) + bonus·P(meilleur score conditionnel)."""
    cond = {"home": lambda h, a: h > a, "draw": lambda h, a: h == a,
            "away": lambda h, a: h < a}
    labels = {"home": f"Victoire {home}", "draw": "Nul", "away": f"Victoire {away}"}
    rows = []
    for o in ISSUES:
        scores = sorted(((s, p) for s, p in grid.items() if cond[o](*s)), key=lambda x: -x[1])
        best_s, best_p = scores[0]
        rows.append({"o": o, "ep": pts[o] * final[o] + bonus * best_p,
                     "ep_outcome": pts[o] * final[o], "score": best_s, "p_score": best_p})
    rows.sort(key=lambda r: -r["ep"])
    fav = max(final, key=final.get)

    print("\n--- MODE CONCOURS (espérance de points, pas probabilité) ---")
    print(f"Barème : {labels['home']}={pts['home']:g} | Nul={pts['draw']:g} | "
          f"{labels['away']}={pts['away']:g} | bonus score exact={bonus:g}")
    print(f"{'Pick':<24}{'P(issue)':>10}{'EP issue':>10}{'Score':>8}{'P(score)':>10}{'EP TOTAL':>10}")
    for r in rows:
        s = f"{r['score'][0]}-{r['score'][1]}"
        print(f"{labels[r['o']]:<24}{fmt(final[r['o']]):>10}{r['ep_outcome']:>10.2f}"
              f"{s:>8}{fmt(r['p_score']):>10}{r['ep']:>10.2f}")
    top = rows[0]
    ts = f"{top['score'][0]}-{top['score'][1]}"
    print(f"\n>>> PICK CONCOURS : {labels[top['o']]}, score {ts} (espérance {top['ep']:.2f} pts)")
    if top["o"] != fav:
        naive = next(r for r in rows if r["o"] == fav)
        print(f"    ⚠ Le pick EP diverge du favori probabiliste ({labels[fav]}, "
              f"EP {naive['ep']:.2f}) : le barème paye l'écart (+{top['ep'] - naive['ep']:.2f} pts).")


# ---------------------------------------------------------------------------
# Journal (format track.py, relisible par le skill)
# ---------------------------------------------------------------------------

def load_journal(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []


def save_journal(path, entries):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=2))


def prediction_bets(res, no_stake=False):
    """Paris théoriques d'une prédiction : une entrée par issue dont la mise
    Kelly est strictement positive, aux cotes effectivement utilisées.

    Purement descriptif — c'est la trace de ce que la section 💰 a affiché, pour
    pouvoir en mesurer le P&L a posteriori. Aucune mise n'est placée et rien
    ici ne décide de parier : la décision reste humaine."""
    if no_stake or not res.get("best_odds"):
        return []
    raw = res.get("stakes")
    if raw is None:
        raw = match_stakes(res, no_stake)
    factor = res.get("exposure_factor", 1.0)
    stakes = final_stakes(res, no_stake)
    bets = []
    for issue in ISSUES:
        if issue not in stakes:
            continue
        bet = {"issue": issue, "odds": float(res["best_odds"][issue]),
               "stake_pct": round(stakes[issue], 6)}
        if factor < 1.0:
            # On garde la mise avant plafond : sans elle, impossible de relire
            # a posteriori si le plafond a mordu et de combien.
            bet["stake_pct_uncapped"] = round(raw[issue], 6)
            bet["exposure_factor"] = round(factor, 6)
        bets.append(bet)
    return bets


def log_prediction(path, res, no_stake=False):
    """Journalise (ou met à jour) la prédiction. Idempotent : un ré-run du même
    match/date écrase l'entrée non réglée au lieu d'en créer une seconde."""
    entries = load_journal(path)
    match = f"{res['home']}-{res['away']}"
    date_iso = res["date"].isoformat()
    entry = {
        "match": match, "date": date_iso, "competition": res["league"],
        "probs": res["final"],
        "market_probs": res["market"],
        "predicted_score": best_score_for_outcome(res["grid"], max(res["final"], key=res["final"].get)),
        "bets": prediction_bets(res, no_stake),
        "actual_score": None, "actual_ht": None,
        "meta": {"model": "M5", "home": res["home"], "away": res["away"],
                 "lambda_home": round(res["lam_h"], 3),
                 "lambda_away": round(res["lam_a"], 3),
                 "market_weight": round(res["market_weight"], 3),
                 "odds_age_days": res["odds_age_days"],
                 "devig": res.get("devig"),
                 "exposure_factor": round(res.get("exposure_factor", 1.0), 6),
                 "correlated_exposure": res.get("correlated", False),
                 "no_odds_reason": res.get("no_odds_reason"),
                 "lineup_adjustment": res.get("lineup_adjustment") or {"applied": False},
                 # Informationnel (roadmap A1) : probas calibrées si figées
                 # (backtest_derived.py --tune), sinon brutes (calibrated=False).
                 # N'alimente PAS bets/stakes — le staking reste 1N2 uniquement.
                 "derived_markets": {m: {"p": round(p, 4), "calibrated": calibrated}
                                     for m, (p, calibrated) in res.get("derived_markets", {}).items()}},
    }
    for i, e in enumerate(entries):
        if e["match"] == match and e["date"] == date_iso and e.get("actual_score") is None:
            entries[i] = entry
            break
    else:
        entries.append(entry)
    save_journal(path, entries)
    return entry


# Sources de cotes acceptées pour calculer un CLV (valeurs de matches.odds_source,
# cf. footballdata.ODDS_1X2).
#
# Le CLV n'a de sens que contre une VRAIE ligne de CLÔTURE d'un book SHARP. La
# clôture Pinnacle est l'étalon du pari sportif : c'est la ligne la plus
# informée du marché au coup d'envoi, et « battre la clôture » ne veut dire
# quelque chose que par rapport à elle. Les autres valeurs que football-data
# peut poser dans odds_source ne conviennent pas :
#   - `avg_close`   : moyenne d'un panel de books, marges hétérogènes, tirée
#                     vers le bas par les books soft — un CLV positif contre
#                     cette moyenne peut n'être qu'une marge soft, pas un edge ;
#   - `pinnacle_open` / `avg_open` : ce sont des OUVERTURES. Comparer une cote
#                     prise à J-3 à une ouverture ne mesure pas « la cote a-t-elle
#                     raccourci jusqu'à la clôture » — souvent le signe s'inverse.
#
# Un CLV calculé contre ces lignes-là ne serait pas un CLV « approximatif » :
# ce serait une autre grandeur, publiée sous le nom de CLV. On préfère donc ne
# rien poser et dire pourquoi (`bets[].clv_skipped`) plutôt que de gonfler
# l'échantillon avec des valeurs non comparables.
CLV_SHARP_SOURCES = ("pinnacle_close",)
CLV_SKIP_REASONS = {
    "no_closing_odds": "aucune cote de clôture en base pour ce match "
                       "(odds_* NULL), ou résultat saisi manuellement",
    "source_not_sharp": f"cote de clôture d'une source non sharp "
                        f"(CLV exigé sur {'/'.join(CLV_SHARP_SOURCES)})",
}


def clv_source_ok(closing_odds):
    """(acceptable ?, raison_de_refus) pour un bloc de cotes de clôture."""
    if not closing_odds:
        return False, "no_closing_odds"
    if closing_odds.get("source") not in CLV_SHARP_SOURCES:
        return False, "source_not_sharp"
    return True, None


def settle_entry(entry, actual, actual_ht=None, closing_odds=None):
    """Pose le résultat réel sur une entrée et règle ses paris théoriques.

    Un pari déjà réglé (champ `realized_pct` présent) n'est jamais recalculé —
    le P&L d'un match est figé une fois posé. Idem pour `clv_pct` : une fois
    posé il ne bouge plus, même si settle_entry est rappelé sans closing_odds
    (ex: `record_result` manuel, qui n'a pas accès à football.db).

    closing_odds, quand fourni (par sync_results, seul appelant qui a accès à
    la base), est {"home":.., "draw":.., "away":.., "source":..} — les cotes
    de clôture de matches.odds_* pour ce match. On pose alors sur chaque pari
    théorique son CLV (closing line value) : l'écart entre la cote prise et
    cette clôture. C'est la mesure qui répond à la question qu'un ROI sur
    quelques dizaines de paris ne peut pas trancher — le pari avait-il une
    vraie value, ou la cote prise était-elle simplement mauvaise/périmée ?

    Le CLV n'est posé que si `closing_odds["source"]` est une clôture sharp
    (CLV_SHARP_SOURCES). Sinon le pari reçoit `clv_skipped` avec la raison, et
    reste hors de la statistique CLV : mieux vaut un échantillon plus petit mais
    homogène qu'un chiffre qui mélange clôtures sharp, moyennes de books et
    ouvertures sous une même étiquette.
    """
    entry["actual_score"] = actual
    entry["actual_ht"] = actual_ht
    if closing_odds is not None:
        entry["closing_odds"] = closing_odds
    sharp_ok, skip_reason = clv_source_ok(closing_odds)
    winner = ISSUES[_outcome_index_score(actual)]
    for bet in entry.get("bets") or []:
        if "realized_pct" not in bet:
            stake, odds = float(bet["stake_pct"]), float(bet["odds"])
            gain = stake * (odds - 1.0) if bet["issue"] == winner else -stake
            bet["realized_pct"] = round(gain, 6)
        if "clv_pct" in bet:
            continue
        if sharp_ok and closing_odds.get(bet["issue"]):
            bet["clv_pct"] = round(float(bet["odds"]) / float(closing_odds[bet["issue"]]) - 1.0, 6)
            bet.pop("clv_skipped", None)
        else:
            # issue absente d'un bloc pourtant sharp : la clôture manque pour
            # CETTE issue, ce qui est bien un défaut de clôture.
            bet["clv_skipped"] = skip_reason or "no_closing_odds"
    return entry


def record_result(path, match, actual, actual_ht=None):
    entries = load_journal(path)
    for e in reversed(entries):
        if e["match"].lower() == match.lower() and e.get("actual_score") is None:
            settle_entry(e, actual, actual_ht)
            save_journal(path, entries)
            return e
    sys.exit(f"Aucune prédiction non réglée pour '{match}' dans {path}.")


# ---------------------------------------------------------------------------
# Synchronisation des résultats depuis football.db (fermeture de la boucle)
#
# `result` demande une commande manuelle par match — en pratique le journal
# reste vide et le monitoring ne mesure rien. `sync-results` va chercher les
# scores dans la base déjà alimentée par pipeline.py --update. Règle absolue :
# on ne remplit que ce que la source contient, jamais un score deviné.
# ---------------------------------------------------------------------------

SYNC_TOLERANCE_DAYS = 2   # report de calendrier toléré (même convention que xgjoin)


def league_teams(conn, league):
    """Noms canoniques des équipes vues dans la base pour cette ligue."""
    if not league:
        return []
    rows = conn.execute(
        "SELECT DISTINCT home AS t FROM matches WHERE league = ? "
        "UNION SELECT DISTINCT away AS t FROM matches WHERE league = ?",
        (league, league))
    return [r["t"] for r in rows]


def split_match_key(match, teams, alias_map):
    """'Domicile-Extérieur' -> (domicile, extérieur) canoniques, ou None.

    Un nom d'équipe peut contenir un tiret : on essaie chaque coupure et on ne
    retient que celles dont les DEUX moitiés se résolvent. Si zéro ou plusieurs
    coupures conviennent, on renvoie None — mieux vaut laisser l'entrée en
    attente que de régler le mauvais match."""
    cands = []
    for i, ch in enumerate(match):
        if ch != "-" or i == 0 or i == len(match) - 1:
            continue
        home, home_ok = resolve_team(match[:i], teams, alias_map)
        away, away_ok = resolve_team(match[i + 1:], teams, alias_map)
        if home_ok and away_ok and home != away:
            cands.append((home, away))
    return cands[0] if len(cands) == 1 else None


def entry_teams(entry, teams, alias_map):
    """(domicile, extérieur) d'une entrée : depuis meta si présent (entrées
    récentes), sinon en redécoupant la clé 'match' (entrées historiques)."""
    meta = entry.get("meta") or {}
    if meta.get("home") and meta.get("away"):
        return meta["home"], meta["away"]
    return split_match_key(entry["match"], teams, alias_map)


def find_actual_result(conn, league, home, away, date_iso, tolerance=SYNC_TOLERANCE_DAYS):
    """(ligne, décalage_en_jours) du match joué correspondant, ou (None, None).

    Fenêtre de ±tolerance jours autour de la date prévue : un match reporté
    garde le même couple d'équipes, et deux fois la même affiche en 5 jours
    n'existe pas. Une ligne sans score (fthg NULL) n'est jamais renvoyée."""
    target = datetime.date.fromisoformat(date_iso)
    lo = (target - datetime.timedelta(days=tolerance)).isoformat()
    hi = (target + datetime.timedelta(days=tolerance)).isoformat()
    rows = conn.execute(
        "SELECT date, fthg, ftag, hthg, htag, odds_h, odds_d, odds_a, odds_source FROM matches "
        "WHERE league = ? AND home = ? AND away = ? AND fthg IS NOT NULL "
        "AND ftag IS NOT NULL AND date BETWEEN ? AND ?",
        (league, home, away, lo, hi)).fetchall()
    if not rows:
        return None, None
    shift = lambda r: (datetime.date.fromisoformat(r["date"]) - target).days
    best = min(rows, key=lambda r: abs(shift(r)))
    return best, shift(best)


def sync_results(conn, path, as_of=None):
    """Remplit actual_score des matchs passés depuis football.db.

    Renvoie (synchronisés, en_attente). Un match passé absent de la base (source
    en retard, alias manquant) reste `null` et ressort en attente : on n'invente
    jamais un score. Suppose que `pipeline.py --update` a déjà tourné."""
    as_of = as_of or datetime.date.today()
    entries = load_journal(path)
    alias_map = db.load_aliases(conn)
    teams_cache = {}
    synced, pending = [], []
    for e in entries:
        if e.get("actual_score") is not None or e["date"] >= as_of.isoformat():
            continue
        league = e.get("competition")
        if league not in teams_cache:
            teams_cache[league] = league_teams(conn, league)
        pair = entry_teams(e, teams_cache[league], alias_map)
        if pair is None:
            pending.append({"match": e["match"], "date": e["date"],
                            "reason": "équipes non résolues (alias manquant ?)"})
            continue
        row, shift = find_actual_result(conn, league, pair[0], pair[1], e["date"])
        if row is None:
            pending.append({"match": e["match"], "date": e["date"],
                            "reason": "absent de football.db (source en retard ?)"})
            continue
        actual_ht = (f"{row['hthg']}-{row['htag']}"
                     if row["hthg"] is not None and row["htag"] is not None else None)
        closing = None
        if row["odds_h"] is not None and row["odds_d"] is not None and row["odds_a"] is not None:
            closing = {"home": row["odds_h"], "draw": row["odds_d"], "away": row["odds_a"],
                      "source": row["odds_source"]}
        settle_entry(e, f"{row['fthg']}-{row['ftag']}", actual_ht, closing)
        bets = e.get("bets") or []
        synced.append({"match": e["match"], "date": e["date"],
                       "actual": f"{row['fthg']}-{row['ftag']}", "shift": shift,
                       "bets_clv": [b["clv_pct"] for b in bets if "clv_pct" in b],
                       "clv_skipped": [b["clv_skipped"] for b in bets
                                       if "clv_pct" not in b and b.get("clv_skipped")],
                       "closing_source": (closing or {}).get("source")})
    if synced:
        save_journal(path, entries)
    return synced, pending


# ---------------------------------------------------------------------------
# Rapport de calibration mensuel
# ---------------------------------------------------------------------------

def _outcome_index_score(score):
    h, a = (int(x) for x in score.split("-"))
    return backtest.outcome_index(h, a)


def _probs_tuple(d):
    return (d["home"], d["draw"], d["away"])


def rps(probs, outcome):
    o = [1.0 if k == outcome else 0.0 for k in range(3)]
    return ((probs[0] - o[0]) ** 2 + ((probs[0] + probs[1]) - (o[0] + o[1])) ** 2) / 2.0


def relative_delta(brier, brier_market):
    """Écart au marché en %, RELATIF — même formule que « Écart rel. marché »
    dans report35.py : (Brier − Brier marché) / Brier marché × 100.

    C'est la seule échelle directement comparable au +1,78 % du backtest M3.5,
    que la routine de suivi mensuel demande de confronter au chiffre du mois.
    L'écart absolu qui figurait ici valait ~1,75× moins sur les mêmes données."""
    return (brier - brier_market) / brier_market * 100


def paired_briers(entries):
    """(Brier FINAL, Brier marché) match par match, sur les entrées qui ont les
    DEUX — séries appariées, dans le même ordre, prêtes pour le bootstrap.

    Les entrées sans `market_probs` (modèle pur) sont exclues des deux séries :
    un Δ vs marché ne se calcule que là où le marché existe."""
    model_b, market_b = [], []
    for e in entries:
        if not e.get("market_probs"):
            continue
        outcome = _outcome_index_score(e["actual_score"])
        model_b.append(backtest.brier(_probs_tuple(e["probs"]), outcome))
        market_b.append(backtest.brier(_probs_tuple(e["market_probs"]), outcome))
    return model_b, market_b


def delta_ci(entries):
    """(point, bas, haut) de l'écart relatif au marché, par bootstrap apparié."""
    model_b, market_b = paired_briers(entries)
    if not model_b:
        return None, None, None
    return bootstrap.ci_relative_delta(model_b, market_b)


def month_metrics(entries):
    n = len(entries)
    b_final = b_mkt = rps_sum = 0.0
    issue_hits = exact_hits = draw_pred = draw_obs = 0.0
    n_mkt = 0
    for e in entries:
        outcome = _outcome_index_score(e["actual_score"])
        pf = _probs_tuple(e["probs"])
        b_final += backtest.brier(pf, outcome)
        rps_sum += rps(pf, outcome)
        draw_pred += e["probs"]["draw"]
        draw_obs += 1.0 if outcome == 1 else 0.0
        if _outcome_index_score(e["predicted_score"]) == outcome:
            issue_hits += 1
        if e["predicted_score"] == e["actual_score"]:
            exact_hits += 1
        if e.get("market_probs"):
            b_mkt += backtest.brier(_probs_tuple(e["market_probs"]), outcome)
            n_mkt += 1
    return {
        "n": n, "brier": b_final / n, "rps": rps_sum / n,
        "issue_rate": issue_hits / n, "exact_rate": exact_hits / n,
        "draw_pred": draw_pred / n, "draw_obs": draw_obs / n,
        "brier_market": (b_mkt / n_mkt) if n_mkt else None, "n_market": n_mkt,
    }


# --- Découpage par fraîcheur des cotes ------------------------------------
#
# Le Brier global agrège des prédictions faites à 92 % de marché et d'autres à
# 28 % : un bon chiffre d'ensemble peut masquer une sous-performance propre aux
# cotes périmées. C'est précisément la zone que backtest_blend.py reconnaît ne
# pas savoir simuler (son proxy sous-estime la péremption réelle), donc la seule
# mesure possible est celle-ci, en production.

BUCKET_MIN_N = 15   # sous ce seuil, lecture indicative et aucun delta

# Seuil d'alerte du bucket « périmées », en points de la colonne « Δ vs marché »
# (écart RELATIF, cf. relative_delta). L'ancien seuil valait 2 pts sur l'échelle
# absolue ; rapporté à un Brier de marché de l'ordre de 0,60 sur du 1N2, cela
# correspond à ~3,3 pts relatifs. Arrondi à 3, donc légèrement plus sensible que
# l'équivalent exact, et lisible face à la référence du backtest (+1,78 % du
# marché) : au-delà, l'écart périmées/fraîches dépasse à lui seul tout l'écart
# modèle/marché mesuré en backtest.
STALE_ALERT_GAP_PCT = 3.0

BUCKET_ORDER = ("fraiches", "intermediaires", "perimees", "inconnue")


def bucket_labels():
    """Libellés des buckets, dérivés des seuils de market_weight() (pas de
    duplication : si un seuil bouge, le rapport suit)."""
    return {
        "fraiches": f"Fraîches (≤ {FRESH_MAX_DAYS} j, poids marché {DEFAULT_BLEND:.0%})",
        "intermediaires": f"Intermédiaires ({FRESH_MAX_DAYS + 1}–{STALE_MIN_DAYS - 1} j, "
                          f"poids dégressif)",
        "perimees": f"Périmées (≥ {STALE_MIN_DAYS} j, poids marché {STALE_FLOOR:.0%})",
        "inconnue": "Fraîcheur non renseignée (hors barème)",
    }


def freshness_bucket(entry):
    """Bucket de fraîcheur d'une entrée, aux seuils exacts de market_weight()."""
    age = (entry.get("meta") or {}).get("odds_age_days")
    if age is None:
        return "inconnue"
    if age <= FRESH_MAX_DAYS:
        return "fraiches"
    if age >= STALE_MIN_DAYS:
        return "perimees"
    return "intermediaires"


def freshness_section(settled):
    """Lignes markdown de la section « Par fraîcheur des cotes »."""
    labels = bucket_labels()
    by_bucket = {}
    for e in settled:
        by_bucket.setdefault(freshness_bucket(e), []).append(e)

    lines = ["## Par fraîcheur des cotes", "",
             f"Découpage sur `meta.odds_age_days` aux seuils du pont marché/modèle "
             f"(`market_weight`) : ≤ {FRESH_MAX_DAYS} j = poids de base "
             f"{DEFAULT_BLEND:.0%}, ≥ {STALE_MIN_DAYS} j = plancher {STALE_FLOOR:.0%}. "
             f"Le Brier global mélange les deux régimes ; c'est ici que se voit une "
             f"sous-performance propre aux cotes périmées.", "",
             "| Fraîcheur | n | Brier | Brier marché | Δ vs marché | IC 95 % du Δ | Lecture |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    deltas = {}
    for key in BUCKET_ORDER:
        rows = by_bucket.get(key)
        if not rows:
            continue
        m = month_metrics(rows)
        bmkt = f"{m['brier_market']:.4f}" if m["brier_market"] is not None else "—"
        ci = "—"
        if m["n"] < BUCKET_MIN_N:
            delta, read = "—", f"indicative (n < {BUCKET_MIN_N})"
        elif m["brier_market"] is None:
            delta, read = "—", "aucune cote journalisée"
        elif m["n_market"] < BUCKET_MIN_N:
            delta, read = "—", f"indicative ({m['n_market']} match(s) avec cotes)"
        else:
            d = relative_delta(m["brier"], m["brier_market"])
            deltas[key] = d
            _, lo, hi = delta_ci(rows)
            ci = bootstrap.fmt_ci(lo, hi)
            delta, read = f"{d:+.2f} %", "exploitable"
        lines.append(f"| {labels[key]} | {m['n']} | {m['brier']:.4f} | {bmkt} | "
                     f"{delta} | {ci} | {read} |")
    lines.append("")

    stale, fresh = deltas.get("perimees"), deltas.get("fraiches")
    if stale is not None and fresh is not None:
        gap = stale - fresh
        sm, smk = paired_briers(by_bucket["perimees"])
        fm, fmk = paired_briers(by_bucket["fraiches"])
        _, glo, ghi = bootstrap.ci_gap_relative_delta(sm, smk, fm, fmk)
        gap_ci = bootstrap.fmt_ci(glo, ghi, unit="pts")
        # Deux conditions pour alerter, pas une : l'écart doit dépasser le seuil
        # ET son intervalle doit exclure 0. Sur n ≈ 15 par bucket, un écart de
        # 3 pts sort tout seul du bruit une fois sur deux ; alerter dessus, c'est
        # se préparer à réviser un barème validé en backtest sur du hasard.
        significant = bootstrap.excludes_zero(glo, ghi)
        if gap > STALE_ALERT_GAP_PCT and significant:
            lines += [f"⚠ Les cotes périmées performent moins bien que prévu par le "
                      f"backtest — le garde-fou mérite d'être revu.",
                      "",
                      f"  (Δ vs marché : {stale:+.2f} % sur cotes périmées contre "
                      f"{fresh:+.2f} % sur cotes fraîches, soit {gap:+.2f} pts d'écart "
                      f"{gap_ci}, au-delà du seuil de {STALE_ALERT_GAP_PCT:.0f} pts et "
                      f"distinguable du bruit. À relire sur un trimestre complet avant "
                      f"de toucher au barème.)", ""]
        elif gap > STALE_ALERT_GAP_PCT:
            lines += [f"- Écart périmées − fraîches : {gap:+.2f} pts de Δ vs marché "
                      f"{gap_ci} — au-dessus du seuil de {STALE_ALERT_GAP_PCT:.0f} pts, "
                      f"mais l'intervalle contient 0 : **pas d'alerte**, l'écart n'est "
                      f"pas distinguable du bruit d'échantillonnage. À revoir quand les "
                      f"deux buckets auront grossi.", ""]
        else:
            lines += [f"- Écart périmées − fraîches : {gap:+.2f} pt(s) de Δ vs marché "
                      f"{gap_ci} (seuil d'alerte {STALE_ALERT_GAP_PCT:.0f} pts) — "
                      f"le barème tient.", ""]
    elif "perimees" in by_bucket:
        lines += [f"- Comparaison périmées vs fraîches indisponible : il faut "
                  f"n ≥ {BUCKET_MIN_N} avec cotes dans LES DEUX buckets.", ""]
    return lines


# --- ROI réalisé des mises Kelly théoriques --------------------------------

ROI_MIN_BETS = 100   # sous ce seuil, la variance des cotes 1N2 rend le ROI non informatif


def roi_summary(settled):
    """(nb_paris_réglés, mise_totale, p_and_l) en fractions de bankroll."""
    n = 0
    staked = pnl = 0.0
    for e in settled:
        for b in e.get("bets") or []:
            if "realized_pct" not in b:
                continue
            n += 1
            staked += float(b["stake_pct"])
            pnl += float(b["realized_pct"])
    return n, staked, pnl


def roi_section(settled):
    """Lignes markdown de la section « ROI réel (mise Kelly théorique) »."""
    n, staked, pnl = roi_summary(settled)
    lines = ["## ROI réel (mise Kelly théorique)", ""]
    if not n:
        lines += ["Aucun pari réglé sur la période : soit les prédictions n'avaient "
                  "pas de cote exploitable, soit leurs résultats ne sont pas encore "
                  "synchronisés (`python predict.py sync-results`).", ""]
        return lines
    lines += [f"- {n} pari(s) réglé(s) — mise cumulée {staked:.2%} de bankroll "
              f"(somme des mises successives, pas une exposition simultanée), "
              f"P&L {pnl:+.3%} de bankroll"
              + (f", soit un ROI de {pnl / staked:+.1%} de la mise." if staked else "."),
              "- Ce ROI est **théorique** : les mises n'ont jamais été placées, elles "
              "sont recalculées depuis les cotes journalisées (Kelly 0.25 plafonné à "
              "5 %). Ce n'est pas un P&L vérifié par un bookmaker."]
    if n < ROI_MIN_BETS:
        lines.append(f"- ⚠ {n} paris réglés (< {ROI_MIN_BETS}) : échantillon insuffisant "
                     f"pour une lecture fiable du ROI — la variance sur des cotes 1N2 "
                     f"rend un tel échantillon quasi non-informatif.")
    lines.append("")
    return lines


# --- CLV (closing line value) des paris théoriques -------------------------
#
# Le ROI a besoin de ~100 paris réglés pour dire quelque chose (variance du
# foot). Le CLV — l'écart entre la cote prise et la cote de clôture — converge
# beaucoup plus vite : ce n'est pas un pari gagné ou perdu (bruit binaire),
# c'est un mouvement de prix continu. C'est la seule mesure disponible
# aujourd'hui pour juger un pari isolé (ex: une grosse cote sur un match qui
# n'est pas encore réglé) sans attendre un échantillon massif.

CLV_MIN_BETS = 20   # bien plus bas que ROI_MIN_BETS : le CLV converge plus vite,
                     # mais reste indicatif en-deçà de ce seuil.


def clv_values(settled):
    """CLV de chaque pari réglé dont la clôture sharp est connue."""
    return [float(b["clv_pct"]) for e in settled for b in (e.get("bets") or [])
            if "clv_pct" in b]


def clv_summary(settled):
    """(n, clv_moyen, taux_positif) sur les paris dont la clôture est connue."""
    vals = clv_values(settled)
    if not vals:
        return 0, None, None
    n = len(vals)
    return n, sum(vals) / n, sum(1 for v in vals if v > 0) / n


def clv_skipped_summary(settled):
    """{raison: nombre de paris réglés sans CLV} — pourquoi l'échantillon est petit.

    Sans ce comptage, un « 4 paris avec clôture connue » ne dit pas si les
    autres attendent la source ou ont été écartés faute de ligne sharp. Le
    second cas est structurel (la base n'a que la moyenne du marché pour ce
    match) et ne se résoudra pas en attendant."""
    counts = {}
    for e in settled:
        for b in e.get("bets") or []:
            if "clv_pct" in b or "realized_pct" not in b:
                continue
            reason = b.get("clv_skipped") or "no_closing_odds"
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def clv_section(settled):
    """Lignes markdown de la section « CLV (closing line value) »."""
    n, avg, positive = clv_summary(settled)
    skipped = clv_skipped_summary(settled)
    lines = ["## CLV (closing line value)", "",
             "Écart entre la cote prise et la cote de clôture (`matches.odds_*`, "
             "posée par `sync-results`) sur chaque pari théorique réglé : "
             "`clv_pct = cote_prise / cote_clôture − 1`. Positif = la cote a "
             "raccourci après la prise (le pari devançait le marché) ; négatif "
             "= elle s'est détendue (la « value » vue au moment du pari a fondu, "
             "voire n'en était pas une). Le CLV converge plus vite que le ROI "
             "réel — c'est le premier signal à lire sur un petit échantillon.",
             "",
             f"**Clôture sharp exigée** : seuls les paris dont la clôture porte "
             f"`odds_source` ∈ {{{', '.join('`' + x + '`' for x in CLV_SHARP_SOURCES)}}} "
             f"entrent ici. Une moyenne de books (`avg_close`) ou une ouverture "
             f"(`*_open`) mesurerait autre chose sous le même nom : battre une "
             f"moyenne tirée par des books soft n'est pas battre le marché, et "
             f"comparer à une ouverture inverse souvent le signe. Les paris "
             f"écartés sont comptés ci-dessous, jamais mélangés à la moyenne.",
             ""]
    if not n:
        lines += ["Aucun pari réglé avec clôture sharp connue : soit aucun "
                  "pari théorique n'a encore de résultat, soit la clôture "
                  "sharp manquait pour ces matchs.", ""]
        lines += _clv_skipped_lines(skipped)
        return lines
    _, lo, hi = bootstrap.ci_mean(clv_values(settled))
    ci_txt = f" — IC 95 % {bootstrap.fmt_ci(lo * 100, hi * 100)}" if lo is not None else ""
    lines += [f"- {n} pari(s) avec clôture sharp — CLV moyen {avg:+.2%}, "
              f"positif sur {positive:.0%} des paris{ci_txt}."]
    significant = bootstrap.excludes_zero(lo, hi)
    if n < CLV_MIN_BETS:
        lines.append(f"- ⚠ {n} pari(s) (< {CLV_MIN_BETS}) : lecture indicative — "
                     f"le CLV converge vite mais pas instantanément.")
    elif significant is False:
        lines.append("- CLV moyen non distinguable de zéro (l'IC contient 0) : "
                     "les paris pris ne devancent ni ne suivent le marché de "
                     "façon mesurable. Pas d'edge démontré, pas d'alerte non plus.")
    elif avg < 0:
        lines.append("- ⚠ CLV moyen négatif ET distinguable de zéro : les cotes "
                     "prises perdent de la valeur avant la clôture — signe que "
                     "l'edge apparent au moment du pari n'était pas réel.")
    else:
        lines.append("- CLV moyen positif et distinguable de zéro : cohérent avec "
                     "un vrai edge (à confirmer par le ROI réel une fois n ≥ 100).")
    lines.append("")
    lines += _clv_skipped_lines(skipped)
    return lines


def _clv_skipped_lines(skipped):
    """Détail des paris réglés restés sans CLV, par raison."""
    if not skipped:
        return []
    total = sum(skipped.values())
    lines = [f"Paris réglés sans CLV ({total}) :"]
    for reason, count in sorted(skipped.items()):
        lines.append(f"- `{reason}` × {count} — "
                     f"{CLV_SKIP_REASONS.get(reason, 'raison inconnue')}.")
    if skipped.get("source_not_sharp"):
        lines.append("  Ces paris-là n'attendent rien : la base n'a pas de clôture "
                     "sharp pour ces matchs, relancer `sync-results` n'y changera "
                     "rien.")
    lines.append("")
    return lines


def build_calibration_report(path, month_filter=None):
    settled = [e for e in load_journal(path) if e.get("actual_score")]
    if month_filter:
        settled = [e for e in settled if e["date"].startswith(month_filter)]
    if not settled:
        sys.exit("Aucune prédiction réglée dans le journal (avec ce filtre) — rien à mesurer.")

    by_month = {}
    for e in settled:
        by_month.setdefault(e["date"][:7], []).append(e)

    lines = ["# Monitoring de production — calibration mensuelle", ""]
    lines += [f"Journal : `{path}` — {len(settled)} prédiction(s) réglée(s), "
              f"{len(by_month)} mois. Référence Brier hasard = 0.6667 (plus bas = mieux). "
              f"« Δ vs marché » = écart **relatif** (Brier − Brier marché) / Brier marché, "
              f"même échelle que le backtest (M3.5 : +1,78 % du marché).", ""]
    lines += ["## Par mois", "",
              "| Mois | n | Brier | Brier marché | Δ vs marché | IC 95 % du Δ | RPS | Issue OK | Score exact | Nuls prédits/obs |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

    def month_row(label, entries):
        m = month_metrics(entries)
        bmkt = f"{m['brier_market']:.4f}" if m["brier_market"] is not None else "—"
        if m["brier_market"] is None:
            delta, ci = "—", "—"
        else:
            point, lo, hi = delta_ci(entries)
            delta = f"{relative_delta(m['brier'], m['brier_market']):+.2f} %"
            ci = bootstrap.fmt_ci(lo, hi)
        return (f"| {label} | {m['n']} | {m['brier']:.4f} | {bmkt} | {delta} | {ci} | "
                f"{m['rps']:.4f} | {m['issue_rate']:.0%} | {m['exact_rate']:.0%} | "
                f"{m['draw_pred']:.0%} / {m['draw_obs']:.0%} |")

    for month in sorted(by_month):
        lines.append(month_row(month, by_month[month]))
    overall = month_metrics(settled)
    lines.append(month_row("**Total**", settled))
    lines.append("")
    lines += [f"L'IC 95 % est un bootstrap **apparié** ({bootstrap.DEFAULT_RESAMPLES} "
              f"rééchantillonnages de matchs, graine {bootstrap.DEFAULT_SEED} pour que "
              f"le rapport se régénère à l'identique) : modèle et marché sont notés sur "
              f"les mêmes matchs, on rééchantillonne donc les matchs, pas deux séries "
              f"indépendantes. Un intervalle qui contient 0 veut dire que le mois ne "
              f"permet pas de distinguer le FINAL du marché — c'est le cas normal sur "
              f"quelques dizaines de matchs, et la raison pour laquelle un Δ mensuel "
              f"isolé ne justifie jamais de toucher aux réglages figés.", ""]

    lines += freshness_section(settled)
    lines += roi_section(settled)
    lines += clv_section(settled)

    # Focus sur le dernier mois (ou le mois filtré)
    focus = month_filter or sorted(by_month)[-1]
    if focus in by_month:
        m = month_metrics(by_month[focus])
        lines += [f"## Focus {focus}", "",
                  f"- {m['n']} match(s) réglé(s), Brier {m['brier']:.4f}, issues correctes "
                  f"{m['issue_rate']:.0%}, scores exacts {m['exact_rate']:.0%}."]
        draw_gap = m["draw_obs"] - m["draw_pred"]
        if abs(draw_gap) > 0.07:
            sens = "sous-estime" if draw_gap > 0 else "surestime"
            lines.append(f"- ⚠ Nuls : le modèle {sens} les nuls ({m['draw_pred']:.0%} prédits "
                         f"vs {m['draw_obs']:.0%} observés).")
        if m["brier_market"] is not None:
            gap = m["brier"] - m["brier_market"]
            if m["n_market"] < 15:
                verdict = "échantillon trop petit pour trancher"
            elif gap < -0.01:
                verdict = "le blend bat le marché seul — la couche modèle ajoute de la valeur"
            elif gap > 0.01:
                verdict = "le marché seul fait mieux — laisser le poids marché élevé sur cotes fraîches"
            else:
                verdict = "équivalent au marché"
            lines.append(f"- FINAL vs marché ({m['n_market']} match(s) avec cotes) : "
                         f"Brier {m['brier']:.4f} vs {m['brier_market']:.4f} → {verdict}.")
        if m["n"] < 15:
            lines.append("- ⚠ Moins de 15 matchs : lecture indicative, pas de conclusion structurelle.")
    lines.append("")
    return "\n".join(lines), overall


# ---------------------------------------------------------------------------
# Export natif du skill football-match-predictor
# ---------------------------------------------------------------------------

def load_skill_json(source):
    """Charge l'export JSON du skill. source='-' lit stdin (coller sans fichier)."""
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text()
    except OSError as e:
        sys.exit(f"--from-skill-json : lecture impossible ({e}).")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"--from-skill-json : JSON malformé ({e}).")
    if not isinstance(doc, dict):
        sys.exit("--from-skill-json : la racine JSON doit être un objet.")
    if doc.get("schema") != SKILL_SCHEMA:
        sys.exit(f"--from-skill-json : schéma '{doc.get('schema')}' inattendu "
                 f"(attendu '{SKILL_SCHEMA}').")
    return doc


def skill_json_to_fixture(doc):
    """Extrait de l'export les champs mappables sur les arguments de `match`.

    Champs lus : league, home, away, match_date, odds_date, odds_1x2 et
    `no_odds_reason` (optionnel : pourquoi l'export ne porte pas de cote — c'est
    le skill qui le sait, pas predict.py). Les champs `ou` (le modèle de
    production price les scores depuis sa propre grille, il ne se cale pas sur
    les cotes O/U) et `final_probs_1x2` (predict.py recalcule son propre FINAL)
    sont ignorés — voir la note émise à l'appel."""
    league = doc.get("league")
    if league not in footballdata.LEAGUES:
        sys.exit(f"--from-skill-json : league '{league}' absente ou invalide "
                 f"(attendu l'une de {footballdata.LEAGUES}) — on ne devine pas.")
    home, away = doc.get("home"), doc.get("away")
    if not home or not away:
        sys.exit("--from-skill-json : champs 'home' et 'away' requis.")
    odds_spec = None
    o = doc.get("odds_1x2")
    if o is not None:
        try:
            odds_spec = f"{float(o['home'])},{float(o['draw'])},{float(o['away'])}"
        except (KeyError, TypeError, ValueError):
            sys.exit("--from-skill-json : 'odds_1x2' doit contenir home, draw, away numériques.")
    reason = doc.get("no_odds_reason")
    if reason is not None and reason not in DECLARABLE_NO_ODDS_REASONS:
        sys.exit(f"--from-skill-json : 'no_odds_reason' vaut '{reason}', attendu l'une de "
                 f"{list(DECLARABLE_NO_ODDS_REASONS)} — on ne devine pas une raison.")
    return {"league": league, "home": home, "away": away, "odds_spec": odds_spec,
            "match_date": doc.get("match_date"), "odds_date": doc.get("odds_date"),
            "no_odds_reason": reason}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_match(args, conn):
    if args.from_skill_json:
        if (args.home or args.away or args.fixture or args.odds or args.date
                or args.odds_date or args.odds_age_days is not None):
            sys.exit("--from-skill-json fournit déjà league/home/away/odds/dates — "
                     "ne les repasse pas aussi en arguments individuels.")
        doc = load_skill_json(args.from_skill_json)
        fx = skill_json_to_fixture(doc)
        args.league, args.home, args.away = fx["league"], fx["home"], fx["away"]
        if fx["odds_spec"]:
            args.odds = [fx["odds_spec"]]
        args.date, args.odds_date = fx["match_date"], fx["odds_date"]
        # L'export sait pourquoi il n'a pas de cote ; le CLI reste prioritaire
        # s'il en déclare une aussi (l'opérateur a le dernier mot).
        if fx["no_odds_reason"] and not args.no_odds_reason:
            args.no_odds_reason = fx["no_odds_reason"]
        if doc.get("ou") is not None:
            log.info("Export skill : champ 'ou' présent mais non consommé — le modèle de "
                     "production price les scores depuis sa propre grille entraînée, il ne se "
                     "cale pas sur les cotes O/U du marché.")


    cfg = backtest35.frozen()
    target_date = (datetime.date.fromisoformat(args.date) if args.date else next_saturday())

    odds_age = None
    if args.odds_date:
        odds_age = max(0, (target_date - datetime.date.fromisoformat(args.odds_date)).days)
    elif args.odds_age_days is not None:
        odds_age = max(0, args.odds_age_days)

    contest = None
    if args.contest_points is not None:
        try:
            ph, pn, pa = (float(x) for x in args.contest_points.split(","))
        except ValueError:
            sys.exit("--contest-points attend 'H,N,A' (ex: 13,50,68)")
        contest = {"home": ph, "draw": pn, "away": pa}

    fixtures = []
    if args.home and args.away:
        if not args.league:
            sys.exit("--home/--away nécessitent --league (ou utilise --fixture LIGUE,Dom,Ext).")
        fixtures.append((args.league, args.home, args.away))
    for spec in args.fixture:
        parts = [p.strip() for p in spec.split(",")]
        if len(parts) != 3:
            sys.exit(f"--fixture invalide: '{spec}' (attendu LIGUE,Domicile,Extérieur)")
        fixtures.append(tuple(parts))
    if not fixtures:
        sys.exit("Fournis --home/--away (avec --league) ou au moins un --fixture LIGUE,Dom,Ext.")
    no_odds_reason = args.no_odds_reason
    if args.odds and len(fixtures) > 1:
        log.warning("--odds ne s'applique qu'à un match unique — ignoré pour un slate "
                    "(%d affiches). Passe chaque match séparément pour blender ses cotes.",
                    len(fixtures))
        # Cette perte était jusqu'ici purement verbale : le journal n'en gardait
        # qu'un market_weight nul. On la nomme dans chaque entrée.
        no_odds_reason = "slate_odds_ignored"

    lineup_adjustment = None
    if args.lineup_adjustment:
        if len(fixtures) > 1:
            log.warning("--lineup-adjustment ne s'applique qu'à un match unique — ignoré "
                        "pour un slate (%d affiches). Passe chaque match séparément.",
                        len(fixtures))
        else:
            lineup_adjustment = load_lineup_adjustment(args.lineup_adjustment)

    for league, _, _ in fixtures:
        if league not in footballdata.LEAGUES:
            sys.exit(f"Ligue inconnue '{league}' (attendu {footballdata.LEAGUES}).")

    # Deux passes : le plafond d'exposition porte sur la SOMME des mises du run,
    # il ne peut donc pas se décider pendant qu'on imprime match par match.
    fit_cache = {}
    results = [predict_match(conn, cfg, league, home, away, target_date,
                             args.odds if len(fixtures) == 1 else [], odds_age,
                             args.blend, fit_cache, no_odds_reason, lineup_adjustment,
                             args.devig)
               for league, home, away in fixtures]
    # Le run courant réécrit ses propres entrées non réglées : elles ne comptent
    # pas comme exposition « déjà engagée » (sinon un ré-run se plafonnerait seul).
    rewritten = [(f"{r['home']}-{r['away']}", r["date"].isoformat()) for r in results]
    pending_matches = pending_bet_matches(args.log, target_date, rewritten)
    prior = sum(m["stake"] for m in pending_matches.values())
    prior_bets = sum(m["n_bets"] for m in pending_matches.values())
    exposure = apply_exposure_cap(results, args.exposure_cap, args.no_stake,
                                  prior, prior_bets, pending_matches)

    for i, res in enumerate(results):
        if i:
            print("\n" + "=" * 72 + "\n")
        print_prediction(res, cfg, contest, args.contest_exact_bonus, args.no_stake)
        if not args.no_log:
            log_prediction(args.log, res, args.no_stake)
    recap = no_odds_recap([r for r in results if r["market"] is None], len(fixtures))
    if recap:
        print("\n" + recap)
    exp = exposure_recap(exposure)
    if exp:
        print("\n" + exp)
    if not args.no_log:
        print(f"\n{len(fixtures)} prédiction(s) journalisée(s) dans {args.log}.")


def cmd_result(args, conn):
    e = record_result(args.log, args.match, args.actual, args.ht)
    pred_out = _outcome_index_score(e["predicted_score"])
    act_out = _outcome_index_score(args.actual)
    issue = "ISSUE OK" if pred_out == act_out else "issue ratée"
    exact = " + SCORE EXACT !" if e["predicted_score"] == args.actual else ""
    print(f"Résultat enregistré : {args.match} {args.actual} — {issue}{exact}")


def cmd_sync_results(args, conn):
    as_of = datetime.date.fromisoformat(args.as_of) if args.as_of else None
    synced, pending = sync_results(conn, args.log, as_of)
    for s in synced:
        note = f"  (joué à {s['shift']:+d} j de la date prévue)" if s["shift"] else ""
        print(f"  OK  {s['date']}  {s['match']} : {s['actual']}{note}")
        for clv in s.get("bets_clv") or []:
            tag = "bat la clôture" if clv > 0 else "clôture plus favorable" if clv < 0 else "= clôture"
            print(f"        CLV {clv:+.2%} ({tag}, vs {s.get('closing_source')})")
        for reason in s.get("clv_skipped") or []:
            print(f"        CLV non calculé — {CLV_SKIP_REASONS.get(reason, reason)}"
                  + (f" (source en base : {s['closing_source']})" if s.get("closing_source") else ""))
    if pending:
        print("\nEn attente de données source :")
        for p in pending:
            print(f"  ..  {p['date']}  {p['match']} — {p['reason']}")
        print("  (relance `python pipeline.py --update` puis cette commande ; si "
              "l'attente persiste, creuse la source football-data ou l'alias manquant.)")
    print(f"\n{len(synced)} résultat(s) synchronisé(s), {len(pending)} encore en attente.")


def cmd_report(args, conn):
    text, overall = build_calibration_report(args.log, args.month)
    CAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAL_REPORT_PATH.write_text(text)
    print(text)
    print(f"Rapport écrit dans {CAL_REPORT_PATH}", file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=str(db.DB_PATH))
    common.add_argument("--log", default=str(JOURNAL_PATH), help="journal de production (JSON)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("match", parents=[common], help="prédire un ou plusieurs matchs à venir")
    p.add_argument("--from-skill-json", default=None, metavar="FICHIER",
                   help=f"Lit un export JSON du skill football-match-predictor (schéma "
                        f"{SKILL_SCHEMA}) et le mappe sur league/home/away/odds/dates ; "
                        f"'-' = stdin. Exclusif des arguments individuels correspondants.")
    p.add_argument("--league", choices=footballdata.LEAGUES)
    p.add_argument("--home")
    p.add_argument("--away")
    p.add_argument("--fixture", action="append", default=[],
                   help="Affiche 'LIGUE,Domicile,Extérieur' (répétable, pour un slate de week-end)")
    p.add_argument("--date", default=None, help="Date du match (défaut : samedi à venir)")
    p.add_argument("--odds", action="append", default=[],
                   help="Cotes 1N2 'home,nul,away' (répétable, un par bookmaker)")
    p.add_argument("--odds-date", default=None,
                   help="Date de publication des cotes (fixe la fraîcheur du pont marché)")
    p.add_argument("--odds-age-days", type=int, default=None,
                   help="Âge des cotes en jours (alternative à --odds-date)")
    p.add_argument("--blend", type=float, default=DEFAULT_BLEND,
                   help=f"Poids marché de base sur cotes fraîches (défaut {DEFAULT_BLEND:g} ; "
                        f"décroît vers un plancher de {STALE_FLOOR:.0%} si périmées)")
    p.add_argument("--devig", choices=DEVIG_METHODS, default=DEVIG_METHOD,
                   help=f"Méthode de démargeage des cotes (défaut {DEVIG_METHOD} : "
                        f"marge modélisée à la Shin plutôt qu'un exposant libre ; "
                        f"comparaison des trois méthodes dans devig_check.py). "
                        f"Journalisé dans meta.devig.")
    p.add_argument("--contest-points", default=None, metavar="H,N,A",
                   help="MODE CONCOURS : points si l'issue est correcte (ex: 13,50,68)")
    p.add_argument("--contest-exact-bonus", type=float, default=0.0, metavar="B",
                   help="Points bonus si le score exact est correct (défaut 0)")
    p.add_argument("--no-odds-reason", choices=DECLARABLE_NO_ODDS_REASONS, default=None,
                   help="Pourquoi aucune cote n'est fournie, journalisé dans "
                        "meta.no_odds_reason. Sans ce drapeau l'entrée est marquée "
                        f"'{DEFAULT_NO_ODDS_REASON}' — jamais une raison devinée.")
    p.add_argument("--lineup-adjustment", default=None, metavar="FICHIER",
                   help="Ajustement post-fit des λ selon les compositions officielles "
                        "confirmées (JSON, fichier ou '-' pour stdin ; ratios attaque/défense "
                        "dérivés de xG par titulaire confirmé vs référence — voir "
                        "load_lineup_adjustment). Journalisé dans meta.lineup_adjustment. "
                        "Ignoré pour un slate (--fixture répété) — un seul match à la fois.")
    p.add_argument("--exposure-cap", type=float, default=SLATE_EXPOSURE_CAP,
                   metavar="FRACTION",
                   help=f"Plafond d'exposition SIMULTANÉE sur l'ensemble du run "
                        f"(défaut {SLATE_EXPOSURE_CAP:g} = {SLATE_EXPOSURE_CAP:.0%} de "
                        f"bankroll). Au-delà, toutes les mises sont réduites d'un même "
                        f"facteur. Le cumul inclut les paris non réglés de la MÊME "
                        f"SEMAINE déjà présents dans le journal — c'est là que "
                        f"l'exposition d'un slate généré match par match s'accumule. "
                        f"Sans effet sur un match seul isolé (3 issues × 5 % = "
                        f"{SLATE_EXPOSURE_CAP:.0%}).")
    p.add_argument("--no-stake", action="store_true", help="désactive la section mise suggérée")
    p.add_argument("--no-log", action="store_true", help="ne pas journaliser la prédiction")
    p.set_defaults(func=cmd_match)

    r = sub.add_parser("result", parents=[common], help="enregistrer le résultat réel d'une prédiction")
    r.add_argument("--match", required=True, help="'Domicile-Extérieur' (comme journalisé)")
    r.add_argument("--actual", required=True, help="Score réel, ex: 2-1")
    r.add_argument("--ht", default=None, help="Score mi-temps 'h-a' (optionnel)")
    r.set_defaults(func=cmd_result)

    s = sub.add_parser("sync-results", parents=[common],
                       help="remplir les résultats réels depuis football.db "
                            "(après `pipeline.py --update`)")
    s.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                   help="Date de référence : seuls les matchs antérieurs sont "
                        "synchronisés (défaut : aujourd'hui)")
    s.set_defaults(func=cmd_sync_results)

    rep = sub.add_parser("report", parents=[common], help="rapport de calibration mensuel du monitoring")
    rep.add_argument("--month", default=None, help="Filtrer sur un mois 'YYYY-MM'")
    rep.set_defaults(func=cmd_report)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    conn = db.connect(args.db)
    try:
        args.func(args, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
