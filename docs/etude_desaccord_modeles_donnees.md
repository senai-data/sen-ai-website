# Étude « deux IA, deux réponses » - données brutes et méthode

> Extraction du 2026-07-18 sur la base de prod, lecture seule. Sert de base
> à une publication /ressources. **Aucun nom de marque, de client ou de
> secteur ne doit apparaître dans la version publiée** (consigne user).

## Le résultat principal

Mêmes questions, **posées le même jour**, à deux modèles d'IA différents,
sur six scans de marques distinctes :

| Métrique | Valeur |
|---|---|
| Questions appariées | **2 023** |
| Désaccord entre les deux modèles | **33,5 %** |
| Recouvrement des sources citées (Jaccard) | **10,4 %** en moyenne (3,4 % à 13,5 %) |
| Un modèle cite la marque plus souvent que l'autre | **6 cas sur 6**, même sens |
| Réponses vides / erreurs | **0** sur les deux modèles (aucun artefact technique) |

Taux de mention par marque (anonymisées), même jour, mêmes questions :

| Marque | Questions | Modèle 1 | Modèle 2 | Désaccord |
|---|---|---|---|---|
| A | 292 | 36,0 % | 13,0 % | 23,6 % |
| B | 267 | 48,3 % | 22,8 % | 38,2 % |
| C | 417 | 48,4 % | 37,6 % | 20,9 % |
| D | 468 | 55,1 % | 29,3 % | 42,9 % |
| E | 292 | 45,5 % | 21,6 % | 33,6 % |
| F | 287 | 69,3 % | 36,6 % | 41,8 % |

Couples de modèles : `gemini-2.5-flash` vs `gpt-4.1-mini` (5 scans, 1 731
questions, désaccord 35,1 %) et `gemini-3.5-flash` vs `gpt-5.6-luna`
(1 scan, 292 questions, désaccord 23,6 %).

## Pourquoi ce résultat est solide

- **Aucune confusion temporelle** : les deux modèles répondent aux mêmes
  questions dans le même scan, le même jour. La différence ne peut pas
  venir d'une évolution du web entre deux dates.
- **Aucun artefact technique** : zéro réponse vide côté Gemini comme côté
  OpenAI. L'écart n'est pas un taux d'erreur déguisé.
- **Direction constante** : 6 marques sur 6 dans le même sens, avec des
  écarts de 11 à 33 points. Ce n'est pas du bruit d'échantillonnage.
- **Jointure par texte normalisé**, jamais par identifiant de question
  (les rescans recopient les questions sous de nouveaux ids).
- Lignes `run_index = 0` (consensus) exclues : seuls les échantillons réels.

## Le résultat qui structure tout : une échelle d'instabilité

Après relance du rescan qui avait échoué, une **seconde lignée** dispose
d'un avant/après natif de la migration - et un **groupe de contrôle** est
apparu : des runs sur le *même* modèle à quelques jours d'écart. On peut
donc enfin séparer ce qui revient au hasard, au temps, et au modèle.

| Condition | Questions qui changent de réponse | Recouvrement des sources |
|---|---|---|
| **Même modèle, 1 jour d'écart** (référence) | **18 %** | 44,5 % |
| Même modèle, 15 jours d'écart | 17 % | 38,8 % |
| **Changement de modèle** (2.5 → 3.5), 2 mois | **34 %** | 26-28 % |
| **Deux modèles différents, le même jour** | **33,5 %** | **10,4 %** |

Lecture :

1. **Le socle d'instabilité est de 18 %.** Reposer exactement la même
   question au même modèle le lendemain change déjà près d'une réponse sur
   cinq. C'est la non-déterminisme intrinsèque des modèles génératifs -
   et la justification chiffrée du paradigme N-runs.
2. **Le temps seul n'ajoute presque rien** : 18 % à un jour, 17 % à quinze
   jours. L'instabilité n'est pas de la dérive lente, elle est immédiate.
3. **Changer de modèle double le phénomène** : 34 %, sur les deux lignées
   indépendamment (chiffre identique des deux côtés).
4. **Deux fournisseurs différents ne lisent pas le même web** : 10,4 % de
   sources communes seulement, contre 44,5 % pour un même modèle d'un jour
   sur l'autre. C'est le chiffre le plus frappant de l'étude.

Avant/après migration, par lignée (Gemini uniquement, questions appariées) :

