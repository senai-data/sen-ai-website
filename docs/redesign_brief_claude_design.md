# Prompt à coller dans Claude Design (claude.ai web)

> Le prompt complet est en bas. Copie-le tel quel pour démarrer la session design. Si tu veux ajuster un paramètre (vertical principal, ton, etc.), modifie avant de coller.

---

## Pourquoi ce prompt est différent du `redesign_brief_2026_06.md`

- `redesign_brief_2026_06.md` = brief technique pour **Claude Code** (édite des fichiers, refactor, ship un PR). Long, riche en chemins de fichiers + références mémoire.
- Ce prompt = brief pour **Claude Design** (chat web, génère des mockups HTML / palettes / specs visuelles). Court, dense, demande des deliverables visuels.

Workflow attendu : Claude Design produit des mockups + un token system + des specs de composants. Tu valides visuellement. Tu colles ensuite la sortie dans Claude Code via le repo pour implémenter.

---

## ⬇ Prompt à copier-coller verbatim ⬇

---

You are helping me redesign a B2B SaaS called **sen-ai.fr**. The current UI has shipped 35+ pages over the last six months but the design language drifted into ad-hoc inline Tailwind classes : inconsistent spacing, mixed card paddings (p-4 vs p-5 vs p-6 vs p-8), mixed radii, no real type scale, no extracted components. I need to ship a coherent design system before opening the platform to paying agency clients.

## Product in one paragraph

sen-ai.fr measures how generative AI systems (ChatGPT, Gemini, Claude, Mistral) cite brands when answering buyer questions. Customers buy "scan credits" that ask each AI 10 times the same question and aggregate the citations + sentiment. Two audiences : (a) solo brand owners (pharma, cosmetics, mid-market consumer brands) running their own brand ; (b) agencies tracking 5-50 client brands at once with their own logo in the sidebar (white-label).

## Constraints (non-negotiable)

- Tech : **Astro 5 SSR + Tailwind CSS 3 + Alpine.js**. No React, no Vue, no Svelte, no CSS-in-JS.
- **No em-dashes anywhere** in copy or design assets. Use ` - ` (hyphen with spaces).
- **Multi-vertical zero hardcode** : every label, illustration, persona must read for cosmetics, pharma, automotive, B2B services. No brand names hardcoded.
- **Laws of UX visible** : Hick (cap choices), Jakob (familiar SaaS patterns), Aesthetic-Usability, Peak-End (great hero + great close), Von Restorff (one critical CTA / page max), Miller (chunk in 5-7), Serial Position (best info first + last), Fitts (big click targets), Doherty Threshold (< 400ms).
- **Inline > modal** : prefer inline editing, inline pickers, inline error feedback.
- **SSR-first** : critical read paths (compliance reports, scan results, marketing) must work without JS.
- Print CSS for compliance pages : the PDF is server-rendered from the same HTML.

## Brand attributes to channel

- **Professional, sober, trustworthy.** Pharma DPOs need to attach our reports to procurement files.
- **Measured, not aggressive.** We are about evidence + audit trail, not growth-hacking.
- **AI-aware but not AI-flavoured.** We measure AI ; we are not "AI for everything".
- **Multi-tenant agency-ready.** The same UI must feel right for a solo brand owner and for an agency managing 30 brands.

Competitor analogues for visual reference : Ahrefs (data density), Linear (motion + restraint), Datadog (multi-tenant + role-based density), Webflow agency dashboard (white-label).

## Current design tokens (drop or refine, don't preserve as-is)

