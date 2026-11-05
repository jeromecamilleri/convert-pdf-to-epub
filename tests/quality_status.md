# État de qualité OCR — 31 octobre 2026

## Périmètre examiné

Le document de contrôle contient 144 pages scannées. L'EPUB complet présent
dans le dossier a un seul flux de texte et ne conserve pas de frontière explicite
pour chaque page source. Cela complique les comparaisons après conversion.

Les défauts examinés sont concentrés sur des dispositions difficiles : titres
typographiques, lettrines, petites colonnes latérales et encarts à plusieurs
colonnes. Les pages 13–15, 23 et 25 du PDF de contrôle forment des cas ciblés de
non-régression. Les échantillons supplémentaires signalés par l'ancienne
heuristique d'image sont les pages 29, 65, 72 et 75.

Pour lancer les contrôles rapides :

```bash
./venv/bin/python tests/check_regressions.py
```

Ils vérifient la structure et la géométrie, sans comparer de phrases. Ils
prennent environ 20 secondes sur la machine utilisée pour ce projet.

## Constats

| Cas de mise en page | État observé | Piste générale |
| --- | --- | --- |
| Titre avec casse mixte | Le filtre conserve maintenant le titre et sa ponctuation. | Garder la détection fondée sur la casse et la forme des lignes, puis vérifier les titres longs ou inhabituels. |
| Article en colonnes avec un grand titre | Le corps suit l'ordre des colonnes ; les chiffres romains du titre peuvent être confondus. | Améliorer la reconnaissance locale des titres et conserver l'image source quand l'OCR reste incertain. |
| Petits encarts | Les blocs de texte sont présents, mais les nombres et libellés courts sont parfois confondus. | Prioriser les blocs selon leur géométrie et confiance OCR, sans dictionnaire propre au document. |
| Lettrine en marge et colonne latérale étroite | La reprise OCR locale nettoie la colonne latérale. La grande initiale n'est pas toujours reconnue. | Détecter une lettrine par taille et position, puis vérifier son image plutôt que deviner le caractère. |
| Titre composé d'illustrations | Le texte d'introduction se lit, mais le titre peut être découpé ou partiellement omis. | Prévoir un contrôle visuel des grandes formes typographiques. |
| Page physique 13, deux colonnes | Le filtre supprimait une ligne complète de cinq mots (« Évitant les impasses… »). `--figures auto` ajoutait aussi son scan, car quelques nombres dispersés suffisaient à classer cette page d'article comme figure. | Les lignes longues de quatre mots ou plus sont conservées dans les colonnes. La seconde passe OCR est retenue si couverture et confiance restent comparables. La détection automatique exige maintenant une densité numérique réelle ; les nombres isolés dans un article ne déclenchent plus l'ajout du scan. |

## Changements récents

### Novembre 2026 — détection des pages visuelles par ratio de mots lisibles

L'ancienne heuristique `image_only` combinait trois seuils AND : nombre de mots
minimum, confiance moyenne et proportion de mots sous 45. Elle ratait les pages
de type « une de magazine » où quelques tokens bien reconnus (logo, date) tiraient
la confiance moyenne vers le haut, même si la quasi-totalité du contenu était du
bruit.

La nouvelle logique pose une seule question : quelle fraction des mots reconnus
est réellement lisible ? Un mot est lisible s'il contient au moins deux lettres
alphabétiques **et** a une confiance ≥ 45. Si ce ratio tombe sous 15 % ou si
moins de 10 mots lisibles sont trouvés, la page est affichée en image seule sans
texte OCR. Les deux constantes `IMAGE_ONLY_LEGIBLE_RATIO` et
`IMAGE_ONLY_MIN_LEGIBLE_WORDS` dans `moulinette_pdf_vers_epub_analyse.py`
permettent de les ajuster.

Sur LesEchos du 5 novembre 2026 (32 pages scannées), seule la page 1 (une
photographique) a basculé en image seule — contre 0 avec l'ancienne logique qui
incluait son bruit dans le texte. Aucune page d'article n'a été affectée (ratio
lisible ≥ 87 % sur toutes les pages de contenu). Le rapport QA expose désormais
`legible_word_count` et `legible_ratio` pour diagnostiquer les cas limites.

### Avant novembre 2026 — reprise OCR par colonne

Les mots Tesseract incluent leur score de confiance. Quand une colonne contient
une part importante de mots peu fiables, le script ré-ocrise uniquement cette
colonne à 300 dpi. Cela améliore les colonnes étroites sans modifier le texte des
colonnes déjà reconnues avec une confiance élevée.

Les tests ciblés couvrent des pages avec titres, colonnes, encarts, lettrine et
petit texte latéral. Ils vérifient également que les changements gardent la
structure EPUB, ses liens de navigation et ses sauts de page.

Le contrôle de la page physique 13 est isolé pour vérifier son volume de texte
et empêcher que le filtre rejette à nouveau les lignes longues des colonnes.
Le contrôle OCR `--figures auto` sur cette seule page vérifie également qu'un
article riche en références numériques n'est pas ajouté comme figure image.

## Suite conseillée

1. Ajouter un rapport de qualité par page avec le nombre de mots, le taux de
   confiance OCR, les lignes écartées et la présence d'une image de secours.
2. Examiner les pages déjà signalées par les métriques et ajouter des cas de
   non-régression uniquement lorsqu'une règle géométrique générale est trouvée.
3. Conserver les contrôles courts pour chaque modification, puis lancer une
   conversion complète avant de remplacer le document EPUB final.

Les contrôles ne remplacent pas une relecture humaine exhaustive des 144 pages.
Les principaux risques restants concernent les lettrines, petits titres, chiffres
isolés et textes fusionnés avec des illustrations.

## Règle de maintenance

Toute amélioration doit préserver les cas déjà convertis correctement. Pour
gagner du temps, chaque correction doit être couverte par un contrôle de
non-régression ciblé sur la page ou la géométrie concernée, ainsi que sur le cas
existant le plus susceptible d'être affecté. Éviter les changements généraux
sans contrôle des colonnes, titres et paragraphes qu'ils peuvent déplacer ou
supprimer.
