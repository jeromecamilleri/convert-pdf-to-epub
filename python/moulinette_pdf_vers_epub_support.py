#!/usr/bin/env python3
"""Convertir un PDF en EPUB reflowable, avec OCR facultatif pour les scans.

Module de support partagé par la moulinette scan et les contrôles du projet.

Les numéros de --figures sont les pages PDF (à partir de 1). Pour un vieux
Kindle, n'ajouter que les pages dont le graphique apporte réellement quelque
chose : les pages sont rendues en JPEG gris et basse résolution.
"""

import argparse
import csv
import html
import io
import os
import re
import signal
import subprocess
import sys
import threading

import pymupdf as fitz
from ebooklib import epub


_OCR_LOCK = threading.Lock()
_OCR_CANCELLED = threading.Event()
_ACTIVE_TESSERACT = set()


class OcrCancelled(RuntimeError):
    """Raised in worker threads after the user interrupts the conversion."""


def reset_ocr_cancellation():
    _OCR_CANCELLED.clear()


def ocr_cancelled():
    return _OCR_CANCELLED.is_set()


def cancel_active_tesseract():
    """Stop all Tesseract process groups started by this Python process."""
    _OCR_CANCELLED.set()
    with _OCR_LOCK:
        processes = list(_ACTIVE_TESSERACT)
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
    # Give OCR a short grace period, then force-stop anything still alive.
    if processes:
        threading.Event().wait(.5)
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()


def run_tesseract(command, input_data):
    """Run OCR in a tracked process group so Ctrl+C can stop it promptly."""
    with _OCR_LOCK:
        if _OCR_CANCELLED.is_set():
            raise OcrCancelled("OCR interrompu par l'utilisateur.")
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        )
        _ACTIVE_TESSERACT.add(process)
    try:
        stdout, stderr = process.communicate(input=input_data)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
            try:
                process.communicate(timeout=.5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                process.communicate()
        raise
    finally:
        with _OCR_LOCK:
            _ACTIVE_TESSERACT.discard(process)
    if _OCR_CANCELLED.is_set():
        raise OcrCancelled("OCR interrompu par l'utilisateur.")
    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode, command, output=stdout, stderr=stderr
        )
    return stdout


def page_text(page, use_ocr=False):
    text = page.get_text("text").strip()
    if text or not use_ocr:
        return text, None
    try:
        # PSM 4 traite les blocs de texte séparément. C'est plus robuste sur
        # les pages mêlant colonnes, dessins et petits encarts qu'un OCR plein
        # page qui tente d'interpréter chaque élément graphique comme du texte.
        dpi = 180
        scale = dpi / 72
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False
        )
        words = tesseract_words(pixmap, page, scale)
        # PSM 4 can interleave text from parallel columns when the columns
        # contain different paragraph heights. For detected multi-column
        # pages, try layout analysis at higher resolution and keep it only
        # when its measured OCR quality is comparable to the first pass.
        if column_ordered_blocks(page, None, words=words):
            high_dpi = 300
            high_scale = high_dpi / 72
            high_pixmap = page.get_pixmap(
                matrix=fitz.Matrix(high_scale, high_scale),
                colorspace=fitz.csGRAY, alpha=False,
            )
            high_result = tesseract_words(high_pixmap, page, high_scale, psm="3")
            if should_use_layout_pass(words, high_result):
                words = high_result
        words = reocr_low_confidence_columns(page, words)
        line_blocks = tesseract_line_blocks(words)
        recognized_text = "\n".join(block[4] for block in line_blocks)
        return recognized_text, {"words": words, "blocks": line_blocks}
    except Exception as exc:
        raise RuntimeError(
            "Cette page est un scan et l'OCR a échoué. Installez Tesseract et "
            "son pack de langue français (fra), puis relancez avec --ocr. "
            f"Détail : {exc}"
        ) from exc


