#!/usr/bin/env bash
set -eu

TEST_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(dirname -- "$TEST_DIR")
PYTHON="$ROOT_DIR/venv/bin/python"
PDF="$ROOT_DIR/pdf/FuturN53Octobre26.pdf"
SCRIPT="$ROOT_DIR/python/moulinette_pdf_vers_epub_support.py"

"$PYTHON" "$SCRIPT" --ocr --pages 1-8 --figures auto "$PDF" "$TEST_DIR/sample_pages_1-8.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 9-12 --figures auto "$PDF" "$TEST_DIR/sample_pages_9-12.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 9-12 --figures auto --fallback-images "$PDF" "$TEST_DIR/sample_pages_9-12_with_fallback.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 35-38 --figures auto "$PDF" "$TEST_DIR/sample_pages_35-38.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 35-38 --figures auto --fallback-images "$PDF" "$TEST_DIR/sample_pages_35-38_with_fallback.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 15-15 "$PDF" "$TEST_DIR/sample_printed_page_13.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 15-15 --figures 15 --fallback-images "$PDF" "$TEST_DIR/sample_printed_page_13_with_fallback.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 14-15 --fallback-images "$PDF" "$TEST_DIR/sample_printed_pages_12-13.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 13-13 "$PDF" "$TEST_DIR/sample_pdf_page_13.epub"
"$PYTHON" "$SCRIPT" --ocr --pages 23-23 --figures 23 "$PDF" "$TEST_DIR/sample_pdf_page_23_with_scan.epub"
