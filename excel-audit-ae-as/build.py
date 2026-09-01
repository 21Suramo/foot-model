# -*- coding: utf-8 -*-
"""Automatise l'onglet 'Historique AE AS' et ajoute les tableaux de bord KPI
du classeur Grille_evaluation_abb_AE_AS.

Entrée  : SRC (classeur original, inchangé)
Sortie  : DST
"""
import copy, datetime, os, re, shutil, zipfile
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as gcl
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule, FormulaRule
from openpyxl.chart import LineChart, BarChart, Reference

SRC = "/root/.claude/uploads/df2a0f54-c801-5124-a6bc-46534cbb9ac3/eedbb5a5-Grille_evaluation_abb_AE_AS__VCF.xlsx"
DST = "/home/user/foot-model/.audit-xlsx/Grille_evaluation_AE_AS_automatisee.xlsx"

# ---------------------------------------------------------------- palette
NAVY   = "1B365D"   # bandeaux principaux (déjà utilisé dans le fichier)
NAVY2  = "2A4D7C"   # en-têtes de tableau
BLUE   = "2E5B9A"
ORANGE = "E87722"   # accent Al Barid Bank
LIGHT  = "E6EEF8"   # fond des cellules calculées
GREY   = "555555"
BORDER = "BFCCE0"
WHITE  = "FFFFFF"
YELLOW = "FFF2CC"   # cellules de saisie
GREEN  = "C6EFCE"; GREEN_T = "006100"
RED    = "FFC7CE";  RED_T  = "9C0006"
AMBER  = "FFEB9C";  AMBER_T= "9C6500"

FONT = "Calibri"
thin = Side(style="thin", color=BORDER)
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)

def f(sz=11, b=False, color="000000"):
    return Font(name=FONT, size=sz, bold=b, color=color)

def fill(c):
    return PatternFill("solid", fgColor=c)

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT   = Alignment(horizontal="left",  vertical="center")
RIGHT  = Alignment(horizontal="right", vertical="center")

PCT  = "0.0%"
PCT2 = "0.0%;[Red]-0.0%"
NUM  = "0"
MIN  = '0.0" min"'
TREND= '▲ 0.0%;▼ 0.0%;"= "0.0%'

# ---------------------------------------------------------------- constantes de structure
AE, AS_ = "Grille AE", "Grille AS"
HIST, KPI, CRIT, HEB, EVO, SYN = ("Historique AE AS", "KPI Agents",
                                  "Analyse Critères", "Suivi Hebdo",
                                  "Evolution", "Synthèse & Performance")
NEVAL = 60                      # colonnes d'évaluation G..BN de chaque grille
GRID = {                        # lignes utiles de chaque grille
    AE:  dict(name=3, log=4, date=5, start=6, end=7, ecoute=8, typo=9, objet=10,
              fond=(15, 24), forme=(26, 40), mfond=43, mforme=44, score=45, statut=46),
    AS_: dict(name=3, log=4, date=5, start=6, end=7, ecoute=8, typo=9, objet=10,
              fond=(16, 22), forme=(24, 31), mfond=34, mforme=35, score=36, statut=37),
}
R_AE = (4, 63)                  # lignes de l'historique alimentées par Grille AE
R_AS = (64, 123)                # ... par Grille AS
CIBLE = "'KPI Agents'!$E$5"     # cellule unique portant la cible (80 %)

H = "'Historique AE AS'!"
def hc(col):                    # colonne complète de l'historique
    return f"{H}${col}$4:${col}$123"
HA, HB, HC, HD, HE = (hc(c) for c in "ABCDE")
HF, HG, HH, HI, HJ = (hc(c) for c in "FGHIJ")
HK, HL, HM, HN     = (hc(c) for c in "KLMN")
HQ, HR             = hc("Q"), hc("R")

wb = openpyxl.load_workbook(SRC)

# ================================================================ 1. correctif Grille AS
# La colonne E (« Note ») de la grille AS avait été transformée en cellules de
# saisie figées à 10 : la synthèse AS affichait donc 100 % quoi qu'il arrive.
# On rétablit la moyenne par critère, comme dans la grille AE.
gas = wb[AS_]
for lo, hi in (GRID[AS_]["fond"], GRID[AS_]["forme"]):
    for r in range(lo, hi + 1):
        gas[f"E{r}"] = f"=IFERROR(AVERAGE(G{r}:BN{r}),\"\")"
        gas[f"E{r}"].number_format = "0.00"
        gas[f"E{r}"].fill = fill(LIGHT)
        gas[f"E{r}"].alignment = CENTER
# la liste déroulante des notes ne doit plus couvrir la colonne E
for dv in list(gas.data_validations.dataValidation):
    if dv.type == "list" and dv.formula1 and "10,5,0" in str(dv.formula1):
        dv.sqref = openpyxl.worksheet.cell_range.MultiCellRange("G16:BN22 G24:BN31")

