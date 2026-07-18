---
title: "Quels robots d'IA lisent vraiment votre site : 30 jours de logs"
description: "GPTBot, ClaudeBot, OAI-SearchBot, ChatGPT-User : ces robots ne font pas le même travail. Nous avons classé 30 jours de logs serveur pour voir qui passe, ce qu'il lit, et ce que cela change pour votre visibilité."
parent: "etre-cite-par-les-ia"
branch: "Les crawlers IA"
priority: coeur
order: 6
updated: "2026-07-18"
related:
  - contenu-extractible-stats-citations-quotes
  - cocon-semantique-et-maillage
  - query-fan-out
  - schema-org-et-ia
lexical:
  - crawler IA
  - GPTBot
  - ClaudeBot
  - ChatGPT-User
  - OAI-SearchBot
  - logs serveur
  - user-agent
  - robots.txt
sources:
  - label: "OpenAI, documentation des crawlers (GPTBot, OAI-SearchBot, ChatGPT-User)"
    url: "https://platform.openai.com/docs/bots"
  - label: "Anthropic, ClaudeBot et le respect de robots.txt"
    url: "https://support.anthropic.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler"
  - label: "Cloudflare, vérifier les bots par plages d'IP publiées"
    url: "https://developers.cloudflare.com/bots/concepts/bot/verified-bots/"
faq:
  - q: "Quelle différence entre GPTBot, OAI-SearchBot et ChatGPT-User ?"
    a: "GPTBot collecte des pages pour l'entraînement des modèles. OAI-SearchBot alimente l'index de recherche de ChatGPT. ChatGPT-User se déclenche en direct, quand un utilisateur pose une question et que le modèle décide d'aller chercher votre page pour répondre. Le troisième est le plus intéressant : il prouve que votre page a servi dans une conversation réelle."
  - q: "Faut-il bloquer les robots d'IA dans robots.txt ?"
    a: "Cela dépend de votre modèle. Un éditeur qui vit de l'audience publicitaire a des raisons de bloquer l'entraînement. Une marque ou un SaaS B2B qui cherche à être cité dans les réponses a intérêt à rester lisible : être absent de l'index, c'est être absent des réponses. Décidez-le consciemment plutôt que par défaut."
  - q: "Comment savoir si un robot d'IA est authentique ?"
    a: "Par ses plages d'IP publiées, jamais par son user-agent seul. Nous avons observé des scanners de vulnérabilités portant les user-agents GPTBot et ClaudeBot pour chercher des fichiers .env et .git : l'usurpation existe déjà, probablement parce que des sites commencent à autoriser ces robots dans leurs règles de pare-feu."
  - q: "Les robots d'IA envoient-ils du trafic ?"
    a: "Très peu pour l'instant. Sur nos 30 jours de mesure, les assistants ont lu nos pages des centaines de fois et n'ont envoyé aucun visiteur depuis chatgpt.com. C'est l'asymétrie de l'ère générative : vous alimentez la réponse, l'assistant garde l'utilisateur. Raison de plus pour mesurer les mentions plutôt que les clics."
cta:
  titre: "Les IA vous lisent-elles, et vous citent-elles ?"
  texte: "Les logs disent qui passe. sen-ai dit ce que les IA répondent quand on parle de votre marché. Premier scan gratuit."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

On parle beaucoup de ce que les IA répondent, rarement de ce qu'elles lisent. Pourtant tout commence là : un assistant ne peut vous citer que s'il a récupéré votre page, d'une manière ou d'une autre. Nous avons donc classé 30 jours de logs de notre propre serveur, user-agent par user-agent, pour répondre à une question simple : qui passe vraiment ?

Les chiffres qui suivent sont les nôtres, sur un site jeune et de faible audience. L'intérêt n'est pas leur valeur absolue mais leur **répartition**, que vous retrouverez à votre échelle.

## Trois familles de robots, trois métiers différents

La première surprise en lisant des logs, c'est que « robot d'IA » ne veut rien dire. Chez OpenAI seul, trois agents distincts se promènent, avec des finalités qui n'ont rien à voir :

- **GPTBot** collecte des pages pour l'entraînement des modèles. C'est celui que bloquent les éditeurs qui refusent que leur contenu nourrisse un modèle. 213 requêtes chez nous sur 30 jours.
- **OAI-SearchBot** alimente l'index de recherche de ChatGPT. Le bloquer revient à sortir du corpus dans lequel ChatGPT va chercher ses réponses sourcées. 236 requêtes.
- **ChatGPT-User** est le plus intéressant : il se déclenche **en direct**, au milieu d'une conversation, quand le modèle décide d'aller lire une page pour répondre à quelqu'un. 117 requêtes, c'est-à-dire 117 fois où notre site a servi dans un échange que nous ne verrons jamais.

Côté Anthropic, **ClaudeBot** a été le crawler d'IA le plus actif sur notre site (669 requêtes). **PerplexityBot**, **Google-Extended** (Gemini), **CCBot** (Common Crawl, qui alimente indirectement de nombreux modèles), **Bytespider** (TikTok), **Applebot** et **Amazonbot** complètent le tableau, chacun avec quelques dizaines à quelques centaines de passages.

