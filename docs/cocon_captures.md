# Cocon /guides - captures produit (liste de courses + convention)

> L'infra image est en place (`GuideLayout` stylise `img` + légende). Reste à **déposer les PNG**
> dans `public/captures/` puis à ajouter **1 ligne markdown** par capture dans la feuille concernée.
> Les captures viennent forcément de l'app (derrière login), donc à produire à la main.

## Convention (dans un `.md` de `src/content/guides/`)

Placer la capture juste sous le H2 / paragraphe qu'elle illustre :

```markdown
![Texte alternatif décrivant la capture, pour l'accessibilité](/captures/overview-variance.png)
*Légende courte visible sous l'image.*
```

- Fichier dans `public/captures/` -> servi à `/captures/<fichier>.png`.
- La ligne `*Légende*` juste sous l'image est stylée automatiquement en légende centrée (via `:has()`).
- Format conseillé : PNG, largeur ~1200-1600px, recadré sur la zone utile, **données démo** (jamais un vrai client).
- 1 à 2 captures max par feuille (le plan en prévoit 1-2). Ne pas surcharger.

## Liste de courses (1 capture par feuille, depuis docs/cocon_plan_redaction.md)

| Feuille (slug) | Capture à prendre | Nom de fichier suggéré |
|---|---|---|
| `methodologie-visibilite-ia` (pilier) | Overview d'un scan (grade + taux de citation par IA) | `overview-grade.png` |
| **A** `un-seul-test-chatgpt-ne-suffit-pas` | Overview + la bande de variance ±pts | `overview-variance.png` |
| **A** `paradigme-n-runs` | Un KPI affiché « X% ± Y pts » | `kpi-confidence.png` |
| **A** `quelles-ia-suivre-marche-francais` | Le filtre / sélecteur de provider (les 4 IA) | `provider-filter.png` |
| **A** `questions-sans-marque-eviter-biais` | Onglet Questions (questions brand-agnostic) | `questions-tab.png` |
| **B** `query-fan-out` | Topics + Personas (= sous-intentions) | `topics-personas.png` |
| **B** `etude-princeton-geo` | Page Audit, score 0-100 | `page-audit-score.png` |
| **B** `contenu-extractible-stats-citations-quotes` | Page Audit, détail des patterns | `page-audit-patterns.png` |
| **B** `schema-org-et-ia` | Onglet Schema | `schema-tab.png` |
| **B** `cocon-semantique-et-maillage` | Onglet Internal linking | `internal-linking.png` |
| **C** `eeat-explique` | (optionnel - pas de capture dédiée au plan) | - |
| **C** `wikipedia-source-plus-citee-chatgpt` | Onglet Wikipedia | `wikipedia-tab.png` |
| **C** `autorite-domaine-da-tf-cf-rd` | Badges autorité (DA) sur les concurrents | `competitors-da-badges.png` |
| **D** `concurrents-cites-par-ia` | Onglet Concurrents | `competitors-tab.png` |
| **D** `medias-cites-par-ia-relations-presse` | Onglet PR / Media | `pr-media-tab.png` |
| **D** `reddit-youtube-sources-ugc-ia` | Onglets Reddit / YouTube | `reddit-youtube.png` |
| **D** `media-cite-trop-cher-alternative` | Modal Find-alternative (alternative média) | `find-alternative-modal.png` |
| **E** `actions-priorisees` | Onglet Actions | `actions-tab.png` |
| **E** `boucle-t14-mesurer-le-lift` | Le delta de position (avant/après) | `position-delta.png` |
| **E** `radar-de-crise` | Onglet Crisis | `crisis-tab.png` |
| **E** `contenu-qui-ne-sent-pas-lia` | (optionnel - pas de capture dédiée) | - |
| **F** `conformite-ai-act-limited-risk` | Onglet Compliance (par scan) | `compliance-scan.png` |
| **F** `donnees-en-europe` | Page Compliance de l'organisation | `compliance-org.png` |
| **G** `outils-seo-sur-lesquels-on-sappuie` | Badges DA concurrents + modal média + score article | `tools-signals.png` |
| FLAGSHIP `on-a-teste-notre-outil-sur-nous-memes` | Overview de la marque « sen-ai » | `overview-senai.png` |

## Une fois les PNG déposés

1. `public/captures/<fichier>.png` (données démo, recadré).
2. Ajouter la ligne markdown (cf convention) dans la feuille, sous le H2 pertinent.
3. `npm run build` (vérifier 0 erreur) puis déployer (scp `.md` + PNG, `docker compose build astro && up -d astro && restart nginx`).
4. Vérifier en 200 + l'image qui charge (`curl -I https://sen-ai.fr/captures/<fichier>.png` -> 200).

> Garde-fou : les captures montrent **quoi** le produit mesure (✅ publier), jamais prompts/poids/formules (🔴), cf grille révéler/protéger §4bis.
