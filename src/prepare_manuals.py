#!/usr/bin/env python3

import argparse
import csv
import difflib
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

MIN_CHARS = 60  # en dessous, la section n'a pas de fichier (simple titre parent)
LOW_CHARS = 250  # en dessous, la section est signalée "peu de texte"

DOCS = {
    "egp": dict(
        filename="e-GP_MANUEL_UTILISATEUR_V_1_B.pdf", mode="toc", style="num",
        plateforme="e-GP", outdir="egp", prefix="egp", toc_pages=(2, 5), body_start=7,
        titre="Manuel utilisateur e-GP, version 1 B (état : provisoire)",
        avertissement=("Document provisoire : les adresses web, les écrans et certaines procédures "
                       "ont pu changer depuis sa rédaction. À confirmer avec la DSI."),
        footer=r"^\s*Etat\s*:\s*\w+\s+Page\s+\d+\s*/\s*\d+\s*$"),
    "sigmp": dict(
        filename="SIGMP_Manu_Util_2_0_Provisoire.pdf", mode="toc", style="label",
        plateforme="SIGMP", outdir="sigmp", prefix="sigmp", toc_pages=(2, 5), body_start=14,
        titre="Manuel utilisateur SIGMP, version 2.0 du 10/01/2017 (état : provisoire)",
        avertissement=("Document provisoire de 2017 : les écrans et procédures ont pu changer. "
                       "À confirmer avec la DSI."),
        footer=r"^\s*~\s*\d+\s*~\s*$"),
    "guide": dict(
        filename="GUIDE-UTILISATEUR-SUR-LA-PASSATION-DE-MARCHE.pdf", mode="chapters",
        plateforme="Réglementation (historique)", outdir="historique_guide_2006", prefix="guide2006",
        body_start=8,
        titre="Guide de l'utilisateur - Passation des marchés publics (août 2006)",
        avertissement=("ATTENTION : guide rédigé en 2006 sur l'ancien Code des marchés publics "
                       "(loi 2004-009, remplacée depuis par la loi 2016-055). Numéros d'articles et "
                       "règles potentiellement périmés. Ne pas utiliser comme droit en vigueur."),
        footer=r"^\s*Jean Jacques Lecat.*août 2006.*$"),
    "bef": dict(
        filename="BENEFICIAIRE_EFFECTIF_A_L_eGP.pdf", mode="single", style="",
        plateforme="e-GP", outdir="egp", prefix="egp_be", body_start=1,
        titre="Mode d'emploi e-GP : bénéficiaire effectif",
        section_title="Comment ajouter un bénéficiaire effectif à un contrat dans e-GP ?",
        extra=("Capture d'écran du formulaire « BENEFICIAIRES EFFECTIFS » : champs Nom, Prénom et "
               "Qualité ; boutons Annuler et Enregistrer."),
        avertissement=None, footer=r"^$"),
}


# ------------------------------------------------------------------ utilitaires
def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def key(s):
    return re.sub(r"[^a-z0-9]", "", strip_accents(s).lower())


def slug(s, n=55):
    s = re.sub(r"[^a-z0-9]+", "-", strip_accents(s).lower()).strip("-")
    return s[:n].strip("-") or "section"


def table_to_md(rows):
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows if any(r)]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w]
    md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    head = [h.lower() for h in rows[0]]
    if head and head[0] == "groupe" and any("consultation" in h for h in head):
        md.append("")
        for r in rows[1:]:
            ok = [rows[0][j].lower() for j in range(1, w) if r[j].strip().upper() in ("X", "OUI", "✓")]
            md.append(f"- Profil {r[0]} : " + (", ".join(ok) if ok else "aucun droit") + ".")
    return "\n".join(md)