# ligne « STATUT OBJECTIF » absente de la grille AS -> on l'ajoute (symétrie avec AE)
gas["D37"] = "STATUT OBJECTIF :"
gas["D37"].font = f(11, True, NAVY); gas["D37"].alignment = RIGHT
for col in ["E"] + [gcl(7 + i) for i in range(NEVAL)]:
    c = gas[f"{col}37"]
    c.value = (f'=IF(ISNUMBER({col}36),IF({col}36>={CIBLE},'
               '"Objectif Atteint ✅","Non Atteint ❌"),"")')
    c.font = f(10, True); c.alignment = CENTER

# Les lignes de synthèse des deux grilles renvoyaient #DIV/0! sur chaque colonne
# d'évaluation vide (soit ~340 cellules en erreur à l'ouverture). On neutralise
# l'affichage sans toucher au calcul.
gae = wb[AE]
for r in range(*[GRID[AE]["fond"][0], GRID[AE]["fond"][1] + 1]):
    gae[f"E{r}"] = f'=IFERROR(AVERAGE(G{r}:BN{r}),"")'
for r in range(GRID[AE]["forme"][0], GRID[AE]["forme"][1] + 1):
    gae[f"E{r}"] = f'=IFERROR(AVERAGE(G{r}:BN{r}),"")'
for sheet in (AE, AS_):
    g, gs = GRID[sheet], wb[sheet]
    for col in ["E"] + [gcl(7 + i) for i in range(NEVAL)]:
        fo, ff = g["fond"], g["forme"]
        gs[f'{col}{g["mfond"]}'] = (f'=IFERROR(AVERAGEIFS({col}{fo[0]}:{col}{fo[1]},'
                                    f'{col}{fo[0]}:{col}{fo[1]},"<>N/A")/10,"")')
        gs[f'{col}{g["mforme"]}'] = (f'=IFERROR(AVERAGEIFS({col}{ff[0]}:{col}{ff[1]},'
                                     f'{col}{ff[0]}:{col}{ff[1]},"<>N/A")/10,"")')
        gs[f'{col}{g["score"]}'] = (f'=IF(OR(NOT(ISNUMBER({col}{g["mfond"]})),'
                                    f'NOT(ISNUMBER({col}{g["mforme"]}))),"",'
                                    f'{col}{g["mfond"]}*0.7+{col}{g["mforme"]}*0.3)')

# les statuts des deux grilles pointent désormais sur la cible unique
for col in ["E"] + [gcl(7 + i) for i in range(NEVAL)]:
    gae[f"{col}46"] = (f'=IF(ISNUMBER({col}45),IF({col}45>={CIBLE},'
                       '"Objectif Atteint ✅","Non Atteint ❌"),"")')

# ================================================================ 2. Historique AE AS automatisé
ws = wb[HIST]
for mr in list(ws.merged_cells.ranges):
    ws.unmerge_cells(str(mr))
ws.delete_rows(1, ws.max_row)

HEAD = ["Date d'écoute", "Nom de l'Agent", "LOG Agent", "Type d'appel",
        "Objet de l'appel", "DMC (min)", "Moyenne Fond", "Moyenne Forme",
        "Score Global", "Statut", "Semaine", "Année", "Source", "Colonne Audit",
        "Typologie de l'appel", "Type d'écoute", "Critères renseignés", "Complétude"]
WIDTHS = [13.5, 24, 11, 12, 16, 10, 13, 14, 12, 16, 9, 8, 11, 13, 22, 15, 17, 12]

ws["A1"] = "HISTORIQUE DES ÉVALUATIONS AE / AS"
ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEAD))
ws["A1"].font = f(16, True, WHITE); ws["A1"].fill = fill(NAVY); ws["A1"].alignment = CENTER
ws.row_dimensions[1].height = 30

ws["A2"] = ("Feuille 100 % calculée — aucune saisie ici. Chaque ligne reflète une colonne "
            "d'évaluation des onglets « Grille AE » et « Grille AS » ; elle se remplit dès "
            "qu'un nom d'agent est choisi dans la grille correspondante.")
ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(HEAD))
ws["A2"].font = f(9, False, GREY); ws["A2"].alignment = LEFT
ws.row_dimensions[2].height = 26

for i, (h, w) in enumerate(zip(HEAD, WIDTHS), start=1):
    c = ws.cell(3, i, h)
    c.font = f(10, True, WHITE); c.fill = fill(NAVY2)
    c.alignment = CENTER; c.border = BOX
    ws.column_dimensions[gcl(i)].width = w
ws.row_dimensions[3].height = 34

def idx(sheet, row, i):
    """INDEX sur la ligne `row` de la grille, colonne d'évaluation n° i."""
    return f"INDEX('{sheet}'!$G${row}:$BN${row},1,{i})"

