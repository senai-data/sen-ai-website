# sen-ai design tokens

## Canonical source

The design system is owned by the Claude Design handoff at `C:\Users\leed\Downloads\sen-ai.fr\design_handoff_sen_ai_redesign\` (v1, 2026-06). In this repo, the canonical files are:

- [src/styles/tokens.css](../src/styles/tokens.css) - CSS variables on `:root` (coral ramp + neutral ramp + semantic roles + status + severity + spacing + radius + shadow + type). Dropped verbatim from the handoff.
- [src/styles/components.css](../src/styles/components.css) - class-based primitives (`.btn .card .chip .stat .score .sev .field .input .table .tabs__macro .tabs__leaf .steps .dots .empty .toast .tip .sentiment`). Dropped verbatim from the handoff.
- [src/styles/global.css](../src/styles/global.css) - entry point. Imports `tailwindcss`, then `tokens.css`, then `components.css`, then bridges the tokens to Tailwind 4 utilities via an `@theme inline {}` block (the Tailwind-4 equivalent of the `tailwind.config.js` mapping shipped in `01-tokens.html` of the handoff).

When the handoff updates (v2, v3, ...), refresh the two `*.css` files from the handoff and adjust `@theme inline {}` only if new tokens were added.

## What you get

After this entry point loads, every Astro page has access to:

| API           | Examples                                          | When to use                          |
|---------------|---------------------------------------------------|--------------------------------------|
| Class API     | `<button class="btn btn--primary btn--md">`        | Components from the handoff library  |
| Tailwind utils| `<div class="bg-surface text-text-primary p-5">`   | One-off layouts, page composition    |
| Raw CSS vars  | `style="background: var(--coral-50)"`              | Last resort for inline overrides     |

All three resolve to the same underlying token values. Prefer the **class API** for primitives and **Tailwind utilities** for layout. Never reach for raw hex.

## Tailwind utility map (what the bridge exposes)

| Utility prefix          | Tokens                                                             |
|-------------------------|--------------------------------------------------------------------|
| `bg-primary` / `text-primary` / `bg-primary-subtle`  | coral primary + hover + subtle                       |
| `bg-surface` / `bg-surface-sunken` / `bg-surface-muted` | white / gray-50 / gray-100                          |
| `border-border` / `border-border-strong`            | gray-200 / gray-300                                  |
| `text-text-primary` / `text-text-secondary` / `text-text-muted` | charcoal ramp                                |
| `bg-status-positive` / `bg-status-warning` / `bg-status-critical` / `bg-status-info` / `bg-status-neutral` / `bg-status-auto` | categorical status colours (+ `-bg` softs) |
| `bg-sev-none/low/medium/high/critical`              | ordered severity ramp                                |
| `bg-coral-{50..900}`                                | brand ramp (white-label settings, hover transitions) |
| `rounded-sm/md/lg/xl/full`                          | 6 / 8 / 12 / 16 / 9999px                             |
| `shadow-xs/sm/md/lg/xl`                             | cool-tinted depth tiers                              |
| `text-micro/small/body`                             | 11 / 13 / 14px UI scale                              |
| `font-sans` / `font-mono`                           | system stacks                                        |

Tailwind's built-in spacing scale (`p-1..p-9`, `gap-*`, etc.) is **not** remapped to the handoff's `space-*` values yet. Existing pages use a chaotic mix (p-3 / 4 / 5 / 6 / 8 with no consistent meaning) and a global remap would silently double `p-8` paddings on 35+ pages. The spacing scale will align page-by-page as each surface migrates, using the handoff rule:

> Cards pad at `space-5` (24px). Compact controls at `space-3` (12px). Section gutter `space-6` (32px).

In Tailwind utilities today that reads as `p-6` for cards (24px under the default scale) and `p-3` for compact rows. Document this on the migrated page.

## Legacy brand aliases

`bg-coral`, `text-charcoal-dark`, etc. are still defined in `global.css` for backwards compatibility with unmigrated pages. Their values now match the new design (coral primary is `#F06A5C`, not `#E8707A`), so unmigrated pages get a small free recolour. These legacy utilities will be deleted in a final sweep once grep shows zero usage.

