"""Modèle Gradient Boosting (LightGBM), roadmap B1-B4 (2026-09-22).

Architecture "hybride" demandée par la roadmap : le modèle Dixon-Coles
(model.py) N'EST PAS remplacé, il devient une FEATURE D'ENTRÉE (stacking) —
ses probas 1N2 et ses λ, calculés walk-forward comme dans backtest.py
(jamais de fuite), sont concaténés aux features/ (Elo, pi-rating, forme,
repos) avant d'entraîner LightGBM. Un classifieur multiclasse (objectif
1N2) et deux régresseurs Poisson (buts attendus domicile/extérieur)
partagent le même jeu de features.

⚠ Statut honnête (à ne jamais présenter autrement, cf. CLAUDE.md) : ce
module est un chantier neuf, validé par backtest_ml.py avec le même
protocole tune/validation/test + IC bootstrap que M3.5, mais SANS
l'ampleur de vérification (plusieurs mois, plusieurs itérations) qui a
précédé le verdict M3.5. Les hyperparamètres par défaut ci-dessous sont
des valeurs raisonnables de la littérature LightGBM, pas des optima
recherchés sur ce dataset — seuls num_leaves/learning_rate/num_boost_round
sont réglés par grid search sur la validation (backtest_ml.tune), le reste
reste fixe et documenté comme tel.
"""
import numpy as np

try:
    import lightgbm as lgb
except ImportError as exc:  # pragma: no cover - dépendance optionnelle documentée
    raise ImportError(
        "lightgbm n'est pas installé — ajouté à requirements.txt le 2026-09-22 "
        "(roadmap B1-B4). Lancer `pip install -r requirements.txt`."
    ) from exc

DEFAULT_CLF_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "verbosity": -1,
    "num_leaves": 15,
    "learning_rate": 0.05,
    "min_data_in_leaf": 30,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": 20260922,
}
DEFAULT_REG_PARAMS = {
    "objective": "poisson",
    "metric": "poisson",
    "verbosity": -1,
    "num_leaves": 15,
    "learning_rate": 0.05,
    "min_data_in_leaf": 30,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": 20260922,
}
DEFAULT_NUM_ROUNDS = 200

# Colonnes catégorielles reconnues par LightGBM nativement (pas de one-hot
# manuel) — league sert au modèle à apprendre un décalage de niveau entre
# championnats plutôt que de prétendre qu'un Elo global les rend
# équivalents.
CATEGORICAL_FEATURES = ["league"]


def _row_to_df(feature_row, feature_names):
    import pandas as pd
    line = {}
    for f in feature_names:
        v = feature_row.get(f)
        line[f] = v if f in CATEGORICAL_FEATURES else (np.nan if v is None else float(v))
    df = pd.DataFrame([line], columns=feature_names)
    for f in CATEGORICAL_FEATURES:
        if f in df.columns:
            df[f] = df[f].astype("category")
    return df


class GBMModel:
    """Modèle ajusté : probas 1N2 et λ pour une ligne de features donnée.

    predict() attend un DataFrame (les colonnes catégorielles, ex. 'league',
    doivent être typées 'category' comme à l'entraînement) plutôt qu'un
    np.array brut — _row_to_df reproduit exactement l'encodage de fit().
    """

    def __init__(self, clf, reg_home, reg_away, feature_names):
        self.clf = clf
        self.reg_home = reg_home
        self.reg_away = reg_away
        self.feature_names = list(feature_names)

    def probs_1x2(self, feature_row):
        df = _row_to_df(feature_row, self.feature_names)
        p = self.clf.predict(df)[0]
        p = np.clip(p, 1e-9, None)
        p = p / p.sum()
        return float(p[0]), float(p[1]), float(p[2])

    def lambdas(self, feature_row):
        df = _row_to_df(feature_row, self.feature_names)
        lam_h = max(float(self.reg_home.predict(df)[0]), 1e-6)
        lam_a = max(float(self.reg_away.predict(df)[0]), 1e-6)
        return lam_h, lam_a

    def save(self, dirpath):
        from pathlib import Path
        import json
        d = Path(dirpath)
        d.mkdir(parents=True, exist_ok=True)
        self.clf.save_model(str(d / "clf.txt"))
        self.reg_home.save_model(str(d / "reg_home.txt"))
        self.reg_away.save_model(str(d / "reg_away.txt"))
        (d / "feature_names.json").write_text(json.dumps(self.feature_names))

    @classmethod
    def load(cls, dirpath):
        from pathlib import Path
        import json
        d = Path(dirpath)
        clf = lgb.Booster(model_file=str(d / "clf.txt"))
        reg_home = lgb.Booster(model_file=str(d / "reg_home.txt"))
        reg_away = lgb.Booster(model_file=str(d / "reg_away.txt"))
        feature_names = json.loads((d / "feature_names.json").read_text())
        return cls(clf, reg_home, reg_away, feature_names)


def fit(rows, feature_names, clf_params=None, reg_params=None, num_rounds=DEFAULT_NUM_ROUNDS):
    """Ajuste classifieur 1N2 + régresseurs de buts sur des lignes de features
    (dicts avec les clés de feature_names + 'outcome'/'fthg'/'ftag').

    Colonnes catégorielles (CATEGORICAL_FEATURES) passées telles quelles à
    LightGBM (encodage natif), le reste en float — les NaN (xG/tirs absents
    sur les ligues secondaires ou en tout début d'historique d'une équipe)
    sont gérés nativement par LightGBM, jamais imputés à la main.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("aucune ligne d'entraînement")

    x_num = []
    for r in rows:
        line = []
        for f in feature_names:
            v = r.get(f)
            if f in CATEGORICAL_FEATURES:
                line.append(v)
            else:
                line.append(np.nan if v is None else float(v))
        x_num.append(line)

    import pandas as pd
    df = pd.DataFrame(x_num, columns=feature_names)
    for f in CATEGORICAL_FEATURES:
        if f in df.columns:
            df[f] = df[f].astype("category")

    y_outcome = np.array([r["outcome"] for r in rows])
    y_home = np.array([r["fthg"] for r in rows], dtype=float)
    y_away = np.array([r["ftag"] for r in rows], dtype=float)

    clf_p = {**DEFAULT_CLF_PARAMS, **(clf_params or {})}
    reg_p = {**DEFAULT_REG_PARAMS, **(reg_params or {})}

    ds_clf = lgb.Dataset(df, label=y_outcome, categorical_feature=[f for f in CATEGORICAL_FEATURES if f in df.columns])
    clf = lgb.train(clf_p, ds_clf, num_boost_round=num_rounds)

    ds_home = lgb.Dataset(df, label=y_home, categorical_feature=[f for f in CATEGORICAL_FEATURES if f in df.columns])
    reg_home = lgb.train(reg_p, ds_home, num_boost_round=num_rounds)

    ds_away = lgb.Dataset(df, label=y_away, categorical_feature=[f for f in CATEGORICAL_FEATURES if f in df.columns])
    reg_away = lgb.train(reg_p, ds_away, num_boost_round=num_rounds)

    return GBMModel(clf, reg_home, reg_away, feature_names)