def block(sheet, first, last, ncrit):
    g = GRID[sheet]
    for r in range(first, last + 1):
        i = r - first + 1
        nm = idx(sheet, g["name"], i)
        dt = idx(sheet, g["date"], i)
        st, en = idx(sheet, g["start"], i), idx(sheet, g["end"], i)
        guard = f'=IF($B{r}="","",'
        vals = {
            "A": f'=IF(OR({nm}="",{dt}=""),"",{dt})',
            "B": f'=IFERROR(IF({nm}="","",{nm}),"")',
            "C": f'{guard}IFERROR({idx(sheet,g["log"],i)},""))',
            "D": f'{guard}"{ "AE" if sheet==AE else "AS" }")',
            "E": f'{guard}IF({idx(sheet,g["objet"],i)}="","",{idx(sheet,g["objet"],i)}))',
            "F": f'{guard}IF(OR({st}="",{en}=""),"",ROUND(MOD({en}-{st},1)*1440,0)))',
            "G": f'{guard}IFERROR({idx(sheet,g["mfond"],i)},""))',
            "H": f'{guard}IFERROR({idx(sheet,g["mforme"],i)},""))',
            "I": f'{guard}IFERROR({idx(sheet,g["score"],i)},""))',
            "J": f'=IF(NOT(ISNUMBER($I{r})),"",IF($I{r}>={CIBLE},"Objectif Atteint","Non Atteint"))',
            "K": f'=IF(NOT(ISNUMBER($A{r})),"",_xlfn.ISOWEEKNUM($A{r}))',
            "L": f'=IF(NOT(ISNUMBER($A{r})),"",YEAR($A{r}))',
            "M": f'{guard}"{sheet}")',
            "N": f'{guard}SUBSTITUTE(ADDRESS(1,6+{i},4),"1",""))',
            "O": f'{guard}IF({idx(sheet,g["typo"],i)}="","",{idx(sheet,g["typo"],i)}))',
            "P": f'{guard}IF({idx(sheet,g["ecoute"],i)}="","",{idx(sheet,g["ecoute"],i)}))',
        }
        fo, ff = g["fond"], g["forme"]
        cnt = (f'COUNTA(INDEX(\'{sheet}\'!$G${fo[0]}:$BN${fo[1]},1,{i}):'
               f'INDEX(\'{sheet}\'!$G${fo[0]}:$BN${fo[1]},{fo[1]-fo[0]+1},{i}))'
               f'+COUNTA(INDEX(\'{sheet}\'!$G${ff[0]}:$BN${ff[1]},1,{i}):'
               f'INDEX(\'{sheet}\'!$G${ff[0]}:$BN${ff[1]},{ff[1]-ff[0]+1},{i}))')
        vals["Q"] = f'{guard}{cnt})'
        vals["R"] = f'=IF(NOT(ISNUMBER($Q{r})),"",$Q{r}/{ncrit})'
        for col, v in vals.items():
            ws[f"{col}{r}"] = v
        for col in "ABCDEFGHIJKLMNOPQR":
            c = ws[f"{col}{r}"]
            c.font = f(10); c.border = BOX
            c.alignment = LEFT if col in "BEMO" else CENTER
        ws[f"A{r}"].number_format = "dd/mm/yyyy"
        ws[f"F{r}"].number_format = MIN
        for col in "GHIR":
            ws[f"{col}{r}"].number_format = PCT
        ws[f"I{r}"].font = f(10, True, NAVY)
        for col in "GHIR":
            ws[f"{col}{r}"].fill = fill(LIGHT)

block(AE, *R_AE, ncrit=25)      # 10 critères Fond + 15 Forme
block(AS_, *R_AS, ncrit=15)     #  7 critères Fond +  8 Forme

ws.freeze_panes = "A4"
ws.auto_filter.ref = f"A3:R{R_AS[1]}"
ws.sheet_view.showGridLines = False
ws.conditional_formatting.add(
    f"J4:J{R_AS[1]}",
    FormulaRule(formula=['$J4="Objectif Atteint"'],
                fill=fill(GREEN), font=Font(name=FONT, size=10, color=GREEN_T)))
ws.conditional_formatting.add(
    f"J4:J{R_AS[1]}",
    FormulaRule(formula=['$J4="Non Atteint"'],
                fill=fill(RED), font=Font(name=FONT, size=10, color=RED_T)))
ws.conditional_formatting.add(
    f"R4:R{R_AS[1]}",
    FormulaRule(formula=['AND(ISNUMBER($R4),$R4<0.5)'],
                fill=fill(AMBER), font=Font(name=FONT, size=10, color=AMBER_T)))

# ================================================================ 3. KPI Agents
agents = [wb["LOG Agent"].cell(r, 1).value for r in range(2, 53)]
NAG = len(agents)
R0, R1 = 12, 12 + NAG - 1          # lignes du tableau agents
TOT = R1 + 1                       # ligne « ensemble du centre »

if KPI in wb.sheetnames:
    del wb[KPI]
k = wb.create_sheet(KPI, wb.sheetnames.index(HIST) + 1)

KH = ["Collaborateur", "Log", "Audits", "dont AE", "dont AS", "Moyenne Fond",
      "Moyenne Forme", "Score global", "Écart vs cible", "Taux de conformité",
      "Complétude grille", "DMC moyenne", "Dernier score", "Score précédent",
      "Tendance", "Meilleur", "Moins bon", "Niveau", "Rang"]
KW = [26, 8, 8, 8, 8, 11, 11, 12, 11, 12, 12, 11, 11, 12, 11, 10, 11, 16, 7]
NC = len(KH)
LAST = gcl(NC)

