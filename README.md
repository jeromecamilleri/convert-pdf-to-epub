# Conversion PDF vers EPUB

## Utilisation courante

Pour un PDF scanné ou composé d'images, utiliser la moulinette scan :

```bash
./venv/bin/python moulinette_pdf_scan_vers_epub.py --workers 10 --images all \
  "pdf/mon-document.pdf" "pdf/mon-document.epub"
```

Avec `--images all`, chaque page du PDF est placée avant sa transcription OCR ;
les pages sans texte reconnu restent présentes sous forme d'image seule. Les
scans sont enregistrés en niveaux de gris JPEG, à 110 dpi et qualité 50 par
défaut, pour limiter la taille de l'EPUB. `Ctrl+C` arrête les tâches OCR.

Pour les PDF qui contiennent déjà une couche texte, utiliser
`convert_pdf_avec_couche_texte.py`. Il convertit les PDF trouvés dans le dossier
courant : lance-le depuis un dossier qui ne contient que les PDF à traiter.

## Avant de faire le ménage

Les dossiers `.parts` et `.qa` sont supprimables après vérification de l'EPUB
final : ils contiennent des EPUB intermédiaires et des rapports OCR, mais ne sont
pas nécessaires pour relire le fichier final.

Ne supprime pas les modules `moulinette_pdf_vers_epub_support.py` et
`moulinette_pdf_vers_epub_analyse.py` : la moulinette scan en dépend. Ne supprime
pas tout le dossier `pdf/`, qui contient les originaux et les EPUB finaux.

La liste des scripts, des dépendances et des dossiers à nettoyer est dans
[`GUIDE_TECHNIQUE.md`](GUIDE_TECHNIQUE.md).