def page_lines(page, pageno, footer_re):
    """Lignes de texte d'une page, tableaux convertis en Markdown, dans l'ordre de lecture."""
    try:
        page = page.dedupe_chars(tolerance=1)  # corrige "Plaan dee Passsation"
    except AttributeError:
        pass
    tables = page.find_tables()
    boxes = [t.bbox for t in tables]

    def outside(obj):
        if obj.get("object_type") != "char":
            return True
        return not any(obj["x0"] >= b[0] - 1 and obj["x1"] <= b[2] + 1 and
                       obj["top"] >= b[1] - 1 and obj["bottom"] <= b[3] + 1 for b in boxes)

    src = page.filter(outside) if boxes else page
    items = [(ln["top"], ln["text"].strip(), False) for ln in src.extract_text_lines()]
    for t in tables:
        md = table_to_md(t.extract())
        if md:
            items.append((t.bbox[1], md, True))
    items.sort(key=lambda x: x[0])
    out = []
    for _, text, is_table in items:
        text = re.sub(r"\(cid:\d+\)\s*", "• ", text).strip()
        if not text or (not is_table and footer_re.match(text)):
            continue
        if not is_table and re.fullmatch(r"[\.\s_\-]{3,}", text):
            continue
        out.append({"page": pageno, "text": text, "table": is_table})
    return out


def read_pdf(path, footer, first, last=None):
    footer_re = re.compile(footer, re.I)
    lines = []
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        for i in range(first, (last or n) + 1):
            lines += page_lines(pdf.pages[i - 1], i, footer_re)
    return lines, n


# ------------------------------------------------------------- sommaire (mode toc)
TOC_E = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s*(\S.*?)\s*\.{3,}\s*(\d+)\s*$")
TOC_S = re.compile(r"^\s*([IVX]+|\d+|[a-z])\.\s*(\S.*?)\s*\.{3,}\s*(\d+)\s*$")


def parse_toc(path, doc):
    lines, _ = read_pdf(path, doc["footer"], doc["toc_pages"][0], doc["toc_pages"][1])
    text = "\n".join(l["text"] for l in lines if not l["table"])
    text = re.sub(r"\n\s*(\.{3,}\s*\d+)", r" \1", text)  # titre sur 2 lignes
    entries = []
    for ln in text.splitlines():
        if doc["style"] == "num":
            m = TOC_E.match(ln)
            if m:
                num = m.group(1)
                entries.append(dict(label=num, title=m.group(2).strip(), level=num.count(".") + 1,
                                    toc_page=int(m.group(3))))
        else:
            m = TOC_S.match(ln)
            if m:
                lab = m.group(1)
                lvl = 1 if re.fullmatch(r"[IVX]+", lab) else (2 if lab.isdigit() else 3)
                entries.append(dict(label=lab, title=m.group(2).strip(), level=lvl,
                                    toc_page=int(m.group(3))))
    return entries


def heading_match(line, entry, style):
    t = line["text"]
    if line["table"]:
        return False
    if style == "num":
        m = re.match(r"^\s*(\d+(?:\.\d+)*)\.?\s*(\S.*)$", t)
    else:
        m = re.match(r"^\s*([IVX]+|\d+|[a-z])\.\s*(\S.*)$", t)
    if not m or m.group(1) != entry["label"]:
        return False
    lk, tk = key(m.group(2)), key(entry["title"])
    if not lk or not tk:
        return False
    if lk.startswith(tk[:30]) or (len(lk) >= 12 and tk.startswith(lk)):
        return True
    return difflib.SequenceMatcher(None, lk[:len(tk)], tk).ratio() >= 0.88


def locate(lines, entries, style):
    pos = 0
    for e in entries:
        e["idx"] = None
        for i in range(pos, len(lines)):
            if heading_match(lines[i], e, style):
                e["idx"] = i
                pos = i + 1
                break
    return entries


def build_toc_sections(lines, entries):
    stack, sections = {}, []
    for e in entries:
        stack[e["level"]] = e["title"]
        for k in [k for k in stack if k > e["level"]]:
            del stack[k]
        e["path"] = [stack[k] for k in sorted(stack)]
    found = [e for e in entries if e["idx"] is not None]
    if found and found[0]["idx"] > 0:
        sections.append(dict(label="", title="Préambule", path=["Préambule"], level=1,
                             lines=lines[:found[0]["idx"]]))
    for j, e in enumerate(found):
        end = found[j + 1]["idx"] if j + 1 < len(found) else len(lines)
        sections.append(dict(label=e["label"], title=e["title"], path=e["path"], level=e["level"],
                             lines=lines[e["idx"] + 1:end], head=lines[e["idx"]]))
    return sections


