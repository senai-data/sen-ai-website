---
title: "Tester sans guider l'IA : pourquoi les questions ne citent jamais votre marque"
description: "Si vous nommez votre marque dans la question, vous soufflez la réponse à l'IA. Une mesure honnête part de questions qui décrivent une situation d'achat, pas une marque."
parent: "mesurer-visibilite-ia"
branch: "Des questions sans biais"
priority: ext1
order: 4
updated: "2026-07-17"
related:
  - un-seul-test-chatgpt-ne-suffit-pas
  - paradigme-n-runs
  - quelles-ia-suivre-marche-francais
  - query-fan-out
lexical:
  - questions brand-agnostic
  - personas
  - intention d'achat
  - biais de mesure
  - question neutre
sources:
  - label: "Google, AI Mode et la technique du query fan-out (Google I/O 2025)"
    url: "https://blog.google/products-and-platforms/products/search/google-search-ai-mode-update/"
faq:
  - q: "Pourquoi ne pas demander directement « que penses-tu de ma marque ? »"
    a: "Parce qu'en nommant la marque, vous forcez l'IA à parler d'elle. Vous mesurez alors sa capacité à décrire une marque qu'on lui impose, pas sa tendance spontanée à la recommander. Seules des questions neutres mesurent la vraie visibilité."
  - q: "Qu'est-ce qu'une question brand-agnostic ?"
    a: "Une question qui décrit un besoin ou une situation d'achat sans citer aucune marque. Par exemple « quelle crème pour peau sensible ? » plutôt que « la crème de telle marque est-elle bien ? ». L'IA recommande alors librement, et on observe qui elle cite."
  - q: "Comment sont construites les questions de sen-ai ?"
    a: "À partir des mots-clés sur lesquels votre site se positionne, sen-ai dérive des personas puis des questions formulées comme un vrai acheteur. Aucune ne nomme votre marque, pour que la mesure reste neutre et reproductible."
cta:
  titre: "Une mesure neutre, pas une mesure flattée"
  texte: "sen-ai pose des questions d'acheteur réel, sans souffler votre marque. Premier scan gratuit."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

La tentation est grande de demander à ChatGPT ce qu'il pense de votre marque. C'est aussi la meilleure façon de fausser la mesure. **Dès que vous nommez votre marque dans la question, vous soufflez la réponse à l'IA et vous ne mesurez plus rien d'utile.** Ce biais est le premier à éliminer avant de [mesurer sa présence dans les IA](/guides/mesurer-visibilite-ia/).

## Nommer sa marque, c'est souffler la réponse

**Une question qui contient votre marque oblige l'IA à parler d'elle, ce qui n'a rien à voir avec sa tendance spontanée à la recommander.** Vous mesurez alors la capacité du modèle à décrire une marque qu'on lui impose, pas la probabilité qu'il la cite de lui-même face à un acheteur.

Imaginez Dermalys, marque fictive de cosmétique. Nommez-la et l'IA en parlera poliment, parce que vous l'y forcez. Décrivez seulement le besoin et elle recommande librement : Dermalys apparaît, ou pas, et seule cette réponse reflète votre visibilité réelle.

| Question guidée (à éviter) | Question neutre (à poser) |
|---|---|
| « Que penses-tu de la crème Dermalys ? » | « Quelle crème pour peau sensible ? » |
| « Dermalys est-elle mieux que ses concurrentes ? » | « Quelles marques de soin pour une peau réactive ? » |

## Des questions qui décrivent un besoin, pas une marque

**sen-ai n'interroge les IA qu'avec des questions brand-agnostic : elles décrivent une situation d'achat, jamais une marque précise.** L'IA recommande librement, et on observe qui elle place, dans quel ordre, et avec quelles sources.

Cette neutralité rend la mesure comparable d'une marque à l'autre et d'un scan à l'autre : toutes les marques d'un même marché sont jugées sur les mêmes questions ouvertes.

Pour tester vous-même, parlez comme un client qui ne vous connaît pas :

> Je cherche [produit ou service] pour [situation : besoin, budget, contrainte]. Quelles marques me conseilles-tu, et pourquoi ?

Mais [un seul test ne prouve rien](/guides/un-seul-test-chatgpt-ne-suffit-pas/) : une mesure fiable répète chaque question puis agrège les résultats, c'est [le principe du N-runs](/guides/paradigme-n-runs/).

## Des personas pour couvrir les vraies intentions

**Pour ne pas se limiter à une poignée de questions, sen-ai part de vos mots-clés, en dérive des personas, puis génère les questions que ces acheteurs poseraient réellement.** Chaque persona représente une intention d'achat différente, et chaque intention donne plusieurs formulations.

Cette construction recoupe [la façon dont les moteurs génératifs lisent une question](/guides/query-fan-out/) : pour répondre, ils l'éclatent en sous-questions et lancent de nombreuses recherches en parallèle, une technique que Google nomme le [query fan-out](https://blog.google/products-and-platforms/products/search/google-search-ai-mode-update/). Couvrir l'éventail des intentions d'un sujet, c'est se donner une chance d'être cité sur l'ensemble de ces sous-questions, au lieu d'une seule.

Reste à poser ces questions aux bons endroits : [les modèles que vos clients utilisent vraiment](/guides/quelles-ia-suivre-marche-francais/).
