# M8 (roadmap) — Source calendrier complémentaire (coupes, C1/C3) : investigation

**Statut : investigué, PAS construit.** Même verdict que `fatigue_signal_check.py`
pour le chantier de calibration lui-même : ne pas lancer un chantier de
collecte de données fragile pour un gain statistique a priori faible.

## Objectif de la demande

`fatigue_signal_check.py` calcule le repos d'une équipe uniquement depuis les
matchs de championnat présents dans `matches` (football-data.co.uk). Une
équipe engagée en coupe nationale ou en Coupe d'Europe (C1/C3) peut donc
apparaître à tort comme "bien reposée" alors qu'elle a joué un match
supplémentaire en milieu de semaine. La roadmap demande d'ajouter cette
source avant de relancer le test.

## Sources candidates testées (connectivité réelle, pas de supposition)

1. **football-data.org (API v4).** `GET /v4/competitions` répond 200 SANS
   clé — mais c'est un endpoint de métadonnées. Les endpoints de matchs
   (`/v4/competitions/{id}/matches`, ce qu'il faudrait pour les dates de
   coupe) exigent un en-tête `X-Auth-Token`, donc un compte personnel
   (inscription email) créé par un humain — pas une décision qu'une session
   autonome doit prendre à la place de l'utilisateur. Le palier gratuit est
   en outre limité en fréquence (10 req/min) et sa profondeur historique sur
   les compétitions de coupe (par opposition aux 5 grands championnats) n'est
   pas garantie sur 2018-19 → 2026-27.
2. **Pages Wikipédia de saison** (ex. *2023–24 UEFA Champions League*,
   *2023–24 FA Cup*). Librement accessibles sans identifiants, mais
   reconstruire des dates de match fiables par équipe suppose de scraper et
   parser des dizaines de pages hétérogènes (coupes nationales E0/SP1/F1 +
   C1/C2/C3) sur 9 saisons, puis réconcilier les noms d'équipes avec
   `team_aliases` — un chantier long et fragile : une erreur de parsing
   corromprait silencieusement une feature qui alimenterait un modèle en
   production, sans qu'aucun garde-fou existant ne la détecte.

## Pourquoi ne pas se lancer quand même

Le résultat déjà mesuré par `fatigue_signal_check.py` **sans** cette source
donne un sous-échantillon "repos court" de ~250 matchs sur 13 242
observations (1,9 %), avec |z| < 1 sur les 4 axes. Ajouter les coupes
reclasserait surtout des matchs de LIGUE d'une tranche de repos à une autre
(une équipe qui joue en C1 le mardi garde le plus souvent ≥ 3 jours avant son
match de championnat du week-end) : le gain de puissance statistique attendu
est modeste, alors que le coût d'implémentation (scraping fragile ou compte
API à faire créer par l'utilisateur) est disproportionné pour un signal déjà
mesuré comme absent à |z| < 1.

## Ce qui déclencherait une reprise de ce chantier

- L'utilisateur fournit explicitement une clé API football-data.org (ou un
  autre fournisseur) et accepte le coût d'inscription — l'intégration d'un
  puller de dates de coupe serait alors rapide une fois la clé disponible.
- Plusieurs saisons supplémentaires s'accumulent, ce qui grossit
  naturellement le sous-échantillon "repos court" (recommandation déjà
  écrite dans `fatigue_signal_check.py`).

Sans l'un de ces deux déclencheurs, relancer ce chantier reviendrait à
investir un temps disproportionné pour, au mieux, affiner un signal déjà
mesuré comme nul.