for i, w in enumerate(KW, start=1):
    k.column_dimensions[gcl(i)].width = w

def band(row, text, sub=None):
    k.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NC)
    c = k.cell(row, 1, text)
    c.font = f(15, True, WHITE); c.fill = fill(NAVY); c.alignment = CENTER
    k.row_dimensions[row].height = 30

band(2, "BILAN & PERFORMANCE PAR AGENT")
k.merge_cells(start_row=3, start_column=1, end_row=3, end_column=NC)
k["A3"] = ("AL BARID BANK — Centre de Relations Clients · Pondération 70 % Fond / 30 % Forme · "
           "toutes les valeurs sont calculées depuis l'onglet « Historique AE AS »")
k["A3"].font = f(9, False, ORANGE); k["A3"].alignment = CENTER
k.row_dimensions[3].height = 18

# --- filtres (seules cellules saisissables de la feuille)
FL = [("Année", 2026, NUM), ("Type d'appel", "Tous", "General"),
      ("Semaine de", 1, NUM), ("Semaine à", 53, NUM), ("Cible score global", 0.8, PCT)]
for i, (lab, val, fmt) in enumerate(FL, start=1):
    c = k.cell(4, i, lab); c.font = f(9, True, GREY); c.alignment = CENTER
    v = k.cell(5, i, val); v.font = f(12, True, NAVY); v.alignment = CENTER
    v.fill = fill(YELLOW); v.border = BOX; v.number_format = fmt
k.cell(4, 6, "◀ zone de saisie (jaune) : filtre l'ensemble du tableau et de la ligne « ensemble du centre »")
k.cell(4, 6).font = f(9, False, GREY); k.cell(4, 6).alignment = LEFT
k.row_dimensions[5].height = 22
dv_type = DataValidation(type="list", formula1='"Tous,AE,AS"', allow_blank=False,
                         showDropDown=False, showErrorMessage=True, showInputMessage=True)
k.add_data_validation(dv_type); dv_type.add(k["B5"])

# critères communs à toutes les agrégations filtrées
FILT = f'{HL},$A$5,{HK},">="&$C$5,{HK},"<="&$D$5,{HD},IF($B$5="Tous","*",$B$5)'
def AG(r):    # critères + agent de la ligne r
    return f'{HB},$A{r},{FILT}'

# --- bandeau de cartes KPI (bilan de la sélection)
CARDS = [
    ("AUDITS RÉALISÉS",   f'=COUNTIFS({FILT})', NUM,  (1, 3)),
    ("AGENTS AUDITÉS",    f'=COUNTIF($C${R0}:$C${R1},">0")', NUM, (4, 6)),
    ("SCORE GLOBAL MOYEN",f'=IFERROR(AVERAGEIFS({HI},{FILT}),"")', PCT, (7, 9)),
    ("TAUX DE CONFORMITÉ",f'=IF($A$8=0,"",COUNTIFS({FILT},{HJ},"Objectif Atteint")/$A$8)', PCT, (10, 12)),
    ("MOYENNE FOND (70%)", f'=IFERROR(AVERAGEIFS({HG},{FILT}),"")', PCT, (13, 15)),
    ("MOYENNE FORME (30%)",f'=IFERROR(AVERAGEIFS({HH},{FILT}),"")', PCT, (16, 17)),
    ("COMPLÉTUDE",        f'=IFERROR(AVERAGEIFS({HR},{FILT}),"")', PCT, (18, 19)),
]
for lab, formula, fmt, (c1, c2) in CARDS:
    k.merge_cells(start_row=7, start_column=c1, end_row=7, end_column=c2)
    k.merge_cells(start_row=8, start_column=c1, end_row=9, end_column=c2)
    h = k.cell(7, c1, lab)
    h.font = f(8, True, WHITE); h.fill = fill(BLUE); h.alignment = CENTER
    v = k.cell(8, c1, formula)
    v.font = f(18, True, NAVY); v.fill = fill(LIGHT); v.alignment = CENTER
    v.number_format = fmt
    for rr in (7, 8, 9):
        for cc in range(c1, c2 + 1):
            k.cell(rr, cc).border = BOX
k.row_dimensions[7].height = 16
k.row_dimensions[8].height = 16
k.row_dimensions[9].height = 20

# --- en-tête du tableau
for i, h in enumerate(KH, start=1):
    c = k.cell(11, i, h)
    c.font = f(10, True, WHITE); c.fill = fill(NAVY2)
    c.alignment = CENTER; c.border = BOX
k.row_dimensions[11].height = 38

