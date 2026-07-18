/**
 * Shared data-fetching for scan result pages.
 * Called by ScanResultsLayout to avoid duplicating fetch logic across 5 sub-pages.
 */

const API = 'http://api:8000/api';

import { modelChangeNote } from './sparkline';

export interface ScanHeaderData {
  scan: {
    id: string;
    domain: string;
    name: string;
    focus_brand: string | null;
    status: string;
    completed_at: string | null;
  };
  overview: {
    domain: string;
    scan_name: string;
    focus_brand: string | null;
    total_tests: number;
    target_cited: number;
    citation_rate: number;
    providers: string[];
    scan_date: string | null;
    editorial: any;
  } | null;
  runs: Array<{
    id: string;
    run_index: number;
    status: string;
    completed_at: string | null;
    summary: any;
  }>;
  // In-flight rescan of this lineage (status pending/scanning, < 24h old).
  // Feeds the header's "Rescan running" chip ; null when nothing is running.
  activeRun: { id: string; status: string; progress_pct: number; progress_message: string | null } | null;
  grade: { letter: string; bg: string; text: string; sub: string; label: string };
  currentRate: number;
  delta: number | null;
  // P3 model eras : non-null when the last two runs ran on different AI model
  // mixes - the header delta is suppressed and this note explains why.
  eraNote: string | null;
  sparkSvg: string;
  tabCounts: {
    topics: number;
    personas: number;
    questions: number;
    citations: number;
    actions: number;
  };
  error: string | null;
}

function getGrade(rate: number | null | undefined) {
  if (rate === null || rate === undefined) return { letter: '?', bg: 'bg-gray-100', text: 'text-gray-600', sub: 'text-gray-500', label: 'No data' };
  if (rate >= 50) return { letter: 'A', bg: 'bg-emerald-50', text: 'text-emerald-600', sub: 'text-emerald-500', label: 'Excellent' };
  if (rate >= 30) return { letter: 'B', bg: 'bg-blue-50', text: 'text-blue-600', sub: 'text-blue-500', label: 'Good' };
  if (rate >= 15) return { letter: 'C', bg: 'bg-amber-50', text: 'text-amber-600', sub: 'text-amber-500', label: 'Average' };
  return { letter: 'D', bg: 'bg-red-50', text: 'text-red-600', sub: 'text-red-500', label: 'Low' };
}

