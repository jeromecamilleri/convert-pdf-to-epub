#!/usr/bin/env python3
"""Version 2 : EPUB hybride, OCR traçable et contrôle page par page.

Cette version garde la v1 intacte. Elle détecte des régions de texte avec
Tesseract, les ré-ocrise séparément, conserve les lignes incertaines et produit
un rapport JSON ainsi que des aperçus pour les pages nécessitant une relecture.
Les catégories de régions sont des estimations géométriques, pas une
classification sémantique garantie.
"""

import argparse
import csv
import html
import io
import json
import os
import re
import subprocess
import sys

import pymupdf as fitz
from ebooklib import epub

from python.moulinette_pdf_vers_epub_support import (
    OcrCancelled, cancel_active_tesseract, column_ordered_blocks,
    ocr_cancelled, run_tesseract, tesseract_line_blocks, tesseract_words,
)


OCR_DPI = 300
LOW_CONFIDENCE = 45.0
# Keep these layout-quality cutoffs independent of any document vocabulary.
MIN_PAGE_WORDS = 8
# A page is treated as image-only when fewer than this fraction of its
# recognised words are legible (≥2 alphabetic characters, confidence ≥45).
# Pages like magazine covers — mostly photos with a handful of large-font
# tokens — score well below this threshold, while normal text pages score
# above 50 %.  The absolute floor avoids triggering on pages with very few
# words overall (e.g. a chapter-break illustration with a single bold word).
IMAGE_ONLY_LEGIBLE_RATIO = 0.15
IMAGE_ONLY_MIN_LEGIBLE_WORDS = 10


def _region_words(page, rect, dpi=OCR_DPI):
    """OCR a detected region and return word boxes in page coordinates."""
    rect = fitz.Rect(rect) & page.rect
    scale = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect,
                          colorspace=fitz.csGRAY, alpha=False)
    output = run_tesseract(
        ["tesseract", "stdin", "stdout", "-l", "fra+eng", "--psm", "6", "tsv"],
        pix.tobytes("png"),
    )
    reader = csv.DictReader(io.StringIO(output.decode("utf-8", "replace")), delimiter="\t")
    result = []
    for row in reader:
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            x0 = rect.x0 + float(row["left"]) / scale
            y0 = rect.y0 + float(row["top"]) / scale
            x1 = x0 + float(row["width"]) / scale
            y1 = y0 + float(row["height"]) / scale
            conf = float(row.get("conf", "-1"))
        except (KeyError, TypeError, ValueError):
            continue
        key = ("region", row.get("block_num"), row.get("par_num"), row.get("line_num"))
        result.append((x0, y0, x1, y1, row["text"].strip(), key, conf))
    return result


def _rect_union(words):
    return fitz.Rect(min(w[0] for w in words), min(w[1] for w in words),
                     max(w[2] for w in words), max(w[3] for w in words))


def _merge_nearby_blocks(blocks, page, typical_height):
    """Join adjacent Tesseract blocks that share a column and line flow."""
    regions = []
    max_gap = max(typical_height * 3.0, page.rect.height * .012)
    x_tolerance = max(typical_height * 3.0, page.rect.width * .018)
    for block_id, words in sorted(blocks.items(), key=lambda item: (
        min(w[1] for w in item[1]), min(w[0] for w in item[1])
    )):
        rect = _rect_union(words)
        choices = []
        for idx, region in enumerate(regions):
            prior = region["bbox"]
            vertical_gap = rect.y0 - prior.y1
            overlap = min(rect.x1, prior.x1) - max(rect.x0, prior.x0)
            overlap_ratio = overlap / max(1, min(rect.width, prior.width))
            same_column = overlap_ratio >= .45 or abs(rect.x0 - prior.x0) <= x_tolerance
            if same_column and -typical_height * .6 <= vertical_gap <= max_gap:
                choices.append((max(0, vertical_gap) + abs(rect.x0 - prior.x0) * .15, idx))
        if choices:
            _, idx = min(choices)
            regions[idx]["words"].extend(words)
            regions[idx]["bbox"] = _rect_union(regions[idx]["words"])
            regions[idx]["source_blocks"].append(str(block_id))
        else:
            regions.append({"words": list(words), "bbox": rect,
                            "source_blocks": [str(block_id)]})
    return regions