def kpi_row(r, name=None):
    """name=None -> ligne d'agrégat « ensemble du centre »."""
    per_agent = name is not None
    crit = AG(r) if per_agent else FILT
    last = f'_xlfn.MAXIFS({HA},{crit})'
    prev = f'_xlfn.MAXIFS({HA},{crit},{HA},"<"&{last})'
    vals = {
        "A": name if per_agent else "ENSEMBLE DU CENTRE (sélection)",
        "B": f'=IFERROR(VLOOKUP($A{r},\'LOG Agent\'!$A:$B,2,FALSE),"")' if per_agent else "",
        "C": f'=COUNTIFS({crit})',
        "D": f'=COUNTIFS({crit},{HD},"AE")',
        "E": f'=COUNTIFS({crit},{HD},"AS")',
        "F": f'=IFERROR(AVERAGEIFS({HG},{crit}),"")',
        "G": f'=IFERROR(AVERAGEIFS({HH},{crit}),"")',
        "H": f'=IFERROR(AVERAGEIFS({HI},{crit}),"")',
        "I": f'=IF(NOT(ISNUMBER($H{r})),"",$H{r}-$E$5)',
        "J": f'=IF($C{r}=0,"",COUNTIFS({crit},{HJ},"Objectif Atteint")/$C{r})',
        "K": f'=IFERROR(AVERAGEIFS({HR},{crit}),"")',
        "L": f'=IFERROR(AVERAGEIFS({HF},{crit}),"")',
        "M": f'=IF($C{r}=0,"",IFERROR(AVERAGEIFS({HI},{crit},{HA},{last}),""))',
        "N": f'=IF($C{r}<2,"",IFERROR(AVERAGEIFS({HI},{crit},{HA},{prev}),""))',
        "O": f'=IF(OR(NOT(ISNUMBER($M{r})),NOT(ISNUMBER($N{r}))),"",$M{r}-$N{r})',
        "P": f'=IF($C{r}=0,"",_xlfn.MAXIFS({HI},{crit}))',
        "Q": f'=IF($C{r}=0,"",_xlfn.MINIFS({HI},{crit}))',
        "R": (f'=IF(NOT(ISNUMBER($H{r})),"",IF($H{r}>=0.9,"Excellent",'
              f'IF($H{r}>=$E$5,"Conforme",IF($H{r}>=$E$5-0.1,"À accompagner","Critique"))))'),
        "S": (f'=IF(NOT(ISNUMBER($H{r})),"",RANK($H{r},$H${R0}:$H${R1}))'
              if per_agent else ""),
    }
    # la colonne D doit combiner le filtre et le type AE : quand le filtre vaut AS,
    # COUNTIFS renvoie 0, ce qui est le comportement attendu.
    for col, v in vals.items():
        c = k[f"{col}{r}"]
        c.value = v if v != "" else None
        c.font = f(10, True, NAVY) if not per_agent else f(10)
        c.border = BOX
        c.alignment = LEFT if col == "A" else CENTER
    for col in "FGHIJKMNOPQ":
        k[f"{col}{r}"].number_format = PCT
    k[f"I{r}"].number_format = PCT2
    k[f"O{r}"].number_format = TREND
    k[f"L{r}"].number_format = MIN
    for col in "CDES":
        k[f"{col}{r}"].number_format = NUM
    k[f"H{r}"].font = f(11, True, NAVY)
    k[f"H{r}"].fill = fill(LIGHT)
    if not per_agent:
        for col in "ABCDEFGHIJKLMNOPQRS":
            k[f"{col}{r}"].fill = fill(LIGHT)
            k[f"{col}{r}"].font = f(10, True, NAVY)

for j, nm in enumerate(agents):
    kpi_row(R0 + j, nm)
kpi_row(TOT)

k.freeze_panes = "C12"
k.auto_filter.ref = f"A11:{LAST}{R1}"
k.sheet_view.showGridLines = False

rng = f"H{R0}:H{R1}"
k.conditional_formatting.add(rng, ColorScaleRule(
    start_type="num", start_value=0.6, start_color="F8696B",
    mid_type="num",   mid_value=0.8,  mid_color="FFEB84",
    end_type="num",   end_value=1.0,  end_color="63BE7B"))
k.conditional_formatting.add(f"J{R0}:J{R1}", DataBarRule(
    start_type="num", start_value=0, end_type="num", end_value=1, color="2E5B9A"))
k.conditional_formatting.add(f"K{R0}:K{R1}", DataBarRule(
    start_type="num", start_value=0, end_type="num", end_value=1, color=ORANGE))
for lvl, bg, fg in (("Excellent", GREEN, GREEN_T), ("Conforme", "DDEBF7", "1F4E79"),
                    ("À accompagner", AMBER, AMBER_T), ("Critique", RED, RED_T)):
    k.conditional_formatting.add(f"R{R0}:R{R1}", FormulaRule(
        formula=[f'$R{R0}="{lvl}"'], fill=fill(bg),
        font=Font(name=FONT, size=10, bold=True, color=fg)))
k.conditional_formatting.add(f"A{R0}:{LAST}{R1}", FormulaRule(
    formula=[f'$C{R0}=0'], font=Font(name=FONT, size=10, color="B7B7B7")))
k.conditional_formatting.add(f"O{R0}:O{R1}", FormulaRule(
    formula=[f'AND(ISNUMBER($O{R0}),$O{R0}<0)'],
    font=Font(name=FONT, size=10, bold=True, color=RED_T)))