Confondre ces agents dans un seul bucket « IA », c'est se priver de la seule distinction qui compte : **entraînement**, **indexation**, **consultation en direct**.

## Ce que les robots d'IA lisent réellement

Le classement des URLs les plus demandées par ces agents est d'un ennui instructif : `robots.txt` (512 requêtes), le sitemap (310), la page d'accueil, la page tarifs, la page méthodologie. Ils entrent par la porte principale, lisent les règles, suivent le plan du site.

Traduction opérationnelle : votre présence dans les IA commence par des fondations très classiques. Un `robots.txt` cohérent, un sitemap à jour, des pages structurées et accessibles sans JavaScript. Si ces trois éléments sont bancals, aucune finesse rédactionnelle ne compensera - le robot ne verra tout simplement pas votre contenu. C'est le même socle que celui décrit dans [le cocon sémantique](/guides/cocon-semantique-et-maillage/) et dans [le contenu extractible](/guides/contenu-extractible-stats-citations-quotes/).

## L'asymétrie : ils lisent beaucoup, ils envoient peu

Sur la même période, l'analyse des référents raconte l'autre moitié de l'histoire. Google nous a envoyé une septantaine de clics, X/Twitter une quatre-vingtaine, Hacker News une trentaine, Bing une quarantaine. Depuis **chatgpt.com : aucun**.

Les assistants ont lu nos pages des centaines de fois et ne nous ont envoyé personne. C'est le déplacement de fond de l'ère générative : **vous alimentez la réponse, l'assistant garde l'utilisateur**. Le trafic référent, indicateur roi du SEO classique, devient aveugle à une part croissante de votre influence réelle.

C'est précisément pour cette raison que [mesurer sa visibilité dans les IA](/guides/mesurer-visibilite-ia/) passe par l'interrogation directe des assistants - poser les questions de votre marché et regarder qui est cité - plutôt que par l'attente d'un trafic qui ne viendra pas dans les mêmes proportions.

## Votre compteur de « visiteurs uniques » est un recensement de robots

Autre enseignement, moins flatteur. Notre tableau de bord réseau affichait fièrement **2 030 visiteurs uniques** sur la période. Notre balise de mesure côté navigateur, qui ne se déclenche que dans un vrai navigateur, en comptait une **vingtaine**.

L'écart n'est pas une erreur : la quasi-totalité des `Mozilla/5.0` de nos logs sont des robots déguisés. Scripts, scanners, crawlers commerciaux, agents sans user-agent du tout. Si vous pilotez vos décisions sur les uniques mesurés au niveau du réseau ou du proxy, vous optimisez pour des scrapers.

## Les scanners de vulnérabilités se déguisent déjà en robots d'IA

Détail qui mérite d'être connu : une partie des requêtes portant les user-agents **GPTBot** et **ClaudeBot** cherchaient chez nous des fichiers `/.env`, `/.git/HEAD`, `secrets.json` ou `firebase-adminsdk.json`. Aucun crawler d'IA légitime ne fait cela.

L'explication la plus probable est que des sites commencent à autoriser explicitement les robots d'IA dans leurs règles de pare-feu, et que les scanners s'habillent en conséquence. La conséquence pratique est simple : **vérifiez les robots par leurs plages d'IP publiées**, jamais par leur user-agent seul, aussi bien pour vos statistiques que pour vos règles de sécurité.

## Ce que nous en avons tiré

Quatre décisions, transposables :

1. **Laisser les robots d'IA accéder au site.** Pour un produit B2B qui cherche à être cité, figurer dans les corpus et les index est de la distribution, pas du vol. Un média vivant de la publicité tranchera peut-être l'inverse : l'important est de choisir, pas de subir le réglage par défaut.
2. **Abandonner les « visiteurs uniques » de niveau réseau** au profit d'une mesure côté navigateur.
3. **Traiter les user-agents d'IA usurpés comme du trafic de scan**, dans les analyses comme dans les règles de sécurité.
4. **Mesurer les mentions, pas seulement les clics.** Les logs disent qui vous lit ; ils ne disent pas si l'on vous cite. C'est l'autre moitié de l'image, celle que sen-ai mesure en interrogeant directement les assistants.

> Une version anglaise de cette analyse, publiée pour la communauté tech internationale, est disponible ici : [ChatGPT visited our site 117 times last month](/ressources/ai-crawlers-30-days/).

## Comment refaire cette analyse chez vous

Vous n'avez besoin d'aucun outil payant. Les logs d'accès de votre serveur suffisent : isolez le user-agent et le référent de chaque requête, classez les user-agents par famille (crawlers d'IA nommés, moteurs classiques, scripts, agents vides, navigateurs), puis comparez le total obtenu au nombre de visites relevé par votre outil de mesure côté navigateur. L'écart entre les deux est votre part de robots.

Deux précautions : si vous êtes derrière un proxy ou un CDN avec un fort taux de cache, votre serveur d'origine ne voit qu'une fraction du trafic - lisez alors les logs du proxy. Et vérifiez les robots que vous comptez comme « IA » par leurs plages d'IP publiées, sans quoi vous compterez des scanners déguisés.
