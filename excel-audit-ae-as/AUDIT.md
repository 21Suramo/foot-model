# Audit du classeur `Grille_evaluation_abb_AE_AS` — Al Barid Bank / CRC

Livrable : **`Grille_evaluation_AE_AS_automatisee.xlsx`**
Scripts de production : `build.py` (reconstruction) + `reinject_dv.py` (restauration des
listes déroulantes inter-feuilles que la librairie Python supprime à l'écriture).

---

## 1. Logique du classeur d'origine

### 1.1 Circuit de la donnée

```
LOG Agent (51 agents + log HERMES)  ─┐
Objet&TYP (typologies / objets)     ─┼─► Grille AE (60 colonnes G..BN)  ─┐
                                     └─► Grille AS (60 colonnes G..BN)  ─┤
                                                                         │
                                     Historique AE AS  ◄── SAISIE MANUELLE
                                                                         │
                            ┌────────────────────────────────────────────┘
                            ├─► Suivi Hebdo  ─► Evolution (14 graphiques)
                            └─► Synthèse & Performance
```

Chaque **colonne** d'une grille (G, H, I… jusqu'à BN) est **une écoute** : en-tête agent
(ligne 3), log HERMES en RECHERCHEV (4), date (5), heures début/fin (6-7), type d'écoute (8),
typologie (9), objet (10), puis les notes 10 / 5 / 0 / N/A des critères.

Notation : **Fond 70 % + Forme 30 %**. `N/A` neutralise un critère via
`AVERAGEIFS(...;"<>N/A")`. Cible = 80 %.

| Grille | Critères Fond | Critères Forme | Lignes de synthèse |
|---|---|---|---|
| AE (entrants) | F01→F10 (l. 15-24) | R01→R15 (l. 26-40) | 43 / 44 / 45 / 46 |
| AS (sortants) | F01→F07 (l. 16-22) | R01→R08 (l. 24-31) | 34 / 35 / 36 |

### 1.2 Ce qui fonctionnait bien

- Grille AE cohérente de bout en bout (colonne E = moyenne du critère sur toutes les écoutes).
- Listes déroulantes correctement câblées (agents, typologies, objets, notes, type d'écoute).
- Pondération 70/30 appliquée uniformément.
- Feuilles de référence isolées et masquées (`LOG Agent`, `Objet&TYP`).

---

## 2. Anomalies relevées

| # | Gravité | Constat | Effet |
|---|---|---|---|
| **A1** | 🔴 Bloquant | **`Grille AS` : la colonne E (« Note ») n'était plus une formule.** Seul `E16` calculait `=MOYENNE(G16:BN16)` ; `E17:E22` et `E24:E31` étaient des **valeurs 10 figées** avec une liste déroulante de saisie. | La synthèse AS (E34/E35/E36) affichait **100 % en permanence**, quelles que soient les notes réellement saisies. `Synthèse & Performance` reprenait ce 100 % comme « Moyenne Appels Sortants ». |
| **A2** | 🔴 Bloquant | **`Historique AE AS` entièrement saisi à la main** (6 lignes recopiées des grilles), avec une colonne « Colonne Audit » servant d'aide-mémoire manuelle. | Toute la chaîne aval (Suivi Hebdo, Evolution, comptage d'audits) dépendait d'une recopie humaine. Aucune trace n'existe si l'évaluateur oublie de recopier. |
| **A3** | 🟠 Majeur | **`Synthèse & Performance` calculait une moyenne fausse.** `C9 = MOYENNE(C13;F13)` avec `C13 = 'Grille AE'!E45` : E45 est la *moyenne des moyennes par critère*, pas la moyenne des écoutes. Les critères n'ayant pas le même nombre d'évaluations, les deux valeurs diffèrent (0,794 au lieu de 0,827 sur les données actuelles). De plus AE et AS pesaient chacun 50 % alors qu'il y a 5 audits AE pour 1 AS. | Score du centre biaisé. |
| **A4** | 🟠 Majeur | **`Suivi Hebdo` codé en dur** : semaines 31 / 35 / 36 écrites *dans* les formules `AVERAGEIFS`, et 5 agents listés en dur sur 51. | À la semaine 37, la feuille reste figée. Un 6ᵉ agent audité n'apparaît jamais. |
| **A5** | 🟠 Majeur | **~340 cellules en `#DIV/0!`** dans les lignes de synthèse des deux grilles, sur chaque colonne d'évaluation non encore utilisée. | Classeur qui « clignote » en rouge, et surtout : impossible d'agréger ces colonnes sans neutraliser l'erreur. |
| **A6** | 🟡 Moyen | **13 graphiques sur 14 pointaient une série vide** (`'Suivi Hebdo'!$B$10:$D$10`, ligne blanche) ; 14 graphiques pour 5 agents, dont des doublons. | Légende « Série 2 » vide sur presque tous les graphiques. |
| **A7** | 🟡 Moyen | **`DMC (min)` = 0 partout** dans l'historique, alors que les heures début/fin sont saisies dans les grilles. | Indicateur de durée de communication inexploitable. |
| **A8** | 🟡 Moyen | **Aucun indicateur de complétude.** Une grille où un seul critère sur 25 est noté produit un score de 100 %. C'est le cas de 3 des 6 audits existants (BATTAL SALMA, CHAMCHATI Hajar, BOURBAH AMINA : **2 critères renseignés sur 25, soit 8 %**). | Trois « 100 % » et « 65 % » du classeur ne sont statistiquement pas interprétables. |
| **A9** | 🔵 Mineur | Cible 80 % réécrite en dur dans ~130 formules ; bandeau « A. ÉVALUATION DU FOND » absent de la grille AE ; ligne « STATUT OBJECTIF » absente de la grille AS ; cellule `F9` « Sélection de semaine » inutilisée. | Maintenance. |