k.conditional_formatting.add(f"O{R0}:O{R1}", FormulaRule(
    formula=[f'AND(ISNUMBER($O{R0}),$O{R0}>0)'],
    font=Font(name=FONT, size=10, bold=True, color=GREEN_T)))

k.cell(TOT + 2, 1, "Lecture : Complétude = part des critères de la grille effectivement "
                   "renseignés (note ou N/A). Un score élevé sur une grille peu remplie "
                   "n'est pas représentatif.")
k.cell(TOT + 2, 1).font = f(9, False, GREY)
k.merge_cells(start_row=TOT + 2, start_column=1, end_row=TOT + 2, end_column=NC)
k.cell(TOT + 3, 1, "Niveaux : Excellent ≥ 90 % · Conforme ≥ cible · À accompagner ≥ cible − 10 pts · "
                   "Critique en dessous.")
k.cell(TOT + 3, 1).font = f(9, False, GREY)
k.merge_cells(start_row=TOT + 3, start_column=1, end_row=TOT + 3, end_column=NC)

# ================================================================ 4. Analyse Critères
if CRIT in wb.sheetnames:
    del wb[CRIT]
a = wb.create_sheet(CRIT, wb.sheetnames.index(KPI) + 1)
AH = ["Réf", "Grille", "Catégorie", "Critère d'évaluation", "Note moyenne /10",
      "Score", "Nb évaluations", "Statut"]
for i, w in enumerate([7, 11, 10, 78, 15, 11, 14, 18], start=1):
    a.column_dimensions[gcl(i)].width = w
a.merge_cells("A2:H2")
a["A2"] = "ANALYSE PAR CRITÈRE — OÙ SE JOUENT LES POINTS PERDUS"
a["A2"].font = f(15, True, WHITE); a["A2"].fill = fill(NAVY); a["A2"].alignment = CENTER
a.row_dimensions[2].height = 30
a.merge_cells("A3:H3")
a["A3"] = ("Moyenne de chaque critère sur l'ensemble des évaluations saisies dans la grille "
           "correspondante (colonnes G à BN). Les critères notés « N/A » sont neutralisés.")
a["A3"].font = f(9, False, ORANGE); a["A3"].alignment = CENTER

for i, h in enumerate(AH, start=1):
    c = a.cell(5, i, h)
    c.font = f(10, True, WHITE); c.fill = fill(NAVY2); c.alignment = CENTER; c.border = BOX
a.row_dimensions[5].height = 30

r = 6
first_data = r
for sheet, label in ((AE, "Grille AE"), (AS_, "Grille AS")):
    g = GRID[sheet]
    a.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    c = a.cell(r, 1, f"{label} — {'appels entrants' if sheet==AE else 'appels sortants'}")
    c.font = f(11, True, WHITE); c.fill = fill(BLUE); c.alignment = CENTER
    r += 1
    for cat, (lo, hi) in (("Fond", g["fond"]), ("Forme", g["forme"])):
        for gr in range(lo, hi + 1):
            a.cell(r, 1, f"='{sheet}'!$B{gr}")
            a.cell(r, 2, label)
            a.cell(r, 3, cat)
            a.cell(r, 4, f"='{sheet}'!$D{gr}")
            a.cell(r, 5, f'=IFERROR(AVERAGE(\'{sheet}\'!$G{gr}:$BN{gr}),"")')
            a.cell(r, 6, f'=IF(NOT(ISNUMBER($E{r})),"",$E{r}/10)')
            a.cell(r, 7, f"=COUNT('{sheet}'!$G{gr}:$BN{gr})")
            a.cell(r, 8, (f'=IF(NOT(ISNUMBER($F{r})),"—",'
                          f'IF($F{r}>=0.9,"Maîtrisé",IF($F{r}>=$E$5x,"À consolider","Point critique")))')
                   .replace("$E$5x", "0.7"))
            for cc in range(1, 9):
                cell = a.cell(r, cc)
                cell.font = f(10); cell.border = BOX
                cell.alignment = LEFT if cc == 4 else CENTER
            a.cell(r, 4).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            a.cell(r, 5).number_format = "0.00"
            a.cell(r, 6).number_format = PCT
            a.cell(r, 6).fill = fill(LIGHT); a.cell(r, 6).font = f(10, True, NAVY)
            a.cell(r, 7).number_format = NUM
            a.row_dimensions[r].height = 28
            r += 1
last_data = r - 1
a.freeze_panes = "A6"
a.sheet_view.showGridLines = False
a.conditional_formatting.add(f"F{first_data}:F{last_data}", DataBarRule(
    start_type="num", start_value=0, end_type="num", end_value=1, color="2E5B9A"))
for lvl, bg, fg in (("Maîtrisé", GREEN, GREEN_T), ("À consolider", AMBER, AMBER_T),
                    ("Point critique", RED, RED_T)):
    a.conditional_formatting.add(f"H{first_data}:H{last_data}", FormulaRule(
        formula=[f'$H{first_data}="{lvl}"'], fill=fill(bg),
        font=Font(name=FONT, size=10, bold=True, color=fg)))

