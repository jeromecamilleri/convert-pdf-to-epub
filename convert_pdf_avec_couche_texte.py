#!/usr/bin/env python3
"""Convert the PDFs in the current directory, choosing the suitable pipeline.

Text PDFs use the direct text extractor in this file. Scanned PDFs are delegated
to moulinette_pdf_scan_vers_epub.py, which performs the OCR and retains scans.
"""

import argparse
import html
import pathlib
import re
import subprocess
import sys

import pymupdf
from ebooklib import epub
from python.moulinette_pdf_vers_epub_support import (
    clean_block,
    join_wrapped_text,
    same_paragraph_flow,
)


MIN_WORDS_PER_TEXT_PAGE = 8


def has_text_layer(pdf_path):
    """Return true when a substantial share of pages has extractable text."""
    with pymupdf.open(pdf_path) as doc:
        if not len(doc):
            return False
        text_pages = 0
        for page in doc:
            words = re.findall(r"\w+", page.get_text("text"), flags=re.UNICODE)
            if len(words) >= MIN_WORDS_PER_TEXT_PAGE:
                text_pages += 1
        minimum_pages = max(1, (len(doc) + 1) // 2)
        return text_pages >= minimum_pages


def convert_text_pdf(pdf_file, epub_file):
    """Extract an existing text layer and keep a page image before its text."""
    with pymupdf.open(pdf_file) as doc:
        book = epub.EpubBook()
        book.set_identifier(f"converted-{pdf_file.stem}")
        book.set_title(pdf_file.stem)
        book.set_language("fr")
        chapters = []

        for page_num, page in enumerate(doc):
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img_filename = f"images/page_{page_num + 1}.png"
            book.add_item(epub.EpubItem(
                uid=f"img_page_{page_num + 1}", file_name=img_filename,
                media_type="image/png", content=img_bytes,
            ))

            rect = page.rect
            page_width = rect.width
            mid_x = page_width / 2
            blocks = page.get_text("blocks")
            text_blocks = [b for b in blocks if len(b) > 6 and b[6] == 0 and b[4].strip()]

            full_width, left_col, right_col = [], [], []
            for block in text_blocks:
                x0, y0, x1, y1, text, block_number, block_type = block
                width = x1 - x0
                center_x = (x0 + x1) / 2
                if width > page_width * .6:
                    full_width.append(block)
                elif center_x < mid_x:
                    left_col.append(block)
                else:
                    right_col.append(block)

            full_width.sort(key=lambda block: block[1])
            left_col.sort(key=lambda block: block[1])
            right_col.sort(key=lambda block: block[1])
            top_titles = [b for b in full_width if b[1] < rect.height * .3]
            bottom_elements = [b for b in full_width if b[1] >= rect.height * .3]
            ordered_blocks = top_titles + left_col + right_col + bottom_elements

            page_content = [
                f'<div style="text-align:center;margin-bottom:20px"><img src="{img_filename}" '
                f'alt="Page originale {page_num + 1}" style="max-width:100%;height:auto" /></div>'
            ]
            paragraph_text = None
            paragraph_box = None
            for block in ordered_blocks:
                raw_text = block[4]
                box = block[:4]

                # Si la première ligne du bloc est un sous-titre en majuscules
                # (≥ 75 % de lettres majuscules, longueur raisonnable), on la
                # sépare du corps : elle devient un <h3> standalone et le reste
                # du bloc repart comme un nouveau paragraphe.
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
                    # Émettre le paragraphe en cours avant d'insérer le titre
                    if paragraph_text:
                        page_content.append(f"<p>{html.escape(paragraph_text)}</p>")
                        paragraph_text = None
                    page_content.append(f"<h3>{html.escape(first_line_raw)}</h3>")
                    # Retirer la première ligne pour traiter le reste normalement
                    rest = "\n".join(raw_text.split("\n")[1:])
                    raw_text = rest

                cleaned = clean_block(raw_text)
                if not cleaned:
                    paragraph_box = box
                    continue

                # Un bloc déjà identifié comme titre inline a rompu le flux ;
                # les autres blocs se poursuivent si la géométrie le permet.
                continues = (
                    paragraph_text
                    and not is_inline_heading
                    and same_paragraph_flow(paragraph_box, box)
                )
                if continues:
                    paragraph_text = join_wrapped_text(paragraph_text, cleaned)
                else:
                    if paragraph_text:
                        page_content.append(f"<p>{html.escape(paragraph_text)}</p>")
                    paragraph_text = cleaned
                paragraph_box = box
            if paragraph_text:
                page_content.append(f"<p>{html.escape(paragraph_text)}</p>")

            chapter = epub.EpubHtml(
                title=f"Page {page_num + 1}",
                file_name=f"page_{page_num + 1}.xhtml",
                lang="fr",
            )
            chapter.content = (
                f"<h2>Page {page_num + 1}</h2>" + "".join(page_content)
            )
            book.add_item(chapter)
            chapters.append(chapter)

        if not chapters:
            raise RuntimeError(f"Aucune page à convertir dans {pdf_file.name}.")
        # Each source page is already its own spine item; expose those items in
        # the navigation so Kindle can jump to the next PDF page as a chapter.
        book.toc = tuple(chapters)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + chapters
        epub.write_epub(str(epub_file), book)


def convert_scanned_pdf(pdf_file, epub_file, workers, scan_script):
    """Delegate scanned PDFs to the OCR pipeline specialized for page scans."""
    command = [
        sys.executable, str(scan_script),
        "--workers", str(workers), "--images", "all",
        str(pdf_file.resolve()), str(epub_file.resolve()),
    ]
    subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Convert a PDF, or all PDFs in the current directory, using the suitable pipeline."
    )
    parser.add_argument(
        "pdf", nargs="?", type=pathlib.Path,
        help="PDF à convertir ; sans argument, traite les PDF du dossier courant",
    )
    parser.add_argument("--workers", type=int, default=10,
                        help="workers for scanned PDFs (1 to 10; default: 10)")
    args = parser.parse_args()
    if not 1 <= args.workers <= 10:
        parser.error("--workers must be between 1 and 10")

    if args.pdf is not None:
        if not args.pdf.is_file():
            parser.error(f"PDF introuvable : {args.pdf}")
        if args.pdf.suffix.lower() != ".pdf":
            parser.error(f"Le fichier indiqué n'est pas un PDF : {args.pdf}")
        pdf_files = [args.pdf]
    else:
        current_dir = pathlib.Path.cwd()
        pdf_files = sorted(current_dir.glob("*.pdf"))
    if not pdf_files:
        print("Aucun fichier PDF trouvé dans le répertoire courant.")
        return 0

    scan_script = pathlib.Path(__file__).resolve().parent / "python" / "moulinette_pdf_scan_vers_epub.py"
    if not scan_script.is_file():
        raise FileNotFoundError(f"Moulinette scan introuvable : {scan_script}")

    print(f"Trouvé {len(pdf_files)} fichier(s) PDF à examiner.\n")
    failures = 0
    for pdf_file in pdf_files:
        if args.pdf is None and pdf_file.stem.endswith("_ocr"):
            continue
        epub_file = pdf_file.with_suffix(".epub")
        try:
            if has_text_layer(pdf_file):
                print(f"-> Couche texte détectée : {pdf_file.name}")
                convert_text_pdf(pdf_file, epub_file)
                print(f"   EPUB créé : {epub_file.name}")
            else:
                print(f"-> PDF scanné : {pdf_file.name} ; délégation à la moulinette scan.")
                convert_scanned_pdf(pdf_file, epub_file, args.workers, scan_script)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            failures += 1
            print(f"   [ERREUR] {pdf_file.name} : {exc}")

    if failures:
        print(f"Conversion terminée avec {failures} erreur(s).")
        return 1
    print("Toutes les conversions sont terminées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
