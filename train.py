"""Entraînement de production du GBM (roadmap B1-B4).

Ajuste le modèle final avec les hyperparamètres figés par
`backtest_ml.py --tune` (data/ml_frozen.json) — ne règle jamais rien
lui-même, échoue explicitement si ce fichier est absent (même discipline
que backtest35.frozen()/predict.py : pas d'hyperparamètre inventé).

Comme Dixon-Coles dans predict.py ("refit à jour, même garde anti-fuite que
le walk-forward"), le modèle de production n'est PAS un artefact binaire
versionné dans le dépôt : seuls les hyperparamètres (JSON, petit, comme
data/m35_frozen.json) sont figés et committés. Le modèle lui-même se
RÉ-ENTRAÎNE à chaque usage sur les données courantes — cohérent avec le
principe du projet que data/ se reconstruit entièrement
(cf. data/README.md), pas un cache qui peut dériver du dataset réel.
predict.py --ml-compare appelle directement fit_production_model() ;
data/ml_model_cache/ (option --cache ci-dessous) est un cache LOCAL
optionnel, jamais committé (même statut que data/raw/ dans .gitignore),
seulement pour éviter de ré-entraîner à chaque appel d'un script batch.

Usage : python train.py [--db data/football.db] [--league E0] [--cache DIR]
"""
import argparse
import logging
import sys
from pathlib import Path

import backtest_ml
import db
import ml_model

log = logging.getLogger("train")


def fit_production_model(conn, leagues=None):
    """Ajuste le GBM de production sur TOUT l'historique disponible pour
    `leagues` (défaut : les ligues couvertes par le tuning figé,
    data/ml_frozen.json). Retourne (model, feature_names, n_matchs)."""
    cfg = backtest_ml.frozen()
    leagues = leagues if leagues is not None else cfg["leagues"]
    table, feature_names = backtest_ml.build_stacked_table(conn, leagues)
    if not table:
        sys.exit(f"Aucun match disponible pour entraîner le modèle GBM sur {leagues}.")
    clf_p = {"num_leaves": cfg["num_leaves"], "learning_rate": cfg["learning_rate"]}
    model = ml_model.fit(table, feature_names, clf_p, clf_p, cfg["num_rounds"])
    return model, feature_names, len(table)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(db.DB_PATH))
    parser.add_argument("--league", help="restreindre l'entraînement à une seule ligue "
                                          "(par défaut : toutes celles du tuning figé)")
    parser.add_argument("--cache", help="dossier où sauvegarder le modèle entraîné "
                                        "(cache local optionnel, jamais committé)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    conn = db.connect(args.db)
    leagues = [args.league] if args.league else None
    model, feature_names, n = fit_production_model(conn, leagues)
    conn.close()
    log.info("Modèle GBM de production entraîné sur %d matchs (%s).", n,
             args.league or "toutes les ligues du tuning figé")
    if args.cache:
        model.save(args.cache)
        log.info("Cache local écrit -> %s (jamais committé, cf. .gitignore data/*).", args.cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
