# sen-ai design tokens

Single source of truth for spacing, radius, shadow, type scale and colour roles. Tokens live in [src/styles/global.css](../src/styles/global.css) inside the Tailwind 4 `@theme {}` block; Tailwind auto-generates the matching utilities.

This document is the contract. The CSS file is the implementation.

## Migration posture (read first)

The previous design system did not have shadow / radius / type tokens at all, just a colour palette. Cards mix `rounded-md / lg / xl / 2xl` (4 sizes for the same role) and padding mixes `p-3 / 4 / 5 / 6 / 8`.

The token system is shipped **without overriding the Tailwind defaults**. That means:

- The 600+ existing `rounded-lg`, `shadow-sm`, `text-xs` utilities keep their current look.
- The new tokens add *additional* utilities (e.g. `rounded-card`, `shadow-card`, `text-body`) on new names.
- Pages migrate one PR at a time: when a page is reworked, its utilities switch to the new names.
- Once the brand utilities (`bg-coral`, `text-charcoal-dark`) are no longer used anywhere, we delete them in a final sweep.

This avoids a big-bang restyle that would silently shift every screen.

## Colour roles

Use these in new code. The brand names (`coral`, `charcoal`, `emerald`, `gold`, `gads`) stay defined and are allowed only inside unmigrated pages.

### Text

| Token              | Utility           | Use                                     |
|--------------------|-------------------|-----------------------------------------|
| `text`             | `text-text`       | Body copy, headings (default)           |
| `text-muted`       | `text-text-muted` | Secondary text, labels, metadata        |
| `text-subtle`      | `text-text-subtle`| Placeholder, tertiary metadata          |
| `text-inverse`     | `text-text-inverse` | Text on coloured fills                |

### Surfaces

| Token              | Utility               | Use                                  |
|--------------------|-----------------------|--------------------------------------|
| `surface`          | `bg-surface`          | Card body, modal body                |
| `surface-muted`    | `bg-surface-muted`    | Page background, table zebra row     |
| `surface-sunken`   | `bg-surface-sunken`   | Inline tag bg, code block            |

### Borders

| Token              | Utility               | Use                                  |
|--------------------|-----------------------|--------------------------------------|
| `border`           | `border-border`       | Card outline, hr                     |
| `border-strong`    | `border-border-strong`| Input outline, focused state         |

### Primary

| Token              | Utility               | Use                                  |
|--------------------|-----------------------|--------------------------------------|
| `primary`          | `bg-primary`          | CTA fill, active tab underline       |
| `primary-hover`    | `hover:bg-primary-hover` | Hover state on primary buttons    |
| `primary-soft`     | `bg-primary-soft`     | Soft button bg, selected chip bg     |
| `primary-contrast` | `text-primary-contrast` | Foreground on primary fill         |

### Status

All four roles ship with a `-soft` tint (for chip backgrounds) and a `-strong` variant (for icons / text on the tint).

| Role     | Solid                | Soft                      | Strong                          |
|----------|----------------------|---------------------------|---------------------------------|
| Success  | `bg-success`         | `bg-success-soft`         | `text-success-strong`           |
| Danger   | `bg-danger`          | `bg-danger-soft`          | `text-danger-strong`            |
| Warning  | `bg-warning`         | `bg-warning-soft`         | `text-warning-strong`           |
| Info     | `bg-info`            | `bg-info-soft`            | `text-info-strong`              |

### Focus ring

`ring-ring` for keyboard-focus outlines. Pairs with `outline-none focus:ring-2 focus:ring-ring/40`.

## Radius

| Utility            | Value      | Use                                     |
|--------------------|------------|-----------------------------------------|
| `rounded-pill`     | `9999px`   | chips, badges, sentiment dot, status pill |
| `rounded-control`  | `0.5rem`   | inputs, selects, sm/md buttons          |
| `rounded-card`     | `0.75rem`  | **default card radius**                  |
| `rounded-sheet`    | `1rem`     | modals, hero cards, empty-state panels  |

Existing `rounded-md/lg/xl/2xl` stay defined - they're the migration surface, not the target.

## Shadow

| Utility            | Use                                            |
|--------------------|------------------------------------------------|
| `shadow-card`      | Resting card. Replaces ad-hoc `shadow-sm`      |
| `shadow-elevated`  | Hover, sticky header, sparkline tooltip        |
| `shadow-overlay`   | Modal, dropdown, toast                         |

## Type scale

| Utility            | px   | Role                                      |
|--------------------|------|-------------------------------------------|
| `text-meta`        | 11   | timestamps, sublabels, "next in 7d"       |
| `text-caption`     | 12   | chips, badges, table footer               |
| `text-body`        | 14   | default UI text                           |
| `text-lede`        | 16   | section intros, tooltip body              |
| `text-h3`          | 18   | card title                                |
| `text-h2`          | 24   | section title                             |
| `text-h1`          | 30   | page title                                |
| `text-display`     | 36   | hero KPI digits, grade letter             |

Tailwind's `text-xs/sm/base/lg/xl/2xl/3xl/4xl` stay defined as the migration surface.

## Spacing (editorial rule, not a token change)

The Tailwind 0.25rem step scale is unchanged. The redesign codifies which steps are allowed for which role; review enforces:

| Role                     | Allowed                              |
|--------------------------|--------------------------------------|
| Card padding             | `p-6` (default) or `p-4` (compact)   |
| Card internal gap        | `gap-4`                              |
| Stack between cards      | `space-y-6`                          |
| Button padding (md)      | `px-4 py-2`                          |
| Button padding (sm)      | `px-3 py-1.5`                        |
| Page gutter              | `px-8` (already standard)            |
| Section vertical rhythm  | `py-8` (in-app) / `py-16` (marketing)|

Anything outside this set in a PR should be questioned during review.

## Non-negotiable copy rules (carried from past sessions)

- **No em-dash (—) or en-dash (–) anywhere** in UI copy or comments. Use ` - `. Enforced by `scripts/lint_ai_tells.py`.
- **No hardcoded vertical**: token names and component variants must work for cosmetics, pharma, automotive, B2B services equally. No `cosmetics-blue` or `pharma-green`.
- **Laws of UX visible in layout**, not buried in comments. Use the semantic tokens so the role (primary / success / warning) is readable in the markup.
- **SSR-first**: no token relies on a JS computation at runtime. All values are static CSS.
- **Inline > modal**: when in doubt, render edit affordances inline using `shadow-card`, not as overlays needing `shadow-overlay`.

## Adding a token

1. Add the CSS variable to `@theme {}` in `src/styles/global.css`.
2. Document it here under the right section.
3. If it duplicates an existing token, delete the older one in the same PR.
4. Never override a Tailwind default (`--radius-lg`, `--shadow-sm`, etc.) without grepping for usage across `src/` and migrating in the same PR.

## What's not in scope

- No new font (system stack stays).
- No new CSS-in-JS or design-token JSON pipeline.
- No dark mode (would need a `@theme dark` block + an explicit decision on which surfaces invert).
- No motion tokens yet (animation durations stay inline until we have ≥ 3 cases that disagree).
