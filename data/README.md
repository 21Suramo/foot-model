# Données versionnées

Ce dossier est ignoré par défaut (`.gitignore`) : la base se reconstruit
entièrement avec `python pipeline.py --update`. **Deux exceptions** sont
versionnées volontairement :

- `football.db` — base SQLite (résultats, cotes de clôture, xG) **et** la table
  de production. Elle contient l'historique des prédictions et paris.
- `production_journal.json` — journal des pronostics et résultats enregistrés en
  production.

## ⚠️ Données personnelles — repo privé obligatoire

Ces deux fichiers contiennent des **données personnelles de paris** (pronostics,
mises, résultats suivis). Ce dépôt **doit rester privé**. Ne le rendez jamais
public et ne partagez pas ces fichiers hors d'un contexte de confiance.

Le reste du dossier — notamment `data/raw/` (cache football-data.co.uk /
Understat) — reste ignoré : il est re-téléchargeable et inutile à versionner.
