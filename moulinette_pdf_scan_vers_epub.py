#!/usr/bin/env python3
"""Essai v3 : repérer les débuts de chapitres, convertir dix lots en parallèle,
puis fusionner les EPUB produits en un livre unique.

Le repérage utilise uniquement la position et la taille OCR des lignes. Les
lots sont des groupes contigus de chapitres afin de garder l'ordre du PDF.
"""

import argparse
import concurrent.futures
import html
import os
import re
import signal
import zipfile

import pymupdf as fitz
from ebooklib import epub

from moulinette_pdf_vers_epub_support import (
    OcrCancelled, cancel_active_tesseract, reset_ocr_cancellation,
)
from moulinette_pdf_vers_epub_analyse import analyze_page, convert_v2


MAX_WORKERS = 10


def _handle_interrupt(signum, frame):
    cancel_active_tesseract()
    raise KeyboardInterrupt


def _analyze_batch(args):
    pdf_path, page_indices = args
    analyzed = {}
    with fitz.open(pdf_path) as doc:
        for index in page_indices:
            number = index + 1
            print(f"OCR v3 : page {number}/{len(doc)}", flush=True)
            analyzed[index] = analyze_page(doc[index], number)
    return analyzed


def _chapter_start(record):
    """Use the v2 geometric title estimate from the cached OCR analysis."""
    page_height = record["page_height"]
    return any(region["role"] == "titre" and region["bbox"][1] <= page_height * .25
               for region in record["regions"])