def tesseract_words(pixmap, page, scale, psm="4"):
    """Return word boxes and confidence scores from one Tesseract TSV pass."""
    output = run_tesseract(
        ["tesseract", "stdin", "stdout", "-l", "fra+eng", "--psm", psm, "tsv"],
        pixmap.tobytes("png"),
    )
    reader = csv.DictReader(
        io.StringIO(output.decode("utf-8", errors="replace")), delimiter="\t"
    )
    words = []
    for row in reader:
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            x0 = float(row["left"]) / scale
            y0 = float(row["top"]) / scale
            x1 = x0 + float(row["width"]) / scale
            y1 = y0 + float(row["height"]) / scale
            block_key = (row["block_num"], row["par_num"], row["line_num"])
            confidence = float(row.get("conf", "-1"))
        except (KeyError, TypeError, ValueError):
            continue
        words.append((x0, y0, x1, y1, row["text"].strip(), block_key, confidence))
    return words


def should_use_layout_pass(original, candidate):
    """Use a column-aware retry only when its word coverage and confidence hold."""
    def quality(words):
        scores = [word[6] for word in words if word[6] >= 0]
        return len(words), sum(scores) / len(scores) if scores else 0

    original_count, original_confidence = quality(original)
    candidate_count, candidate_confidence = quality(candidate)
    return (
        original_count >= 40
        and candidate_count >= original_count * 0.97
        and candidate_confidence >= original_confidence - 2
    )


def tesseract_line_blocks(words):
    """Group recognized words by Tesseract's line segmentation and geometry."""
    lines = {}
    for word in words:
        lines.setdefault(word[5], []).append(word)
    blocks = []
    for line_words in lines.values():
        line_words.sort(key=lambda word: word[0])
        blocks.append((
            min(word[0] for word in line_words),
            min(word[1] for word in line_words),
            max(word[2] for word in line_words),
            max(word[3] for word in line_words),
            " ".join(word[4] for word in line_words),
        ))
    return sorted(blocks, key=lambda block: (block[1], block[0]))


def reocr_low_confidence_columns(page, words):
    """Re-OCR only text columns with a high share of low-confidence words."""
    centers = sorted((word[0] + word[2]) / 2 for word in words)
    if len(centers) < 80:
        return words
    candidates = []
    for i in range(len(centers) - 1):
        gap = centers[i + 1] - centers[i]
        center = (centers[i + 1] + centers[i]) / 2
        if gap >= page.rect.width * 0.055 and page.rect.width * 0.18 < center < page.rect.width * 0.82:
            candidates.append((gap, center))
    if not candidates:
        return words

    cuts = []
    minimum_count = max(20, int(len(words) * 0.12))
    for _, cut in sorted(candidates, reverse=True):
        proposed = sorted(cuts + [cut])
        counts = []
        left = float("-inf")
        for edge in proposed:
            counts.append(sum(left <= (word[0] + word[2]) / 2 < edge for word in words))
            left = edge
        counts.append(sum((word[0] + word[2]) / 2 >= left for word in words))
        if all(count >= minimum_count for count in counts):
            cuts = proposed
            if len(cuts) == 2:
                break
    if not cuts:
        return words

    columns = [[] for _ in range(len(cuts) + 1)]
    for word in words:
        center = (word[0] + word[2]) / 2
        columns[sum(center >= cut for cut in cuts)].append(word)

    updated = []
    for column_index, column in enumerate(columns):
        low_confidence_ratio = sum(word[6] >= 0 and word[6] < 45 for word in column) / max(1, len(column))
        if len(column) < 30 or low_confidence_ratio < 0.05:
            updated.extend(column)
            continue
        x0 = max(page.rect.x0, min(word[0] for word in column) - 12)
        y0 = max(page.rect.y0, min(word[1] for word in column) - 12)
        x1 = min(page.rect.x1, max(word[2] for word in column) + 12)
        y1 = min(page.rect.y1, max(word[3] for word in column) + 12)
        clip = fitz.Rect(x0, y0, x1, y1)
        dpi = 300
        scale = dpi / 72
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale), clip=clip,
            colorspace=fitz.csGRAY, alpha=False,
        )
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "fra+eng", "--psm", "6", "tsv"],
                input=pixmap.tobytes("png"), capture_output=True, check=True,
            )
            reader = csv.DictReader(
                io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"
            )
            replacement = []
            for row in reader:
                if row.get("level") != "5" or not row.get("text", "").strip():
                    continue
                left = x0 + float(row["left"]) / scale
                top = y0 + float(row["top"]) / scale
                right = left + float(row["width"]) / scale
                bottom = top + float(row["height"]) / scale
                try:
                    confidence = float(row.get("conf", "-1"))
                except (TypeError, ValueError):
                    confidence = -1
                key = ("column", column_index, row.get("block_num"), row.get("par_num"), row.get("line_num"))
                replacement.append((left, top, right, bottom, row["text"].strip(), key, confidence))
            updated.extend(replacement if len(replacement) >= 20 else column)
        except (OSError, subprocess.CalledProcessError, KeyError, ValueError):
            updated.extend(column)
    return updated