| Lignée | Période | Taux de mention | Bascules | Sources communes |
|---|---|---|---|---|
| 1 | 21 mai → 17 juil. | 45,5 % → 36,0 % (-9,6 pts) | 34 % | 26,4 % |
| 2 | 19 mai → 17 juil. | 69,3 % → 50,9 % (-18,5 pts) | 34 % | 28,4 % |

## Réserves méthodologiques à conserver dans l'article

- Le groupe de contrôle provient de runs importés (pipeline d'analyse
  antérieur). La comparaison import↔import reste cohérente en interne,
  mais le 18 % doit être présenté comme un ordre de grandeur, pas comme
  une mesure de précision.
- Les contrôles à 1 et 15 jours viennent d'une seule lignée.
- L'avant/après migration reste séparé de deux mois : la baisse du taux
  de mention (-9,6 et -18,5 points) ne peut pas être attribuée au seul
  modèle. En revanche le **taux de bascule** (34 % contre 18 % de
  référence) est un écart franc et reproduit à l'identique sur deux
  lignées - c'est lui qu'il faut mettre en avant, pas la baisse.
- Pour trancher définitivement : faire tourner les mêmes questions sur
  les deux versions de modèle **le même jour** via le sélecteur de modèle
  (gated BYOK). `gemini-2.5-flash` s'éteint le 16 octobre.

## Le sujet qu'on a écarté, et pourquoi

L'idée de départ était « quel est l'impact de la migration Gemini
2.5 → 3.5 sur la visibilité ». Les chiffres existent et sont
spectaculaires (sur la seule lignée disposant d'un avant/après natif :
45,5 % → 36,0 % de taux de mention, 100 questions sur 292 qui basculent,
26,4 % de recouvrement des sources).

**Ils ne sont pas publiables tels quels** : les deux runs sont séparés de
deux mois (21 mai → 17 juillet). L'écart mélange l'effet du changement de
modèle et l'évolution réelle du web sur la période - or la littérature
donne 40 à 60 % de renouvellement mensuel des domaines cités. Attribuer
la totalité de l'écart au modèle serait malhonnête.

Pour isoler proprement l'effet migration, il faudrait faire tourner les
mêmes questions sur les deux versions de modèle **en même temps**, ce que
permet désormais le sélecteur de modèle (gated BYOK). À arbitrer : coût
LLM, et `gemini-2.5-flash` s'éteint le 16 octobre.

Une seule lignée disposait d'un avant/après natif ; la seconde tentative
de rescan post-migration a échoué le 17 juillet.

## Contraintes de publication

1. **Anonymat total** : aucun nom de marque, de groupe, ni de secteur
   précis. « Six marques grand public, questions en français » suffit -
   nommer le secteur avec six marques rendrait le groupe identifiable.
2. **Données issues de scans clients** : même agrégées et anonymisées,
   elles proviennent du workspace d'un client réel. Faire valider en
   interne avant publication, ou reproduire l'étude sur des marques
   scannées par nos soins.
3. Ne pas publier les taux par marque si le risque de recoupement est
   jugé trop élevé : les totaux agrégés (2 023 questions, 33,5 %,
   10,4 %) portent déjà le message.

## Angle proposé pour l'article

Titre de travail : **« Reposez la même question à la même IA demain :
une réponse sur cinq aura changé »**

Le fil conducteur est l'échelle d'instabilité, du plus petit au plus
grand : 18 % (même modèle, lendemain) → 34 % (changement de version) →
33,5 % de désaccord et 10 % de sources communes (deux fournisseurs). La
conclusion s'impose d'elle-même : **un test unique sur une seule IA ne
mesure rien**, ce qui est exactement la thèse du produit - démontrée par
la donnée au lieu d'être affirmée.

Ce qui rend l'article rare : il a un **groupe de contrôle**. Dans un
marché où les chiffres circulent sans méthode, publier « voici notre
référence de bruit, et voici ce qui la dépasse » est un différenciateur
de crédibilité plus fort que le chiffre lui-même.

Section à conserver telle quelle : **« ce que nos données ne prouvent
pas »** - la baisse du taux de mention après migration, écartée faute de
pouvoir la distinguer de l'évolution naturelle du web sur deux mois.

Base : 2 023 questions appariées pour la comparaison inter-modèles,
579 pour l'avant/après, 617 pour le contrôle. Six marques anonymisées.
