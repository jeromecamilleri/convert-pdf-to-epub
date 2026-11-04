# Conversion PDF vers EPUB

## Utilisation courante

Pour convertir un seul PDF, indique son chemin :

```bash
python /chemin/vers/epub/convert_pdf_avec_couche_texte.py pdf/LesSourcesBDHelo.pdf
```

Pour convertir tous les PDF du dossier courant, omets le chemin :

```bash
python /chemin/vers/epub/convert_pdf_avec_couche_texte.py --workers 10
```

Il choisit automatiquement le traitement selon le contenu. Les PDF avec une
couche texte sont convertis directement. Les PDF scannés sont transmis à la
moulinette scan ; chaque page est alors placée avant sa transcription OCR, et
les pages sans texte reconnu restent présentes sous forme d'image seule. Les
scans sont en niveaux de gris JPEG à 110 dpi et qualité 50 par défaut. `Ctrl+C`
arrête les tâches OCR.

Pour les PDF avec couche texte, les lignes proches sont regroupées en
paragraphes et les mots coupés en fin de ligne sont recollés.
Le sommaire EPUB contient une entrée par page PDF pour permettre d'avancer par
page depuis les commandes de navigation Kindle ; les scans conservent aussi les
titres reconnus comme sous-entrées.

L'EPUB est créé à côté du PDF source. Sans chemin en argument, le script traite
tous les fichiers `*.pdf` du dossier courant.

## Avant de faire le ménage

Les dossiers `.parts` et `.qa` sont supprimables après vérification de l'EPUB
final : ils contiennent des EPUB intermédiaires et des rapports OCR, mais ne sont
pas nécessaires pour relire le fichier final.

Ne supprime pas le dossier `python/` : il contient les modules internes dont la
moulinette scan dépend. Ne supprime pas tout le dossier `pdf/`, qui contient les
originaux et les EPUB finaux.

La liste des dépendances et des fichiers internes est dans
[`GUIDE_TECHNIQUE.md`](GUIDE_TECHNIQUE.md).