def refine_heading_line(page, box):
    """Re-OCR a display line at higher resolution to recover small punctuation."""
    height = max(1, box[3] - box[1])
    horizontal_padding = max(4, height * 0.4)
    vertical_padding = max(2, height * 0.12)
    clip = fitz.Rect(
        max(page.rect.x0, box[0] - horizontal_padding),
        max(page.rect.y0, box[1] - vertical_padding),
        min(page.rect.x1, box[2] + horizontal_padding),
        min(page.rect.y1, box[3] + vertical_padding),
    )
    try:
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(300 / 72, 300 / 72), clip=clip,
            colorspace=fitz.csGRAY, alpha=False,
        )
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "fra+eng", "--psm", "7"],
            input=pixmap.tobytes("png"), capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    refined = clean_block(result.stdout.decode("utf-8", errors="replace"))
    return refined or None


def clean_block(text):
    # Reconstituer les mots coupés en fin de ligne puis normaliser les espaces.
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def looks_like_display_heading(text):
    """Recognize display titles from capitalization and line shape alone."""
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]+", text)
    if not 3 <= len(words) <= 14 or len(text) > 85:
        return False
    uppercase_prefix = 0
    for word in words:
        if word.isupper():
            uppercase_prefix += 1
        else:
            break
    return uppercase_prefix >= 2 and uppercase_prefix / len(words) >= 0.4


def readable_ocr_block(text, allow_short=False):
    """Garde ou écarte un bloc entier afin de ne pas tronquer les phrases."""
    if allow_short and looks_like_display_heading(text):
        return True
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]+", text)
    letters = "".join(words)
    if len(letters) < (6 if allow_short else 8):
        return False
    lowercase = sum(char.islower() for char in letters)
    # Dans les scans de magazine, le corps est souvent en italique ou en
    # colonne étroite. La segmentation OCR peut produire des lignes de quatre
    # mots dont la forme des voyelles paraît atypique ; les rejeter supprime
    # des morceaux de phrases alors que leur longueur et leur casse sont
    # caractéristiques du texte courant.
    if len(words) >= 4 and len(letters) >= 22 and lowercase / len(letters) >= 0.55:
        return True
    # Les fragments venant des illustrations ont souvent peu de mots complets,
    # beaucoup de capitales ou une structure peu proche d'une phrase française.
    vowels = sum(char.casefold() in "aeiouyàâäéèêëïîôöùûüÿœ" for char in letters)
    if not 0.22 <= vowels / len(letters) <= 0.62:
        return False
    substantial_letters = sum(len(word) for word in words if len(word) >= 3)
    minimum_substantial_ratio = 0.60 if allow_short else 0.72
    if substantial_letters / len(letters) < minimum_substantial_ratio:
        return False
    # Une citation mise en capitales au début d'un article reste du texte ;
    # seules les suites presque entièrement en capitales sont éliminées.
    if len(letters) > 35 and lowercase / len(letters) < 0.25:
        return False
    if len(letters) >= 35:
        # In narrow magazine columns, a complete line can contain only four
        # or five long words. Do not discard it just for falling below the
        # full-page minimum.
        return len(words) >= (4 if allow_short else 6)
    if allow_short and re.search(r"\d", text) and letters and lowercase / len(letters) >= 0.5:
        return True
    if allow_short and len(letters) < 14:
        # Short lines occur in headings, labels and captions. Keep plausible
        # lowercase language fragments based on spelling shape, not vocabulary.
        return lowercase / len(letters) >= 0.65 and 0.22 <= vowels / len(letters) <= 0.62
    return len(letters) >= 14 and len(words) >= 2 and lowercase / len(letters) >= 0.55


