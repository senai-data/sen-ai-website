---
title: "Tester sa marque une seule fois sur ChatGPT ne suffit pas"
description: "Une même question posée deux fois à une IA donne souvent deux listes de marques différentes. Voici pourquoi un seul test est du bruit, et combien il en faut pour une mesure fiable."
parent: "mesurer-visibilite-ia"
branch: "Un seul test ne suffit pas"
priority: coeur
order: 1
updated: "2026-07-17"
related:
  - paradigme-n-runs
  - quelles-ia-suivre-marche-francais
  - questions-sans-marque-eviter-biais
  - boucle-t14-mesurer-le-lift
lexical:
  - non-déterminisme
  - variance par run
  - échantillonnage
  - marge d'erreur
  - taux de citation
sources:
  - label: "SparkToro, AIs are highly inconsistent when recommending brands (Fishkin et O'Donnell, 27 janvier 2026)"
    url: "https://sparktoro.com/blog/new-research-ais-are-highly-inconsistent-when-recommending-brands-or-products-marketers-should-take-care-when-tracking-ai-visibility/"
  - label: "Sielinski, Quantifying Uncertainty in AI Visibility (arXiv:2603.08924)"
    url: "https://arxiv.org/abs/2603.08924"
  - label: "Schulte et al., Don't Measure Once (arXiv:2604.07585)"
    url: "https://arxiv.org/abs/2604.07585"
faq:
  - q: "Combien de fois faut-il poser la même question à une IA ?"
    a: "Au moins une dizaine de fois par modèle pour une marque suivie sérieusement. En dessous, la marge d'erreur est trop large pour distinguer un vrai taux de citation d'un coup de chance. sen-ai répète chaque question dix fois par IA par défaut sur les offres payantes."
  - q: "Pourquoi une IA ne répond-elle pas toujours pareil ?"
    a: "Les moteurs génératifs tirent leur réponse au sort à chaque appel, parmi les suites de mots les plus probables. Deux appels identiques peuvent donc produire deux listes de marques différentes, sans que rien n'ait changé chez vous ni chez vos concurrents."
  - q: "Un taux de citation peut-il bouger sans que j'aie rien fait ?"
    a: "Oui. Une partie des variations d'un scan à l'autre est du bruit statistique, pas un vrai mouvement. C'est précisément pour cela que sen-ai affiche une marge d'erreur : elle vous dit si une hausse ou une baisse est réelle ou si elle tient dans le hasard."
cta:
  titre: "Mesurez votre visibilité IA, marge d'erreur comprise"
  texte: "Votre premier scan est gratuit. Vous voyez votre taux de citation par IA, et s'il est solide ou bruité."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

Vous demandez à ChatGPT « quelle est la meilleure marque pour... », votre marque sort, vous notez le résultat. Le lendemain, même question, elle a disparu. **Un seul test ne mesure rien de fiable : il faut répéter la question pour lire un signal plutôt que du bruit.** C'est le point de départ pour [mesurer sa présence dans les IA](/guides/mesurer-visibilite-ia/).

## Pourquoi la même question donne deux réponses différentes

**Les moteurs génératifs ne sont pas déterministes : à chaque appel, ils tirent leur réponse au sort parmi les suites de mots les plus probables.** Deux requêtes strictement identiques peuvent donc produire deux listes de marques différentes, dans un ordre différent, sans qu'aucune donnée n'ait bougé. Ce non-déterminisme n'est pas un défaut, c'est le fonctionnement normal de ces modèles.

Prenez une marque fictive de matelas, Dormalis. Lundi, sa fondatrice demande à ChatGPT quel matelas choisir quand on dort à deux : Dormalis sort en deuxième position. Mardi, un investisseur pose la même question : Dormalis a disparu. Rien n'a changé ; chacun a observé un tirage différent du même modèle.

## La preuve chiffrée : moins d'une chance sur cent

**Une [étude SparkToro de janvier 2026](https://sparktoro.com/blog/new-research-ais-are-highly-inconsistent-when-recommending-brands-or-products-marketers-should-take-care-when-tracking-ai-visibility/) a mesuré l'ampleur du phénomène : sur 2 961 réponses, il y a moins d'une chance sur cent que deux d'entre elles donnent la même liste de marques.** Fishkin et O'Donnell ont reposé les mêmes questions en boucle aux principales IA. Le même ordre de marques, lui, n'apparaît qu'environ une fois sur mille.

Un suivi de visibilité IA fondé sur un scan unique regarde donc, plus de quatre-vingt-dix-neuf fois sur cent, une liste qui ne se reproduira pas. Décider sur cette base, c'est décider sur un coup de dé.

## La variance ne vient pas que du hasard d'un run

**Même en moyennant plusieurs réponses, le résultat bouge encore selon le jour et la formulation : il faut mesurer dans la durée, pas une fois pour toutes.** La variance par run n'est que la première couche ; une mesure unique sous-estime toujours la dispersion réelle d'un modèle.

C'est la logique défendue par des chercheurs comme Sielinski ([« Quantifying Uncertainty in AI Visibility »](https://arxiv.org/abs/2603.08924)) et Schulte et ses coauteurs ([« Don't Measure Once »](https://arxiv.org/abs/2604.07585)) : une seule observation ne suffit pas à caractériser un système qui répond de façon variable. La bonne unité de mesure n'est pas le test, c'est l'échantillon.

## Ce qu'un test unique vous fait croire

| Ce que le test unique fait croire | Ce qui se passe vraiment |
|---|---|
| « ChatGPT nous cite, c'est acquis » | Un tirage favorable ; le suivant peut vous omettre |
| « Nous avons disparu » | Bruit statistique possible ; seule la répétition tranche |
| « Le concurrent est passé devant » | Sans marge d'erreur, impossible de dire si l'écart est réel |

**La règle avant de réagir à une capture d'écran :**

> La question a-t-elle été posée une dizaine de fois, sur plusieurs jours, sans nommer la marque, marge d'erreur à l'appui ? Sinon, c'est une anecdote, pas une mesure.

## Ce que sen-ai fait à la place

**sen-ai ne pose jamais une question une seule fois : il applique [le paradigme N-runs](/guides/paradigme-n-runs/), qui répète chaque question plusieurs fois par modèle puis agrège les réponses en un taux assorti de sa marge d'erreur.** Par défaut, dix répétitions par IA sur les offres payantes.

Cet échantillonnage porte sur [les modèles que vos clients utilisent vraiment en France](/guides/quelles-ia-suivre-marche-francais/), à partir de [questions qui décrivent une situation d'achat sans jamais nommer votre marque](/guides/questions-sans-marque-eviter-biais/).

Sur votre rapport, un taux de citation de 22 % apparaît « solide à plus ou moins 3 points » ou « bruité à plus ou moins 18 points ». Vous savez alors si une variation entre deux scans est un vrai mouvement ou un simple aléa. De quoi suivre une tendance sans se raconter d'histoires, puis [mesurer l'effet réel d'un contenu publié](/guides/boucle-t14-mesurer-le-lift/).