# ------------------------------------------------------------- chapitres (guide)
def build_chapter_sections(lines):
    sections, part, cur = [], "", None
    i = 0
    while i < len(lines):
        t = lines[i]["text"].strip()
        mp = re.fullmatch(r"(PREMIERE|DEUXIEME|TROISIEME) PARTIE", strip_accents(t).upper())
        mc = re.fullmatch(r"CHAPITRE\s+([IVXL]+)\s*[:\.]?", t.upper()) if not lines[i]["table"] else None
        if mp:
            part = t.capitalize()
            sections.append(dict(label="", title=part, path=[part], level=1, lines=[], head=lines[i]))
            cur = sections[-1]
        elif mc:
            head, words = lines[i], []
            while (len(words) < 3 and i + 1 < len(lines) and not lines[i + 1]["table"]
                   and lines[i + 1]["text"].isupper() and len(lines[i + 1]["text"]) > 3):
                words.append(lines[i + 1]["text"].strip())
                i += 1
            title = " ".join(words).capitalize()
            name = f"Chapitre {mc.group(1)}" + (f" - {title}" if title else "")
            sections.append(dict(label="", title=name, path=[p for p in [part, name] if p],
                                 level=2, lines=[], head=head))
            cur = sections[-1]
        else:
            if cur is None:
                sections.append(dict(label="", title="Définitions", path=["Définitions"], level=1,
                                     lines=[], head=lines[i]))
                cur = sections[-1]
            cur["lines"].append(lines[i])
        i += 1
    return sections


# -------------------------------------------------------------------- écriture
def dewrap(blocks):
    """Recolle les lignes coupées par la mise en page ; garde tableaux et listes."""
    out = []  # liste de (type, texte) ; type : T tableau, P paragraphe, B puce, F légende
    for b in blocks:
        if b["table"]:
            out.append(("T", b["text"]))
            continue
        t = b["text"]
        if re.match(r"^(Figure|Tableau)\s*\d+", t):
            out.append(("F", t))
        elif re.match(r"^([•●▪\-–—]|\d+[\.\)]|[a-z][\.\)])\s", t):
            out.append(("B", t))
        elif out and out[-1][0] in ("P", "B") and (
                t[0].islower() or (out[-1][0] == "P" and not re.search(r"[\.\:\;\?\!»]$", out[-1][1]))):
            out[-1] = (out[-1][0], out[-1][1] + " " + t)
        else:
            out.append(("P", t))
    parts = []
    for i, (typ, txt) in enumerate(out):
        sep = "\n" if (i > 0 and typ == "B" and out[i - 1][0] == "B") else "\n\n"
        parts.append((sep if i else "") + txt)
    return "".join(parts)


def write_sections(doc, sections, outdir):
    rows = []
    d = Path(outdir) / doc["outdir"]
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for s in sections:
        body = dewrap(s["lines"]).strip()
        pages = [l["page"] for l in s["lines"]] or ([s["head"]["page"]] if s.get("head") else [])
        if s.get("head"):
            pages.append(s["head"]["page"])
        p1, p2 = (min(pages), max(pages)) if pages else ("", "")
        nchars = len(body)
        flag = ""
        if nchars < MIN_CHARS:
            flag = "titre_seul"
        elif nchars < LOW_CHARS:
            flag = "peu_de_texte"
        fname = ""
        if flag != "titre_seul":
            n += 1
            fname = f"{doc['prefix']}_{n:03d}_{slug((s['label'] + ' ' + s['title']).strip())}.md"
            head = [f"# {s['title']}", "",
                    f"- Plateforme : {doc['plateforme']}",
                    f"- Document source : {doc['titre']}",
                    f"- Chemin dans le document : {' > '.join(s['path'])}",
                    f"- Pages du PDF : {p1}-{p2}" if p1 != p2 else f"- Page du PDF : {p1}"]
            if doc.get("avertissement"):
                head.append(f"- Avertissement : {doc['avertissement']}")
            if flag == "peu_de_texte":
                head.append("- Remarque : les étapes de cette section sont surtout dans des captures d'écran.")
            (d / fname).write_text("\n".join(head) + "\n\n---\n\n" + body + "\n", encoding="utf-8")
        rows.append(dict(document=doc["filename"], fichier=fname, niveau=s["level"], section=s["title"],
                         chemin=" > ".join(s["path"]), pages=f"{p1}-{p2}", caracteres=nchars, signalement=flag))
    return rows


