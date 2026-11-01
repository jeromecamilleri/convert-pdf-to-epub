import os
import pathlib
import subprocess
import pymupdf
from ebooklib import epub


def has_text_layer(pdf_path):
  """Vérifie si le PDF possède une couche de texte exploitable."""
  doc = pymupdf.open(pdf_path)
  for page in doc:
    if page.get_text().strip():
      doc.close()
      return True
  doc.close()
  return False


# Récupération du répertoire courant
current_dir = pathlib.Path.cwd()
pdf_files = list(current_dir.glob("*.pdf"))

if not pdf_files:
  print("Aucun fichier PDF trouvé dans le répertoire courant.")
  exit(0)

print(f"Trouvé {len(pdf_files)} fichier(s) PDF à convertir.\n")

for pdf_file in pdf_files:
  # Ignore les fichiers temporaires générés par l'OCR
  if pdf_file.name.endswith("_ocr.pdf"):
    continue

  epub_file = pdf_file.with_suffix(".epub")
  target_pdf = pdf_file

  # Étape 1 : Vérification de la couche texte et OCR automatique si besoin
  if not has_text_layer(pdf_file):
    print(f"-> {pdf_file.name} : Aucun texte détecté (PDF scanné).")
    ocrmypdf_available = (
        subprocess.run(["which", "ocrmypdf"], capture_output=True).returncode
        == 0
    )

    if ocrmypdf_available:
      ocr_pdf = current_dir / f"{pdf_file.stem}_ocr.pdf"
      print(f"   Lancement automatique de l'OCR (ocrmypdf en français)...")
      try:
        subprocess.run(
            ["ocrmypdf", "-l", "fra", str(pdf_file), str(ocr_pdf)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        target_pdf = ocr_pdf
        print(f"   OCR réussi, passage au traitement...")
      except subprocess.CalledProcessError:
        print(f"   [ERREUR] Échec de l'OCR pour {pdf_file.name}. Fichier ignoré.")
        continue
    else:
      print(
          "   [AVERTISSEMENT] L'outil 'ocrmypdf' n'est pas installé sur le"
          " système."
      )
      print(f"   [IGNORÉ] {pdf_file.name}\n")
      continue
  else:
    print(f"-> Traitement de : {pdf_file.name}")

  # Étape 2 : Ouverture et création du livre EPUB
  doc = pymupdf.open(target_pdf)
  book = epub.EpubBook()
  book.set_identifier(f"converted-{pdf_file.stem}")
  book.set_title(pdf_file.stem)
  book.set_language("fr")

  chapters = []

  for page_num, page in enumerate(doc):
    # Rendu de la page PDF en image (PNG) pour la conserver comme référence visuelle
    pix = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    img_filename = f"images/page_{page_num + 1}.png"

    img_item = epub.EpubItem(
        uid=f"img_page_{page_num + 1}",
        file_name=img_filename,
        media_type="image/png",
        content=img_bytes,
    )
    book.add_item(img_item)

    # Analyse géométrique et tri multi-colonnes du texte
    rect = page.rect
    page_width = rect.width
    mid_x = page_width / 2

    blocks = page.get_text("blocks")
    text_blocks = [b for b in blocks if len(b) > 6 and b[6] == 0 and b[4].strip()]

    full_width = []
    left_col = []
    right_col = []

    for b in text_blocks:
      x0, y0, x1, y1, text, bno, btype = b
      width = x1 - x0
      center_x = (x0 + x1) / 2

      if width > page_width * 0.6:
        full_width.append(b)
      elif center_x < mid_x:
        left_col.append(b)
      else:
        right_col.append(b)

    full_width.sort(key=lambda b: b[1])
    left_col.sort(key=lambda b: b[1])
    right_col.sort(key=lambda b: b[1])

    top_titles = [b for b in full_width if b[1] < rect.height * 0.3]
    bottom_elements = [b for b in full_width if b[1] >= rect.height * 0.3]

    ordered_blocks = top_titles + left_col + right_col + bottom_elements

    # Construction du contenu : Image de la page d'origine en premier, suivie du texte
    page_content = [
        f'<div style="text-align: center; margin-bottom: 20px;"><img'
        f' src="{img_filename}" alt="Page originale {page_num + 1}"'
        ' style="max-width: 100%; height: auto; border: 1px solid #ccc;"'
        " /></div><hr style='margin: 20px 0;'>"
    ]

    for b in ordered_blocks:
      text = b[4].strip()
      cleaned = " ".join(text.splitlines())
      page_content.append(f"<p>{cleaned}</p>")

    chapter = epub.EpubHtml(
        title=f"Page {page_num + 1}",
        file_name=f"page_{page_num + 1}.xhtml",
        lang="fr",
    )
    chapter.content = (
        f"<h2>Page {page_num + 1} (Référence & Texte)</h2>"
        + "".join(page_content)
    )
    book.add_item(chapter)
    chapters.append(chapter)

  if not chapters:
    print(
        f"  [AVERTISSEMENT] Aucun contenu textuel extrait pour"
        f" {pdf_file.name}. Ignoré.\n"
    )
    continue

  book.add_item(epub.EpubNcx())
  book.add_item(epub.EpubNav())
  book.spine = ["nav"] + chapters

  epub.write_epub(str(epub_file), book)
  print(f"  [SUCCÈS] EPUB généré avec images de référence : {epub_file.name}\n")

print("Toutes les conversions sont terminées !")
