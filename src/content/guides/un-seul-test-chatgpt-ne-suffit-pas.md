---
title: "Tester sa marque une seule fois sur ChatGPT ne suffit pas"
description: "Une même question posée deux fois à une IA donne souvent deux listes de marques différentes. Voici pourquoi un seul test est du bruit, et combien il en faut pour une mesure fiable."
parent: "mesurer-visibilite-ia"
branch: "Un seul test ne suffit pas"
priority: coeur
order: 1
updated: "2026-06-28"
related:
  - paradigme-n-runs
  - quelles-ia-suivre-marche-francais
  - questions-sans-marque-eviter-biais
lexical:
  - non-déterminisme
  - variance par run
  - échantillonnage
  - marge d'erreur
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
    a: "Les modèles génératifs tirent leur réponse au sort à chaque appel, parmi les suites de mots les plus probables. Deux appels identiques peuvent donc produire deux listes de marques différentes, sans que rien n'ait changé chez vous ni chez vos concurrents."
  - q: "Un taux de citation peut-il bouger sans que j'aie rien fait ?"
    a: "Oui. Une partie des variations d'un scan à l'autre est du bruit statistique, pas un vrai mouvement. C'est précisément pour cela que sen-ai affiche une marge d'erreur : elle vous dit si une hausse ou une baisse est réelle ou si elle tient dans le hasard."
cta:
  titre: "Mesurez votre visibilité IA, marge d'erreur comprise"
  texte: "Votre premier scan est gratuit. Vous voyez votre taux de citation par IA, et s'il est solide ou bruité."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

Vous demandez à ChatGPT « quelle est la meilleure marque pour... », votre marque sort, vous notez le résultat. Le lendemain, même question, elle a disparu. **Un seul test ne mesure rien de fiable : il faut répéter la question pour lire un signal plutôt que du bruit.**

## Pourquoi la même question donne deux réponses différentes

**Les moteurs génératifs ne sont pas déterministes : à chaque appel, ils tirent leur réponse au sort parmi les suites de mots les plus probables.** Deux requêtes strictement identiques peuvent donc produire deux listes de marques différentes, dans un ordre différent, sans qu'aucune donnée n'ait bougé.

Ce n'est pas un défaut à corriger, c'est le fonctionnement normal de ces modèles. La conséquence pratique est simple : la photo que vous prenez en posant une question une fois ne représente pas votre vraie visibilité, mais un seul tirage parmi beaucoup de tirages possibles.

## La preuve chiffrée : moins d'une chance sur cent

**Une étude SparkToro de janvier 2026 a mesuré l'ampleur du phénomène : sur 2 961 réponses, il y a moins d'une chance sur cent que deux d'entre elles donnent la même liste de marques.** Fishkin et O'Donnell ont reposé les mêmes questions en boucle aux principaux assistants. Le même ordre de marques, lui, n'apparaît qu'environ une fois sur mille.

Autrement dit, si votre suivi de visibilité IA repose sur un scan unique, vous avez plus de quatre-vingt-dix-neuf chances sur cent de regarder une liste qui ne se reproduira pas. Toute décision prise sur cette base est une décision prise sur un coup de dé.

## La variance ne vient pas que du hasard d'un run

**Même en moyennant plusieurs réponses, le résultat bouge encore selon le jour et la formulation : il faut donc mesurer dans la durée, pas une fois pour toutes.** Les travaux récents sur l'incertitude de la visibilité IA montrent que la dispersion d'un modèle se quantifie, et qu'une mesure unique sous-estime toujours cette incertitude.

C'est la logique défendue par des chercheurs comme Sielinski (« Quantifying Uncertainty in AI Visibility ») et Schulte et ses coauteurs (« Don't Measure Once ») : une seule observation ne suffit pas à caractériser un système qui répond de façon variable. La bonne unité de mesure n'est pas le test, c'est l'échantillon.

## Ce que sen-ai fait à la place

**sen-ai ne pose jamais une question une seule fois : il la répète plusieurs fois par modèle, puis agrège les réponses en un taux assorti de sa marge d'erreur.** Par défaut, dix répétitions par IA sur les offres payantes. Le résultat affiché n'est pas un chiffre nu mais une fourchette.

Concrètement, un taux de citation de 22 % apparaît « solide à plus ou moins 3 points » ou « bruité à plus ou moins 15 points ». Vous savez alors si une variation entre deux scans est un vrai mouvement à exploiter ou un simple aléa à ignorer. C'est la condition pour suivre une tendance sans se raconter d'histoires, et pour mesurer ensuite l'effet réel d'une action de contenu.