# ================================================================ 5. Suivi Hebdo (dynamique)
s = wb[HEB]
for mr in list(s.merged_cells.ranges):
    s.unmerge_cells(str(mr))
s.delete_rows(1, s.max_row)
SH = ["Semaine", "Audits centre", "Score centre", "Fond centre", "Forme centre",
      "Taux conformité", "Audits agent", "Score agent"]
for i, w in enumerate([11, 14, 14, 14, 14, 15, 14, 14], start=1):
    s.column_dimensions[gcl(i)].width = w
s.merge_cells("A1:H1")
s["A1"] = "SUIVI HEBDOMADAIRE DES PERFORMANCES"
s["A1"].font = f(15, True, WHITE); s["A1"].fill = fill(NAVY); s["A1"].alignment = CENTER
s.row_dimensions[1].height = 30
s["A3"] = "Année"; s["A4"] = "Agent suivi"
for cc in ("A3", "A4"):
    s[cc].font = f(10, True, GREY); s[cc].alignment = RIGHT
s["B3"] = 2026; s["B4"] = agents[1] if len(agents) > 1 else agents[0]
for cc in ("B3", "B4"):
    s[cc].font = f(11, True, NAVY); s[cc].fill = fill(YELLOW)
    s[cc].border = BOX; s[cc].alignment = CENTER
s.merge_cells("B4:C4")
s["D3"] = "◀ cellules de saisie : pilotent le tableau et les graphiques de l'onglet « Evolution »."
s["D3"].font = f(9, False, GREY); s["D3"].alignment = LEFT
dv_ag = DataValidation(type="list", formula1="'LOG Agent'!$A$2:$A$52", allow_blank=True,
                       showDropDown=False, showErrorMessage=True, showInputMessage=True)
s.add_data_validation(dv_ag); dv_ag.add(s["B4"])

for i, h in enumerate(SH, start=1):
    c = s.cell(6, i, h)
    c.font = f(10, True, WHITE); c.fill = fill(NAVY2); c.alignment = CENTER; c.border = BOX
s.row_dimensions[6].height = 30
HEB_R0, HEB_R1 = 7, 59          # semaines 1 à 53
for w in range(1, 54):
    r = HEB_R0 + w - 1
    base = f'{HL},$B$3,{HK},$A{r}'
    s.cell(r, 1, w).number_format = '"S"0'
    s.cell(r, 2, f'=COUNTIFS({base})')
    s.cell(r, 3, f'=IFERROR(AVERAGEIFS({HI},{base}),"")')
    s.cell(r, 4, f'=IFERROR(AVERAGEIFS({HG},{base}),"")')
    s.cell(r, 5, f'=IFERROR(AVERAGEIFS({HH},{base}),"")')
    s.cell(r, 6, f'=IF($B{r}=0,"",COUNTIFS({base},{HJ},"Objectif Atteint")/$B{r})')
    s.cell(r, 7, f'=COUNTIFS({base},{HB},$B$4)')
    s.cell(r, 8, f'=IFERROR(AVERAGEIFS({HI},{base},{HB},$B$4),"")')
    for cc in range(1, 9):
        cell = s.cell(r, cc); cell.font = f(10); cell.border = BOX; cell.alignment = CENTER
    for cc in (3, 4, 5, 6, 8):
        s.cell(r, cc).number_format = PCT
    for cc in (2, 7):
        s.cell(r, cc).number_format = NUM
    s.cell(r, 3).fill = fill(LIGHT); s.cell(r, 8).fill = fill(LIGHT)
s.freeze_panes = "A7"
s.sheet_view.showGridLines = False
s.conditional_formatting.add(f"C{HEB_R0}:C{HEB_R1}", ColorScaleRule(
    start_type="num", start_value=0.6, start_color="F8696B",
    mid_type="num", mid_value=0.8, mid_color="FFEB84",
    end_type="num", end_value=1.0, end_color="63BE7B"))
s.conditional_formatting.add(f"B{HEB_R0}:B{HEB_R1}", DataBarRule(
    start_type="num", start_value=0, end_type="max", color="9DC3E6"))

# ================================================================ 6. Evolution (graphiques)
e = wb[EVO]
e.delete_rows(1, max(e.max_row, 3))
for ch in list(getattr(e, "_charts", [])):
    e._charts.remove(ch)
e.merge_cells("A1:P1")
e["A1"] = "ÉVOLUTION DES PERFORMANCES AE / AS"
e["A1"].font = f(15, True, WHITE); e["A1"].fill = fill(NAVY); e["A1"].alignment = CENTER
e.row_dimensions[1].height = 30
e.merge_cells("A2:P2")
e["A2"] = ("Graphiques alimentés par l'onglet « Suivi Hebdo ». Changez l'année ou l'agent suivi "
           "dans les cellules jaunes de cet onglet : les courbes se recalculent.")
e["A2"].font = f(9, False, ORANGE); e["A2"].alignment = CENTER
e.sheet_view.showGridLines = False
for i in range(1, 17):
    e.column_dimensions[gcl(i)].width = 9

cats = Reference(s, min_col=1, min_row=HEB_R0, max_row=HEB_R1)