def clean_ocr_block(text, allow_short=False):
    # Les scans décoratifs sont fréquemment fusionnés au corps d'un bloc OCR.
    # Filtrer par ligne évite de laisser ces fragments contaminer le paragraphe.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    retained = [line for line in lines if readable_ocr_block(line, allow_short=allow_short)]
    if not retained:
        return "", len(lines)
    return clean_block(" ".join(retained)), len(lines) - len(retained)


def column_ordered_blocks(page, text_page, words=None):
    """Rebuild clear columns, adapting when a page changes layout by section."""
    if words is None:
        words = page.get_text("words", textpage=text_page)
    words = [
        word for word in words
        if len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]", word[4])) >= 1
        or (re.search(r"\d", word[4]) and word[1] < page.rect.height - 80)
        or (word[4] in {"%", "€"} and word[1] < page.rect.height - 80)
    ]
    if len(words) < 40:
        return None
    typical_word_height = sorted(max(1, word[3] - word[1]) for word in words)[len(words) // 2]

    def find_cuts(band_words):
        centers = sorted((word[0] + word[2]) / 2 for word in band_words)
        candidates = []
        for i in range(len(centers) - 1):
            gap = centers[i + 1] - centers[i]
            if gap >= page.rect.width * 0.055 and page.rect.width * 0.18 < centers[i] < page.rect.width * 0.82:
                candidates.append((gap, (centers[i] + centers[i + 1]) / 2))
        cuts = []
        minimum_words = max(10, int(len(band_words) * 0.12))
        for _, cut in sorted(candidates, reverse=True):
            proposed = sorted(cuts + [cut])
            counts = []
            left_edge = float("-inf")
            for edge in proposed:
                counts.append(sum(left_edge <= (word[0] + word[2]) / 2 < edge for word in band_words))
                left_edge = edge
            counts.append(sum((word[0] + word[2]) / 2 >= left_edge for word in band_words))
            if all(count >= minimum_words for count in counts):
                cuts = proposed
                if len(cuts) == 2:
                    break
        columns = [[] for _ in range(len(cuts) + 1)]
        for word in band_words:
            center = (word[0] + word[2]) / 2
            columns[sum(center >= cut for cut in cuts)].append(word)
        return cuts, columns

    # Split the page at large vertical whitespace only when substantial text
    # exists on both sides. This handles pages with (for example) a two-column
    # article above a three-column fact panel.
    bands = [words]
    while len(bands) < 4:
        candidates = []
        for band_index, band in enumerate(bands):
            y_centers = sorted((word[1] + word[3]) / 2 for word in band)
            for i in range(len(y_centers) - 1):
                gap = y_centers[i + 1] - y_centers[i]
                if gap < max(typical_word_height * 7, page.rect.height * 0.035):
                    continue
                cut_y = (y_centers[i] + y_centers[i + 1]) / 2
                above = [word for word in band if (word[1] + word[3]) / 2 < cut_y]
                below = [word for word in band if (word[1] + word[3]) / 2 >= cut_y]
                minimum = max(12, int(len(band) * 0.18))
                if len(above) >= minimum and len(below) >= minimum:
                    candidates.append((gap, band_index, cut_y, above, below))
        if not candidates:
            break
        _, band_index, _, above, below = max(candidates, key=lambda item: item[0])
        bands[band_index:band_index + 1] = [above, below]

    layouts = [(find_cuts(band)[0], find_cuts(band)[1]) for band in bands]
    has_multiple_columns = any(len(columns) > 1 for _, columns in layouts)
    if not has_multiple_columns:
        return None

    # If each vertical section has the same column boundaries, read full
    # columns top-to-bottom. Otherwise read each section left-to-right.
    stable_columns = all(len(layout[1]) == len(layouts[0][1]) for layout in layouts)
    if stable_columns and layouts[0][0]:
        stable_columns = all(
            len(cuts) == len(layouts[0][0])
            and all(abs(a - b) <= page.rect.width * 0.12 for a, b in zip(cuts, layouts[0][0]))
            for cuts, _ in layouts[1:]
        )
    ordered_columns = []
    if stable_columns:
        for column_index in range(len(layouts[0][1])):
            ordered_columns.append([word for _, columns in layouts for word in columns[column_index]])
    else:
        for _, columns in layouts:
            ordered_columns.extend(columns)

    result = []
    for column in ordered_columns:
        column.sort(key=lambda word: ((word[1] + word[3]) / 2, word[0]))
        lines = []
        for word in column:
            center_y = (word[1] + word[3]) / 2
            if not lines or abs(center_y - lines[-1][0]) > max(2, typical_word_height * 0.65):
                lines.append([center_y, [], word[1], word[3]])
            lines[-1][1].append(word)
            lines[-1][2] = min(lines[-1][2], word[1])
            lines[-1][3] = max(lines[-1][3], word[3])
        for center_y, line_words, y0, y1 in lines:
            line_words.sort(key=lambda word: word[0])
            value = " ".join(word[4] for word in line_words)
            x0 = min(word[0] for word in line_words)
            x1 = max(word[2] for word in line_words)
            result.append((x0, y0, x1, y1, value))
    return result


def figure_score(text):
    """Estimate whether a page has data-heavy visual material, language-free."""
    numbers = re.findall(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?\s*(?:%|‰|€|£|\$)?(?!\w)", text)
    if len(numbers) < 8:
        return 0
    numeric_density = len(numbers) / max(1, len(re.findall(r"\S+", text)))
    # Several years, page references, and product numbers can occur in an
    # ordinary article. Auto mode selects data-heavy pages only when numbers
    # make up a meaningful share of the recognized text.
    return 3 if numeric_density >= 0.08 else 2


def looks_like_heading(text):
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ0-9]+", text)
    return (
        len(text) <= 65
        and 3 <= len(words) <= 9
        and len(text) >= 14
        and text[:1].isupper()
        and not re.search(r"[.!?;:,]$", text)
    )


def normalize_reconstructed_line(text):
    """Normalize spacing without applying document-specific vocabulary rules."""
    return clean_block(text)


def same_paragraph_flow(previous_box, current_box):
    """True when two OCR blocks look like consecutive lines in one column."""
    vertical_gap = current_box[1] - previous_box[3]
    typical_line_height = max(previous_box[3] - previous_box[1], current_box[3] - current_box[1], 1)
    if vertical_gap < -typical_line_height * 0.35 or vertical_gap > typical_line_height * 0.85:
        return False
    previous_width = max(1, previous_box[2] - previous_box[0])
    current_width = max(1, current_box[2] - current_box[0])
    overlap = min(previous_box[2], current_box[2]) - max(previous_box[0], current_box[0])
    overlap_ratio = overlap / min(previous_width, current_width)
    return abs(current_box[0] - previous_box[0]) <= typical_line_height * 7 or overlap_ratio >= 0.65


def join_wrapped_text(previous, current):
    # Un tiret de césure se reconnaît à sa position : il suit immédiatement une
    # lettre (pas un espace ni un guillemet) et précède une lettre au début du
    # bloc suivant.  On accepte minuscule ET majuscule après le tiret : en
    # presse française les noms propres et les débuts de phrase après coupure
    # de bloc commencent souvent par une majuscule.
    # Un tiret de liste ou de dialogue (précédé d'un espace ou d'un signe de
    # ponctuation) n'est jamais en fin de mot — il ne correspond pas au pattern.
    if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]-$", previous) and re.match(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]", current):
        return previous[:-1] + current
    return previous + " " + current