- Primary : `coral` (warm orange-pink, ~#F06A5C)
- Text : `charcoal-dark` (~#1A1A1A) + `charcoal-light` (~#6B7280)
- Surfaces : white + gray-50 / gray-100
- Status : emerald (positive), red (critical), amber (warning), blue (info), sky (auto / neutral chip)
- Font : system stack only
- Spacing : inconsistent
- Radii : `rounded-md` / `rounded-lg` / `rounded-xl` mixed unpredictably
- Shadows : `shadow-sm` everywhere on cards, no depth hierarchy

The coral primary is a brand asset, keep it. Everything else is open.

## The 35+ surfaces (don't redesign individually, but understand the inventory)

### Marketing tier (anonymous)
landing `/`, `/agency`, `/pricing`, `/methodology` (+ `/fr/methodology`), `/privacy`, `/terms`, `/mentions-legales`, `/register`, `/login`, `/forgot-password`, `/reset-password`, `/verify-email`, `/404`, `/invite/[token]`.

### Onboarding
`/welcome` (first-workspace wizard, branches on agency-intent flag).

### In-app dashboard (auth, DashboardLayout = sidebar + main)
- `/app/dashboard` per-workspace home (KPIs + recent scans)
- `/app/agency/overview` cross-workspace dashboard (cards per client, sort by crisis severity DESC then weakest brand-mention-rate)
- `/app/agency/bulk` bulk-create N workspaces from a textarea
- `/app/agency/branding` per-org white-label (logo URL + display name + accent color)
- `/app/scans` scan list
- `/app/scans/new` create-scan wizard
- `/app/content` content pipeline kanban (FAQ + article generation)
- `/app/reports` snapshot
- `/app/settings` per-workspace settings (credits, brands, trust sources, members)
- `/app/compliance` org-level AI Act compliance hub (audit log + DPIA template + sub-processors table + changelog)

### Scan-results sub-pages (auth, ScanResultsLayout = sticky header + tabs)
The header shows : grade letter badge (A/B/C/D), brand name + domain, rate %, delta arrow + sparkline, date range picker, provider filter (All / Gemini / OpenAI), schedule chip (Manual / Weekly / Monthly), Rescan button.

Tabs (16, currently a horizontal strip overflowing on most screens) : Overview, Topics, Personas, Questions, Citations, Wikipedia, Page Audit, Schema, Competitors, Reddit, PR / Media, Internal links, YouTube, Crisis, Compliance, Actions.

**This 16-tab strip is the highest-impact redesign target.** I want it grouped into 3 macro-categories with 2-level nav :
1. **Visibility** (Overview, Topics, Personas, Questions, Citations)
2. **Investigation** (Wikipedia, Page Audit, Schema, Competitors, Reddit, PR / Media, Internal links, YouTube)
3. **Action** (Crisis, Actions, Compliance)

Propose a final grouping you think is more honest.

## Deliverables I need from this session

In order of priority :

1. **Design token system** : one source of truth for spacing scale, radius scale, shadow tiers, type scale (h1..h5 + body + small + micro + code), colour roles (primary, surface, surface-elevated, border, text-primary, text-secondary, text-muted, status-positive, status-warning, status-critical, status-info, status-neutral). Express as CSS variables ready to drop into a `tailwind.config.js` theme extension.

2. **Component inventory** with a visual mockup for each (PNG-ish via HTML preview is fine). At minimum :
    - Card (3 variants : flat, elevated, highlighted with status border)
    - Stat (KPI tile : large number + small label + delta arrow + sparkline)
    - Chip (3 sizes, 6 colour roles, with optional icon + dot)
    - Badge / Score badge (A/B/C/D letter grade)
    - Severity badge (none/low/medium/high/critical with the AI Act sentiment colour scale)
    - Button (primary, secondary, tertiary / link, danger, ghost, 3 sizes)
    - Input / Select / Textarea (with focus state, error state, helper text)
    - Table (clean, sortable, with optional row-level expand)
    - EmptyState (illustration slot + heading + body + 1 CTA)
    - Modal (inline-preferred fallback only)
    - Toast (auto-dismiss, 4 status colours)
    - Tooltip (Tip wrapper, already exists in repo)
    - Tabs (2-level nav with macro-category + leaf)
    - BreadcrumbStep (wizard progress indicator)
    - Sparkline (inline SVG, 0-100 range, last point highlighted)
    - ProgressDots (3-5 dot wizard step indicator)
    - WorkspaceCard (for the cross-workspace agency dashboard : name + domain + grade + status chip + crisis chip + mini-stats grid)
    - SentimentChip (positive / neutral / negative with counts, used 3 places already)

3. **Two flagship page mockups** that exercise the new tokens + components :
    - `/app/scans/{id}/results` Overview tab (target audience : agency owner triaging her 30 clients)
    - `/app/agency/overview` cross-workspace dashboard (12 workspace cards, mixed status, one in crisis)

4. **Tab navigation restructure** : visual mock of the new 2-level nav + a click-through prediction for the top-3 user flows (open a scan, drill into a competitor, download a compliance PDF).

5. **Mobile decision** : pick one of (a) full mobile-first redesign for marketing + key in-app pages, (b) desktop-only ≥ 1024px with a graceful "open on a wider screen" landing on small viewports. Defend the choice in 3 lines.

## Non-goals (don't propose)

- Don't redesign the API, the worker, or the database schemas.
- Don't replace Alpine with React.
- Don't introduce a new font foundry licence in the proposal.
- Don't break existing URLs (audit reports + email links depend on them).
- Don't redesign the print CSS independently of the on-screen CSS.

## How to ship

Once the token system + flagship mockups are validated, I'll bring them back into my Claude Code session in the repo to implement page by page. So your output should be high-fidelity enough for a developer to translate into Astro components without re-guessing intent. Where you propose a Tailwind class change, name it. Where you propose a CSS variable, give me the variable name and value.

## First thing to do

Start by acknowledging the brief, then propose the token system. Don't jump to page mockups before tokens are agreed.

---

(end of paste-into-Claude-Design prompt)
