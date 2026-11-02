/**
 * Issue #26: in-app update prompt keyed off the GitHub Releases feed.
 *
 * The repo is public, so the releases feed needs no credentials and the check
 * sends no user data — it is a plain GET of the public feed below, and it is
 * the only network call this module makes. The repo currently has zero
 * releases, so in practice every check today ends in the silent paths:
 * an empty feed, an unreachable feed, or no applicable newer release all
 * produce no prompt, by design.
 *
 * Play-installed builds defer to Google Play's own updater. Installer
 * detection lands with the Capacitor wrap (Req. 2); until then this
 * web-layer check ships in the shared app shell for every build, and the
 * deferral is applied at that layer when it exists.
 */

/** The public GitHub Releases feed for this repo. No user data is sent. */
export const RELEASES_URL = 'https://api.github.com/repos/jasonzawacki-arch/jobledger/releases';

/**
 * The running app's version. The source of truth is `version` in
 * package.json; build.mjs surfaces it at build time as the boot constant
 * window.__JL_VERSION__ (the same pattern as window.__JL_MODE__ /
 * window.__JL_REMOTE__), prefixed onto every built bundle. The literal
 * fallback only covers running the TypeScript outside a built page.
 */
export const CURRENT_VERSION: string =
  (typeof window !== 'undefined' && (window as unknown as { __JL_VERSION__?: string }).__JL_VERSION__) || '0.1.0';

const DAY_MS = 24 * 60 * 60 * 1000;
const LAST_CHECK_KEY = 'jobledger.update.lastCheck';
const SNOOZED_KEY = 'jobledger.update.snoozed';

export interface ReleaseInfo {
  /** Normalized version (no leading "v"), e.g. "0.2.0" or "0.3.0-beta.1". */
  version: string;
  /** The release tag as published, e.g. "v0.2.0". */
  tag: string;
  /** Release-notes page. */
  htmlUrl: string;
  /** Hand-off target for the Update button: first asset download, else the release page. */
  downloadUrl: string;
  /** Release notes body (may be empty). */
  notes: string;
  prerelease: boolean;
}

interface ParsedVersion { core: number[]; pre: string[] }

export function parseVersion(raw: string): ParsedVersion | null {
  const v = String(raw || '').trim().replace(/^v/i, '');
  const m = /^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/.exec(v);
  if (!m) return null;
  return {
    core: [Number(m[1]), Number(m[2] || 0), Number(m[3] || 0)],
    pre: m[4] ? m[4].split('.') : [],
  };
}

export function normalizeVersion(raw: string): string | null {
  const p = parseVersion(raw);
  if (!p) return null;
  return p.core.join('.') + (p.pre.length ? '-' + p.pre.join('.') : '');
}

const isNum = (s: string) => /^\d+$/.test(s);

/** Semver-ish compare: negative if a < b, 0 if equal, positive if a > b. Unparseable sorts lowest. */
export function compareVersions(a: string, b: string): number {
  const pa = parseVersion(a), pb = parseVersion(b);
  if (!pa && !pb) return 0;
  if (!pa) return -1;
  if (!pb) return 1;
  for (let i = 0; i < 3; i++) {
    if (pa.core[i] !== pb.core[i]) return pa.core[i] < pb.core[i] ? -1 : 1;
  }
  // A release outranks the same core with a prerelease suffix (1.0.0 > 1.0.0-beta).
  if (!pa.pre.length && !pb.pre.length) return 0;
  if (!pa.pre.length) return 1;
  if (!pb.pre.length) return -1;
  const n = Math.max(pa.pre.length, pb.pre.length);
  for (let i = 0; i < n; i++) {
    if (i >= pa.pre.length) return -1; // shorter prerelease list is lower when it is a prefix
    if (i >= pb.pre.length) return 1;
    const x = pa.pre[i], y = pb.pre[i];
    if (x === y) continue;
    if (isNum(x) && isNum(y)) return Number(x) < Number(y) ? -1 : 1;
    if (isNum(x)) return -1; // numeric identifiers rank below alphanumeric
    if (isNum(y)) return 1;
    return x < y ? -1 : 1;
  }
  return 0;
}

