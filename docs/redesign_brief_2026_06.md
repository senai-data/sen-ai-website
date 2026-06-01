# sen-ai.fr - Full UI Redesign Brief (Jun 2026)

> **For the next session.** This is the brief to paste into Claude when starting a fresh design work session. The redesign goal is a consistent, professional visual language across the 35+ surfaces of the app + marketing pages, ready to onboard agency clients with confidence.

---

## 1. Product context (read first)

**sen-ai.fr** is a SaaS that measures how generative AI systems (ChatGPT, Gemini, Claude, Mistral) cite brands when answering buyer questions. Customers buy "scan credits" that run questions through the LLMs N times and aggregate the brand mentions, citations and sentiment.

Two audiences :
1. **Solo brand owners** (pharma, cosmetics, consumer-tier brands). Buy 200-1000 scan credits, run their own brand.
2. **Agencies** (SEO / marketing / branding shops). Buy 5000-20000 credits, manage 5-50 client workspaces, want their own logo / display name in the sidebar (white-label lite already shipped 2026-05-30).

The platform is live in production on a Hetzner VPS. The 20+ feature surfaces ship in 5 categories : **scans** (AI visibility tracker), **content** (FAQ + article generation for SEO), **reports**, **agency tooling**, **compliance** (EU AI Act, GDPR).

---

## 2. Tech stack (constraints)

- **Astro 5** with SSR (Node adapter). Most pages are server-rendered ; client-side interactivity is **Alpine.js** + plain JS.
- **Tailwind CSS 3** with the `prose` typography plugin.
- **No React / Vue / Svelte.** Adding one is out of scope for the redesign.
- **No build-time API calls** (Astro frontmatter fetches via internal docker network at runtime).
- **No external CDN for fonts** today (just system font stack).
- Auth = HttpOnly JWT cookie. Multi-tenant via `organizations.id` + `client.id` cookies.

---

## 3. Current design tokens (what to inherit OR explicitly break)

```
colors:
  coral (primary CTA + active state)
  coral-dark (hover state)
  coral-light (italic taglines)
  charcoal-dark (body text + headings)
  charcoal-light (secondary text, metadata)
  emerald (positive, success, completed)
  red (critical, negative, error)
  amber (warning, in-progress)
  blue (info, navigation focus)
  sky (auto-rescan badge, neutral info)
  gray-50/100/200 (surfaces, borders)

typography:
  System font stack (no custom font).
  Headings : bold, charcoal-dark.
  Body : leading-relaxed, charcoal-light.
  Code / tabular : tabular-nums + bg-gray-100 inline.

spacing:
  Inconsistent across pages. Mix of p-4 / p-5 / p-6 / p-8 on cards.
  Buttons : px-3 py-1.5 (small), px-4 py-2 (medium).
  Rounded : rounded-md / rounded-lg / rounded-xl mixed.

shadows:
  shadow-sm everywhere on cards. shadow-md on hover. No depth hierarchy.
```

**The redesign should propose a tighter design token system** (e.g. one card padding, one card radius, one set of button sizes) and apply it consistently.

---

## 4. Surfaces inventory (35+)

### Marketing tier (anonymous + new visitors)
- `/` landing
- `/agency` agency-mode landing
- `/pricing` pay-as-you-go credit packs
- `/methodology` + `/fr/methodology` public AI Act / GDPR whitepaper
- `/privacy` privacy policy
- `/terms` terms of service
- `/mentions-legales` legal notice
- `/register` signup form (+ Google OAuth)
- `/login` + `/forgot-password` + `/reset-password` auth flow
- `/verify-email` email verification landing
- `/404` not found
- `/invite/[token]` accept invite
- `/audit/[id]` legacy
- `/dashboard/[id]` legacy

### Onboarding
- `/welcome` first-workspace wizard (post-signup, branches on `?intent=agency`)

### In-app (auth required, DashboardLayout)
- `/app/dashboard` per-workspace home (KPIs + recent scans)
- `/app/agency/overview` cross-workspace dashboard (NEW)
- `/app/agency/bulk` bulk-add workspaces (NEW)
- `/app/agency/branding` white-label settings (NEW)
- `/app/scans` scan list
- `/app/scans/new` create-scan wizard
- `/app/content` content pipeline kanban (FAQ + article generation)
- `/app/reports` PowerPoint-style snapshot
- `/app/settings` per-workspace settings (credits, brands, trust sources, members)
- `/app/compliance` org-level AI Act compliance hub (audit log + DPIA template + sub-processors + changelog)
- `/app/admin/...` superadmin
- `/app/org` org members + invites