def _region_role(rect, words, page, typical_height):
    """Return a deliberately conservative, geometry-based role estimate."""
    text = " ".join(w[4] for w in words)
    tokens = re.findall(r"\w+", text, flags=re.UNICODE)
    upper_ratio = sum(c.isupper() for c in text if c.isalpha()) / max(1, sum(c.isalpha() for c in text))
    width_ratio = rect.width / page.rect.width
    mean_height = sum(w[3] - w[1] for w in words) / max(1, len(words))
    if rect.y0 < page.rect.height * .28 and len(tokens) <= 18 and (
        upper_ratio > .28 or mean_height > typical_height * 1.3
    ):
        return "titre", .62
    if width_ratio < .34 and (rect.x0 < page.rect.width * .2 or rect.x1 > page.rect.width * .8):
        return "encart_ou_colonne_laterale", .55
    line_count = len({w[5] for w in words})
    if len(tokens) <= 24 and line_count <= 3 and width_ratio < .72:
        return "legende_ou_encart", .48
    return "texte", .72


def analyze_page(page, page_number):
    """Stage 1/2: detect text regions, re-OCR each region, retain all lines."""
    scale = OCR_DPI / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    detected = tesseract_words(pix, page, scale, psm="3")
    source_blocks = {}
    for word in detected:
        source_blocks.setdefault(word[5][0], []).append(word)

    heights = sorted(max(1, w[3] - w[1]) for w in detected)
    typical_height = heights[len(heights) // 2] if heights else 1
    detected_regions = _merge_nearby_blocks(source_blocks, page, typical_height)
    regions = []
    for source_region in detected_regions:
        initial_words = source_region["words"]
        source_rect = source_region["bbox"]
        pad = max(2, typical_height * .45)
        crop = fitz.Rect(source_rect.x0 - pad, source_rect.y0 - pad,
                         source_rect.x1 + pad, source_rect.y1 + pad) & page.rect
        try:
            refined = _region_words(page, crop)
        except (OSError, subprocess.CalledProcessError):
            refined = []
        # Prefer the region OCR only when it retains most of the detected words.
        # Otherwise keep the full-page recognition; neither candidate is filtered.
        initial_scores = [w[6] for w in initial_words if w[6] >= 0]
        refined_scores = [w[6] for w in refined if w[6] >= 0]
        initial_mean = sum(initial_scores) / len(initial_scores) if initial_scores else 0
        refined_mean = sum(refined_scores) / len(refined_scores) if refined_scores else 0
        min_words = max(3, len(initial_words) * .7)
        max_words = max(len(initial_words) + 3, len(initial_words) * 1.3)
        use_refined = (
            len(initial_words) >= 3 and min_words <= len(refined) <= max_words
            and refined_mean >= initial_mean - 5
        )
        words = refined if use_refined else initial_words
        used_region_ocr = words is refined
        lines = tesseract_line_blocks(words)
        rect = _rect_union(words) if words else source_rect
        role, role_confidence = _region_role(rect, words, page, typical_height)
        scores = [w[6] for w in words if w[6] >= 0]
        avg_conf = sum(scores) / len(scores) if scores else 0.0
        regions.append({
            "block_id": ",".join(source_region["source_blocks"]),
            "role": role, "role_confidence": role_confidence,
            "bbox": [round(v, 1) for v in rect], "words": words, "lines": lines,
            "word_count": len(words), "mean_confidence": round(avg_conf, 1),
            "used_region_ocr": used_region_ocr,
        })

    # Reuse the project's geometric column ordering to rank regions. The report
    # stores boxes so a reviewer can spot an ordering mistake on the page image.
    ordered_lines = column_ordered_blocks(page, None, words=detected) or []
    for rank, line in enumerate(ordered_lines):
        cx, cy = (line[0] + line[2]) / 2, (line[1] + line[3]) / 2
        candidates = []
        for region in regions:
            x0, y0, x1, y1 = region["bbox"]
            if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
                candidates.append(region)
        if candidates:
            candidates[0]["order_key"] = min(candidates[0].get("order_key", rank), rank)
    regions.sort(key=lambda r: (r.get("order_key", 10**9), r["bbox"][1], r["bbox"][0]))

    all_words = [w for region in regions for w in region["words"]]
    confidence_values = [w[6] for w in all_words if w[6] >= 0]
    low_words = sum(score < LOW_CONFIDENCE for score in confidence_values)
    mean_confidence = sum(confidence_values) / max(1, len(confidence_values))
    low_confidence_ratio = low_words / max(1, len(confidence_values))
    # Cluster left edges: center positions vary with line length and falsely
    # suggest columns on ordinary, ragged-right paragraphs.
    left_edges = sorted(r["bbox"][0] for r in regions)
    column_count = 0
    last_edge = None
    for edge in left_edges:
        if last_edge is None or edge - last_edge >= page.rect.width * .12:
            column_count += 1
            last_edge = edge
    reasons = []
    if column_count > 1:
        reasons.append("plusieurs zones horizontales détectées")
    if confidence_values and low_words / len(confidence_values) >= .12:
        reasons.append("part notable de mots à faible confiance")
    if len(all_words) < 55:
        reasons.append("peu de texte reconnu")
    if any(r["role"] != "texte" for r in regions):
        reasons.append("titre, légende ou encart estimé")
    # Sparse scans are usually separators, full-page illustrations, or blanks.
    sparse_page = len(all_words) < MIN_PAGE_WORDS
    # A page is image-only when the recognised text is mostly noise: fewer than
    # IMAGE_ONLY_LEGIBLE_RATIO of words are both alphabetically meaningful (≥2
    # letters) and OCR-confident (≥ LOW_CONFIDENCE).  This catches magazine
    # covers and full-page photographs where Tesseract produces a trickle of
    # random characters with mixed confidence — a pattern the old approach
    # (mean confidence + low-ratio thresholds) missed because a handful of
    # high-confidence header tokens kept the mean above the cutoff.
    legible_words = sum(
        1 for w in all_words
        if w[6] >= LOW_CONFIDENCE and re.search(r"[a-zA-ZÀ-ÿ]{2,}", w[4])
    )
    legible_ratio = legible_words / max(1, len(all_words))
    image_only = legible_ratio < IMAGE_ONLY_LEGIBLE_RATIO or legible_words < IMAGE_ONLY_MIN_LEGIBLE_WORDS
    if sparse_page:
        reasons.append("page presque sans texte reconnu")
    if image_only:
        reasons.append(
            f"OCR trop incertain pour être présenté comme texte "
            f"({legible_words} mot(s) lisible(s) sur {len(all_words)}, ratio {legible_ratio:.0%})"
        )
    return {
        "page": page_number,
        "page_width": round(page.rect.width, 1), "page_height": round(page.rect.height, 1),
        "detected_word_count": len(detected), "retained_word_count": len(all_words),
        "low_confidence_word_count": low_words,
        "low_confidence_ratio": round(low_words / max(1, len(confidence_values)), 3),
        "mean_confidence": round(mean_confidence, 1),
        "legible_word_count": legible_words,
        "legible_ratio": round(legible_ratio, 3),
        "sparse_page": sparse_page,
        "image_only": image_only,
        "excluded_lines": 0,
        "review_reasons": reasons,
        "regions": regions,
    }


def _render_region(region):
    lines = region["lines"]
    if not lines:
        return ""
    content = []
    for line in lines:
        text = html.escape(line[4])
        ws = [w for w in region["words"]
              if abs((w[1] + w[3]) / 2 - (line[1] + line[3]) / 2) <= max(2, line[3] - line[1])]
        scores = [w[6] for w in ws if w[6] >= 0]
        mean = sum(scores) / len(scores) if scores else 0
        if mean < LOW_CONFIDENCE:
            text = f'<span class="uncertain" title="Confiance OCR moyenne : {mean:.0f}/100">{text}</span>'
        content.append(text)
    text = " ".join(content)
    role = region["role"]
    if role == "titre":
        return f'<h3 class="ocr-region title-region">{text}</h3>'
    if role in {"legende_ou_encart", "encart_ou_colonne_laterale"}:
        return f'<aside class="ocr-region">{text}</aside>'
    return f'<p class="ocr-region">{text}</p>'


def _page_image(page, page_number, dpi, image_format="png", image_quality=55):
    colorspace = fitz.csGRAY if image_format == "jpeg" else fitz.csRGB
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72),
                          colorspace=colorspace, alpha=False)
    if image_format == "jpeg":
        return pix.tobytes("jpeg", jpg_quality=image_quality)
    return pix.tobytes("png")


