---
title: "Comment sen-ai mesure votre visibilité dans les IA génératives"
description: "ChatGPT, Gemini, Claude et Mistral citent certaines marques et pas d'autres. Voici comment sen-ai mesure cette visibilité, de façon reproductible et sourcée."
branch: "Méthodologie"
priority: coeur
order: 1
updated: "2026-06-28"
cta:
  titre: "Voyez ce que les IA disent de votre marque"
  texte: "Votre premier scan est gratuit, sans carte bancaire. Vous voyez quand votre marque est citée, quand elle ne l'est pas, et qui est recommandé à votre place."
  label: "Lancer mon scan gratuit"
  href: "/register"
sources:
  - label: "Google, AI Mode et la technique du query fan-out (Google I/O 2025)"
    url: "https://blog.google/products-and-platforms/products/search/google-search-ai-mode-update/"
  - label: "SparkToro, AIs are highly inconsistent when recommending brands (Fishkin et O'Donnell, 2026)"
    url: "https://sparktoro.com/blog/new-research-ais-are-highly-inconsistent-when-recommending-brands-or-products-marketers-should-take-care-when-tracking-ai-visibility/"
  - label: "Aggarwal et al., GEO: Generative Engine Optimization, KDD 2024"
    url: "https://arxiv.org/abs/2311.09735"
faq:
  - q: "Combien de fois faut-il interroger une IA pour une mesure fiable ?"
    a: "Une seule fois ne suffit pas. Les réponses varient d'un run à l'autre. sen-ai répète chaque question plusieurs fois par modèle (10 par défaut sur les offres payantes) et publie la mesure avec sa marge d'erreur, pour distinguer un taux solide d'un taux bruité."
  - q: "sen-ai entraîne-t-il ou influence-t-il les modèles d'IA ?"
    a: "Non. sen-ai interroge les API en lecture seule et analyse les réponses. Aucun entraînement, aucun fine-tuning, aucune réinjection. C'est une application classée à risque limité au sens de l'AI Act européen."
  - q: "Quelles IA sen-ai suit-il ?"
    a: "ChatGPT (OpenAI), Gemini (Google), Claude (Anthropic) et Mistral. Le mix évolue quand un acteur dépasse un seuil d'adoption, et il est publié sur chaque rapport pour que les comparaisons restent reproductibles."
---

Quand un client demande à ChatGPT « quelle crème pour peau sensible ? », la réponse cite certaines marques et en ignore d'autres. **Ce classement est le nouveau référencement.** sen-ai le mesure, de façon reproductible et sourcée. Voici la méthode.

## Un seul test ne veut rien dire

**Interroger une IA une seule fois sur votre marque, c'est lire du bruit.** Les réponses des moteurs génératifs ne sont pas déterministes : la même question, posée deux fois, peut produire deux listes de marques différentes.

Ce n'est pas une impression. Une étude SparkToro de 2026 (Fishkin et O'Donnell, 2 961 runs) a mesuré qu'il y a moins d'une chance sur cent que deux réponses à la même question donnent la même liste de marques, et environ une sur mille pour le même ordre. Tout outil qui juge votre visibilité IA sur un seul scan se trompe.

## Mesurer une distribution, pas un score

**sen-ai répète chaque question plusieurs fois, par modèle, puis agrège.** Par défaut, dix fois par IA sur les offres payantes. Le résultat n'est pas un chiffre unique mais une distribution, publiée avec sa marge d'erreur.

Concrètement, un taux de citation de 22 % est affiché « solide à plus ou moins 3 points » ou « bruité à plus ou moins 18 points ». Vous savez si une variation est un vrai mouvement ou du hasard. C'est la condition pour suivre une tendance dans le temps sans se raconter d'histoires.

## L'IA ne lit pas une page, elle décompose votre question

**Pour répondre, un moteur génératif éclate votre question en plusieurs sous-questions et lance de nombreuses recherches en parallèle.** Google appelle cette technique le « query fan-out » et la décrit dans sa documentation produit comme dans un brevet déposé.

Cela change la façon de penser le contenu. Être cité ne dépend pas d'une page magique, mais de couvrir l'ensemble des sous-intentions d'un sujet, avec des passages clairs et autonomes que l'IA peut extraire. C'est exactement la logique d'un cocon sémantique.

## Comment sen-ai collecte, traite et analyse

La mesure suit trois étapes, sans intervention manuelle.

- **Collecte.** À partir des mots-clés sur lesquels votre site se positionne, sen-ai génère des personas puis des questions formulées comme un vrai acheteur, sans citer votre marque, pour ne pas biaiser l'IA. Chaque question est posée aux quatre modèles, N fois.
- **Traitement.** Chaque réponse est analysée pour en extraire les citations (les URL réellement citées par l'IA) et les entités (marques, produits, gammes, sources), avec leur sentiment et leur position.
- **Analyse.** sen-ai en tire vos indicateurs : taux de citation de la marque, position moyenne, part de voix face aux concurrents, et la marge d'erreur associée.

Sur ces fondations, le produit propose des actions priorisées (contenu, autorité, présence média) et mesure leur effet après publication.

## Une optimisation qui dépend du sujet

**Il n'existe pas de recette universelle.** Le travail académique de référence sur le sujet (Aggarwal et al., GEO, KDD 2024) montre que l'efficacité des optimisations de contenu varie selon le domaine. Statistiques, citations de sources, citations d'experts aident dans bien des cas, mais le bon levier dépend de votre secteur et de la question posée. sen-ai mesure, vous n'optimisez pas à l'aveugle.