export function isNewer(candidate: string, current: string): boolean {
  return compareVersions(candidate, current) > 0;
}

/**
 * Update channel, derived from the current version string: a version with a
 * prerelease suffix (e.g. 0.3.0-beta.1) is a beta build and follows GitHub
 * pre-releases (stable releases count too — a beta user should still be
 * offered the stable release that supersedes their beta); any other version
 * is a stable build and follows stable releases only. There is no separate
 * build/mode flag for channel in the existing build data, so the version
 * string — the thing the channel is a property of — is the determination.
 */
export function channelForVersion(version: string): 'beta' | 'stable' {
  const p = parseVersion(version);
  return p && p.pre.length ? 'beta' : 'stable';
}

/** Pick the newest applicable release strictly newer than `current`, or null. Pure; unit-testable shape. */
export function pickUpdate(releases: unknown, current: string): ReleaseInfo | null {
  if (!Array.isArray(releases)) return null;
  const channel = channelForVersion(current);
  let best: ReleaseInfo | null = null;
  for (const r of releases as Array<Record<string, unknown>>) {
    if (!r || r.draft) continue;
    const prerelease = !!r.prerelease;
    if (channel === 'stable' && prerelease) continue;
    const version = typeof r.tag_name === 'string' ? normalizeVersion(r.tag_name) : null;
    if (!version || !isNewer(version, current)) continue;
    const assets = Array.isArray(r.assets) ? (r.assets as Array<Record<string, unknown>>) : [];
    const firstAsset = assets.find((a) => a && typeof a.browser_download_url === 'string');
    const htmlUrl = typeof r.html_url === 'string' ? r.html_url : RELEASES_URL;
    const info: ReleaseInfo = {
      version,
      tag: String(r.tag_name),
      htmlUrl,
      downloadUrl: firstAsset ? String(firstAsset.browser_download_url) : htmlUrl,
      notes: typeof r.body === 'string' ? r.body : '',
      prerelease,
    };
    if (!best || compareVersions(info.version, best.version) > 0) best = info;
  }
  return best;
}

/* ---------- localStorage state (all access fail-silent) ---------- */
function lsGet(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function lsSet(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable: the prompt just re-checks next launch */ }
}

/** The version the user dismissed, if any; that version never re-prompts. */
export function snoozedVersion(): string | null {
  const v = lsGet(SNOOZED_KEY);
  return v ? normalizeVersion(v) : null;
}

/** Snooze a version: dismissing its banner suppresses that version only; a newer one re-prompts. */
export function snoozeVersion(version: string): void {
  const v = normalizeVersion(version);
  if (v) lsSet(SNOOZED_KEY, v);
}

export function lastCheckAt(): number {
  const n = Number(lsGet(LAST_CHECK_KEY) || 0);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Check the releases feed at most once a day and return the update to offer,
 * or null for every silent outcome: checked recently, offline/unreachable/
 * malformed feed, empty feed, nothing applicable newer, or the offered
 * version was already dismissed. Never throws.
 */
export async function checkForUpdate(opts?: {
  now?: number;
  current?: string;
  fetchFn?: typeof fetch;
}): Promise<ReleaseInfo | null> {
  const now = opts?.now ?? Date.now();
  const current = opts?.current ?? CURRENT_VERSION;
  if (now - lastCheckAt() < DAY_MS) return null;
  lsSet(LAST_CHECK_KEY, String(now)); // an attempt counts, success or failure: at most one check a day
  const fetchFn = opts?.fetchFn ?? (typeof fetch === 'function' ? fetch : null);
  if (!fetchFn) return null;
  let data: unknown;
  try {
    const res = await fetchFn(RELEASES_URL, { headers: { Accept: 'application/vnd.github+json' } });
    if (!res || !res.ok) return null;
    data = await res.json();
  } catch {
    return null; // offline / unreachable / malformed: fail silent, by design
  }
  const info = pickUpdate(data, current);
  if (!info) return null;
  if (snoozedVersion() === info.version) return null;
  return info;
}
