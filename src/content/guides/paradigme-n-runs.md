---
title: "Le paradigme N-runs : comment obtenir une mesure de visibilité IA fiable"
description: "Répéter chaque question, puis agréger les réponses en un taux avec sa marge d'erreur. C'est le principe du N-runs, et c'est ce qui sépare une vraie mesure d'un chiffre rassurant."
parent: "mesurer-visibilite-ia"
branch: "Le paradigme N-runs"
priority: coeur
order: 2
updated: "2026-07-17"
related:
  - un-seul-test-chatgpt-ne-suffit-pas
  - quelles-ia-suivre-marche-francais
  - questions-sans-marque-eviter-biais
  - boucle-t14-mesurer-le-lift
lexical:
  - échantillonnage
  - distribution
  - intervalle de confiance
  - taux de citation
  - marge d'erreur
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

[Un test unique ne dit rien de fiable](/guides/un-seul-test-chatgpt-ne-suffit-pas/) ; la solution, elle, tient en une phrase. **Poser chaque question plusieurs fois, compter combien de fois votre marque apparaît, et transformer ce comptage en un taux avec sa marge d'erreur.** C'est le paradigme N-runs, celui que la recherche résume d'une formule : [ne mesurez pas une seule fois](https://arxiv.org/abs/2604.07585).

## Compter sur une distribution, pas sur un tirage

**Au lieu d'une réponse, on en collecte N par question et par modèle, puis on mesure la fréquence à laquelle votre marque est citée.** Chaque réponse est un tirage dans la distribution des réponses possibles du modèle : c'est un échantillonnage, et la fréquence observée est votre taux de citation.

Reprenons Dormalis, la marque fictive de matelas. « Quel matelas pour un mal de dos ? », posée dix fois à la même IA, la fait apparaître trois fois : 30 % de taux de citation. Un test unique aurait conclu présent ou absent, selon le tirage du jour.

| Un test unique | Une mesure N-runs |
|---|---|
| Une réponse, prise pour la vérité | N réponses, lues comme un échantillon |
| Cité ou pas cité, au hasard du tirage | Un taux de citation : 3 sur 10 = 30 % |
| Fiabilité inconnue | Marge d'erreur affichée à côté du taux |

Un taux stable sur dix tirages est un signal solide. Un taux qui saute de 10 % à 50 % selon le tirage est un signal fragile, et il faut le savoir avant d'en tirer des conclusions.

## La marge d'erreur, pour distinguer le réel du bruit

**Chaque taux est publié avec un [intervalle de confiance](https://fr.wikipedia.org/wiki/Intervalle_de_confiance) à 95 %, la mesure statistique standard de l'incertitude d'une proportion estimée sur un échantillon.** Plus le nombre de répétitions est grand, plus cet intervalle se resserre : la précision progresse à mesure qu'on accumule les réponses, d'abord vite, puis de plus en plus lentement.

C'est ce qui permet d'afficher un taux de 22 % comme « solide à plus ou moins 3 points » ou « bruité à plus ou moins 18 points ». Sans cette marge, deux scans qui passent de 20 % à 24 % semblent montrer une progression ; avec elle, on voit souvent qu'ils sont indiscernables.

À reprendre telle quelle en réunion :

> « Sur cette IA, notre taux de citation est de 22 %, à plus ou moins 3 points : la réalité se situe très probablement entre 19 % et 25 %. »

## Pourquoi dix par défaut

**Sur les offres payantes, sen-ai répète dix fois chaque question sur [chaque IA suivie pour le marché français](/guides/quelles-ia-suivre-marche-francais/), parce que c'est le point où la fourchette devient assez étroite pour décider sans faire exploser le coût.** Chaque répétition supplémentaire resserre l'intervalle un peu moins que la précédente, alors que le coût d'appel aux modèles, lui, reste constant à chaque ajout.

Avant de lancer un scan, vous voyez le nombre de questions multiplié par le nombre de répétitions, et le coût en crédits qui en découle. La mesure n'est jamais une boîte noire : vous savez ce que vous achetez, et pourquoi une mesure fiable coûte plus qu'un coup de sonde. Et pour ne pas mesurer un biais, chaque question posée [ne mentionne jamais votre marque](/guides/questions-sans-marque-eviter-biais/).

## Ce que le N-runs débloque ensuite

**Une fois la mesure stable, tout le reste devient possible : suivre une tendance dans le temps, comparer deux IA, et [mesurer l'effet réel d'une action après publication](/guides/boucle-t14-mesurer-le-lift/).** Sans marge d'erreur, on ne peut pas dire si un contenu publié a changé quoi que ce soit. Avec elle, on compare la fourchette avant et la fourchette après, et on conclut honnêtement.

C'est pour cette raison que la répétition n'est pas un détail technique, mais le socle de [la méthode de mesure](/guides/mesurer-visibilite-ia/). Mesurer une fois, c'est deviner. Mesurer N fois, c'est savoir avec quelle confiance.
