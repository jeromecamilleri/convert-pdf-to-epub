#!/usr/bin/env python3
"""Structural OCR/EPUB checks, without assertions on document wording."""

import os
import re
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from python.moulinette_pdf_vers_epub_support import (  # noqa: E402
    column_ordered_blocks,
    convert,
    figure_score,
    readable_ocr_block,
    reocr_low_confidence_columns,
)




def check_column_order():
    """Keep synthetic two-column word boxes in geometric reading order."""
    page = SimpleNamespace(rect=pymupdf.Rect(0, 0, 1000, 800))
    words = []
    for column, left in enumerate((70, 570)):
        for row in range(15):
            y = 100 + row * 24
            for word_in_line in range(3):
                x = left + word_in_line * 100
                token = f"w{column}{row}{word_in_line}token"
                words.append((x, y, x + 80, y + 10, token))

    blocks = column_ordered_blocks(page, None, words=words)
    assert blocks and len(blocks) >= 20, "Les lignes synthétiques doivent être conservées."
    first_right_column = next(i for i, block in enumerate(blocks) if block[0] > 500)
    assert all(block[0] < 500 for block in blocks[:first_right_column])
    assert all(block[0] > 500 for block in blocks[first_right_column:])


def check_low_confidence_retry():
    """A low-confidence geometric column is replaced by its second OCR pass."""
    page = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 1000, 800),
        get_pixmap=lambda **kwargs: SimpleNamespace(tobytes=lambda _format: b"synthetic"),
    )
    words = []
    for column, left in enumerate((70, 570)):
        confidence = 95 if column == 0 else 10
        for row in range(15):
            y = 100 + row * 24
            for word_in_line in range(3):
                x = left + word_in_line * 100
                token = f"w{column}{row}{word_in_line}token"
                key = (column, row, word_in_line)
                words.append((x, y, x + 80, y + 10, token, key, confidence))

    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    rows = [
        f"5\t1\t1\t1\t{i + 1}\t1\t{i * 12}\t{i * 14}\t80\t10\t95\t{f'token{i}'}"
        for i in range(24)
    ]
    response = SimpleNamespace(stdout=(header + "\n".join(rows)).encode("utf-8"))
    original_run = subprocess.run
    calls = []

    def mocked_run(*args, **kwargs):
        calls.append(kwargs.get("input"))
        return response

    subprocess.run = mocked_run
    try:
        updated = reocr_low_confidence_columns(page, words)
    finally:
        subprocess.run = original_run

    assert len(calls) == 1, "Seule la colonne peu fiable doit être relancée."
    assert len(updated) == 69, "La colonne relue remplace ses mots d'origine."
    assert figure_score(" ".join(str(value) for value in range(1, 15))) >= 2
    assert figure_score(" ".join("x" for _ in range(40))) == 0
    article_with_references = " ".join(
        [*(str(value) for value in range(1931, 1939)), *("article" for _ in range(200))]
    )
    assert figure_score(article_with_references) == 2, (
        "Des chiffres isolés dans un article ne doivent pas déclencher "
        "la détection automatique d'une figure."
    )


def check_column_continuation_filter():
    """Keep a complete five-word line from a narrow text column."""
    line = "Reconstruire explicitement chaque paragraphe imprimé"
    assert readable_ocr_block(line, allow_short=True)


def read_epub_metrics(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "EPUB/texte.xhtml" in names
        assert "EPUB/nav.xhtml" in names
        xhtml = archive.read("EPUB/texte.xhtml")
        nav = archive.read("EPUB/nav.xhtml")
        css = archive.read("EPUB/style/book.css").decode("utf-8")
        for name in names:
            if name.endswith((".xhtml", ".opf", ".ncx")):
                ET.fromstring(archive.read(name))

    text = re.sub(r"<[^>]+>", " ", xhtml.decode("utf-8"))
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]{2,}", text)
    paragraphs = len(re.findall(r"<p(?:\s|>)", xhtml.decode("utf-8")))
    heading_ids = set(re.findall(r'<h2[^>]*id="([^"]+)"', xhtml.decode("utf-8")))
    nav_targets = set(re.findall(r'href="texte\.xhtml#([^"]+)"', nav.decode("utf-8")))
    assert nav_targets <= heading_ids, "Les liens de navigation doivent pointer vers un titre existant."
    assert "page-break-before: always" in css
    assert paragraphs > 0 and len(words) > 30
    return len(words), paragraphs, len(heading_ids)