## Non-negotiable conventions (from the handoff README)

- **No em-dash (—) or en-dash (–) anywhere.** Use ` - `. Lint: `scripts/lint_ai_tells.py`.
- **Multi-vertical, zero hardcode.** No brand or vertical name baked into a token, class, or template.
- **"Colour earns attention".** Healthy / neutral / A-B grades stay quiet charcoal. Reserve colour for what needs eyes : C/D grades, warning to critical escalation, negative sentiment. Coral is the **one** primary CTA per page (Von Restorff). White-label accent recolours nav-active + primary fills only. Status colours are NEVER rebranded by the accent.
- **Inline > modal.** Modals are reserved for destructive confirmation.
- **SSR-first.** Critical read paths (compliance, scan results, marketing) must work without JS. Compliance PDF prints from the same HTML via `@media print`.
- **Laws of UX visible in the layout** : Hick, Jakob, Peak-End, Von Restorff (1 critical CTA / page), Miller, Serial Position, Fitts (>=44px tap targets), Doherty (<400ms).

## Migration plan

Foundation (this PR) ships the tokens + class library + Tailwind bridge. No pages are migrated yet.

Surface-by-surface migration follows, one PR each. Suggested order, densest first so we catch token gaps early:

1. `/app/scans/{id}/citations` (`06-citations.html` in handoff) - sortable table, inline row expand, unified filter chip row.
2. `/app/scans/new` (`07-create-scan.html`) - 4-step wizard with inline validation + live credit cost.
3. `/app/scans/{id}/results` Overview (`03-flagship.html` lower half) - hero KPIs + topic visibility + sentiment chip.
4. `/app/agency/overview` (`03-flagship.html` upper half) - workspace cards sorted crisis-first.
5. Tab nav restructure (`04-navigation.html`) - swaps `ScanTabs.astro` from 16 flat tabs to 3 macros over leaves.
6. `/app/compliance` (`08-compliance.html`) - audit log + DPIA + sub-processors, with print CSS.
7. `/app/content` (`09-content.html`) - kanban with filters + brand chips.
8. `/app/dashboard` (`10-dashboard.html`).
9. `/app/agency/branding` (`11-branding.html`) - live preview + accent injection.
10. Marketing tier (`12-marketing.html`) + auth pages.

Each migration PR rewrites one surface, uses the class API where a primitive exists, and adds the surface to the changelog at the bottom of this doc.

## Reference surfaces

The handoff folder ships 12 high-fidelity HTML reference surfaces + screenshots. Implementers should open the relevant `.html` next to the page they are migrating and match the layout. The HTML is a reference, not code to copy verbatim. Astro components stay Astro components.

## Brand lockup (decided 2026-06-11)

The compact brand lockup used on auth / welcome / future white-label credit is :

```html
<img src="/logo-mark.svg" alt="" style="width:28px;height:28px" />
<span class="brand__name">sen-ai<span>.fr</span></span>
```

- **Glyph** : `public/logo-mark.svg` - the real lotus network mark, a coral
  (`#F06A5C`) recolour of `favicon.svg`. Do NOT use the handoff's square+ring
  placeholder (`.brand__mark` CSS) - it was a Claude Design invention with no
  brand story ; the lotus carries the "from data to bloom" identity.
- **Wordmark** : `sen-ai` bold `--color-text-primary`, `.fr` weight 500
  `--color-text-muted` (handoff treatment kept - brand reads first, TLD
  recedes). Pattern : `sen-ai<span>.fr</span>` with
  `.brand__name span { color: var(--color-text-muted); font-weight: 500; }`.
- The full-size PNG (`/logo-sen-ai-fr.png`, glyph + uniform wordmark) stays
  for large marketing placements (landing nav, og-image).

## Changelog

- 2026-06-08 - Foundation shipped : `tokens.css`, `components.css`, Tailwind 4 bridge in `global.css`. No surfaces migrated yet.
- 2026-06-08 → 06-11 - All 12 handoff surfaces migrated (see project memory tracker). Brand lockup decided : lotus glyph + handoff wordmark.
