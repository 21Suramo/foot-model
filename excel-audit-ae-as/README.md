# Audit et automatisation du classeur d'évaluation AE / AS (Al Barid Bank — CRC)

Travail annexe au dépôt `foot-model`, sans lien avec le modèle de pronostics.

| Fichier | Rôle |
|---|---|
| `AUDIT.md` | Audit du classeur d'origine, anomalies, et détail des deux tâches livrées |
| `Grille_evaluation_AE_AS_automatisee.xlsx` | Le livrable |
| `build.py` | Script de reconstruction (openpyxl) à partir du classeur d'origine |
| `reinject_dv.py` | Restaure les listes déroulantes inter-feuilles supprimées par openpyxl |

Régénération :

```bash
pip install openpyxl
python build.py            # SRC/DST en tête de fichier
python reinject_dv.py
```
