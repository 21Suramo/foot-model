"""Roadmap A2 — capture un snapshot de cotes multi-books -> table book_odds.

Nécessite ODDS_API_KEY dans l'environnement (jamais dans un fichier committé,
cf. oddsapi.py et CLAUDE.md).

⚠ Budget de crédits — calcul honnête, à ne jamais perdre de vue avant de
câbler une cadence automatisée : coût = 1 crédit par (ligue × région ×
marché). Défaut (3 ligues × regions=eu × markets=h2h) = 3 crédits/snapshot ;
avec markets=h2h,totals = 6 crédits/snapshot. Free tier = 500 crédits/mois :
- markets=h2h seul : ~5,5 snapshots/jour soutenables en continu (500/30/3).
- markets=h2h,totals : ~2,7 snapshots/jour (500/30/6).
Une cadence "toutes les 6h" (4/jour) demandée par un vrai suivi de CLV coûte
24 à 48 crédits/jour selon les marchés, soit 720 à 1440/mois — AU-DESSUS du
quota gratuit. Le free tier permet de valider que le pipeline tourne, PAS
de faire tourner une cadence CLV réelle. Ne construis aucune automatisation
qui suppose une cadence supérieure à ~2/jour (marge de sécurité incluse pour
les appels de test/debug dans le même mois) sans être passé au tier payant
(30 €/mois pour 20 000 crédits au moment de la vérification).

Résolution de noms d'équipe : les noms renvoyés par l'API (ex. "Manchester
United", "Atlético Madrid") sont résolus vers la convention football-data.co.uk
("Man United", "Ath Madrid") via team_aliases (aliases.py, même table que
pour Understat) AVANT d'être stockés — book_odds est donc directement
joignable à matches sur (league, home, away), sans étape de résolution
différée à écrire ailleurs.

Ne planifie rien lui-même (pas de cron dans ce script) : à lancer à la main,
ou câblé dans .github/workflows/ si l'utilisateur ajoute ODDS_API_KEY comme
secret du dépôt ET choisit explicitement une cadence dans le budget ci-dessus
— décision volontairement pas prise ici.

Usage : python odds_snapshot.py [--markets h2h,totals] [--regions eu,uk] [--db data/football.db]
"""
import argparse
import datetime
import logging
import sys

import aliases
import db
import oddsapi

log = logging.getLogger("odds_snapshot")


def _known_teams(conn, league):
    return {r[0] for r in conn.execute(
        "SELECT home FROM matches WHERE league = ? "
        "UNION SELECT away FROM matches WHERE league = ?", (league, league))}


def resolve(name, alias_map, known):
    """(nom_canonique, résolu?) — alias_map en priorité, sinon le nom tel
    quel (convention d'aliases.py : un nom absent de la table est utilisé
    tel quel). `résolu` dit si CE nom canonique correspond bien à une
    équipe déjà vue dans `matches` pour cette ligue — sert seulement au
    diagnostic (équipe nouvellement promue, alias manquant...), n'empêche
    jamais l'insertion."""
    canonical = alias_map.get(name, name)
    return canonical, canonical in known


def run(conn, markets=oddsapi.DEFAULT_MARKETS, regions=oddsapi.DEFAULT_REGIONS):
    aliases.seed(conn)
    alias_map = db.load_aliases(conn)
    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    total = 0
    unresolved = set()
    for league in oddsapi.SPORT_KEYS:
        known = _known_teams(conn, league)
        raw = oddsapi.fetch(league, regions=regions, markets=markets)
        rows = oddsapi.parse_snapshot(league, raw, fetched_at)
        for row in rows:
            row["home"], ok_h = resolve(row["home"], alias_map, known)
            row["away"], ok_a = resolve(row["away"], alias_map, known)
            if not ok_h:
                unresolved.add((league, row["home"]))
            if not ok_a:
                unresolved.add((league, row["away"]))
            db.insert_book_odds(conn, row)
        conn.commit()
        log.info("%s : %d matchs à venir, %d lignes de cotes capturées.",
                 league, len(raw), len(rows))
        total += len(rows)
    for league, name in sorted(unresolved):
        log.warning("%s : équipe non résolue (ni alias, ni nom football-data connu) : "
                    "%s — vérifier une promotion récente ou compléter aliases.py.",
                    league, name)
    log.info("%d lignes écrites dans book_odds (fetched_at=%s).", total, fetched_at)
    return total


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markets", default=oddsapi.DEFAULT_MARKETS,
                        help="marchés API séparés par virgule (ex. h2h,totals) — "
                             "coût = nb marchés x nb régions x 3 ligues")
    parser.add_argument("--regions", default=oddsapi.DEFAULT_REGIONS)
    parser.add_argument("--db", default=str(db.DB_PATH))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    try:
        oddsapi.api_key()
    except RuntimeError as e:
        sys.exit(str(e))
    conn = db.connect(args.db)
    run(conn, args.markets, args.regions)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