def convert_v2(pdf_path, output_path, image_mode="flagged", image_dpi=144,
               selected_pages=None, analyzed_records=None,
               image_format="png", image_quality=55):
    doc = fitz.open(pdf_path)
    if not len(doc):
        raise RuntimeError("Le PDF ne contient aucune page.")
    title = os.path.splitext(os.path.basename(pdf_path))[0]
    qa_dir = os.path.splitext(output_path)[0] + ".qa"
    os.makedirs(qa_dir, exist_ok=True)
    report_pages = []
    body, image_items = [], []
    page_indices = list(range(len(doc))) if selected_pages is None else sorted(selected_pages)
    for index in page_indices:
        if ocr_cancelled():
            raise OcrCancelled("Conversion interrompue par l'utilisateur.")
        page = doc[index]
        number = index + 1
        if analyzed_records is None:
            print(f"Analyse OCR v2 : page {number}/{len(doc)}", flush=True)
        else:
            print(f"Assemblage EPUB v3 : page {number}/{len(doc)} (OCR réutilisé)", flush=True)
        record = (analyzed_records[index] if analyzed_records is not None
                  else analyze_page(page, number))
        include_image = image_mode == "all" or (
            image_mode == "flagged" and not record["sparse_page"]
            and bool(record["review_reasons"])
        )
        record["original_page_image_embedded"] = include_image
        record["image_path"] = None
        record["epub_anchor"] = f"source-page-{number}"
        record["original_image_anchor"] = f"source-image-{number}" if include_image else None
        if record["review_reasons"]:
            preview_name = f"page-{number:03}.jpg"
            preview_path = os.path.join(qa_dir, preview_name)
            pix = page.get_pixmap(matrix=fitz.Matrix(0.55, 0.55), colorspace=fitz.csRGB, alpha=False)
            pix.save(preview_path)
            record["preview"] = preview_name
        else:
            record["preview"] = None
        if include_image:
            extension = "jpg" if image_format == "jpeg" else "png"
            media_type = "image/jpeg" if image_format == "jpeg" else "image/png"
            image_name = f"page-{number:03}.{extension}"
            item = epub.EpubItem(uid=f"source-page-{number}", file_name=f"images/{image_name}",
                                 media_type=media_type,
                                 content=_page_image(page, number, image_dpi, image_format, image_quality))
            image_items.append(item)
            record["image_path"] = image_name

        page_label = f"PDF p. {number}"
        page_marker = (
            f'<small class="page-marker" id="pdf-page-{number}">'
            f'<a href="#source-image-{number}" title="Voir le scan de la page {number}">{page_label}</a></small>'
            if include_image else
            f'<small class="page-marker" id="pdf-page-{number}">{page_label}</small>'
        )
        if not record["sparse_page"] or image_mode == "all":
            body.append(f'<section class="source-page" id="source-page-{number}" epub:type="pagebreak">{page_marker}')
            if include_image:
                body.append(f'<figure id="source-image-{number}"><img src="images/page-{number:03}.{extension}" '
                            f'alt="Scan de la page source {number}" />'
                            f'<figcaption>Page originale {number} — consulter si la mise en page ou l’OCR est incertain.</figcaption></figure>')
            if not record["image_only"]:
                for region in record["regions"]:
                    body.append(_render_region(region))
            if not record["regions"]:
                body.append('<p class="empty-page">Aucun texte détecté. Consulter le rapport de contrôle.</p>')
            body.append('</section>')
        # JSON contains only serializable region data; preserve all word boxes,
        # text, and confidence scores for downstream review.
        for region in record["regions"]:
            region["words"] = [list(w[:6]) + [round(w[6], 1)] for w in region["words"]]
        report_pages.append(record)

    book = epub.EpubBook()
    book.set_identifier("pdf-v2-" + os.path.basename(pdf_path))
    book.set_title(title)
    book.set_language("fr")
    chapter = epub.EpubHtml(title=title, file_name="texte.xhtml", lang="fr")
    chapter.content = f'<h1>{html.escape(title)}</h1>' + "\n".join(body)
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
    for item in image_items:
        book.add_item(item)
    book.toc = (chapter,)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = ["nav", chapter]
    epub.write_epub(output_path, book)

    report = {
        "schema_version": 2,
        "source_pdf": os.path.abspath(pdf_path),
        "output_epub": os.path.abspath(output_path),
        "image_mode": image_mode,
        "ocr_dpi": OCR_DPI,
        "low_confidence_threshold": LOW_CONFIDENCE,
        "source_page_count": len(doc),
        "processed_page_count": len(page_indices),
        "pages_for_review": [p["page"] for p in report_pages if p["review_reasons"]],
        "pages": report_pages,
    }
    report_path = os.path.splitext(output_path)[0] + ".qa.json"
    with open(report_path, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    _write_review_html(qa_dir, report_pages, title)
    print(f"EPUB v2 : {output_path}")
    print(f"Rapport page par page : {report_path}")
    print(f"Aperçus de contrôle : {qa_dir}")


def _write_review_html(qa_dir, pages, title):
    reviewed = [p for p in pages if p["review_reasons"]]
    rows = []
    for page in reviewed:
        preview = page.get("preview")
        img = f'<img src="{html.escape(preview)}" alt="Page {page["page"]}" />' if preview else ""
        regions = "".join(
            f'<li>{html.escape(r["role"])} — {r["word_count"]} mots, confiance moyenne {r["mean_confidence"]:.0f}</li>'
            for r in page["regions"]
        )
        rows.append(f'<article><h2>Page {page["page"]}</h2>{img}'
                    f'<p>{html.escape(" ; ".join(page["review_reasons"]))}</p>'
                    f'<p>{page["retained_word_count"]} mots reconnus ; '
                    f'{page["low_confidence_word_count"]} de faible confiance ; '
                    f'image source dans EPUB : {"oui" if page["original_page_image_embedded"] else "non"}</p>'
                    f'<ul>{regions}</ul></article>')
    content = ('<!doctype html><html lang="fr"><meta charset="utf-8">'
               f'<title>Contrôle OCR — {html.escape(title)}</title>'
               '<style>body{font:16px sans-serif;max-width:1000px;margin:auto}article{border-top:1px solid #aaa;padding:1em 0}img{max-width:100%;height:auto}</style>'
               f'<h1>Pages à relire — {html.escape(title)}</h1>' + "\n".join(rows) + '</html>')
    with open(os.path.join(qa_dir, "index.html"), "w", encoding="utf-8") as stream:
        stream.write(content)


def _parse_pages(value, page_count):
    """Parse physical pages such as 34,51,63-64,74."""
    selected = set()
    try:
        for part in value.split(","):
            part = part.strip()
            if "-" in part:
                first, last = map(int, part.split("-", 1))
            else:
                first = last = int(part)
            if first < 1 or last < first or last > page_count:
                raise ValueError
            selected.update(range(first - 1, last))
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            "utiliser des pages comme 34,51,63-64,74"
        )
    return selected


