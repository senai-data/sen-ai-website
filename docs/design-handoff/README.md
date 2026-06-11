# Handoff: sen-ai.fr redesign system

## Overview

This package is the design foundation + reference surfaces for the **sen-ai.fr** redesign - a multi-vertical B2B SaaS that measures how generative-AI systems (ChatGPT, Gemini, Claude, Mistral) cite brands when answering buyer questions. It serves two audiences: solo brand owners, and agencies managing 5-50 client brands white-label.

The package contains:
1. A **design token system** (`tokens.css`) - the single source of truth.
2. A **component library** (`components.css`) - 18 class-based primitives built on the tokens.
3. **Twelve reference surfaces** (HTML) exercising the system across every interaction pattern in the app.

## About the design files

The `.html` files here are **design references created in HTML** - high-fidelity prototypes showing intended look and behavior. They are **not** production code to copy verbatim.

**Your task:** recreate these designs in the target codebase - **Astro 5 SSR + Tailwind CSS 3 + Alpine.js** (no React/Vue/Svelte, no CSS-in-JS) - using its established patterns. The two `.css` files, however, ARE intended to ship: drop `tokens.css` into the root layout and map it in `tailwind.config.js` (mapping provided in `01-tokens.html`). `components.css` can ship as-is or be translated into Astro components / Tailwind `@apply` utilities - your call.

## Fidelity

**High-fidelity.** Final colors, typography, spacing, radii, shadows and interactions are all decided. Recreate the UI faithfully. Every value traces to a token - reference the token, never a raw hex.

## Non-negotiable conventions

- **No em-dashes** anywhere in copy or assets. Use ` - ` (hyphen with spaces).
- **Multi-vertical, zero hardcode.** Every label, persona, illustration must read for cosmetics, automotive, B2B services, finance, travel, etc. No real brand names hardcoded. (Sample data in the mocks rotates across verticals deliberately.)
- **"Colour earns attention" (governing principle).** Healthy/neutral states stay quiet charcoal: A/B grades, "stable", positive deltas, "signed/reviewed". Reserve colour for what needs eyes - C/D grades, warning/critical/crisis, negative sentiment (amber to red escalation). Coral is spent on the **one** primary action per page (Von Restorff). Status colours are NOT rebranded by white-label accents.
- **Inline > modal.** Prefer inline editing, inline pickers, inline error feedback. The only modal is destructive confirmation.
- **SSR-first.** Critical read paths (compliance, scan results, marketing) must work without JS. Compliance prints from the same HTML (print CSS, not a separate document).
- **Laws of UX:** Hick (cap choices), Jakob (familiar SaaS patterns), Peak-End, Von Restorff (1 critical CTA/page), Miller (chunk 5-7), Serial Position, Fitts (>=44px tap targets), Doherty (<400ms).

## Design tokens

Canonical values live in `tokens.css` (and the annotated `tailwind.config.js` block at the bottom of `01-tokens.html`). Summary:

