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
 *
 * Issue #29: a discovered release is cached locally so its banner persists
 * on same-day relaunches (until dismissed or superseded) without breaking
 * the at-most-once-a-day feed throttle; see cachedUpdate().
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
const CACHED_KEY = 'jobledger.update.cached';

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
function lsRemove(key: string): void {
  try { localStorage.removeItem(key); } catch { /* storage unavailable: fail silent, as above */ }
}

/** The version the user dismissed, if any; that version never re-prompts. */
export function snoozedVersion(): string | null {
  const v = lsGet(SNOOZED_KEY);
  return v ? normalizeVersion(v) : null;
}

/** Snooze a version: dismissing its banner suppresses that version only; a newer one re-prompts. */
export function snoozeVersion(version: string): void {
  const v = normalizeVersion(version);
  if (!v) return;
  lsSet(SNOOZED_KEY, v);
  // Dismissing also drops the cached copy of that version (Issue #29), so a
  // same-day relaunch cannot resurrect a banner the user already dismissed.
  try {
    const raw = lsGet(CACHED_KEY);
    const cached = raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
    if (cached && typeof cached.version === 'string' && normalizeVersion(cached.version) === v) lsRemove(CACHED_KEY);
  } catch { /* a malformed cache entry is ignored by cachedUpdate() anyway */ }
}

/* ---------- Issue #29: cached discovered release ---------- */
/**
 * A successfully discovered release is cached (a bounded summary only:
 * version, tag, the two URLs, the prerelease flag and a truncated notes
 * excerpt) so the banner survives relaunches inside the 24-hour throttle
 * without a second feed request. The cache is never trusted blindly: every
 * read revalidates it against the running build (cachedUpdate below).
 */
function cacheUpdate(info: ReleaseInfo): void {
  lsSet(CACHED_KEY, JSON.stringify({
    version: info.version,
    tag: info.tag,
    htmlUrl: info.htmlUrl,
    downloadUrl: info.downloadUrl,
    notes: info.notes.slice(0, 1000),
    prerelease: info.prerelease,
  }));
}

function clearCachedUpdate(): void {
  lsRemove(CACHED_KEY);
}

const isHttpUrl = (u: unknown): u is string =>
  typeof u === 'string' && u.length > 0 && u.length <= 2048 && /^https?:\/\//i.test(u);

/**
 * The cached release, revalidated for the running build, or null when there
 * is nothing applicable to show: no cache, malformed or tampered cache
 * (unparseable version, tag/version mismatch, non-http(s) URLs — the banner
 * also passes URLs through safeUrl at render, but a bad URL invalidates the
 * whole entry here), a version no longer newer than the running build, a
 * pre-release on the stable channel, or the snoozed version.
 */
export function cachedUpdate(current: string): ReleaseInfo | null {
  let cached: Record<string, unknown> | null = null;
  try {
    const raw = lsGet(CACHED_KEY);
    cached = raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
  } catch { return null; }
  if (!cached || typeof cached !== 'object') return null;
  const version = typeof cached.version === 'string' ? normalizeVersion(cached.version) : null;
  if (!version || !isNewer(version, current)) return null;
  if (typeof cached.tag !== 'string' || normalizeVersion(cached.tag) !== version) return null;
  if (!isHttpUrl(cached.htmlUrl) || !isHttpUrl(cached.downloadUrl)) return null;
  const prerelease = cached.prerelease === true;
  if (channelForVersion(current) === 'stable' && prerelease) return null;
  if (snoozedVersion() === version) return null;
  return {
    version,
    tag: cached.tag,
    htmlUrl: cached.htmlUrl,
    downloadUrl: cached.downloadUrl,
    notes: typeof cached.notes === 'string' ? cached.notes.slice(0, 1000) : '',
    prerelease,
  };
}

export function lastCheckAt(): number {
  const n = Number(lsGet(LAST_CHECK_KEY) || 0);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Check the releases feed at most once a day and return the update to offer,
 * or null for every silent outcome: nothing discovered yet, an authoritative
 * empty/superseded feed, nothing applicable newer, or the offered version
 * was already dismissed. Never throws.
 *
 * Issue #29: inside the 24-hour throttle — and when a fresh attempt cannot
 * reach the feed — the revalidated cached release is returned instead of
 * null, so a discovered, undismissed prompt persists across relaunches
 * without a second feed request. Only an authoritative feed response (a
 * successful fetch whose applicable pick is empty, or a different release)
 * clears or replaces the cache; offline/unreachable/malformed attempts
 * leave it intact.
 */
export async function checkForUpdate(opts?: {
  now?: number;
  current?: string;
  fetchFn?: typeof fetch;
}): Promise<ReleaseInfo | null> {
  const now = opts?.now ?? Date.now();
  const current = opts?.current ?? CURRENT_VERSION;
  if (now - lastCheckAt() < DAY_MS) return cachedUpdate(current);
  lsSet(LAST_CHECK_KEY, String(now)); // an attempt counts, success or failure: at most one check a day
  const fetchFn = opts?.fetchFn ?? (typeof fetch === 'function' ? fetch : null);
  if (!fetchFn) return cachedUpdate(current);
  let data: unknown;
  try {
    const res = await fetchFn(RELEASES_URL, { headers: { Accept: 'application/vnd.github+json' } });
    if (!res || !res.ok) return cachedUpdate(current);
    data = await res.json();
  } catch {
    return cachedUpdate(current); // offline / unreachable / malformed: keep any cached prompt, else silent
  }
  if (!Array.isArray(data)) return cachedUpdate(current); // malformed feed shape: not authoritative
  const info = pickUpdate(data, current);
  if (!info) { clearCachedUpdate(); return null; } // authoritative: nothing applicable (any more)
  if (snoozedVersion() === info.version) { clearCachedUpdate(); return null; }
  cacheUpdate(info);
  return info;
}
