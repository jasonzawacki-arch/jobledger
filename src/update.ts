// In-app update prompt (2026-10-22 cross-app addendum, §6; Second Draw's UPDATE-PROMPT-1 shape).
// On launch, at most once a day, read this repo's GitHub Releases feed and offer a newer release in a
// slim, non-blocking banner. Every failure — offline, feed unreachable, feed empty (the shipping state
// today: the repo has no releases) — is silent by design. The request carries no user data: no
// credentials, no referrer, no query beyond the page size.
//
// Play-installed builds defer to Play's own updater. Telling a Play install apart from a sideloaded
// one needs installer detection, which lands with the Capacitor build; until then the Play bundle
// (dist/play) skips the check entirely, which also keeps its privacy policy's "nothing is sent by the
// app itself" true. The demo preview never checks either.

/** The app's own version, from package.json; build.mjs substitutes it into the bundle. */
declare const __APP_VERSION__: string;
export const APP_VERSION: string = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '0.0.0';
export const FEED = 'https://api.github.com/repos/jasonzawacki-arch/jobledger/releases?per_page=20';
const DAY = 24 * 60 * 60 * 1000;
const CHECKED_KEY = 'jobledger.update.checked';
const LATEST_KEY = 'jobledger.update.latest';
const SNOOZE_KEY = 'jobledger.update.snoozed';

export interface Release { version: string; notes: string; download: string }
interface FeedItem {
  tag_name?: unknown; draft?: unknown; prerelease?: unknown; html_url?: unknown;
  assets?: { name?: unknown; browser_download_url?: unknown }[];
}

const https = (u: unknown): string => (typeof u === 'string' && /^https:\/\//i.test(u) ? u : '');
/** "v1.2.3-beta.1" → "1.2.3-beta.1"; null when the tag is not a version. */
export function parseVersion(tag: string): string | null {
  const v = tag.trim().replace(/^v/i, '');
  return /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(v) ? v : null;
}
/** Semver precedence: <0, 0, >0. A pre-release sorts before its release (1.0.0-beta.2 < 1.0.0). */
export function compareVersions(a: string, b: string): number {
  const [ac, ap] = split(a), [bc, bp] = split(b);
  for (let i = 0; i < 3; i++) if (ac[i] !== bc[i]) return ac[i] - bc[i];
  if (!ap.length || !bp.length) return bp.length - ap.length;
  for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
    const x = ap[i], y = bp[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const nx = /^\d+$/.test(x), ny = /^\d+$/.test(y);
    if (nx && ny && Number(x) !== Number(y)) return Number(x) - Number(y);
    if (nx !== ny) return nx ? -1 : 1;
    if (!nx && x !== y) return x < y ? -1 : 1;
  }
  return 0;
}
function split(v: string): [number[], string[]] {
  const [core, pre] = v.split(/-(.*)/s);
  return [core.split('.').map(Number), pre ? pre.split('.') : []];
}
/** Channel rule: a beta build (its version carries a pre-release tag) follows GitHub pre-releases as
 * well as stable releases; a stable build follows stable releases only. */
export const isBeta = (version: string): boolean => version.includes('-');

/** The newest applicable release in the feed that is newer than `current`, or null. */
export function pickRelease(feed: unknown, current: string): Release | null {
  if (!Array.isArray(feed)) return null;
  const beta = isBeta(current);
  let best: Release | null = null;
  for (const r of feed as FeedItem[]) {
    if (!r || r.draft === true || (r.prerelease === true && !beta)) continue;
    const version = typeof r.tag_name === 'string' ? parseVersion(r.tag_name) : null;
    const notes = https(r.html_url);
    if (!version || !notes || compareVersions(version, current) <= 0) continue;
    if (best && compareVersions(version, best.version) <= 0) continue;
    const assets = (Array.isArray(r.assets) ? r.assets : [])
      .map((a) => ({ name: typeof a?.name === 'string' ? a.name : '', url: https(a?.browser_download_url) }))
      .filter((a) => a.url);
    const asset = assets.find((a) => /\.apk$/i.test(a.name)) || assets[0];
    best = { version, notes, download: asset ? asset.url : notes };
  }
  return best;
}

const get = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const set = (k: string, v: string): void => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } };
function cached(current: string): Release | null {
  try {
    const r = JSON.parse(get(LATEST_KEY) || 'null') as Release | null;
    return r && typeof r.version === 'string' && compareVersions(r.version, current) > 0 ? r : null;
  } catch { return null; }
}
/** Dismissing snoozes that version only; a newer release prompts again. */
export const snooze = (version: string): void => set(SNOOZE_KEY, version);
const snoozed = (r: Release | null): Release | null => (r && get(SNOOZE_KEY) === r.version ? null : r);

/** The release to offer, or null. Hits the feed at most once a day; between checks the last answer
 * is reused so a shown banner survives reloads. Never throws. */
export async function checkForUpdate(current: string = APP_VERSION, now: number = Date.now()): Promise<Release | null> {
  const last = Number(get(CHECKED_KEY) || 0);
  if (last && now - last >= 0 && now - last < DAY) return snoozed(cached(current));
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return null;
  try {
    const ctl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), 10000) : undefined;
    const res = await fetch(FEED, {
      credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store',
      headers: { Accept: 'application/vnd.github+json' }, signal: ctl?.signal,
    }).finally(() => clearTimeout(timer));
    if (!res.ok) return null;
    const best = pickRelease(await res.json(), current);
    set(CHECKED_KEY, String(now));
    set(LATEST_KEY, JSON.stringify(best));
    return snoozed(best);
  } catch { return null; }
}