def main():
    parser = argparse.ArgumentParser(description="Conversion PDF vers EPUB hybride, version 2")
    parser.add_argument("pdf", help="PDF source")
    parser.add_argument("output", nargs="?", help="EPUB de sortie")
    parser.add_argument("--images", choices=("flagged", "all", "none"), default="flagged",
                        help="pages originales intégrées : signalées (défaut), toutes ou aucune")
    parser.add_argument("--image-dpi", type=int, default=144,
                        help="résolution des pages originales intégrées (défaut : 144)")
    parser.add_argument("--pages", metavar="PAGES",
                        help="pages physiques, par exemple 34,51,63-64,74")
    args = parser.parse_args()
    if not os.path.isfile(args.pdf):
        parser.error(f"fichier introuvable : {args.pdf}")
    if args.image_dpi < 72 or args.image_dpi > 300:
        parser.error("--image-dpi doit être compris entre 72 et 300")
    selected_pages = None
    if args.pages:
        with fitz.open(args.pdf) as source:
            try:
                selected_pages = _parse_pages(args.pages, len(source))
            except argparse.ArgumentTypeError as exc:
                parser.error(str(exc))
    output = args.output or os.path.splitext(args.pdf)[0] + ".v2.epub"
    try:
        convert_v2(args.pdf, output, image_mode=args.images, image_dpi=args.image_dpi,
                   selected_pages=selected_pages)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
