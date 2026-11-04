# Guide technique et rangement du dossier

## Scripts

### Flux actuellement prévu

- `convert_pdf_avec_couche_texte.py` : point d'entrée courant. Avec un chemin
  PDF en argument, il convertit ce fichier seul ; sans argument, il traite les
  PDF du dossier courant. Il détecte la couche texte et choisit le convertisseur
  adapté.
- `python/moulinette_pdf_scan_vers_epub.py` : moteur spécialisé appelé pour les
  PDF scannés. Il analyse les pages, découpe le travail en lots et les fusionne
  avec tous les scans avant leur transcription. Le sommaire expose chaque page
  PDF et conserve les titres reconnus comme sous-entrées.

### Fichiers liés à la v3

La moulinette scan importe des fonctions de
`python/moulinette_pdf_vers_epub_analyse.py`, qui importe à son tour
`python/moulinette_pdf_vers_epub_support.py`. `python/optimiser_epub_kindle.py`
est un outil optionnel pour compresser un EPUB déjà créé. Garde le dossier
`python/` avec le convertisseur à la racine ; ces fichiers ne sont pas des
commandes à lancer dans le flux automatique.

Autres scripts présents :

- `archive_convert_nouvelobs.py` : ancien script dédié à un numéro précis du
  Nouvel Observateur ; ne pas utiliser pour les nouveaux PDF.
- `convert_pdf_avec_couche_texte.py` : script actif pour les PDF avec texte et
  point d'entrée automatique pour les scans ; accepte un PDF en argument ou
  traite le dossier courant sans argument.
- `intercaler_pages_pdf.py` : ancien utilitaire de post-traitement ; la
  moulinette scan actuelle assemble déjà les pages elle-même.

## Dépendances

Le virtualenv `venv/` contient les dépendances Python utilisées : `PyMuPDF`
(`pymupdf`) et `EbookLib` (`ebooklib`). Le conserver évite de recréer
l'environnement.

À partir d'un environnement neuf, installer les modules listés à la racine :

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

Pour les scans, installer Tesseract et ses langues française et anglaise :

```bash
sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
```

Le vieux `convert1.py`, supprimé du dossier, n'est pas nécessaire.
`archive_convert_nouvelobs.py` est autonome et n'est pas requis par les flux
actuels.

## Nettoyage prudent

### À garder

- `pdf/*.pdf` : sources originales. Ne pas supprimer le dossier `pdf/` en bloc.
- Les EPUB finaux que tu souhaites conserver, dans le dossier courant ou `pdf/`.
- `venv/`, `convert_pdf_avec_couche_texte.py` et le dossier `python/`.
- `tests/` si tu veux garder les scripts et échantillons de contrôle.

### Résultats intermédiaires actuels

Oui. Les dossiers `.parts` et `.qa` sont des sorties générées. Après vérification
de l'EPUB final, ils peuvent être supprimés sans modifier les PDF sources ni
l'EPUB final. Leur suppression enlève toutefois les EPUB temporaires, les
aperçus et les rapports détaillés utiles pour diagnostiquer une erreur OCR.
Les dossiers présents actuellement sont :

```text
nouvelobs23.parts/
v3.parts/
v3-pages.parts/
pdf/Marianne29Octobre26.parts/
v2.qa/
```

Avant suppression, vérifier leur taille et leur contenu :

```bash
du -sh nouvelobs23.parts v3.parts v3-pages.parts \
  pdf/Marianne29Octobre26.parts v2.qa
```

Puis, si tu confirmes que les EPUB finaux sont bons et que tu n'as plus besoin
des contrôles, supprimer uniquement ces dossiers :

```bash
rm -r nouvelobs23.parts v3.parts v3-pages.parts \
  pdf/Marianne29Octobre26.parts v2.qa
```

`__pycache__/` et `tests/__pycache__/` sont des caches Python recréés
automatiquement. Ils peuvent être effacés à tout moment. Les fichiers
`tests/sample_*.epub` sont des échantillons générés pour les contrôles ; garde-les
si tu relances les scripts de test.

### EPUB multiples

Le dossier contient plusieurs versions des mêmes conversions : par exemple des
fichiers `v2.epub`, `v3.epub`, `v3-kindle.epub`, `FuturN53.epub` et des EPUB dans
`pdf/`. Choisis celui que tu veux garder pour chaque titre, vérifie-le sur la
liseuse, puis supprime manuellement les anciennes copies. Ne supprime pas un EPUB
uniquement parce qu'il est dans `pdf/` : certains sont les résultats finaux.

Pour examiner les plus gros fichiers avant de décider :

```bash
du -ah . | sort -h | tail -30
```

## Détails des sorties v3

- Le fichier demandé à la commande est l'EPUB fusionné, par exemple
  `FuturN53.epub`.
- Le dossier `<nom-de-sortie>.parts/` contient les EPUB temporaires et les
  rapports `.qa.json` / `.qa/` de chaque lot.
- `--workers` règle le nombre de lots traités simultanément (1 à 10).
- `--images all` ajoute chaque scan PDF avant son texte, y compris les pages
  sans transcription OCR. Les scans sont en niveaux de gris et en JPEG pour
  limiter la taille sur une liseuse noir et blanc.
- `Ctrl+C` arrête les processus Tesseract actifs. Après une interruption, une
  nouvelle exécution recommence l'analyse OCR depuis le début.