### In-app scan results (auth required, ScanResultsLayout = sticky header + tabs)
- `/app/scans/{id}/index` setup wizard (pipeline status)
- `/app/scans/{id}/results` Overview tab (hero KPIs + topic visibility + sentiment chip)
- `/app/scans/{id}/topics` per-topic drill-down
- `/app/scans/{id}/personas` per-persona cards (Brand Perception chip with sentiment breakdown)
- `/app/scans/{id}/questions` question explorer (filters + AI response viewer)
- `/app/scans/{id}/citations` citations dive
- `/app/scans/{id}/wikipedia` Wikipedia entity audit
- `/app/scans/{id}/audit` Princeton GEO page audit
- `/app/scans/{id}/schema` Schema.org JSON-LD audit
- `/app/scans/{id}/competitors` competitor reverse-engineering (with LLM recommendation card)
- `/app/scans/{id}/reddit` Reddit opportunity finder
- `/app/scans/{id}/pr-outreach` PR / media outreach list
- `/app/scans/{id}/internal-linking` internal linking audit
- `/app/scans/{id}/youtube` YouTube creator mapping
- `/app/scans/{id}/crisis` Crisis radar (sparkline + 3σ anomaly)
- `/app/scans/{id}/compliance` per-scan AI Act transparency report (downloadable PDF)
- `/app/scans/{id}/actions` opportunities funnel

---

## 5. Design system priorities

In rough order of expected payoff :

1. **Extract a component library** (currently inlined ad-hoc on every page) :
   - `Card`, `Stat`, `Chip`, `Badge`, `Button`, `Input`, `Select`, `Textarea`, `Table`, `EmptyState`, `Modal`, `Toast`, `Tooltip`, `Tabs`, `BreadcrumbStep`, `Sparkline`, `ProgressDots`, `KpiCard`, `WorkspaceCard`, `SentimentChip`, `SeverityBadge`, `ScoreBadge` (A/B/C/D grade).
   - Astro components in `src/components/ui/` with TypeScript props + Tailwind variants.
2. **Sticky header / sidebar layout audit**. The current sidebar is dense (org chip + workspace switcher + Add client + Workspaces / Bulk / Branding agency links + main nav + credits widget + user). Hard to scan at a glance.
3. **KPI hierarchy on Overview**. Eight KPIs (Brand Mention Rate, Top Competitor, Critical Gaps, Tests Run, Position when cited 5-bucket, Recommendation Rate, Brand Sentiment, Position in Response) compete for attention. Apply Serial Position + Hick - pick a top-3, demote the rest into a secondary band.
4. **Tab navigation density** on ScanResultsLayout (16 tabs : Overview / Topics / Personas / Questions / Citations / Wikipedia / Page Audit / Schema / Competitors / Reddit / PR / Media / Internal links / YouTube / Crisis / Compliance / Actions). Way past Hick's threshold. Group into 3 macro-categories : *Visibility*, *Investigation*, *Action* with a 2-level nav.
5. **Compliance pages print CSS** is partial. Either polish it to be the source of truth (PDF rendered server-side via weasyprint already uses the same HTML) or accept it as good-enough and stop iterating.
6. **Mobile (375-768px)**. Currently broken in most places (sidebar fixed-width, dense tables). Decide : mobile-first redesign, or explicit "desktop only ≥ 1024px" disclaimer.
7. **i18n parity**. Only `/methodology` is bilingual today. Decide whether to extend FR coverage to the in-app surfaces or keep marketing-only.

---

## 6. Non-negotiable rules (from past sessions)

These are documented in memory under `feedback_*` and `project_*` files. Don't break them in the redesign.

