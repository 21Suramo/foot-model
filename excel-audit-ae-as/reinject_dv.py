# -*- coding: utf-8 -*-
"""openpyxl supprime les listes déroulantes « x14 » (celles qui pointent vers une
autre feuille : LOG Agent, Objet&TYP). On les réinjecte dans le XML après sauvegarde."""
import re, shutil, zipfile, os, sys

SRC = "/root/.claude/uploads/df2a0f54-c801-5124-a6bc-46534cbb9ac3/eedbb5a5-Grille_evaluation_abb_AE_AS__VCF.xlsx"
DST = sys.argv[1] if len(sys.argv) > 1 else "/home/user/foot-model/excel-audit-ae-as/Grille_evaluation_AE_AS_automatisee.xlsx"
EXT_URI = "{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}"

def sheet_map(z):
    wbx = z.read("xl/workbook.xml").decode("utf8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf8")
    rid2t = {}
    for rel in re.findall(r"<Relationship\b[^>]*/>", rels):
        rid = re.search(r'Id="([^"]+)"', rel)
        tgt = re.search(r'Target="([^"]+)"', rel)
        if rid and tgt:
            rid2t[rid.group(1)] = tgt.group(1)
    out = {}
    for tag in re.findall(r"<sheet\b[^>]*/>", wbx):
        name = re.search(r'name="([^"]+)"', tag).group(1)
        rid = re.search(r'r:id="([^"]+)"', tag).group(1)
        t = rid2t[rid].lstrip("/")
        out[name] = t if t.startswith("xl/") else "xl/" + t
    return out

with zipfile.ZipFile(SRC) as z:
    smap = sheet_map(z)
    blocks = {}
    for name in ("Grille AE", "Grille AS"):
        xml = z.read(smap[name]).decode("utf8")
        m = re.search(r'<ext uri="\{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF\}".*?</ext>', xml, re.S)
        blk = m.group(0) if m else None
        if blk:
            # openpyxl ne déclare pas le namespace "xr" sur <worksheet> : on retire les
            # attributs xr:uid, sinon le XML produit est invalide (préfixe non lié).
            blk = re.sub(r'\s+xr:uid="[^"]*"', "", blk)
        blocks[name] = blk

with zipfile.ZipFile(DST) as z:
    dmap = sheet_map(z)
    items = [(i, z.read(i.filename)) for i in z.infolist()]

changed = 0
new_items = []
for info, data in items:
    for name, blk in blocks.items():
        if blk and info.filename == dmap[name]:
            xml = data.decode("utf8")
            if EXT_URI in xml:
                break
            if "<extLst>" in xml:
                xml = xml.replace("<extLst>", "<extLst>" + blk, 1)
            else:
                xml = xml.replace("</worksheet>", f"<extLst>{blk}</extLst></worksheet>")
            data = xml.encode("utf8")
            changed += 1
            break
    new_items.append((info, data))

tmp = DST + ".tmp"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
    for info, data in new_items:
        z.writestr(info.filename, data)
shutil.move(tmp, DST)

# garde-fou : le classeur doit rester lisible après réinjection
import openpyxl
openpyxl.load_workbook(DST)
print(f"x14 dataValidations réinjectées dans {changed} feuille(s) — classeur relu sans erreur")
