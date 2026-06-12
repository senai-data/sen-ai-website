/**
 * Act-scope resolution (act-scope plan, P1).
 *
 * Act / Sources leaves (actions, crisis, pr-outreach, youtube, reddit,
 * wikipedia, audit, schema, internal-linking, competitors) document the
 * PRESENT: they must read the latest completed scan of the lineage, not
 * whatever scan id happens to be in the URL (root vs child depends on the
 * entry path, and the date picker rebuilds its URLs on the root).
 *
 * Exceptions:
 * - An explicit ?range=latest&run=X deep link (a specific scan picked in
 *   the date picker) is honored verbatim.
 * - compliance is NOT act-scoped: it documents one precise scan
 *   (audit-grade) and must never silently switch.
 * - Visibility leaves (overview, citations, questions, personas, topics)
 *   honor the date picker and do not use this helper.
 */

const API = 'http://api:8000/api';

export interface ActScan {
  /** Scan id the leaf should fetch its data with. */
  scanId: string;
  /** True when the resolved id differs from the URL scan id (show the notice). */
  resolved: boolean;
  /** completed_at of the resolved scan, for the notice line. */
  completedAt: string | null;
}

export async function resolveActScan(
  urlScanId: string,
  token: string,
  searchParams: URLSearchParams,
): Promise<ActScan> {
  // Explicit scan picked in the date picker - honor the user's intent.
  const explicitRun = searchParams.get('range') === 'latest' ? searchParams.get('run') : null;
  if (explicitRun) {
    return { scanId: explicitRun, resolved: false, completedAt: null };
  }

  const fallback: ActScan = { scanId: urlScanId, resolved: false, completedAt: null };
  try {
    const res = await fetch(`${API}/scans/${urlScanId}/lineage`, {
      headers: { Cookie: `token=${token}` },
    });
    if (!res.ok) return fallback;
    const data = await res.json();
    // Lineage is ordered by run_index asc - last completed = current scan.
    const completed = (data?.runs || []).filter((r: any) => r.status === 'completed');
    const latest = completed[completed.length - 1];
    if (!latest?.id) return fallback;
    return {
      scanId: String(latest.id),
      resolved: String(latest.id) !== String(urlScanId),
      completedAt: latest.completed_at || null,
    };
  } catch {
    return fallback;
  }
}