function buildSparkline(vals: number[], w = 80, h = 24): string {
  if (!vals || vals.length < 2) return '';
  const max = Math.max(...vals, 1);
  const min = Math.min(...vals, 0);
  const rng = max - min || 1;
  const step = w / (vals.length - 1);
  const pts = vals.map((v, i) => `${(i * step).toFixed(1)},${(h - ((v - min) / rng) * h).toFixed(1)}`).join(' ');
  const last = vals[vals.length - 1];
  const c = last >= 30 ? '#10b981' : last >= 15 ? '#f59e0b' : '#ef4444';
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="inline-block align-middle"><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="${((vals.length - 1) * step).toFixed(1)}" cy="${(h - ((last - min) / rng) * h).toFixed(1)}" r="2" fill="${c}"/></svg>`;
}

export async function fetchScanHeader(
  scanId: string,
  token: string,
  range: string = 'all',
  fromDate?: string,
  toDate?: string,
  provider?: string,
): Promise<ScanHeaderData> {
  const headers = { Cookie: `token=${token}` };
  let error: string | null = null;

  // Build query params for results endpoint
  const useAggregated = range !== 'latest';
  const resultParams = new URLSearchParams();
  if (useAggregated) {
    if (fromDate) resultParams.set('from_date', fromDate);
    if (toDate) resultParams.set('to_date', toDate);
    // Perf 2026-07-18 : the header only needs aggregates + tab counts. The
    // full aggregated payload was 30 MB / ~5s and every tab page ALSO
    // fetches it for its own content - summary mode keeps the header call
    // at a few hundred KB.
    resultParams.set('summary', 'true');
  }
  if (provider && provider !== 'all') resultParams.set('provider', provider);
  const aggParams = resultParams.toString() ? `?${resultParams.toString()}` : '';

  try {
    const resultsUrl = useAggregated
      ? `${API}/scans/${scanId}/results/aggregated${aggParams}`
      : `${API}/scans/${scanId}/results`;

    const [resScan, resResults, resLineage, resOpps] = await Promise.all([
      fetch(`${API}/scans/${scanId}`, { headers }),
      fetch(resultsUrl, { headers }),
      fetch(`${API}/scans/${scanId}/lineage`, { headers }),
      fetch(`${API}/scans/${scanId}/opportunities`, { headers }),
    ]);

    if (resScan.status === 404) {
      return {
        scan: { id: scanId, domain: '', name: '', focus_brand: null, status: 'not_found', completed_at: null },
        overview: null, runs: [], activeRun: null, grade: getGrade(null), currentRate: 0, delta: null, eraNote: null, sparkSvg: '',
        tabCounts: { topics: 0, personas: 0, questions: 0, citations: 0, actions: 0 }, error: 'Scan not found',
      };
    }

    const scan = resScan.ok ? await resScan.json() : null;
    const resultsData = resResults.ok ? await resResults.json() : null;
    const lineageData = resLineage.ok ? await resLineage.json() : null;
    const oppsData = resOpps.ok ? await resOpps.json() : null;

    const overview = resultsData?.overview || null;
    const byPersona = resultsData?.by_persona || [];
    const details = resultsData?.details || [];
    const oppsSummary = oppsData?.summary || {};

    // Lineage
    const allRuns = lineageData?.runs || [];
    const runs = allRuns
      .filter((r: any) => r.status === 'completed')
      .sort((a: any, b: any) => (a.run_index ?? 0) - (b.run_index ?? 0));
    // In-flight rescan detection. The 24h cut guards against crashed runs
    // stuck at status='scanning' forever - a real rescan takes < 1h.
    const IN_FLIGHT_MAX_AGE_MS = 24 * 3600 * 1000;
    const activeRaw = allRuns.find((r: any) => {
      if (r.status !== 'pending' && r.status !== 'scanning') return false;
      if (!r.created_at) return false;
      const iso = r.created_at.endsWith('Z') || r.created_at.includes('+') ? r.created_at : r.created_at + 'Z';
      return Date.now() - new Date(iso).getTime() < IN_FLIGHT_MAX_AGE_MS;
    });
    const activeRun = activeRaw
      ? {
          id: activeRaw.id,
          status: activeRaw.status,
          progress_pct: activeRaw.progress_pct || 0,
          progress_message: activeRaw.progress_message || null,
        }
      : null;
    const hasLineage = runs.length > 1;
    const sparkRates = runs.map((r: any) => r.summary?.brand_mention_rate ?? null).filter((v: any) => v !== null);
    const prevRun = hasLineage ? runs[runs.length - 2] : null;
    const lastRun = hasLineage ? runs[runs.length - 1] : null;
    const currentRate = overview?.citation_rate ?? 0;
    const prevRate = prevRun?.summary?.brand_mention_rate ?? null;
    // P3 model eras : "vs last run" across a model-mix change compares two
    // different instruments - suppress the delta, surface the note instead.
    // Unknown models on either side (empty dict) = no boundary claimed.
    const prevModels = prevRun?.summary?.models || {};
    const lastModels = lastRun?.summary?.models || {};
    const crossesEra = Object.keys(prevModels).length > 0 && Object.keys(lastModels).length > 0
      && modelChangeNote(prevModels, lastModels) !== '';
    const eraNote = crossesEra ? modelChangeNote(prevModels, lastModels) : null;
    const delta = prevRate !== null && !crossesEra ? currentRate - prevRate : null;

    // Tab counts. Aggregated summary mode precomputes them server-side
    // (details is empty there) ; the legacy details walk stays as the
    // fallback for the non-aggregated /results path.
    const headerCounts = resultsData?.header_counts || null;
    const uniqueTopics = new Set(byPersona.map((p: any) => p.topic).filter(Boolean));
    const allCitationDomains = new Set<string>();
    details.forEach((d: any) => {
      (d.citations || []).forEach((c: any) => {
        const dom = c.domaine || c.domain || '';
        if (dom) allCitationDomains.add(dom.toLowerCase());
      });
    });
    const tabCounts = {
      topics: uniqueTopics.size,
      personas: byPersona.length,
      questions: headerCounts ? headerCounts.questions : details.length,
      citations: headerCounts ? headerCounts.citation_domains : allCitationDomains.size,
      actions: (oppsSummary.critique || 0) + (oppsSummary.haute || 0) + (oppsSummary.moyenne || 0),
    };

    return {
      scan: {
        id: scanId,
        domain: scan?.domain || overview?.domain || '',
        name: scan?.name || overview?.scan_name || '',
        focus_brand: scan?.focus_brand_name || overview?.focus_brand || null,
        status: scan?.status || 'unknown',
        completed_at: scan?.completed_at || null,
      },
      overview,
      runs,
      activeRun,
      grade: getGrade(currentRate),
      currentRate,
      delta,
      eraNote,
      sparkSvg: buildSparkline(sparkRates),
      tabCounts,
      error: null,
    };
  } catch (e: any) {
    return {
      scan: { id: scanId, domain: '', name: '', focus_brand: null, status: 'error', completed_at: null },
      overview: null, runs: [], activeRun: null, grade: getGrade(null), currentRate: 0, delta: null, eraNote: null, sparkSvg: '',
      tabCounts: { topics: 0, personas: 0, questions: 0, citations: 0, actions: 0 }, error: e.message,
    };
  }
}
