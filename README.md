# Conversion PDF vers EPUB

## Utilisation courante

Pour les PDF scannés ou composés d'images, comme Futur et Marianne, utiliser la
v3 avec `--images all` pour conserver les pages source avant le texte reconnu :

```bash
./venv/bin/python moulinette_pdf_scan_vers_epub.py --workers 10 --images all \
  "pdf/mon-document.pdf" "pdf/mon-document.epub"
```

La v3 place chaque scan avant sa transcription. Les scans sont réduits en niveaux
de gris pour limiter la taille de l'EPUB. `--images all` garde aussi les pages
sans transcription. `Ctrl+C` arrête les tâches OCR.

Pour les PDF qui contiennent déjà une couche texte, comme Le Nouvel Observateur,
utiliser `convert_pdf_avec_couche_texte.py`. Il convertit les PDF trouvés dans
le dossier courant : lance-le depuis un dossier qui ne contient que les PDF à
traiter.

## Avant de faire le ménage

Les dossiers `.parts` et `.qa` sont supprimables après vérification de l'EPUB
final : ils contiennent des EPUB intermédiaires et des rapports OCR, mais ne sont
pas nécessaires pour relire le fichier final.

Ne supprime pas les modules `moulinette_pdf_vers_epub_support.py` et
`moulinette_pdf_vers_epub_analyse.py` : la moulinette scan en dépend. Ne supprime
pas tout le dossier `pdf/`, qui contient les originaux et les EPUB finaux.

La liste des scripts, des dépendances et des dossiers à nettoyer est dans
[`GUIDE_TECHNIQUE.md`](GUIDE_TECHNIQUE.md).
