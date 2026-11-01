#!/usr/bin/env python3
"""Reduce embedded page scans in an EPUB for small grayscale e-readers."""

import argparse
from datetime import datetime, timezone
import os
import re
import tempfile
import zipfile

import pymupdf as fitz


def _manifest_images(opf_text):
    paths = []
    for item in re.findall(r"<item\b[^>]*/>", opf_text):
        media = re.search(r'\bmedia-type="([^"]+)"', item)
        href = re.search(r'\bhref="([^"]+)"', item)
        if media and media.group(1) == "image/png" and href:
            paths.append(href.group(1))
    return paths


def _compact_image(data, max_width, max_height, quality):
    source = fitz.Pixmap(data)
    ratio = min(1.0, max_width / source.width, max_height / source.height)
    width = max(1, round(source.width * ratio))
    height = max(1, round(source.height * ratio))
    scaled = fitz.Pixmap(source, width, height) if ratio < 1 else source
    grayscale = fitz.Pixmap(fitz.csGRAY, scaled)
    return grayscale.tobytes("jpeg", jpg_quality=quality)


def optimize(input_path, output_path, max_width=840, max_height=1200, quality=50):
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    if input_path == output_path:
        raise ValueError("Choisir un fichier de sortie distinct pour conserver l'original.")

    temp_fd, temp_path = tempfile.mkstemp(prefix="epub-kindle-", suffix=".epub",
                                          dir=os.path.dirname(output_path))
    os.close(temp_fd)
    try:
        with zipfile.ZipFile(input_path, "r") as source_zip:
            names = source_zip.namelist()
            opf_path = next(name for name in names if name.endswith("content.opf"))
            opf_text = source_zip.read(opf_path).decode("utf-8")
            opf_dir = os.path.dirname(opf_path)
            image_paths = _manifest_images(opf_text)
            renamed = {}
            optimized_images = {}
            old_total = new_total = 0

            for href in image_paths:
                old_path = f"{opf_dir}/{href}" if opf_dir else href
                if old_path not in names:
                    continue
                new_href = re.sub(r"\.png$", ".jpg", href, flags=re.I)
                new_path = f"{opf_dir}/{new_href}" if opf_dir else new_href
                jpeg = _compact_image(source_zip.read(old_path), max_width, max_height, quality)
                renamed[href] = new_href
                optimized_images[new_path] = jpeg
                old_total += source_zip.getinfo(old_path).file_size
                new_total += len(jpeg)

            def update_manifest(match):
                item = match.group(0)
                href_match = re.search(r'\bhref="([^"]+)"', item)
                if not href_match or href_match.group(1) not in renamed:
                    return item
                item = item.replace(f'href="{href_match.group(1)}"',
                                    f'href="{renamed[href_match.group(1)]}"')
                item = item.replace('media-type="image/png"', 'media-type="image/jpeg"')
                return item

            opf_text = re.sub(r"<item\b[^>]*/>", update_manifest, opf_text)
            opf_text = re.sub(
                r'(<meta\s+property="dcterms:modified">)[^<]*(</meta>)',
                lambda match: match.group(1) + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + match.group(2),
                opf_text,
            )

            with zipfile.ZipFile(temp_path, "w") as output_zip:
                output_zip.writestr("mimetype", source_zip.read("mimetype"),
                                    compress_type=zipfile.ZIP_STORED)
                for info in source_zip.infolist():
                    if info.filename == "mimetype" or info.filename in optimized_images:
                        continue
                    if info.filename in {f"{opf_dir}/{href}" for href in image_paths}:
                        continue
                    content = source_zip.read(info.filename)
                    if info.filename == opf_path:
                        content = opf_text.encode("utf-8")
                    elif info.filename.endswith((".xhtml", ".html")):
                        text = content.decode("utf-8")
                        for old_href, new_href in renamed.items():
                            text = text.replace(old_href, new_href)
                        content = text.encode("utf-8")
                    output_zip.writestr(info, content)
                for path, content in optimized_images.items():
                    output_zip.writestr(path, content, compress_type=zipfile.ZIP_STORED)

        os.replace(temp_path, output_path)
        print(f"EPUB optimisé : {output_path}")
        print(f"Scans convertis : {len(optimized_images)}")
        print(f"Images : {old_total / 1_000_000:.1f} Mo → {new_total / 1_000_000:.1f} Mo")
        print(f"EPUB : {os.path.getsize(input_path) / 1_000_000:.1f} Mo → {os.path.getsize(output_path) / 1_000_000:.1f} Mo")
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def main():
    parser = argparse.ArgumentParser(description="Compresser les scans d'un EPUB pour une liseuse noir et blanc")
    parser.add_argument("epub", help="EPUB source")
    parser.add_argument("output", nargs="?", help="EPUB optimisé (défaut : <nom>.kindle.epub)")
    parser.add_argument("--max-width", type=int, default=840, help="largeur maximale des scans (défaut : 840 px)")
    parser.add_argument("--max-height", type=int, default=1200, help="hauteur maximale des scans (défaut : 1200 px)")
    parser.add_argument("--quality", type=int, default=50, help="qualité JPEG, de 25 à 90 (défaut : 50)")
    args = parser.parse_args()
    if not os.path.isfile(args.epub):
        parser.error(f"fichier introuvable : {args.epub}")
    if args.max_width < 1 or args.max_height < 1:
        parser.error("les dimensions maximales doivent être positives")
    if not 25 <= args.quality <= 90:
        parser.error("--quality doit être compris entre 25 et 90")
    output = args.output or os.path.splitext(args.epub)[0] + ".kindle.epub"
    if os.path.abspath(output) == os.path.abspath(args.epub):
        parser.error("la sortie doit être distincte de l'EPUB source")
    optimize(args.epub, output, args.max_width, args.max_height, args.quality)


if __name__ == "__main__":
    main()
