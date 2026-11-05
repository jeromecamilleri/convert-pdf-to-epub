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
    ranges = [(13, 13), (14, 15), (23, 23), (25, 25)]
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
            minimum_words = 500 if (first, last) == (13, 13) else (
                250 if last - first >= 1 else 30
            )
            assert words > minimum_words
            print(
                f"Pages PDF {first}-{last}: {words} mots, "
                f"{paragraphs} paragraphes, {headings} titres de navigation."
            )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage : python tests/check_regressions.py document.pdf")
    pdf_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(pdf_path):
        raise SystemExit(f"PDF de contrôle introuvable : {pdf_path}")
    check_column_order()
    check_low_confidence_retry()
    check_column_continuation_filter()
    check_document(pdf_path)
    print("Contrôles structurels et de non-régression réussis.")


if __name__ == "__main__":
    main()