def add_line(anchor, title, cols, colors, ytitle="Score", pct=True):
    ch = LineChart()
    ch.title = title
    ch.style = 2
    ch.height, ch.width = 8.5, 17
    ch.y_axis.title = ytitle
    ch.x_axis.title = "Semaine ISO"
    ch.dispBlanksAs = "gap"
    for col in cols:
        ref = Reference(s, min_col=col, min_row=HEB_R0 - 1, max_row=HEB_R1)
        ch.add_data(ref, titles_from_data=True)
    ch.set_categories(cats)
    for ser, colr in zip(ch.series, colors):
        ser.graphicalProperties.line.solidFill = colr
        ser.graphicalProperties.line.width = 22000
        ser.smooth = False
    if pct:
        ch.y_axis.numFmt = "0%"
    e.add_chart(ch, anchor)

add_line("A4",  "Score global — centre vs agent suivi", [3, 8], [NAVY, ORANGE])
add_line("I4",  "Fond (70 %) vs Forme (30 %) — centre", [4, 5], [BLUE, "70AD47"])
add_line("A22", "Taux de conformité hebdomadaire (part des audits ≥ cible)", [6], [ORANGE])

bar = BarChart(); bar.type = "col"; bar.style = 2
bar.title = "Volume d'audits réalisés par semaine"
bar.height, bar.width = 8.5, 17
bar.y_axis.title = "Nb d'audits"; bar.x_axis.title = "Semaine ISO"
bref = Reference(s, min_col=2, min_row=HEB_R0 - 1, max_row=HEB_R1)
bar.add_data(bref, titles_from_data=True); bar.set_categories(cats)
bar.series[0].graphicalProperties.solidFill = BLUE
e.add_chart(bar, "I22")

# ================================================================ 7. Synthèse & Performance
y = wb[SYN]
y["B9"]  = f'=COUNT({HI})'
y["C9"]  = f'=IFERROR(AVERAGE({HI}),"")'
y["D9"]  = "=C13"
y["E9"]  = "=F13"
y["F8"]  = "TAUX DE CONFORMITÉ"
y["F9"]  = f'=IF($B$9=0,"",COUNTIF({HJ},"Objectif Atteint")/$B$9)'
y["F9"].number_format = PCT
y["G9"]  = "=_xlfn.ISOWEEKNUM(TODAY())"
y["C13"] = f'=IFERROR(AVERAGEIFS({HI},{HD},"AE"),"")'
y["C14"] = f'=IFERROR(AVERAGEIFS({HG},{HD},"AE"),"")'
y["C15"] = f'=IFERROR(AVERAGEIFS({HH},{HD},"AE"),"")'
y["F13"] = f'=IFERROR(AVERAGEIFS({HI},{HD},"AS"),"")'
y["F14"] = f'=IFERROR(AVERAGEIFS({HG},{HD},"AS"),"")'
y["F15"] = f'=IFERROR(AVERAGEIFS({HH},{HD},"AS"),"")'
y["C16"] = (f'=IF(NOT(ISNUMBER(C13)),"—",IF(C13>={CIBLE},'
            '"Objectif Atteint ✅","Non Atteint ❌"))')
y["F16"] = (f'=IF(NOT(ISNUMBER(F13)),"—",IF(F13>={CIBLE},'
            '"Objectif Atteint ✅","Non Atteint ❌"))')
y["D13"] = f'="Cible : ≥ "&TEXT({CIBLE},"0.0%")'
y["G13"] = f'="Cible : ≥ "&TEXT({CIBLE},"0.0%")'
y["C9"].number_format = PCT
y["B18"] = ("Les moyennes ci-dessus proviennent de l'onglet « Historique AE AS », "
            "lui-même alimenté automatiquement par les grilles AE et AS. "
            "Détail par agent : onglet « KPI Agents » · détail par critère : onglet « Analyse Critères ».")
y.merge_cells("B18:G18")
y["B18"].font = f(9, False, GREY)
y["B18"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
y.row_dimensions[18].height = 30

# ================================================================ 8. finitions
TABS = {SYN: NAVY, AE: BLUE, AS_: BLUE, HIST: "70AD47", KPI: ORANGE,
        CRIT: ORANGE, HEB: "7F7F7F", EVO: "7F7F7F"}
for name, col in TABS.items():
    wb[name].sheet_properties.tabColor = col
wb.move_sheet(KPI, offset=0)
wb.active = wb.sheetnames.index(SYN)

# mise en page pour impression / export PDF des feuilles de restitution
for name, cols in ((KPI, NC), (CRIT, 8), (HIST, len(HEAD)), (HEB, 8)):
    p_ws = wb[name]
    p_ws.page_setup.orientation = "landscape"
    p_ws.page_setup.fitToWidth = 1
    p_ws.page_setup.fitToHeight = 0
    p_ws.sheet_properties.pageSetUpPr.fitToPage = True
    p_ws.print_title_rows = "1:11" if name == KPI else ("1:5" if name == CRIT else "1:3")

wb.save(DST)
print("saved", DST)
