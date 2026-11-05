# Essais ciblés PDF vers EPUB

Les échantillons utilisent quelques plages d'un PDF de contrôle pour évaluer
des dispositions variées sans convertir toutes ses pages à chaque changement.
Les numéros sont les pages physiques du PDF, à partir de 1.

## Recréer les échantillons

```bash
bash tests/rebuild_samples.sh
```

Les EPUB produits couvrent notamment des pages avec plusieurs colonnes, des
zones de texte à tailles variées, des éléments décoratifs et des images de
secours. Les échantillons `sample_pdf_page_13.epub` et
`sample_pdf_page_23_with_scan.epub` permettent de comparer le texte reflowable
avec l'image source d'une page.

## Contrôles structurels et de non-régression

```bash
./venv/bin/python tests/check_regressions.py "document.pdf"
```

Le contrôle ne compare aucun passage textuel. Il vérifie que les mots restent
dans l'ordre géométrique des colonnes, que les pages d'essai produisent un texte
suffisant, que les paragraphes et titres gardent une structure valide, que les
liens de navigation pointent vers des titres existants et que le style EPUB
conserve les sauts de page.

On peut fournir un autre PDF de contrôle en argument. Il doit contenir au moins
25 pages et être lisible par Tesseract :

```bash
./venv/bin/python tests/check_regressions.py "autre-document.pdf"
```

### Non-régression de la v2 sur les pages difficiles

```bash
./venv/bin/python tests/check_v2_regressions.py "Futu&r N°53 - Octobre 2026.pdf"
```

Ce contrôle convertit uniquement les pages physiques 34, 51, 63, 64, 69 et 74 dans
un dossier temporaire. Il vérifie des seuils prudents de volume OCR, la
conservation des coordonnées et confiances dans le rapport JSON, les repères de
page discrets (« PDF p. N ») et leur lien vers le scan, les images source des
pages visuelles et la validité des documents EPUB.
Il ne compare pas le texte OCR mot à mot : les erreurs de reconnaissance peuvent
varier selon les versions de Tesseract, mais une chute marquée du contenu doit
faire échouer le contrôle.

Sur la version de Tesseract de l'environnement de développement, l'essai a
reconnu 734, 342, 445, 430, 533 et 243 mots sur ces pages, dans l'ordre indiqué.
Les six ont été signalées et leur rendu couleur conservé dans l'EPUB. La page 69
est un cas de non-régression de l'ordre : l'article en colonne gauche doit rester
groupé avant la citation latérale. Son repère discret « PDF p. 69 » ouvre le scan
dans l'EPUB. La page 74 reste la plus incertaine : 95 mots sont sous le seuil de
confiance de 45/100 ; son image source est donc particulièrement utile à relire.

## Traitement et limites

Les scans sont reconnus avec Tesseract en mode de segmentation adapté aux pages
qui mélangent colonnes et illustrations. Le script utilise les coordonnées OCR,
les espacements et la hauteur locale des caractères pour reconstruire l'ordre
de lecture. Quand une colonne présente beaucoup de mots à faible confiance,
elle est reconnue une seconde fois, seule, à plus haute résolution.

Les grands titres détectés en haut d'une page alimentent la navigation EPUB et
peuvent déclencher un saut de page. Les lignes rapprochées sont réunies ; les
blancs plus grands séparent les paragraphes. Ces décisions sont géométriques et
peuvent encore confondre un intertitre avec un titre de chapitre.

L'OCR reste moins fiable pour les lettrines, petits caractères, titres dessinés
et textes superposés à une illustration. Les pages de type « une de magazine »
(grande photo, logo, peu de texte) sont automatiquement affichées en image seule
quand le ratio de mots réellement lisibles est trop faible — évitant ainsi
l'inclusion de bruit OCR illisible. L'option `--fallback-images` aide à
relire les pages signalées, sans garantir que toutes les erreurs soient
détectées.

Le suivi des défauts examinés et des améliorations possibles se trouve dans
[`quality_status.md`](quality_status.md).