def _contiguous_ranges(page_count, chapter_starts, requested_workers):
    starts = sorted(set([0] + [i for i in chapter_starts if 0 < i < page_count]))
    if len(starts) < 2:
        # If no reliable heading boundary was found, use balanced contiguous
        # ranges and make the fallback explicit in the report.
        count = min(requested_workers, page_count)
        return [(i * page_count // count, (i + 1) * page_count // count)
                for i in range(count)], False

    chapter_ranges = [(start, starts[i + 1] if i + 1 < len(starts) else page_count)
                      for i, start in enumerate(starts)]
    count = min(requested_workers, len(chapter_ranges))
    groups = []
    cursor = 0
    for group_index in range(count):
        remaining_groups = count - group_index
        remaining_chapters = len(chapter_ranges) - cursor
        target_pages = sum(end - start for start, end in chapter_ranges[cursor:]) / remaining_groups
        begin = chapter_ranges[cursor][0]
        end = begin
        added = 0
        while cursor < len(chapter_ranges) and (added == 0 or
                (added < remaining_chapters - remaining_groups + 1 and end - begin < target_pages)):
            _, end = chapter_ranges[cursor]
            cursor += 1
            added += 1
        groups.append((begin, end))
    return groups, True


def _convert_part(args):
    pdf_path, output_path, start, end, image_mode, image_dpi, image_quality, records = args
    convert_v2(pdf_path, output_path, image_mode=image_mode, image_dpi=image_dpi,
               selected_pages=set(range(start, end)), analyzed_records=records,
               image_format="jpeg", image_quality=image_quality)
    return output_path


def _body_fragment(xhtml):
    text = xhtml.decode("utf-8", "replace")
    match = re.search(r"<body[^>]*>(.*?)</body>", text, flags=re.S | re.I)
    if not match:
        raise ValueError("Le fragment XHTML d'un EPUB intermédiaire ne contient pas de body.")
    fragment = match.group(1)
    return re.sub(r"<h1\b[^>]*>.*?</h1>", "", fragment, count=1, flags=re.S | re.I)


def _merge_epubs(part_paths, output_path, title):
    page_text, assets, toc = {}, {}, []
    section_re = re.compile(
        r'<section class="source-page" id="source-page-(\d+)"[^>]*>(.*?)</section>', re.S
    )
    for path in part_paths:
        with zipfile.ZipFile(path) as archive:
            fragment = _body_fragment(archive.read("EPUB/texte.xhtml"))
            for match in section_re.finditer(fragment):
                number = int(match.group(1))
                body = re.sub(r'<small\b[^>]*class="page-marker"[^>]*>.*?</small>',
                              "", match.group(2), count=1, flags=re.S)
                body = re.sub(r'<figure\b[^>]*id="source-image-\d+"[^>]*>.*?</figure>',
                              "", body, count=1, flags=re.S)
                page_text[number] = body.strip()
            for name in archive.namelist():
                if name.startswith("EPUB/images/"):
                    assets[name.removeprefix("EPUB/")] = archive.read(name)

    page_images = {}
    for name in assets:
        match = re.fullmatch(r"images/page-(\d+)\.(jpg|jpeg|png)", name, re.I)
        if match:
            page_images[int(match.group(1))] = name

    ordered_sections = []
    for number in sorted(set(page_text) | set(page_images)):
        marker = f'<small class="page-marker" id="pdf-page-{number}">'
        if number in page_images:
            marker += (f'<a href="#source-image-{number}" title="Voir le scan PDF de la page {number}">'
                       f'PDF p. {number}</a></small>')
            image_name = page_images[number]
            figure = (f'<figure id="source-image-{number}"><img src="{image_name}" '
                      f'alt="Scan original de la page PDF {number}" />'
                      f'<figcaption>Page PDF {number}</figcaption></figure>')
        else:
            marker += f'PDF p. {number}</small>'
            figure = ""
        body = page_text.get(number, "")
        ordered_sections.append(
            f'<section class="source-page" id="source-page-{number}" epub:type="pagebreak">'
            f'{marker}{figure}{body}</section>'
        )
        for match in re.finditer(r'<h3\b[^>]*id="([^"]+)"[^>]*>(.*?)</h3>', body, re.S):
            label = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            toc.append(epub.Link(f"texte.xhtml#{match.group(1)}", label or f"Page {number}", match.group(1)))

    book = epub.EpubBook()
    book.set_identifier("pdf-v3-" + os.path.basename(output_path))
    book.set_title(title)
    book.set_language("fr")
    chapter = epub.EpubHtml(title=title, file_name="texte.xhtml", lang="fr")
    chapter.content = f'<h1>{html.escape(title)}</h1>' + "\n".join(ordered_sections)
    css = '''
    body { font-family: serif; line-height: 1.4; margin: 4%; }
    h1 { font-size: 1.4em; }
    .source-page { break-before: page; page-break-before: always; }
    .page-marker { display: block; font: .68em sans-serif; color: #888; margin: .4em 0 .65em; }
    .page-marker a { color: #888; text-decoration: none; }
    .ocr-region { text-indent: 1em; margin: 0 0 .8em; }
    .title-region { text-indent: 0; }
    aside { margin: .8em 1em; padding-left: .8em; border-left: .15em solid #aaa; }
    .uncertain { background: #fff0b3; text-decoration: underline dotted; }
    figure { margin: 1em 0; page-break-inside: avoid; text-align: center; }
    img { max-width: 100%; height: auto; }
    figcaption { font: .85em sans-serif; }
    '''
    style = epub.EpubItem(uid="style", file_name="style/book.css", media_type="text/css", content=css)
    book.add_item(style)
    chapter.add_item(style)
    book.add_item(chapter)
    for name, content in assets.items():
        media_type = "image/jpeg" if name.lower().endswith((".jpg", ".jpeg")) else "image/png"
        book.add_item(epub.EpubItem(uid=name.replace("/", "-"), file_name=name,
                                   media_type=media_type, content=content))
    book.toc = tuple(toc) if toc else (chapter,)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = ["nav", chapter]
    epub.write_epub(output_path, book)


def main():
    parser = argparse.ArgumentParser(description="Conversion PDF vers EPUB v3 par lots de chapitres parallèles")
    parser.add_argument("pdf", help="PDF source")
    parser.add_argument("output", nargs="?", help="EPUB fusionné, par défaut <nom>.v3.epub")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help="nombre maximal de lots convertis en parallèle (défaut : 10)")
    parser.add_argument("--images", choices=("flagged", "all", "none"), default="all",
                        help="scans inclus : toutes les pages (défaut), signalées ou aucune")
    parser.add_argument("--image-dpi", type=int, default=110,
                        help="résolution des scans intégrés (défaut : 110 dpi)")
    parser.add_argument("--image-quality", type=int, default=50,
                        help="qualité JPEG en niveaux de gris, de 25 à 90 (défaut : 50)")
    args = parser.parse_args()
    if not os.path.isfile(args.pdf):
        parser.error(f"fichier introuvable : {args.pdf}")
    if not 1 <= args.workers <= 10:
        parser.error("--workers doit être compris entre 1 et 10")
    if not 72 <= args.image_dpi <= 300:
        parser.error("--image-dpi doit être compris entre 72 et 300")
    if not 25 <= args.image_quality <= 90:
        parser.error("--image-quality doit être compris entre 25 et 90")

    reset_ocr_cancellation()
    signal.signal(signal.SIGINT, _handle_interrupt)

    # Tesseract otherwise starts a CPU thread per core in every concurrent
    # worker, which can make ten simultaneous jobs slower than one.
    os.environ["OMP_THREAD_LIMIT"] = "1"

    output = args.output or os.path.splitext(args.pdf)[0] + ".v3.epub"
    with fitz.open(args.pdf) as doc:
        page_count = len(doc)
    scan_workers = min(args.workers, page_count)
    scan_tasks = []
    for worker in range(scan_workers):
        start = worker * page_count // scan_workers
        end = (worker + 1) * page_count // scan_workers
        scan_tasks.append((args.pdf, list(range(start, end))))
    print(f"Analyse OCR complète de {page_count} pages avec {scan_workers} fils de travail…", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=scan_workers) as pool:
        batches = list(pool.map(_analyze_batch, scan_tasks))
    analyzed_records = {}
    for batch in batches:
        analyzed_records.update(batch)
    chapter_starts = [index for index, record in analyzed_records.items()
                      if _chapter_start(record)]

    groups, used_chapter_cues = _contiguous_ranges(page_count, chapter_starts, args.workers)
    parts_dir = os.path.splitext(output)[0] + ".parts"
    os.makedirs(parts_dir, exist_ok=True)
    tasks = []
    for index, (start, end) in enumerate(groups, 1):
        part_path = os.path.join(parts_dir, f"lot-{index:02d}.epub")
        page_records = {index: analyzed_records[index] for index in range(start, end)}
        tasks.append((args.pdf, part_path, start, end, args.images, args.image_dpi,
                      args.image_quality, page_records))
    print(f"{len(chapter_starts)} débuts de chapitre estimés ; conversion de {len(tasks)} lots en parallèle…", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        part_paths = list(pool.map(_convert_part, tasks))

    title = os.path.splitext(os.path.basename(args.pdf))[0]
    _merge_epubs(part_paths, output, title)
    print(f"EPUB v3 fusionné : {output}")
    print(f"EPUB intermédiaires : {parts_dir}")
    print("Découpage par indices de titres géométriques : " + ("oui" if used_chapter_cues else "repli sur des lots de pages équilibrés"))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, OcrCancelled):
        cancel_active_tesseract()
        print("Conversion interrompue ; les processus Tesseract ont été arrêtés.", flush=True)
        raise SystemExit(130)