---

## 3. Tâche 1 — `Historique AE AS` automatisé

La feuille est désormais **100 % calculée** : plus aucune saisie. Elle est dimensionnée pour
les 60 colonnes d'évaluation de chaque grille.

| Lignes | Source |
|---|---|
| 4 → 63 | `Grille AE`, évaluations 1 à 60 (colonnes G → BN) |
| 64 → 123 | `Grille AS`, évaluations 1 à 60 (colonnes G → BN) |

Chaque ligne se remplit dès qu'un **nom d'agent** est sélectionné dans la colonne
correspondante de la grille ; sinon elle reste vide.

Mécanique : `INDEX(plage_ligne_grille ; 1 ; n° d'évaluation)` — pas de `INDIRECT` volatil,
pas de macro, le classeur reste un `.xlsx` standard.

**Colonnes conservées à l'identique** (A→N) : Date d'écoute, Nom de l'Agent, LOG Agent,
Type d'appel, Objet de l'appel, DMC (min), Moyenne Fond, Moyenne Forme, Score Global,
Statut, Semaine, Année, Source, Colonne Audit.

**Colonnes ajoutées** (O→R) :

| Col. | Champ | Calcul |
|---|---|---|
| O | Typologie de l'appel | ligne 9 de la grille |
| P | Type d'écoute | ligne 8 de la grille (chaud / froid / double) |
| Q | Critères renseignés | `NBVAL` sur les blocs Fond + Forme de la colonne (les `N/A` comptent comme renseignés) |
| R | Complétude | Q ÷ 25 (AE) ou Q ÷ 15 (AS) — **répond à l'anomalie A8** |