# ------------------------------------------------------------------------ main
def find_pdf(root, name):
    for p in Path(root).rglob(name):
        return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="data/raw", help="dossier contenant les PDF (recherche récursive)")
    ap.add_argument("--out", default="data/prepared")
    ap.add_argument("--only", default="", help="egp,sigmp,guide (par défaut : tous)")
    a = ap.parse_args()
    wanted = [k.strip() for k in a.only.split(",") if k.strip()] or list(DOCS)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    index, report = [], ["# Rapport de préparation\n"]

    for k in wanted:
        doc = DOCS[k]
        pdf = find_pdf(a.raw, doc["filename"])
        if not pdf:
            print(f"[absent] {doc['filename']}")
            report.append(f"## {doc['filename']}\n\nFichier introuvable.\n")
            continue
        print(f"[lecture] {pdf.name}")
        lines, npages = read_pdf(pdf, doc["footer"], doc["body_start"])
        report.append(f"## {doc['filename']}\n\n{npages} pages, texte lu à partir de la page {doc['body_start']}.\n")
        if doc["mode"] == "toc":
            entries = parse_toc(pdf, doc)
            locate(lines, entries, doc["style"])
            miss = [e for e in entries if e["idx"] is None]
            report.append(f"Sommaire : {len(entries)} titres lus, {len(entries) - len(miss)} retrouvés dans le texte.\n")
            if miss:
                report.append("Titres non retrouvés (leur texte est rattaché à la section précédente) :\n")
                report += [f"- {e['label']} {e['title']} (page {e['toc_page']})" for e in miss]
                report.append("")
            sections = build_toc_sections(lines, entries)
        elif doc["mode"] == "single":
            if doc.get("extra"):
                lines.append({"page": 1, "text": doc["extra"], "table": False})
            sections = [dict(label="", title=doc["section_title"], path=[doc["section_title"]],
                             level=1, lines=lines)]
        else:
            sections = build_chapter_sections(lines)
        rows = write_sections(doc, sections, out)
        index += rows
        faibles = [r for r in rows if r["signalement"] == "peu_de_texte"]
        titres = [r for r in rows if r["signalement"] == "titre_seul"]
        procs = [r for r in titres if r["section"].lower().startswith("comment")]
        if procs:
            report.append("Procédures (« Comment ... ») sans aucun texte, donc sans fichier : "
                          "à documenter avec la DSI :\n")
            report += [f"- p.{r['pages']} : {r['section']}" for r in procs]
            report.append("")
        report.append(f"{sum(1 for r in rows if r['fichier'])} fichiers écrits, {len(faibles)} sections avec peu de texte, "
                      f"{len(titres)} titres sans contenu propre.\n")
        if faibles:
            report.append("Sections avec peu de texte (étapes probablement dans des captures d'écran) :\n")
            report += [f"- p.{r['pages']} : {r['section']} ({r['caracteres']} caractères)" for r in faibles]
            report.append("")
        print(f"  -> {sum(1 for r in rows if r['fichier'])} fichiers, {len(faibles)} sections pauvres, "
              f"{len(titres)} titres seuls, {len(miss) if doc['mode'] == 'toc' else 0} titres non retrouvés")

    cols = ["document", "fichier", "niveau", "section", "chemin", "pages", "caracteres", "signalement"]
    with open(out / "_index.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(index)
    (out / "_rapport.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nRapport : {out / '_rapport.md'}")


if __name__ == "__main__":
    main()