def check_document(pdf_path):
    page_count = len(pymupdf.open(pdf_path))
    # These page ranges are structural fixtures in the sample magazine. The
    # checks below measure package and extraction shape, never literal wording.
    # Pages 14-15 (2 pages, 16 s) et 23 (14 s) remplacées par la page 15 seule
    # qui couvre le même cas de double colonne en ~8 s.  La page 25 reste car
    # elle est rapide (1,5 s) et vérifie la détection de figure automatique.
    ranges = [(13, 13), (15, 15), (25, 25)]
    if page_count < max(end for _, end in ranges):
        raise SystemExit("Le PDF d'essai doit contenir au moins 25 pages.")

    with tempfile.TemporaryDirectory(prefix="epub-regression-") as temp_dir:
        for index, (first, last) in enumerate(ranges, start=1):
            output = os.path.join(temp_dir, f"case-{index}.epub")
            convert(
                pdf_path, output, "OCR regression sample", use_ocr=True,
                selected_pages=set(range(first - 1, last)),
            )
            words, paragraphs, headings = read_epub_metrics(output)
            minimum_words = 500 if (first, last) == (13, 13) else 30
            assert words > minimum_words
            print(
                f"Pages PDF {first}-{last}: {words} mots, "
                f"{paragraphs} paragraphes, {headings} titres de navigation."
            )


def check_join_wrapped_text():
    """join_wrapped_text doit recoller les tirets de césure, majuscule ou non."""
    from python.moulinette_pdf_vers_epub_support import join_wrapped_text

    # Tiret de césure suivi d'une minuscule — comportement de base inchangé
    assert join_wrapped_text("déci-", "dée par une poignée") == "décidée par une poignée"
    assert join_wrapped_text("pro-", "phètes » – lancent") == "prophètes » – lancent"

    # Tiret de césure suivi d'une MAJUSCULE — nom propre, début de phrase après bloc
    assert join_wrapped_text("Infor-", "Mation importante") == "InforMation importante", \
        "Le tiret de césure avant majuscule doit être supprimé sans espace"
    assert join_wrapped_text("tra-", "Vail bien fait") == "traVail bien fait", \
        "Le tiret de césure avant majuscule doit être supprimé sans espace"

    # Tiret de liste ou de dialogue — ne doit PAS être recollé (ne finit pas par lettre-)
    assert join_wrapped_text("comme suit", "– premier point") == "comme suit – premier point"
    assert join_wrapped_text("parti", "- réforme") == "parti - réforme"

    # Pas de tiret — simple espace
    assert join_wrapped_text("bonjour", "monde") == "bonjour monde"


