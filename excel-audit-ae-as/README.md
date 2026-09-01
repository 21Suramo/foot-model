# Audit et automatisation du classeur d'évaluation AE / AS (Al Barid Bank — CRC)

Travail annexe au dépôt `foot-model`, sans lien avec le modèle de pronostics.

| Fichier | Rôle |
|---|---|
| `AUDIT.md` | Audit du classeur d'origine, les 9 anomalies et leur correction, détail des livrables |
| `Grille_evaluation_AE_AS_automatisee.xlsx` | Le livrable |
| `build.py` | Reconstruction complète du classeur (openpyxl) à partir de l'original |
| `reinject_dv.py` | Restaure les listes déroulantes inter-feuilles supprimées par openpyxl, puis relit le classeur pour vérifier qu'il reste valide |

## Onglets

| Onglet | Saisie | Rôle |
|---|---|---|
| Synthèse & Performance | — | Vue centre : audits, moyennes AE/AS, taux de conformité |
| **Fiche Agent** | nom **ou** log HERMES | 10 cartes KPI + 8 graphiques pour un agent |
| Grille AE / Grille AS | ✏️ **oui** | Les deux seules feuilles de saisie (60 écoutes chacune) |
| Historique AE AS | — | 100 % calculé depuis les grilles (120 lignes) |
| KPI Agents | filtres jaunes | 51 agents × 20 indicateurs, cible et seuil de complétude |
| Analyse Critères | — | Les 40 critères classés maîtrisé / à consolider / point critique |
| Suivi Hebdo | filtres jaunes | Semaines S1 à S53, centre et agent suivi |
| Evolution | — | 4 graphiques centre |

## Régénération

```bash
pip install openpyxl
python build.py            # SRC/DST en tête de fichier
python reinject_dv.py
```

Vérification des formules (nécessite LibreOffice Calc) : recalcul complet du classeur,
attendu **5 022 formules, 0 erreur**.
