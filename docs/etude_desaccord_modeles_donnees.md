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

Titre de travail : **« Interroger une seule IA ne mesure pas votre
visibilité : deux modèles sont en désaccord sur un tiers des questions »**

L'article démontre par la donnée ce que le produit affirme : la visibilité
IA n'est pas une valeur unique mais une distribution qui dépend du modèle
interrogé. Le chiffre le plus frappant reste le recouvrement des sources
à 10 % - deux modèles répondant à la même question ne lisent pas le même
web.

Angle secondaire honnête et publiable : **« ce que nos données ne
prouvent pas »** - la section sur la migration écartée pour cause de
confusion temporelle. Rare et crédibilisant dans un marché où tout le
monde publie des chiffres sans méthode.
