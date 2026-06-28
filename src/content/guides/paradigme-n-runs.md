---
title: "Le paradigme N-runs : comment obtenir une mesure de visibilité IA fiable"
description: "Répéter chaque question, puis agréger les réponses en un taux avec sa marge d'erreur. C'est le principe du N-runs, et c'est ce qui sépare une vraie mesure d'un chiffre rassurant."
parent: "mesurer-visibilite-ia"
branch: "Le paradigme N-runs"
priority: coeur
order: 2
updated: "2026-06-28"
related:
  - un-seul-test-chatgpt-ne-suffit-pas
  - quelles-ia-suivre-marche-francais
  - questions-sans-marque-eviter-biais
lexical:
  - échantillonnage
  - distribution
  - intervalle de confiance
  - taux de citation
sources:
  - label: "Schulte et al., Don't Measure Once (arXiv:2604.07585)"
    url: "https://arxiv.org/abs/2604.07585"
  - label: "Intervalle de confiance d'une proportion (binomial), Wikipedia"
    url: "https://fr.wikipedia.org/wiki/Intervalle_de_confiance"
faq:
  - q: "Pourquoi dix répétitions et pas trois ou cent ?"
    a: "Plus on répète, plus la marge d'erreur se resserre, mais le gain diminue à chaque répétition pendant que le coût, lui, continue de monter. Dix répétitions par IA offrent un bon compromis pour la plupart des marques : la fourchette devient assez étroite pour décider, sans interroger les modèles à l'infini."
  - q: "Qu'est-ce qu'un intervalle de confiance, en clair ?"
    a: "C'est la fourchette dans laquelle se situe vraisemblablement votre vrai taux de citation, compte tenu du nombre de réponses observées. Un taux de 22 % à plus ou moins 3 points signifie que la réalité est très probablement entre 19 % et 25 %."
  - q: "Le N-runs coûte-t-il plus cher qu'un scan unique ?"
    a: "Oui, puisqu'on multiplie les appels aux IA. C'est pour cela que la répétition à dix est réservée aux offres payantes, et que le coût d'un scan est affiché avant lancement. Le surcoût achète une chose précise : une mesure sur laquelle vous pouvez réellement décider."
cta:
  titre: "Une mesure que vous pouvez défendre en réunion"
  texte: "sen-ai affiche votre taux de citation et sa marge d'erreur, par IA. Premier scan gratuit."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

Si une question posée une fois ne dit rien de fiable, la solution tient en une phrase. **La poser plusieurs fois, compter combien de fois votre marque apparaît, et transformer ce comptage en un taux avec sa marge d'erreur.** C'est le paradigme N-runs, et c'est la base de la mesure sen-ai.

## Compter sur une distribution, pas sur un tirage

**Au lieu d'une réponse, on en collecte N par question et par modèle, puis on mesure la fréquence à laquelle votre marque est citée.** Cette fréquence est votre taux de citation. Sur dix réponses où la marque apparaît trois fois, le taux est de 30 % pour cette question et ce modèle.

L'intérêt n'est pas le chiffre seul, mais la régularité avec laquelle on l'obtient. Un taux stable sur dix tirages est un signal solide. Un taux qui saute de 10 % à 50 % selon le tirage est un signal fragile, et il faut le savoir avant d'en tirer des conclusions.

## La marge d'erreur, pour distinguer le réel du bruit

**Chaque taux est publié avec un intervalle de confiance à 95 %, la mesure statistique standard de l'incertitude d'une proportion estimée sur un échantillon.** Plus le nombre de répétitions est grand, plus cet intervalle se resserre : la précision progresse à mesure qu'on accumule les réponses, d'abord vite, puis de plus en plus lentement.

C'est ce qui permet d'afficher un taux de 22 % comme « solide à plus ou moins 3 points » ou « bruité à plus ou moins 15 points ». La même valeur centrale ne se lit pas de la même façon selon la largeur de la fourchette. Sans cette marge, deux scans qui passent de 20 % à 24 % semblent montrer une progression ; avec elle, on voit souvent qu'ils sont indiscernables.

## Pourquoi dix par défaut

**sen-ai répète dix fois par IA sur les offres payantes, parce que c'est le point où la fourchette devient assez étroite pour décider sans faire exploser le coût.** Chaque répétition supplémentaire resserre l'intervalle un peu moins que la précédente, alors que le coût d'appel aux modèles, lui, reste constant à chaque ajout.

Avant de lancer un scan, vous voyez le nombre de questions multiplié par le nombre de répétitions, et le coût en crédits qui en découle. La mesure n'est jamais une boîte noire : vous savez ce que vous achetez, et pourquoi une mesure fiable coûte plus qu'un coup de sonde.

## Ce que le N-runs débloque ensuite

**Une fois la mesure stable, tout le reste devient possible : suivre une tendance dans le temps, comparer deux IA, et mesurer l'effet réel d'une action.** Sans marge d'erreur, on ne peut pas dire si un contenu publié a changé quoi que ce soit. Avec elle, on compare la fourchette avant et la fourchette après, et on conclut honnêtement.

C'est pour cette raison que la répétition n'est pas un détail technique mais le socle de la méthode. Mesurer une fois, c'est deviner. Mesurer N fois, c'est savoir avec quelle confiance.
