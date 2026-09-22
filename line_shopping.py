"""Roadmap A2 — line shopping : meilleure cote disponible tous books confondus.

Lit book_odds (capturé par odds_snapshot.py, roadmap A2) pour une affiche
donnée et retourne, par issue 1N2 ou par ligne O/U, la cote MAXIMALE
disponible et le book qui la propose — jamais une moyenne, jamais une cote
inventée : si book_odds n'a aucune ligne pour ce match (pas encore capturé,
ou hors des ligues suivies par The Odds API), la fonction retourne un dict
vide plutôt qu'une valeur de repli silencieuse.

Usage prévu : PUREMENT INFORMATIONNEL dans predict.py (meta.line_shopping).
Ne remplace JAMAIS --odds (source de vérité actuelle du blend marché/modèle
et du staking Kelly) et ne déclenche aucune mise — même statut que
meta.derived_markets (A1) ou meta.correlated_exposure (M9) : affiché, jamais
utilisé pour calculer un montant à miser tant que ce chantier n'a pas son
propre backtest (A2 n'a jamais été validé comme source de pricing, cf.
CLAUDE.md — book_odds sert aujourd'hui au CLV provisoire R1, pas au pricing).
"""
import datetime


def _rows(conn, league, home, away, market, asof=None):
    """Lignes book_odds pour cette affiche/marché, restreintes aux snapshots
    capturés à `asof` ou avant (défaut : maintenant) — jamais un snapshot
    postérieur à l'instant de la requête."""
    asof = asof or datetime.datetime.now(datetime.timezone.utc).isoformat()
    return conn.execute(
        "SELECT book, outcome, point, price, fetched_at FROM book_odds "
        "WHERE league = ? AND home = ? AND away = ? AND market = ? AND fetched_at <= ? "
        "ORDER BY fetched_at DESC",
        (league, home, away, market, asof),
    ).fetchall()


def best_odds_1x2(conn, league, home, away, asof=None):
    """{'home': {'price':, 'book':, 'fetched_at':}, 'draw': {...}, 'away': {...}}
    — la meilleure cote 1N2 tous books/snapshots confondus pour cette
    affiche, ou {} si book_odds n'a rien capturé pour elle."""
    best = {}
    for r in _rows(conn, league, home, away, "h2h", asof):
        cur = best.get(r["outcome"])
        if cur is None or r["price"] > cur["price"]:
            best[r["outcome"]] = {"price": r["price"], "book": r["book"], "fetched_at": r["fetched_at"]}
    return best


def best_odds_totals(conn, league, home, away, point, asof=None):
    """Idem pour un marché totals (Over/Under) à une ligne `point` donnée
    (ex. 2.5) — {'Over': {...}, 'Under': {...}}."""
    best = {}
    for r in _rows(conn, league, home, away, "totals", asof):
        if r["point"] != point:
            continue
        cur = best.get(r["outcome"])
        if cur is None or r["price"] > cur["price"]:
            best[r["outcome"]] = {"price": r["price"], "book": r["book"], "fetched_at": r["fetched_at"]}
    return best


def summarize(conn, league, home, away, asof=None, ou_point=2.5):
    """Résumé prêt pour meta.line_shopping : 1N2 + O/U à une ligne. Chaque
    issue absente de book_odds est simplement absente du dict (pas de None
    inventé) ; {} global si aucune capture n'existe pour cette affiche."""
    out = {}
    h2h = best_odds_1x2(conn, league, home, away, asof)
    if h2h:
        out["h2h"] = h2h
    ou = best_odds_totals(conn, league, home, away, ou_point, asof)
    if ou:
        out[f"ou{ou_point}"] = ou
    return out