**Colour - coral primary (brand asset, kept)**
`--color-primary:#F06A5C` / `--color-primary-hover:#DC4F40` (added for WCAG-AA legible interaction states; primary fill stays #F06A5C) / `--color-primary-subtle:#FEF3F1`

**Colour - cool-charcoal neutral**
surface #FFFFFF / surface-sunken #F9FAFB / surface-muted #F3F4F6 / border #E5E7EB / border-strong #D1D5DB / text-primary #1A1A1A / text-secondary #4B5563 / text-muted #6B7280

**Status (categorical):** positive #047857 / warning #B54708 / critical #D92D20 / info #175CD3 / neutral #475569 (each with a -bg tint)

**Severity (ordered 5-scale, perceptual escalation):** none #94A3B8 -> low #2E90FA -> medium #F79009 -> high #EF6820 -> critical #D92D20

**Spacing (4px base):** 1=4 2=8 3=12 4=16 5=24 6=32 7=48 8=64 9=96px. Rule: cards pad at space-5 (24px); compact controls at space-3 (12px).

**Radius:** sm 6 / md 8 (inputs, chips) / lg 12 (cards) / xl 16 (modals) / full (pills, avatars).

**Shadow tiers:** xs (flat card on border) / sm (hover lift) / md (dropdown) / lg (popover) / xl (modal). Cool-tinted, defined in tokens.css.

**Type (system stack, no licence):** h1 30/680, h2 24/640, h3 20/620, h4 16/600, h5 14/600, body 14/1.57, small 13, micro 11 uppercase, code (mono). Tight tracking on headings (-.02 to -.025em), generous line-height on body.

## Component library

`components.css` ships these (class API in parens):

- **Button** (`.btn` + `.btn--primary/secondary/tertiary/danger/ghost` + `.btn--sm/md/lg`) - heights 32/40/48px.
- **Card** (`.card` + `.card--flat/elevated/highlighted`) - highlighted uses `--card-accent`.
- **Stat / KPI** (`.stat`, `.stat__label/value/delta`) + delta up/down/flat.
- **Chip** (`.chip` + `.chip--sm/md/lg` + 6 roles) - optional `.chip__dot` / icon.
- **Score badge** (`.score` + `.score--a/b/c/d` + `--sm/lg`) - A/B quiet, C/D coloured.
- **Severity badge** (`.sev` + `.sev--none/low/medium/high/critical`).
- **Inputs** (`.field`, `.input`, `.select`, `.textarea`) + `.field--error`, helper/error text.
- **Table** (`.table`) - sortable headers, row hover, optional expand.
- **2-level tabs** (`.tabs__macro` over `.tabs__leaf`).
- **Steps** (`.steps`) + **Progress dots** (`.dots`).
- **Empty state** (`.empty`), **Toast** (`.toast` + 4 statuses), **Tooltip** (`.tip`).
- **Sentiment chip** (`.sentiment` tri-segment).
- Composite **WorkspaceCard** and **Sparkline** are demonstrated in the surfaces (sparkline = one small inline-SVG render function, no chart lib).

Full live demo + exact markup: `02-components.html`.

## Surfaces (reference prototypes)

| File | Surface | Notes for implementation |
|---|---|---|
| `01-tokens.html` | Token spec | Copy the CSS + tailwind.config blocks from the bottom. |
| `02-components.html` | Component inventory | Live demo of every primitive; copy class markup. |
| `03-flagship.html` | Agency overview + scan Overview | Clickable: workspace card -> scan drill-down. Agency cards sort crisis-first. |
| `04-navigation.html` | Tab restructure | 16 flat tabs -> 3 macros (Visibility 6 / Sources 7 / Act 3). Honest grouping + rationale + top-3 flow predictions. **This is the nav model to build.** |
| `05-mobile.html` | Mobile strategy | Decision: hybrid - mobile-first marketing/auth, desktop app (>=1024px) with a graceful "open on a wider screen" gate that still surfaces crisis brands. |
| `06-citations.html` | Citations tab (hi-fi) | Sortable table, inline verbatim-answer expansion (no modal), source tracing. Filters: provider + sentiment segments, Source + Topic dropdowns, unified active-filter chip row + Clear all. |
| `07-create-scan.html` | Create-scan wizard | 4 steps, inline validation, vertical-aware question suggestions, live credit cost (`questions x providers x 10`), launch -> success state. |
| `08-compliance.html` | AI Act compliance hub | Audit log (filterable), DPIA summary, sub-processors table (DPA status), methodology changelog. **Has print CSS** - `window.print()` hides chrome and renders the document from the same HTML. |
| `09-content.html` | Content pipeline kanban | Drag-drop columns with live counts, gap-driven cards, AI-draft progress, filters (brand chips + Type/Topic/Assignee dropdowns + active chips). |
| `10-dashboard.html` | Workspace dashboard | Single-brand cockpit: hero, KPI row, recent-scans history, provider breakdown, next scan, top gaps (link to content), quick actions, workspace switcher. |
| `11-branding.html` | White-label branding | Logo (drag-drop) + display name + curated accent, with a live client-facing preview that recolours interactive elements only (status colours stay fixed). |
| `12-marketing.html` | Marketing + auth | Mobile-first landing + register, shown in device frames. >=48px tap targets. |
| `index.html` | Hub | Links all of the above. |

## Interactions & behavior (key patterns to reproduce)

- **2-level tab nav** (`04`): macro segmented control swaps a contextual leaf row. Default leaf = Overview. Grade badge in the sticky scan header confirms arrival before body paints.
- **Inline row expansion** (`06`): clicking a table row expands a detail panel in place (animate transform only, never opacity, so content is never left invisible). No modal.
- **Wizard validation** (`07`): validate on Continue; show inline field errors; block advance; never a modal. Live cost recalculates on every change.
- **Drag-drop kanban** (`09`): native HTML5 DnD; on drop, move card + recompute column counts. Filters AND together and recompute counts live.
- **Filter pattern** (shared by `06` + `09`): few-value/frequent dimensions = segmented controls or chips; many-value/extensible dimensions = dropdown menus; a unified active-filter chip row aggregates ALL active filters (removable, + Clear all).
- **White-label** (`11`): accent is a CSS variable scoped to the workspace; recolours nav-active, primary buttons, links only. Print + status colours unaffected.
- **Print** (`08`): `@media print` hides `.side/.top/.jump`, shows a print-only document header, sets page breaks. Same DOM, server-renderable.

## State management (Alpine.js targets)

- Scan-results header filters (date range, provider, schedule) drive all tab content.
- Citations: `{provider, sentiment, source, topic, query, sort, dir, openRows}`.
- Wizard: `{step, brand, domain, vertical, questions[], providers[], schedule, errors{}}` + derived `cost`.
- Kanban: `{cards[], brandFilter, typeFilter, topicFilter, assigneeFilter, dragId}`.
- Branding: `{logo, displayName, accent, showPoweredBy}` persisted per org; injected as CSS vars into the layout.

## Assets

No external image/icon assets - all icons are inline SVG (stroke-based, 1.6-1.8 stroke width, currentColor). Logos/illustrations in the mocks are placeholders (dashed slots or drag-drop targets); the real app supplies brand logos via the white-label settings. No font licence needed (system stack).

A `screenshots/` folder holds a reference PNG of each surface (`01-tokens.png` ... `12-marketing.png`) for PRs and async review.

## Files in this bundle

- `tokens.css`, `components.css` - ship these.
- `01-tokens.html` ... `12-marketing.html`, `index.html` - design references (open `index.html` first).
- The HTML files reference `tokens.css` + `components.css` by relative path, so keep them in the same folder.