def convert(pdf_path, output_path, title, use_ocr=False, figure_pages=None, auto_figures=False, selected_pages=None, fallback_images=False):
    doc = fitz.open(pdf_path)
    if not len(doc):
        raise RuntimeError("Le PDF ne contient aucune page.")

    body = []
    images = []
    detected_figure_pages = []
    heading_links = []
    dropped_ocr_blocks = 0
    low_confidence_pages = []
    empty_ocr_pages = []
    page_indices = sorted(selected_pages) if selected_pages is not None else list(range(len(doc)))
    for progress_index, page_index in enumerate(page_indices, start=1):
        page = doc[page_index]
        page_number = page_index + 1
        print(f"OCR / extraction : page {page_number} ({progress_index}/{len(page_indices)})", flush=True)
        text, ocr_text_page = page_text(page, use_ocr=use_ocr)
        include_figure = page_number in (figure_pages or set())
        if auto_figures:
            score = figure_score(text)
            include_figure = score >= 3
            if include_figure:
                detected_figure_pages.append(page_number)
        # Pour les scans, utiliser les lignes et mots fournis par Tesseract.
        # Les blocs natifs du PDF source ne contiennent souvent que l'image.
        custom_ocr = isinstance(ocr_text_page, dict)
        if custom_ocr and not ocr_text_page["words"]:
            empty_ocr_pages.append(page_number)
        blocks = (
            ocr_text_page["blocks"] if custom_ocr
            else page.get_text("blocks", textpage=ocr_text_page)
            if ocr_text_page is not None
            else page.get_text("blocks") if text else []
        )
        columnized = False
        if ocr_text_page is not None:
            ordered_columns = column_ordered_blocks(
                page, None if custom_ocr else ocr_text_page,
                words=ocr_text_page["words"] if custom_ocr else None,
            )
            if ordered_columns:
                blocks = ordered_columns
                columnized = True
        if text and not blocks:
            blocks = [(0, 0, 0, 0, text)]
        page_parts = []
        retained_text = []
        page_dropped_blocks = 0
        needs_image_fallback = bool(custom_ocr and not ocr_text_page["words"])
        paragraph_text = None
        paragraph_box = None
        last_heading_box = None
        last_heading_text = None
        last_heading_is_display = False
        measurable_heights = sorted(
            max(1, block[3] - block[1])
            for block in blocks
            if len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]", block[4])) >= 8
            and len(block[4]) >= 12
        )
        typical_page_text_height = (
            measurable_heights[len(measurable_heights) // 2] if measurable_heights else 1
        )

        def flush_paragraph():
            nonlocal paragraph_text, paragraph_box
            if paragraph_text:
                page_parts.append(f"<p>{html.escape(paragraph_text)}</p>")
            paragraph_text = None
            paragraph_box = None

        for block in blocks:
            if ocr_text_page is not None:
                value, rejected_lines = clean_ocr_block(block[4], allow_short=columnized)
                dropped_ocr_blocks += rejected_lines
                page_dropped_blocks += rejected_lines
            else:
                value = clean_block(block[4])
            if value:
                if columnized:
                    value = normalize_reconstructed_line(value)
                is_display_line = looks_like_display_heading(value)
                continues_display_heading = (
                    last_heading_is_display
                    and value[:1].islower()
                    and same_paragraph_flow(last_heading_box, block[:4])
                )
                if custom_ocr and (is_display_line or continues_display_heading):
                    refined = refine_heading_line(page, block[:4])
                    if refined:
                        value = refined
                retained_text.append(value)
                large_top_line = (
                    block[1] <= page.rect.height * 0.30
                    and block[3] - block[1] >= typical_page_text_height * 1.5
                )
                display_heading = looks_like_display_heading(value)
                display_heading_at_top = display_heading and block[1] <= page.rect.height * 0.30
                heading_continuation = (
                    last_heading_box is not None
                    and len(value) <= 65
                    and same_paragraph_flow(last_heading_box, block[:4])
                    and (
                        (
                            not re.search(r"[.!?;:,]$", value)
                            and block[3] - block[1] >= typical_page_text_height * 1.5
                        )
                        or (last_heading_is_display and value[:1].islower())
                    )
                )
                heading_candidate = (
                    (looks_like_heading(value) and (not columnized or large_top_line))
                    or display_heading_at_top
                    or heading_continuation
                )
                if heading_candidate:
                    box = block[:4]
                    chapter_title_candidate = large_top_line or display_heading_at_top
                    chapter_start = chapter_title_candidate
                    if last_heading_box and same_paragraph_flow(last_heading_box, box):
                        last_heading_text = join_wrapped_text(last_heading_text, value)
                        anchor = heading_links[-1][0]
                        heading_links[-1] = (anchor, last_heading_text)
                        css_class = "chapter-start" if "chapter-start" in page_parts[-1] else "section-heading"
                        page_parts[-1] = (
                            f'<h2 class="{css_class}" id="{anchor}">'
                            f'{html.escape(last_heading_text)}</h2>'
                        )
                        last_heading_box = box
                        last_heading_is_display = last_heading_is_display or display_heading
                    else:
                        flush_paragraph()
                        anchor = f"section-{page_number}-{len(heading_links) + 1}"
                        css_class = "chapter-start" if chapter_start else "section-heading"
                        page_parts.append(
                            f'<h2 class="{css_class}" id="{anchor}">{html.escape(value)}</h2>'
                        )
                        heading_links.append((anchor, value))
                        last_heading_box = box
                        last_heading_text = value
                        last_heading_is_display = display_heading
                else:
                    last_heading_box = None
                    last_heading_text = None
                    last_heading_is_display = False
                    box = block[:4]
                    if paragraph_text and paragraph_box and same_paragraph_flow(paragraph_box, box):
                        paragraph_text = join_wrapped_text(paragraph_text, value)
                    else:
                        flush_paragraph()
                        paragraph_text = value
                    paragraph_box = box
        flush_paragraph()
        if ocr_text_page is not None:
            raw_letters = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]", text))
            kept_letters = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŒœ]", " ".join(retained_text)))
            fused_words = len(re.findall(r"\b[A-Za-zÀ-ÖØ-öø-ÿŒœ]{20,}\b", text))
            needs_image_fallback = bool(
                custom_ocr and not ocr_text_page["words"]
            ) or bool(raw_letters >= 180 and retained_text and (
                kept_letters / raw_letters < 0.45 or fused_words >= 8 or page_dropped_blocks >= 8
            ))
            if needs_image_fallback and raw_letters:
                low_confidence_pages.append((page_number, round(kept_letters / raw_letters, 2), fused_words))
        if page_parts:
            body.extend(page_parts)

        if include_figure or (fallback_images and needs_image_fallback):
            pix = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), colorspace=fitz.csGRAY, alpha=False)
            image_name = f"page-{page_number}.jpg"
            image_data = pix.tobytes("jpeg", jpg_quality=65)
            image_item = epub.EpubItem(
                uid=f"page-image-{page_number}", file_name=f"images/{image_name}",
                media_type="image/jpeg", content=image_data,
            )
            images.append(image_item)
            caption = (
                f"Page {page_number} du document source"
                if include_figure
                else f"Page {page_number} à agrandir si l'OCR est incomplet"
            )
            body.append(
                f'<figure><img src="images/{image_name}" alt="Page {page_number} du PDF" />'
                f'<figcaption>{html.escape(caption)}</figcaption></figure>'
            )

    if not body:
        if not use_ocr:
            raise RuntimeError(
                "Aucun texte extractible : ce PDF semble être un scan. "
                "Relancez avec --ocr (Tesseract et la langue fra doivent être installés)."
            )
        raise RuntimeError("Aucun texte n'a pu être reconnu dans ce PDF.")

    book = epub.EpubBook()
    book.set_identifier("pdf-" + os.path.basename(pdf_path))
    book.set_title(title)
    book.set_language("fr")
    chapter = epub.EpubHtml(title=title, file_name="texte.xhtml", lang="fr")
    chapter.content = "<h1>" + html.escape(title) + "</h1>" + "\n".join(body)
    book.add_item(chapter)
    for image_item in images:
        book.add_item(image_item)

    css = '''
    body { font-family: serif; margin: 4%; line-height: 1.35; }
    h1 { font-size: 1.4em; }
    h2 { font-size: 1.2em; margin: 1.1em 0 .6em; break-after: avoid; page-break-after: avoid; }
    h2.chapter-start { break-before: page; page-break-before: always; margin-top: 0; }
    p { text-indent: 1em; margin: 0 0 .9em; text-align: left; widows: 2; orphans: 2; }
    figure { margin: 1em 0; page-break-inside: avoid; text-align: center; }
    img { max-width: 100%; height: auto; }
    figcaption { font-size: .85em; }
    '''
    style = epub.EpubItem(uid="style", file_name="style/book.css", media_type="text/css", content=css)
    book.add_item(style)
    chapter.add_item(style)
    book.toc = tuple([chapter] + [epub.Link(f"texte.xhtml#{anchor}", label, anchor) for anchor, label in heading_links])
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = ["nav", chapter]
    epub.write_epub(output_path, book)
    print(f"EPUB créé : {output_path} ({len(page_indices)} page(s) traitée(s) sur {len(doc)}, {len(images)} image(s))")
    if use_ocr:
        print(f"Lignes OCR écartées comme artefacts : {dropped_ocr_blocks}")
    if low_confidence_pages:
        details = ", ".join(f"{page} (texte conservé {ratio:.0%}, mots fusionnés {fused})" for page, ratio, fused in low_confidence_pages)
        print("Pages OCR à relire : " + details)
    if auto_figures:
        print("Pages retenues automatiquement : " + (", ".join(map(str, detected_figure_pages)) or "aucune"))
    if empty_ocr_pages:
        print("Pages sans texte reconnu (ignorées, sauf image de secours) : " + ", ".join(map(str, empty_ocr_pages)))