Corrections intégrées au passage :
- **DMC réellement calculée** : `MOD(heure fin − heure début; 1) × 1440`, arrondie à la minute
  (l'écoute 1 passe de 0 à 10 min).
- **Semaine ISO** via `ISOWEEKNUM` (identique aux valeurs saisies à la main : 31, 35, 36).
- **Statut** adossé à la cellule cible unique `'KPI Agents'!$E$5`.

**Contrôle de non-régression** : les 6 lignes saisies manuellement dans le fichier d'origine
sont reproduites au chiffre près — scores 0,88 / 0,605 / 1 / 1 / 0,65 / 1, mêmes dates, mêmes
logs, mêmes semaines.

> Capacité : 120 audits simultanés (60 AE + 60 AS), soit exactement la capacité des grilles.
> Au-delà, archiver une copie du classeur par trimestre — les grilles elles-mêmes sont
> plafonnées à 60 colonnes dans le fichier d'origine.

---

## 4. Tâche 2 — Bilan et performance par agent

### 4.1 Nouvel onglet `KPI Agents`

**Zone de filtres (cellules jaunes, seules cellules saisissables)** : Année · Type d'appel
(Tous / AE / AS) · Semaine de → Semaine à · **Cible score global** (la cellule `E5` pilote
désormais tous les statuts du classeur, grilles comprises).

**Bandeau de 7 cartes** recalculées selon les filtres : Audits réalisés · Agents audités ·
Score global moyen · Taux de conformité · Moyenne Fond · Moyenne Forme · Complétude.

**Tableau de 51 agents × 19 indicateurs**, filtrable, volets figés :

Collaborateur · Log · Audits · dont AE · dont AS · Moyenne Fond · Moyenne Forme ·
**Score global** · Écart vs cible · Taux de conformité · Complétude grille · DMC moyenne ·
Dernier score · Score précédent · **Tendance** (▲/▼) · Meilleur · Moins bon · **Niveau** · Rang

Ligne de synthèse **« Ensemble du centre (sélection) »** en bas de tableau.

Mise en forme conditionnelle : échelle rouge→vert sur le score, barres de données sur le taux
de conformité et la complétude, pastilles de couleur sur le Niveau
(Excellent ≥ 90 % · Conforme ≥ cible · À accompagner ≥ cible − 10 pts · Critique),
tendance en vert/rouge, agents sans audit grisés automatiquement.

*Lecture sur les données actuelles* : 6 audits, 5 agents, score moyen 85,6 %, conformité 66,7 %
— mais **complétude moyenne 46 %**, ce qui relativise fortement le 85,6 %.

### 4.2 Nouvel onglet `Analyse Critères`

Les 40 critères des deux grilles, avec note moyenne /10, score %, nombre d'évaluations et
statut (Maîtrisé ≥ 90 % · À consolider ≥ 70 % · Point critique). C'est la vue qui dit **où**
les points se perdent, pour cibler le plan d'action collectif.

*Sur les données actuelles, points critiques AE* : F04 « réponses claires et complètes » (50 %),
F05 « mise en attente justifiée » (50 %), R02 « l'agent se présente » (50 %),
R15 « formules de congé » (0 %), F02 « ne déborde pas du sujet » (62,5 %).

### 4.3 `Suivi Hebdo` reconstruit (dynamique)

Une ligne par semaine ISO (S1 → S53), plus aucune semaine codée en dur. Colonnes : audits
centre · score centre · Fond · Forme · taux de conformité · audits agent · score agent.
Deux cellules jaunes pilotent la feuille : **Année** et **Agent suivi** (liste déroulante).

### 4.4 `Evolution` reconstruit

Les 14 graphiques (dont 13 avec une série vide) sont remplacés par **4 graphiques propres** :
score centre vs agent suivi · Fond vs Forme · taux de conformité hebdomadaire · volume d'audits.
Changer l'agent dans `Suivi Hebdo` met à jour la courbe orange.

### 4.5 `Synthèse & Performance` corrigé

Toutes les cartes sont désormais alimentées par l'historique, pas par les moyennes de moyennes
des grilles : total d'audits, moyenne du centre (moyenne réelle des écoutes), moyennes AE et AS
séparées, **taux de conformité** (remplace la cellule « Sélection de semaine » inutilisée),
semaine ISO courante. Les libellés « Cible : ≥ 80 % » deviennent dynamiques.

*Effet mesuré* : moyenne AE 79,4 % → **82,7 %** (calcul corrigé), moyenne AS 100 % → 100 %
mais cette fois réellement calculée, moyenne du centre **85,6 %**.

---

## 5. Correctifs appliqués aux grilles de saisie

Les grilles restent la **seule zone de saisie** et gardent leur mise en forme et leurs listes
déroulantes (y compris celles pointant vers `LOG Agent` et `Objet&TYP`, restaurées après
traitement).

1. **`Grille AS` colonne E rétablie en formule** (anomalie A1) et retirée de la liste
   déroulante de notation, pour qu'on ne puisse plus l'écraser par erreur.
2. **`#DIV/0!` neutralisés** sur les lignes de synthèse des deux grilles (A5) : les colonnes
   non utilisées restent vides. 0 erreur dans le classeur, contre ~340 auparavant.
3. **Ligne « STATUT OBJECTIF » ajoutée à la grille AS** (l. 37), par symétrie avec la grille AE.
4. **Cible centralisée** : les statuts des deux grilles et de la synthèse pointent
   `'KPI Agents'!$E$5`.

---

## 6. Vérification

- Recalcul complet du classeur : **4 376 formules, 0 erreur**.
- Les 6 audits historiques du fichier d'origine sont reproduits à l'identique.
- Listes déroulantes, mises en forme conditionnelles et graphiques présents dans le livrable
  (contrôlés directement dans le XML du fichier).

## 7. Points d'attention pour l'exploitation

1. **Ne rien saisir en dehors des grilles AE/AS et des cellules jaunes.** Toutes les autres
   feuilles sont calculées.
2. **La complétude est le garde-fou** : un score sur une grille remplie à 8 % ne veut rien dire.
   Fixer une règle interne (ex. complétude ≥ 80 % pour qu'un audit compte).
3. **Capacité 60 évaluations par grille.** Prévoir un archivage périodique.
4. **Le fichier reste un `.xlsx` sans macro** : il s'ouvre et se recalcule dans Excel, Excel
   Online et LibreOffice.
