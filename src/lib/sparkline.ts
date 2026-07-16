/**
 * Shared SSR sparkline builder - single source for the Overview KPI tiles,
 * the dashboard hero and the agency overview wscards (was copy-pasted in
 * all three).
 *
 * Design notes (kept from the original results.astro implementation) :
 *  - each run gets a visible micro-dot + a generous invisible hit circle
 *    (r=8 >> r=2) carrying a native SVG <title> tooltip "23 Mar : 41%".
 *    Fitts : the touch target is 4x the visible dot. Doherty + SSR-first :
 *    browser-native tooltip, zero JS, zero chart lib.
 *  - the LAST dot stays the big one (Recency / Serial Position : the
 *    current value is the anchor).
 *  - P3 model eras : markers[i]=true rings the dot - the AI model mix
 *    changed AT that point, so the segment leading into it compares two
 *    different instruments. The <title> carries the old → new detail
 *    (callers append it to the label).
 */

export type SparkTone = 'up' | 'down' | 'flat';

export function sparkSvg(
  vals: number[],
  tone: SparkTone = 'up',
  labels: string[] = [],
  w = 120,
  h = 34,
  markers: boolean[] = [],
): string {
  if (!vals || vals.length < 2) return '';
  const pad = 4;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const rng = max - min || 1;
  const xf = (i: number) => pad + (i * (w - 2 * pad)) / (vals.length - 1);
  const yf = (v: number) => h - pad - ((v - min) / rng) * (h - 2 * pad);
  const pts = vals.map((v, i) => [xf(i), yf(v)] as [number, number]);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const colour = tone === 'down' ? 'var(--status-critical)' : tone === 'flat' ? 'var(--color-text-muted)' : 'var(--status-positive)';
  const last = pts[pts.length - 1];
  const id = 'g' + Math.random().toString(36).slice(2, 7);
  const area = d + ` L${last[0].toFixed(1)} ${h - pad} L${pts[0][0].toFixed(1)} ${h - pad} Z`;
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const dots = pts.map((p, i) => {
    const isLast = i === pts.length - 1;
    const title = labels[i] ? `<title>${esc(labels[i])}</title>` : '';
    const ring = markers[i]
      ? `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4.6" fill="none" stroke="${colour}" stroke-width="1.1" opacity=".75" pointer-events="none"/>`
      : '';
    return `<g>${title}
      <circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="8" fill="transparent" style="cursor:${labels[i] ? 'help' : 'default'}"/>
      ${ring}
      <circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${isLast ? 2.6 : 1.8}" fill="${colour}" stroke="#fff" stroke-width="${isLast ? 1.5 : 1}" pointer-events="none"/>
    </g>`;
  }).join('');
  return `<svg width="${w}" height="${h}" style="display:block;overflow:visible">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${colour}" stop-opacity=".16"/><stop offset="1" stop-color="${colour}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#${id})"/>
    <path d="${d}" fill="none" stroke="${colour}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" pointer-events="none"/>
    ${dots}
  </svg>`;
}

/** P3 model eras - human note for a boundary point's tooltip.
 * prev/curr = the two consecutive trend points' summary.models dicts. */
export function modelChangeNote(prev: Record<string, string> | null | undefined, curr: Record<string, string> | null | undefined): string {
  const p = prev || {};
  const c = curr || {};
  const keys = Array.from(new Set([...Object.keys(p), ...Object.keys(c)])).sort();
  const parts = keys
    .filter((k) => p[k] !== c[k])
    .map((k) => `${k}: ${p[k] || 'none'} → ${c[k] || 'none'}`);
  return parts.length ? `AI models updated - ${parts.join(', ')}` : '';
}

/** P3 model eras - chip wording : a provider joining/leaving is a coverage
 * change (outage or setup change), a version bump is a model update. */
export function boundaryChipLabel(prev: Record<string, string> | null | undefined, curr: Record<string, string> | null | undefined): string {
  const pk = Object.keys(prev || {}).sort().join(',');
  const ck = Object.keys(curr || {}).sort().join(',');
  return pk === ck ? 'AI models updated' : 'AI coverage changed';
}
