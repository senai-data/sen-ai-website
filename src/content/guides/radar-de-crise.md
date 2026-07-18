---
title: "Surveiller sa réputation dans les IA : le radar de crise"
description: "Les IA peuvent relayer des signaux négatifs sur votre marque. Un radar de crise repère ces signaux, en jauge la gravité et écarte les faux positifs avant qu'ils ne deviennent un vrai problème."
parent: "agir-et-mesurer"
branch: "Le radar de crise"
priority: ext1
order: 3
updated: "2026-07-17"
related:
  - actions-priorisees
  - boucle-t14-mesurer-le-lift
  - reddit-youtube-sources-ugc-ia
  - paradigme-n-runs
lexical:
  - réputation
  - signaux négatifs
  - sentiment
  - faux positifs
  - gravité
faq:
  - q: "Une IA peut-elle nuire à la réputation d'une marque ?"
    a: "Oui. En répondant, une IA peut relayer une critique, un problème de produit ou une controverse, parfois reprise de forums ou de médias. Surveiller ces signaux dans les réponses IA permet de réagir avant qu'ils ne s'installent."
  - q: "Comment éviter les fausses alertes ?"
    a: "C'est le rôle d'une relecture du sentiment. Une mention peut sembler négative à cause d'une négation mal lue, par exemple un avis qui dit l'inverse de ce qu'un détecteur automatique croit. sen-ai fait relire chaque mention négative pour confirmer ou annuler l'alerte."
  - q: "Comment est jugée la gravité d'un signal ?"
    a: "Par un score qui combine plusieurs facteurs : le volume de mentions négatives, leur proportion, leur gravité, et leur dispersion. La façon exacte de les pondérer relève de notre méthode ; ce que vous voyez, c'est un niveau de gravité clair et hiérarchisé."
cta:
  titre: "Gardez un œil sur ce que les IA disent de vous"
  texte: "sen-ai repère les signaux négatifs dans les réponses IA et écarte les fausses alertes. Premier scan gratuit."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

La visibilité IA n'est pas qu'une affaire de présence : être cité négativement peut faire plus de mal qu'être absent. **Un radar de crise surveille les signaux négatifs dans les réponses IA, en jauge la gravité, et écarte les fausses alertes.** C'est le versant défensif d'une même démarche : [transformer un diagnostic en actions, puis en remesure](/guides/agir-et-mesurer/).

## Quand l'IA relaie du négatif

**En répondant à une question, une IA peut reprendre une critique, un défaut de produit ou une controverse, parfois puisée dans des forums ou des médias.** Dormalis, la marque fictive de literie, en fait les frais : un acheteur demande « quel matelas mémoire de forme éviter ? » et l'IA ressort un vieux fil de forum sur des retards de livraison pourtant réglés. La mention arrive au moment précis où ce client se décide, avec l'autorité apparente d'une réponse neutre - d'autant que les IA puisent dans [le contenu communautaire de Reddit ou de YouTube](/guides/reddit-youtube-sources-ugc-ia/).

Repérer ces signaux tôt change tout : une critique isolée se traite, ignorée, elle s'ancre dans les réponses au fil du temps. Le radar écoute en continu ce que les IA disent de votre réputation, plutôt que de le découvrir trop tard.

## Le piège des faux positifs

**Tout ce qui ressemble à du négatif n'en est pas, et réagir au bruit est aussi coûteux que rater un vrai signal.** Une analyse automatique du sentiment se trompe souvent sur les négations.

| Ce qui ressemble à une alerte | Ce que c'est vraiment |
|---|---|
| « Aucun défaut signalé chez Dormalis » lu comme négatif | Un faux positif : la négation dit l'inverse |
| Une critique qui ne ressort qu'une fois sur [plusieurs runs de la même question](/guides/paradigme-n-runs/) | Du bruit à garder à l'œil, pas une crise |
| Le même reproche repris par plusieurs modèles, sur plusieurs questions | Un vrai signal, à traiter en priorité |

sen-ai fait donc relire chaque mention négative par un second modèle, qui confirme ou annule l'alerte selon le contexte. Cette relecture, le Sentiment Judge, retire les fausses alarmes avant qu'elles ne polluent votre tableau de bord.

## Jauger la gravité, sans formule publique

**Chaque signal confirmé reçoit un niveau de gravité qui combine plusieurs facteurs : le volume des mentions négatives, leur proportion, leur gravité intrinsèque et leur dispersion.** Une critique isolée sur un point mineur ne pèse pas comme un problème récurrent évoqué partout.

La pondération exacte de ces facteurs relève de notre méthode, et n'est pas publiée. Ce que vous voyez sur l'écran de crise est l'essentiel : un niveau de gravité clair, hiérarchisé, qui vous dit où regarder en priorité. Un signal sérieux devient alors une [action à mener](/guides/actions-priorisees/), pas une angoisse vague ; une fois l'action menée, [une remesure à T+14](/guides/boucle-t14-mesurer-le-lift/) vérifie que le signal recule.
