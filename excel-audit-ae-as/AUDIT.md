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

Après reconstruction :

```
LOG Agent · Objet&TYP ─► Grille AE / Grille AS   ← SEULE ZONE DE SAISIE
                                    │
                          Historique AE AS  (100 % calculé, 120 lignes)
                                    │
        ┌───────────────┬───────────┴───────────┬──────────────────┐
   Fiche Agent      KPI Agents            Suivi Hebdo        Synthèse &
 (nom OU log →   (51 agents,               (S1..S53)         Performance
  10 KPI +          20 KPI)                    │
  8 graphiques)                            Evolution
                                     Analyse Critères
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

Onze anomalies, toutes corrigées dans le livrable.

| # | Gravité | Constat | Effet | Statut |
|---|---|---|---|---|
| **A1** | 🔴 Bloquant | **`Grille AS` : la colonne E (« Note ») n'était plus une formule.** Seul `E16` calculait `=MOYENNE(G16:BN16)` ; `E17:E22` et `E24:E31` étaient des **valeurs 10 figées** avec une liste déroulante de saisie. | La synthèse AS (E34/E35/E36) affichait **100 % en permanence**, quelles que soient les notes réellement saisies. `Synthèse & Performance` reprenait ce 100 % comme « Moyenne Appels Sortants ». | ✅ corrigé (§5.1) |
| **A2** | 🔴 Bloquant | **`Historique AE AS` entièrement saisi à la main** (6 lignes recopiées des grilles), avec une colonne « Colonne Audit » servant d'aide-mémoire manuelle. | Toute la chaîne aval (Suivi Hebdo, Evolution, comptage d'audits) dépendait d'une recopie humaine. Aucune trace n'existe si l'évaluateur oublie de recopier. | ✅ corrigé (§3) |
| **A3** | 🟠 Majeur | **`Synthèse & Performance` calculait une moyenne fausse.** `C9 = MOYENNE(C13;F13)` avec `C13 = 'Grille AE'!E45` : E45 est la *moyenne des moyennes par critère*, pas la moyenne des écoutes. Les critères n'ayant pas le même nombre d'évaluations, les deux valeurs diffèrent (0,794 au lieu de 0,827 sur les données actuelles). De plus AE et AS pesaient chacun 50 % alors qu'il y a 5 audits AE pour 1 AS. | Score du centre biaisé. | ✅ corrigé (§4.6) |
| **A4** | 🟠 Majeur | **`Suivi Hebdo` codé en dur** : semaines 31 / 35 / 36 écrites *dans* les formules `AVERAGEIFS`, et 5 agents listés en dur sur 51. | À la semaine 37, la feuille reste figée. Un 6ᵉ agent audité n'apparaît jamais. | ✅ corrigé (§4.4) |
| **A5** | 🟠 Majeur | **~340 cellules en `#DIV/0!`** dans les lignes de synthèse des deux grilles, sur chaque colonne d'évaluation non encore utilisée. | Classeur qui « clignote » en rouge, et surtout : impossible d'agréger ces colonnes sans neutraliser l'erreur. | ✅ corrigé (§5.2) |
| **A6** | 🟡 Moyen | **13 graphiques sur 14 pointaient une série vide** (`'Suivi Hebdo'!$B$10:$D$10`, ligne blanche) ; 14 graphiques pour 5 agents, dont des doublons. | Légende « Série 2 » vide sur presque tous les graphiques. | ✅ corrigé (§4.5) |
| **A7** | 🟡 Moyen | **`DMC (min)` = 0 partout** dans l'historique, alors que les heures début/fin sont saisies dans les grilles. | Indicateur de durée de communication inexploitable. | ✅ corrigé (§3) |
| **A8** | 🟡 Moyen | **Aucun indicateur de complétude ni de garde-fou.** Une grille où un seul critère sur 25 est noté produit un score de 100 %. C'est le cas de 3 des 6 audits existants (BATTAL SALMA, CHAMCHATI Hajar, BOURBAH AMINA : **2 critères renseignés sur 25, soit 8 %**). | Trois « 100 % » et « 65 % » du classeur ne sont statistiquement pas interprétables. | ✅ corrigé (§3 et §5.5) |
| **A10** | 🔴 Bloquant | **`Grille AE` impossible à faire défiler.** Les volets étaient figés en `A38`, soit **37 lignes bloquées en haut de feuille ≈ 1 300 pixels** — plus haut qu'un écran. Il ne restait aucune zone défilable : la molette et les barres de défilement n'avaient plus d'effet. La grille AS, elle, était correctement réglée (`A16`, ~300 px). | Les critères Forme et la synthèse de la grille AE étaient inatteignables à la souris. | ✅ corrigé (§5.6) |
| **A11** | 🟠 Majeur | **Les écoutes AS semblaient absentes de l'historique.** La feuille réservait 60 lignes à la grille AE puis 60 à la grille AS : avec 5 audits AE, le premier audit AS se retrouvait **ligne 64, après 55 lignes vides**. À l'écran, seule la partie AE était visible. La donnée était juste, la mise en page la rendait introuvable. | Les appels sortants paraissaient non pris en compte alors qu'ils l'étaient. | ✅ corrigé (§3) |
| **A9** | 🔵 Mineur | Cible 80 % réécrite en dur dans ~130 formules ; bandeau « A. ÉVALUATION DU FOND » absent de la grille AE (les deux grilles n'avaient pas la même structure) ; ligne « STATUT OBJECTIF » absente de la grille AS ; cellule `F9` « Sélection de semaine » inutilisée. | Maintenance. | ✅ corrigé (§5.3-5.4) |

---

## 3. Tâche 1 — `Historique AE AS` automatisé

La feuille est désormais **100 % calculée** : plus aucune saisie. Elle est dimensionnée pour
les 60 colonnes d'évaluation de chaque grille.

**Architecture en deux temps** (correction de l'anomalie A11) :

1. Une feuille technique **masquée**, `Données grilles`, porte les formules sources : 60 lignes
   pour la grille AE puis 60 pour la grille AS, plus une clé de tri et un rang.
2. L'onglet visible `Historique AE AS` n'est qu'une **vue** de cette feuille : une seule liste
   continue, **AE et AS mélangés**, triée de l'écoute la plus récente à la plus ancienne, sans
   aucune ligne vide entre les deux types.

Chaque ligne apparaît dès qu'un **nom d'agent** est sélectionné dans une colonne de l'une des
deux grilles. Un bandeau en haut de feuille affiche en permanence
`Total : n audits · AE : x · AS : y`, et la colonne « Type d'appel » est colorée (bleu pour AE,
orange pour AS) pour que le mélange se lise d'un coup d'œil.

Mécanique : `INDEX(plage_ligne_grille ; 1 ; n° d'évaluation)` côté source, puis
`INDEX(... ; EQUIV(rang ; colonne des rangs ; 0))` côté vue — pas de `INDIRECT` volatil,
pas de macro, pas de fonction matricielle, le classeur reste un `.xlsx` standard.

**Colonnes conservées à l'identique** (A→N) : Date d'écoute, Nom de l'Agent, LOG Agent,
Type d'appel, Objet de l'appel, DMC (min), Moyenne Fond, Moyenne Forme, Score Global,
Statut, Semaine, Année, Source, Colonne Audit.

**Colonnes ajoutées** (O→R) :

| Col. | Champ | Calcul |
|---|---|---|
| O | Typologie de l'appel | ligne 9 de la grille |
| P | Type d'écoute | ligne 8 de la grille (chaud / froid / double) |
| Q | Critères renseignés | `NBVAL` sur les blocs Fond + Forme de la colonne (les `N/A` comptent comme renseignés) |
| R | Complétude | Q ÷ 25 (AE) ou Q ÷ 15 (AS) |
| S | Audit exploitable | `Oui` / `Non` selon le seuil de complétude minimale (`'KPI Agents'!$F$5`, 80 % par défaut) — **garde-fou de l'anomalie A8** |
| T | Mois | `MOIS(date)`, utilisé par la vue mensuelle de la fiche agent |

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

### 4.1 Nouvel onglet `Fiche Agent` — tableau de bord individuel

**Une seule saisie : le nom de l'agent (liste déroulante) OU son log HERMES.** Le log est
prioritaire s'il est renseigné ; il est résolu en nom par
`INDEX('LOG Agent'!A:A ; EQUIV(log ; 'LOG Agent'!B:B ; 0))`. Un troisième champ fixe l'année.
Si rien n'est saisi, ou si l'agent n'a aucun audit sur l'année, un message le dit explicitement
au lieu d'afficher des cases vides.

**10 cartes** : Audits · Exploitables · Score global · Écart vs cible · Conformité · Fond ·
Forme · Complétude · DMC moyenne · Niveau. Plus une ligne d'identité : nom, log, **rang parmi
les agents audités**.

**8 graphiques** qui se recalculent à chaque changement de sélection :

| Graphique | Type | Ce qu'il montre |
|---|---|---|
| Indicateurs clés — agent vs centre | Barres groupées | Fond, Forme, Score, Conformité, Complétude, l'agent face à la moyenne du centre |
| Évolution hebdomadaire du score | Courbes | Agent, centre et ligne de cible sur les 53 semaines |
| Score mensuel — agent vs centre | Barres groupées | Tendance sur 12 mois |
| Score moyen par objet d'appel | Barres | Sur quels objets (BBM, CC, Monétique, Crédits…) l'agent est le plus fragile |
| Profil par critère — FOND | Barres horizontales | Les 17 critères Fond (AE + AS), un par un |
| Profil par critère — FORME | Barres horizontales | Les 23 critères Forme (AE + AS) |
| Répartition AE / AS | Anneau | Volume d'écoutes entrantes vs sortantes |
| Objectif atteint / non atteint | Anneau | Taux de réussite en volume |

Les profils par critère utilisent `MOYENNE.SI` sur la ligne des noms d'agent de chaque grille
(`AVERAGEIF('Grille AE'!$G$3:$BN$3 ; agent ; 'Grille AE'!$G16:$BN16)`), ce qui permet une
lecture critère par critère sans aucune colonne intermédiaire.

Le bloc de données qui alimente les graphiques est visible en bas de la feuille, sous un
bandeau « ne rien modifier » — rien n'est caché.

### 4.2 Onglet `KPI Agents`

**Zone de filtres (cellules jaunes, seules cellules saisissables)** : Année · Type d'appel
(Tous / AE / AS) · Semaine de → Semaine à · **Cible score global** (`E5`) · **Complétude
minimale** (`F5`). Ces deux dernières cellules pilotent désormais tout le classeur : statuts
des grilles AE et AS, colonnes Statut et « Audit exploitable » de l'historique, fiche agent
et synthèse.

**Bandeau de 8 cartes** recalculées selon les filtres : Audits réalisés · dont exploitables ·
Agents audités · Score global moyen · Taux de conformité · Moyenne Fond · Moyenne Forme ·
Complétude moyenne.

**Tableau de 51 agents × 20 indicateurs**, filtrable, volets figés :

Collaborateur · Log · Audits · **Exploitables** · dont AE · dont AS · Moyenne Fond ·
Moyenne Forme · **Score global** · Écart vs cible · Taux de conformité · Complétude grille ·
DMC moyenne · Dernier score · Score précédent · **Tendance** (▲/▼) · Meilleur · Moins bon ·
**Niveau** · Rang

Ligne de synthèse **« Ensemble du centre (sélection) »** en bas de tableau.

Mise en forme conditionnelle : échelle rouge→vert sur le score, barres de données sur le taux
de conformité et la complétude, pastilles de couleur sur le Niveau
(Excellent ≥ 90 % · Conforme ≥ cible · À accompagner ≥ cible − 10 pts · Critique),
tendance en vert/rouge, agents sans audit grisés automatiquement.

*Lecture sur les données actuelles* : 6 audits, 5 agents, score moyen 85,6 %, conformité 66,7 %
— mais **complétude moyenne 46 %**, ce qui relativise fortement le 85,6 %.

### 4.3 Onglet `Analyse Critères`

Les 40 critères des deux grilles, avec note moyenne /10, score %, nombre d'évaluations et
statut (Maîtrisé ≥ 90 % · À consolider ≥ 70 % · Point critique). C'est la vue qui dit **où**
les points se perdent, pour cibler le plan d'action collectif.

*Sur les données actuelles, points critiques AE* : F04 « réponses claires et complètes » (50 %),
F05 « mise en attente justifiée » (50 %), R02 « l'agent se présente » (50 %),
R15 « formules de congé » (0 %), F02 « ne déborde pas du sujet » (62,5 %).

### 4.4 `Suivi Hebdo` reconstruit (dynamique)

Une ligne par semaine ISO (S1 → S53), plus aucune semaine codée en dur. Colonnes : audits
centre · score centre · Fond · Forme · taux de conformité · audits agent · score agent.
Deux cellules jaunes pilotent la feuille : **Année** et **Agent suivi** (liste déroulante).

### 4.5 `Evolution` reconstruit

Les 14 graphiques (dont 13 avec une série vide) sont remplacés par **4 graphiques propres** :
score centre vs agent suivi · Fond vs Forme · taux de conformité hebdomadaire · volume d'audits.
Changer l'agent dans `Suivi Hebdo` met à jour la courbe orange.

### 4.6 `Synthèse & Performance` corrigé

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

**5.1 — `Grille AS`, colonne E rétablie en formule** (anomalie A1) et retirée de la liste
déroulante de notation, pour qu'on ne puisse plus l'écraser par erreur.

**5.2 — `#DIV/0!` neutralisés** sur les lignes de synthèse des deux grilles (A5) : les colonnes
non utilisées restent vides. 0 erreur dans le classeur, contre ~340 auparavant.

**5.3 — Bandeau « A. ÉVALUATION DU FOND » ajouté à la grille AE** (A9). Il manquait une ligne :
la grille AE affichait ses critères Fond sans titre de section alors que la grille AS en avait un.
Une ligne a été insérée en 14, avec le même style que le bandeau FORME, et **toutes les formules
et plages en aval ont été régénérées** en conséquence (critères Fond 16→25, Forme 27→41, synthèse
44→47). Les deux grilles ont enfin la même structure.

**5.4 — Ligne « STATUT OBJECTIF » ajoutée à la grille AS** (l. 37), par symétrie avec la grille AE.

**5.6 — Volets figés remis à une taille utilisable** (A10). `Grille AE` passe de `A38` à `A16`,
comme la grille AS : 232 pt d'en-tête figé (~309 px) au lieu de 979 pt (~1 305 px), et 37 lignes
redeviennent défilables. Deux volets utiles ont été ajoutés au passage : `Fiche Agent` fige les
8 lignes d'identité (le nom de l'agent reste visible pendant qu'on fait défiler les graphiques)
et `Historique AE AS` fige la date et le nom d'agent quand on défile vers la droite sur les
20 colonnes. `build.py` refuse maintenant de produire un classeur dont un volet figé dépasse
320 pt de haut ou 45 caractères de large — l'anomalie ne peut plus revenir.

**5.5 — Cible et seuil de complétude centralisés** : les statuts des deux grilles, de
l'historique et de la synthèse pointent `'KPI Agents'!$E$5` (cible) et `'KPI Agents'!$F$5`
(complétude minimale). Changer 80 % en 85 % se fait maintenant dans une seule cellule.

---

## 6. Vérification

- Recalcul complet du classeur : **7 663 formules, 0 erreur**.
- Les 6 audits historiques du fichier d'origine sont reproduits à l'identique (scores 0,88 /
  0,605 / 1 / 1 / 0,65 / 1), après l'insertion de ligne dans la grille AE.
- Historique fusionné vérifié : les 6 audits se suivent en lignes 4 à 9, dans l'ordre
  01/09 (AE) · 31/08 (AE) · **28/08 (AS)** · 01/08 (AE ×3), sans ligne vide intercalée.
  Les moyennes de la synthèse, du tableau KPI et de la fiche agent sont inchangées après ce
  passage en vue triée.
- Sélection par **log HERMES** testée de bout en bout : saisir `3071` sans nom résout
  « BATTAL SALMA », rang 1/5, 1 audit dont **0 exploitable** (complétude 8 %).
- Conservé dans le livrable, contrôlé dans le XML : 6 listes déroulantes des grilles (dont les
  3 listes inter-feuilles que la librairie Python supprime et que `reinject_dv.py` restaure),
  15 blocs de mise en forme conditionnelle, **12 graphiques**.
- Un attribut `xr:uid` réinjecté depuis le fichier d'origine référençait un espace de noms non
  déclaré par le générateur : XML techniquement invalide qu'Excel pouvait refuser d'ouvrir.
  Il est désormais retiré, et `reinject_dv.py` relit le classeur après écriture pour le vérifier.
- Volets figés mesurés feuille par feuille : le plus grand fait 309 px de haut (`Grille AE`),
  bien en dessous de la limite de 320 pt contrôlée automatiquement par `build.py`.

## 7. Points d'attention pour l'exploitation

1. **Ne rien saisir en dehors des grilles AE/AS et des cellules jaunes.** Toutes les autres
   feuilles sont calculées. Trois feuilles sont masquées car purement techniques :
   `LOG Agent`, `Objet&TYP` et `Données grilles` (clic droit sur un onglet → Afficher, si
   besoin de les consulter).
2. **La complétude est le garde-fou**, et il est désormais actif : le seuil se règle en
   `'KPI Agents'!$F$5` (80 % par défaut). L'historique marque chaque audit `Oui` / `Non` en
   colonne « Audit exploitable », et les tableaux comptent les deux séparément. Sur les données
   actuelles, **2 audits sur 6 seulement sont exploitables**.
3. **Capacité 60 évaluations par grille.** Prévoir un archivage périodique.
4. **Le fichier reste un `.xlsx` sans macro** : il s'ouvre et se recalcule dans Excel, Excel
   Online et LibreOffice.