def main():
    parser = argparse.ArgumentParser(description="PDF vers EPUB simple pour liseuse")
    parser.add_argument("--ocr", action="store_true", help="activer Tesseract pour les pages scannées")
    parser.add_argument("--figures", metavar="PAGES|auto", help="inclure des pages en images : numéros (12,34-36) ou auto pour les repérer")
    parser.add_argument("--pages", metavar="DEBUT-FIN", help="ne traiter qu'une plage de pages pour un essai, ex. 1-8")
    parser.add_argument("--fallback-images", action="store_true", help="ajouter l'image source des pages où l'OCR semble douteux")
    parser.add_argument("pdf", help="fichier PDF source")
    parser.add_argument("output", nargs="?", help="fichier EPUB de sortie")
    args = parser.parse_args()
    if not os.path.isfile(args.pdf):
        parser.error(f"fichier introuvable : {args.pdf}")

    figures = set()
    auto_figures = args.figures == "auto"
    if args.figures and not auto_figures:
        try:
            for part in args.figures.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-", 1))
                    if start < 1 or end < start:
                        raise ValueError
                    figures.update(range(start, end + 1))
                else:
                    figures.add(int(part))
            if min(figures) < 1:
                raise ValueError
        except ValueError:
            parser.error("--figures attend auto ou des pages comme 12,34-36")

    selected_pages = None
    if args.pages:
        try:
            start_page, end_page = map(int, args.pages.split("-", 1))
            if start_page < 1 or end_page < start_page:
                raise ValueError
        except ValueError:
            parser.error("--pages attend un intervalle comme 1-8")

    output = args.output or os.path.splitext(args.pdf)[0] + ".epub"
    title = os.path.splitext(os.path.basename(args.pdf))[0]
    try:
        if args.pages:
            with fitz.open(args.pdf) as source:
                if end_page > len(source):
                    parser.error(f"le PDF contient {len(source)} pages, pas {end_page}")
            selected_pages = set(range(start_page - 1, end_page))
        convert(args.pdf, output, title, use_ocr=args.ocr, figure_pages=figures, auto_figures=auto_figures, selected_pages=selected_pages, fallback_images=args.fallback_images)
    except (RuntimeError, fitz.FileDataError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