def check_text_pdf_hyphenation(_pdf_path=None):
    """Vérifie le pipeline de recollage des césures typographiques sans PDF externe.

    Des blocs de texte synthétiques reproduisent la structure d'une page à deux
    colonnes : titres pleine largeur, colonne gauche et colonne droite avec des
    césures intra-bloc (dis-\\ncipline, cher-\\ncheurs, etc.) et un sous-titre
    détectable en majuscules.  Le pipeline d'ordonnancement et de fusion est
    exercé exactement comme dans convert_text_pdf.
    """
    from python.moulinette_pdf_vers_epub_support import (
        clean_block, join_wrapped_text, same_paragraph_flow,
    )

    # --- Géométrie synthétique -----------------------------------------------
    # Page 595 x 842 pt  (A4), deux colonnes de 230 pt chacune.
    PAGE_W, PAGE_H = 595.0, 842.0
    MID_X = PAGE_W / 2
    LH = 12.0   # hauteur d'une ligne de corps (pt)

    def _block(x0, y0, x1, y1, text, idx=0):
        """Retourne un tuple simulant pymupdf get_text('blocks')."""
        return (x0, y0, x1, y1, text, idx, 0)

    blocks = []

    # Titre pleine largeur (y < 0.3 * PAGE_H)
    blocks.append(_block(40, 60, PAGE_W - 40, 80, "TITRE PRINCIPAL DE LA PAGE\n", 0))

    # Colonne gauche : blocs contenant des césures typographiques
    # Chaque mot coupé : première moitié en fin de ligne, seconde au début de la suivante.
    left_texts = [
        "La dis-\ncipline scientifique re-\npose sur des cher-\ncheurs rigoureux.\n",
        "La techno-\nlogie numé-\nrique est deve-\nnue univer-\nselle.\n",
        "La pro-\nchaine étape sera de valoris-\ner les compé-\ntences valoris-\nées.\n",
        "Les fa-\ncultés de l'hu-\nmanité face à l'intel-\nligence artificielle.\n",
    ]
    y = 180.0
    for i, txt in enumerate(left_texts):
        lines = txt.count("\n")
        h = lines * LH
        blocks.append(_block(40, y, 265, y + h, txt, 10 + i))
        y += h + 4

    # Sous-titre pleine largeur détectable (majuscules, position centrale)
    blocks.append(_block(40, 480, PAGE_W - 40, 495, "PROPRIÉTÉ INTELLECTUELLE\n", 20))

    # Colonne droite : blocs avec césures
    right_texts = [
        "La con-\nnaissance parta-\ngée via des con-\nversations éclai-\nrées.\n",
        "Des pro-\nfessionnels aver-\ntis jugent cette situa-\ntion danger-\neuse.\n",
        "L'usage des smart-\nphones et des ré-\nseaux progresse.\n",
    ]
    y = 180.0
    for i, txt in enumerate(right_texts):
        lines = txt.count("\n")
        h = lines * LH
        blocks.append(_block(330, y, 555, y + h, txt, 30 + i))
        y += h + 4

    # --- Pipeline d'ordonnancement (reproduction fidèle de convert_text_pdf) --
    full_width, left_col, right_col = [], [], []
    for block in blocks:
        x0, y0, x1, y1 = block[:4]
        width = x1 - x0
        center_x = (x0 + x1) / 2
        if width > PAGE_W * 0.6:
            full_width.append(block)
        elif center_x < MID_X:
            left_col.append(block)
        else:
            right_col.append(block)

    full_width.sort(key=lambda b: b[1])
    left_col.sort(key=lambda b: b[1])
    right_col.sort(key=lambda b: b[1])
    top_titles = [b for b in full_width if b[1] < PAGE_H * 0.3]
    bottom_elements = [b for b in full_width if b[1] >= PAGE_H * 0.3]
    ordered_blocks = top_titles + left_col + right_col + bottom_elements

    # --- Fusion des blocs (détection sous-titres inline + recollage) ----------
    paragraph_text = None
    paragraph_box = None
    full_text = []
    headings = []
    for block in ordered_blocks:
        raw_text = block[4]
        box = block[:4]
        first_line_raw = raw_text.split("\n")[0].replace("\xa0", " ").strip()
        alpha_chars = [c for c in first_line_raw if c.isalpha()]
        is_inline_heading = (
            first_line_raw
            and len(first_line_raw) <= 60
            and alpha_chars
            and sum(c.isupper() for c in alpha_chars) / len(alpha_chars) >= 0.75
            and not first_line_raw.startswith("©")
            and "/" not in first_line_raw
        )
        if is_inline_heading:
            if paragraph_text:
                full_text.append(paragraph_text)
                paragraph_text = None
            headings.append(first_line_raw)
            raw_text = "\n".join(raw_text.split("\n")[1:])
        cleaned = clean_block(raw_text)
        if not cleaned:
            paragraph_box = box
            continue
        continues = (
            paragraph_text
            and not is_inline_heading
            and same_paragraph_flow(paragraph_box, box)
        )
        if continues:
            paragraph_text = join_wrapped_text(paragraph_text, cleaned)
        else:
            if paragraph_text:
                full_text.append(paragraph_text)
            paragraph_text = cleaned
        paragraph_box = box
    if paragraph_text:
        full_text.append(paragraph_text)
    text = " ".join(full_text)

    # --- Assertions -----------------------------------------------------------
    expected_words = [
        "discipline", "chercheurs", "technologie", "universelle",
        "prochaine", "valorisées", "facultés", "humanité",
        "intelligence", "connaissance", "conversations",
        "professionnels", "dangereuse", "smartphones",
    ]
    for word in expected_words:
        assert word in text, (
            f"Le mot « {word} » devrait être recollé depuis sa césure typographique "
            f"mais est absent ou encore coupé dans le texte produit."
        )

    # Aucune césure résiduelle de la forme «lettre- lettre» ne doit subsister
    residual = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]- [A-Za-zÀ-ÖØ-öø-ÿŒœ]", text)
    assert not residual, f"Césures résiduelles détectées après conversion : {residual}"

    # Le sous-titre «PROPRIÉTÉ INTELLECTUELLE» doit être extrait en heading distinct
    assert "PROPRIÉTÉ INTELLECTUELLE" in headings, (
        "Le sous-titre «PROPRIÉTÉ INTELLECTUELLE» doit être détecté et séparé "
        "en élément distinct (h3), non fusionné dans un paragraphe."
    )
    for body in full_text:
        assert not body.startswith("PROPRIÉTÉ INTELLECTUELLE"), (
            "Le sous-titre ne doit pas apparaître en tête d'un paragraphe de corps."
        )

    print(f"  check_text_pdf_hyphenation : {len(expected_words)} mots recollés, "
          f"0 césure résiduelle, {len(headings)} sous-titre(s) séparé(s).")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage : python tests/check_regressions.py document.pdf")
    pdf_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(pdf_path):
        raise SystemExit(f"PDF de contrôle introuvable : {pdf_path}")
    check_column_order()
    check_low_confidence_retry()
    check_column_continuation_filter()
    check_join_wrapped_text()
    check_text_pdf_hyphenation()
    check_document(pdf_path)
    print("Contrôles structurels et de non-régression réussis.")


if __name__ == "__main__":
    main()
