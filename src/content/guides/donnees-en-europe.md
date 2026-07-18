---
title: "Vos données restent-elles en Europe ? L'hébergement de sen-ai"
description: "L'application et la base de données de sen-ai sont hébergées en Europe, chez Hetzner en Finlande. Les rares traitements hors UE, par des fournisseurs de modèles, sont encadrés par des garanties de transfert."
parent: "confiance"
branch: "Données en Europe"
priority: coeur
order: 2
updated: "2026-07-17"
lexical:
  - hébergement européen
  - RGPD
  - sous-traitants
  - clauses contractuelles types
  - lecture seule
  - BYOK
related:
  - conformite-ai-act-limited-risk
  - methodologie-visibilite-ia
faq:
  - q: "Où sont hébergées les données de sen-ai ?"
    a: "L'application, la base de données et le stockage sont hébergés en Europe, chez Hetzner à Helsinki, en Finlande. Vos données de scan ne quittent pas cet hébergement européen pour leur stockage."
  - q: "Des données partent-elles aux États-Unis ?"
    a: "Seules les requêtes envoyées à certains fournisseurs de modèles établis hors UE, comme OpenAI et Anthropic, transitent hors d'Europe, et ce en lecture seule. Ces transferts sont encadrés par le cadre de protection des données UE-États-Unis et des clauses contractuelles types."
  - q: "Quels sous-traitants sen-ai utilise-t-il ?"
    a: "Un hébergeur, des fournisseurs de modèles, un service de paiement et des services d'enrichissement SEO (autorité, mots-clés, sémantique, prix médias). La liste complète, datée et tenue à jour avec un journal des changements est publique sur notre page méthodologie."
  - q: "Puis-je utiliser mes propres clés API ?"
    a: "Oui. Les organisations peuvent enregistrer leurs propres clés OpenAI, Gemini, Anthropic ou Mistral (option BYOK). Les requêtes concernées partent alors via leur propre compte fournisseur, dans le cadre de leur propre contrat, avec un plafond de dépense mensuel par fournisseur. Sans clé client, les clés plateforme de sen-ai s'appliquent avec les garanties décrites ici."
cta:
  titre: "Des données européennes, en toute transparence"
  texte: "Le détail de l'hébergement et des sous-traitants est public. Premier scan gratuit."
  label: "Lancer mon scan gratuit"
  href: "/register"
---

Pour beaucoup d'organisations, surtout en Europe, la première question n'est pas « combien ça coûte » mais « où vont mes données ». **L'application et la base de données de sen-ai sont hébergées en Europe, chez Hetzner en Finlande, et vos données de scan y restent.** De quoi répondre précisément à votre DPO.

## Un hébergement européen

**L'ensemble de l'application, sa base de données et son stockage sont hébergés à Helsinki, en Finlande, chez Hetzner.** Vos données de scan, vos marques suivies, vos résultats sont conservés sur cette infrastructure européenne, sous le régime du RGPD.

Ce choix est délibéré. Héberger en Europe simplifie la conformité et la rend vérifiable : pas de transfert de stockage hors UE à justifier, un hébergeur soumis au droit européen, une localisation claire. C'est la fondation sur laquelle repose le reste du [cadre de confiance de sen-ai](/guides/confiance/).

## Qu'est-ce qui transite hors d'Europe ?

**Les seules données qui quittent l'Europe sont les requêtes envoyées à certains fournisseurs de modèles établis aux États-Unis, en lecture seule, et ces transferts sont encadrés.** Prenons une marque fictive de literie, Dormea : [mesurer sa visibilité](/guides/methodologie-visibilite-ia/) suppose d'envoyer à OpenAI la question « quel matelas quand on a mal au dos », une question qui [ne nomme jamais la marque suivie](/guides/questions-sans-marque-eviter-biais/), puis de stocker la réponse analysée à Helsinki. Rien d'autre ne quitte l'hébergement européen.

| Donnée | Où elle vit |
|---|---|
| Application, base de données, résultats de scan | Helsinki, Finlande (Hetzner), sous RGPD |
| Requêtes vers OpenAI et Anthropic | Transit hors UE, lecture seule, garanties de transfert |
| Requêtes vers Gemini | Google Ireland, depuis l'Union |
| Paiement, enrichissement SEO | Fournisseurs opérant depuis l'Union |

Ces transferts s'appuient sur le cadre de protection des données UE-États-Unis et sur des clauses contractuelles types. Et les modèles sont interrogés sans entraînement, conformément au [statut à risque limité](/guides/conformite-ai-act-limited-risk/) de sen-ai au sens de l'AI Act.

## Vos propres clés API, si vous préférez

**Les organisations peuvent enregistrer leurs propres clés API : les requêtes concernées partent alors via leur propre compte fournisseur, dans le cadre de leur propre contrat.** C'est l'option BYOK (Bring Your Own Key), qui couvre OpenAI, Gemini, Anthropic et Mistral - [les modèles qui comptent pour le marché français](/guides/quelles-ia-suivre-marche-francais/) - avec un plafond de dépense mensuel par fournisseur et des clés chiffrées, supprimables à tout moment.

Pour les équipes conformité, cela déplace la relation fournisseur là où elles la maîtrisent déjà : conditions contractuelles, garanties de transfert et facturation passent par leur propre compte. Sans clé client, les clés plateforme de sen-ai s'appliquent, avec les garanties décrites ci-dessus.

## Une liste publique et datée

**sen-ai s'appuie sur une courte liste de sous-traitants, publiée et datée : un hébergeur, des fournisseurs de modèles, un service de paiement et des services d'enrichissement SEO.** Rien n'est caché, et chaque ajout ou changement est consigné dans un journal.

Qui traite quoi, où, et sous quelles garanties : le détail, tenu à jour, est consultable sur la [page méthodologie publique](/methodology) et reporté sur chaque rapport de scan.

**Modèle à coller dans votre questionnaire fournisseur :**

> sen-ai héberge application, base de données et stockage chez Hetzner à Helsinki (Finlande, UE), sous RGPD. Seules les requêtes vers des fournisseurs de modèles hors UE quittent l'Europe, en lecture seule, sans entraînement, encadrées par le cadre de protection des données UE-États-Unis et des clauses contractuelles types. Liste des sous-traitants publique, datée, tenue à jour.
