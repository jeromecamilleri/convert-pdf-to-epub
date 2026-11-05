#!/usr/bin/env python3
"""Page-level non-regression checks for the v2 hybrid OCR pipeline."""

import json
import os
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from python.moulinette_pdf_vers_epub_analyse import convert_v2  # noqa: E402


# Conservative floors from pages previously reported as incomplete, plus the
# complex two-column/sidebar page shown in the latest reading-order defect.
# They check coverage shape, not exact recognized wording.
PAGE_MIN_WORDS = {34: 550, 51: 260, 63: 330, 64: 320, 69: 350, 74: 180}
PAGES = set(PAGE_MIN_WORDS)


def check_target_pages(pdf_path):
    with tempfile.TemporaryDirectory(prefix="epub-v2-regression-") as temp_dir:
        output = os.path.join(temp_dir, "targeted.epub")
        convert_v2(
            pdf_path, output, image_mode="flagged",
            selected_pages={number - 1 for number in PAGES},
        )
        report_path = os.path.join(temp_dir, "targeted.qa.json")
        with open(report_path, encoding="utf-8") as stream:
            report = json.load(stream)

        records = {page["page"]: page for page in report["pages"]}
        assert set(records) == PAGES, "Le rapport doit contenir exactement les pages ciblées."
        for number, minimum in PAGE_MIN_WORDS.items():
            page = records[number]
            assert page["detected_word_count"] >= minimum, (
                f"Page {number}: OCR détecté trop faible ({page['detected_word_count']} mots)."
            )
            assert page["retained_word_count"] >= minimum, (
                f"Page {number}: texte conservé trop faible ({page['retained_word_count']} mots)."
            )
            assert page["excluded_lines"] == 0, (
                f"Page {number}: la v2 ne doit pas filtrer des lignes OCR."
            )
            assert 0 <= page["low_confidence_ratio"] <= 1
            assert page["regions"], f"Page {number}: les régions OCR sont absentes du rapport."
            for region in page["regions"]:
                assert region["bbox"] and region["words"]
                assert all(len(word) == 7 for word in region["words"]), (
                    "Chaque mot du rapport doit garder boîte, texte, clé de ligne et confiance."
                )

        # These two layouts contain substantial visual material and must retain
        # a source-page rendering in addition to reflowable text.
        assert records[51]["original_page_image_embedded"]
        assert records[74]["original_page_image_embedded"]
        page69 = records[69]
        region_text = [
            " ".join(word[4] for word in region["words"])
            for region in page69["regions"]
        ]
        story_regions = [
            i for i, text in enumerate(region_text)
            if "Mais voilà" in text and "Sydney Goggins" in text
        ]
        quote_regions = [i for i, text in enumerate(region_text)
                         if "récit partagé" in text and "Patrick" in text]
        assert story_regions, (
            "La colonne de l'article page 69 doit rester un bloc suivi, sans insertion de la citation."
        )
        assert quote_regions and min(story_regions) < min(quote_regions), (
            "La citation latérale doit rester après le corps principal dans l'ordre EPUB."
        )
        assert all(records[n]["preview"] for n in PAGES), (
            "Les pages ciblées doivent apparaître dans le contrôle visuel."
        )

        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            xhtml_name = "EPUB/texte.xhtml"
            assert xhtml_name in names
            xhtml = archive.read(xhtml_name).decode("utf-8")
            ET.fromstring(archive.read(xhtml_name))
            for number in PAGES:
                assert f'id="source-page-{number}"' in xhtml
                assert f'id="pdf-page-{number}"' in xhtml
                assert f"PDF p. {number}" in xhtml
            assert 'href="#source-image-69"' in xhtml, (
                "Le repère discret de la page 69 doit mener à son scan dans l'EPUB."
            )
            image_names = [name for name in names if name.startswith("EPUB/images/page-")]
            assert len(image_names) >= 2, "Les pages visuelles doivent être incluses dans l'EPUB."
            for name in names:
                if name.endswith((".xhtml", ".opf", ".ncx")):
                    ET.fromstring(archive.read(name))

        print("V2 pages ciblées : " + ", ".join(map(str, sorted(PAGES))))
        for number in sorted(PAGES):
            page = records[number]
            print(
                f"  {number}: {page['retained_word_count']} mots, "
                f"{page['low_confidence_word_count']} confiance faible, "
                f"{len(page['regions'])} régions, image={'oui' if page['original_page_image_embedded'] else 'non'}"
            )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage : python tests/check_v2_regressions.py document.pdf")
    pdf_path = os.path.abspath(sys.argv[1])
    if not os.path.isfile(pdf_path):
        raise SystemExit(f"PDF de contrôle introuvable : {pdf_path}")
    check_target_pages(pdf_path)
    print("Contrôles de non-régression v2 réussis.")


if __name__ == "__main__":
    main()