- **NO em-dash anywhere** (UI copy, code comments, memos). Use ` - ` instead.
- **Multi-vertical zero hardcode** : no brand / vertical / country names in templates. Copy must read for cosmetics, pharma, automotive, B2B services.
- **Laws of UX** referenced on every interactive surface (Hick, Jakob, Aesthetic-Usability, Peak-End, Von Restorff, Miller, Serial Position, Fitts, Goal-Gradient, Doherty Threshold). The redesign should make these laws *visible* in the layout, not buried in code comments.
- **Inline > modal**. The current app prefers inline editing for renames, schedule pickers, etc. Keep this.
- **Server-side rendering** : every page must work with JavaScript disabled for the critical read paths (compliance reports, scan results, marketing pages). Alpine is fine for interactivity but not as a load-bearing renderer.

---

## 7. Reference patterns shipped recently (good examples to standardise)

- **Sentiment chip** with 3 buckets : `/app/scans/{id}/results` Overview section + `/personas` Brand Perception block. Same shape, same colour mapping (emerald / gray / red), used in 2 places. Extract.
- **Severity sparkline + 3σ badge** : `/app/scans/{id}/crisis` next to severity scores. Use the same primitive for any time-series metric.
- **Score grade badge** (A/B/C/D large) : ScanResultsLayout header + cross-workspace overview cards. Extract.
- **Schedule picker chip** : ScanResultsLayout next to Rescan button. Inline select + computed "next in Xd" hint. Extract for any per-row schedule editor.
- **Bulk input form** : `/app/agency/bulk` textarea + live count + cap warning + 3-bucket result panel. Extract for any future bulk creation.
- **PDF download + browser print** dual button : compliance pages. Extract.

---

## 8. Reference patterns shipped recently that need rework

- **Tab strip** on ScanResultsLayout : 16 tabs, scrolls horizontally on most screens, no grouping. Highest-impact redesign target.
- **Sidebar agency links** : "Workspaces overview" + "Bulk add" + "Branding" buried at the bottom of an already-busy sidebar. Discoverability is poor.
- **Empty states** : inconsistent visual treatment (some have illustrations, some have just text, some have CTA).
- **Form validation feedback** : per-page custom error rendering, no consistent pattern.

---

## 9. Non-goals (don't do)

- Don't redesign the worker, API, or database schemas.
- Don't refactor Alpine into React/Vue.
- Don't introduce a CSS-in-JS solution.
- Don't add a new auth flow (Google OAuth + email/password + invites stay).
- Don't break the existing routes (URLs are linked from emails, audit reports, social shares).
- Don't redesign the print CSS independently of the on-screen CSS (PDF endpoint renders the same HTML).

---

## 10. Suggested delivery plan (for the design session)

1. **Audit pass** : screenshot each of the 35+ surfaces, identify the inconsistencies.
2. **Token system proposal** : single source of truth for spacing, radius, shadow, type scale, colour roles.
3. **Component inventory** : list of Astro components to extract from the current ad-hoc inline markup. Migrate one component at a time + replace usages page by page.
4. **Tab nav restructure** : reduce 16 scan tabs to a 2-level nav (3 macro-groups + 5-6 leaves each).
5. **Mobile decision** : ship mobile-first redesign for marketing + key in-app pages, OR accept desktop-only with a graceful "use a wider screen" landing on small viewports.
6. **i18n decision** : extend FR beyond methodology, or hold.

---

## 11. Project memory pointers

The persistent memory for this project lives at `C:\Users\leed\.claude\projects\c--Users-leed-sen-ai-website\memory\`. Key files to load for context :

- `MEMORY.md` - the index. Loaded automatically by Claude.
- `project_todo_tracker.md` - shipped features changelog.
- `NEXT_SESSION_PROMPT.md` - state at end of last session.
- `feedback_no_em_dash.md` (referenced but worth reading first)
- `feedback_no_hardcoded_vertical.md`
- `feedback_inline_over_modal.md`
- `feedback_ux_laws.md`

---

## 12. First message to Claude (paste this verbatim)

> I'm starting a full UI redesign of sen-ai.fr. The brief is in `docs/redesign_brief_2026_06.md`. Read it, then propose : (a) a tightened design token system to replace the inconsistent inline values, (b) a list of Astro components to extract for a `src/components/ui/` library, (c) a tab navigation restructure for the 16-tab ScanResultsLayout. Ship the token system as a PR first. Don't touch the worker / API / DB.
