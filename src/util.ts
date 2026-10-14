const ENT: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, (c) => ENT[c]);
export const shortDate = (epochSeconds: number): string =>
  new Date(epochSeconds * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
export const isoDay = (iso: string): string => {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};
export const safeUrl = (u: string): string => (/^https?:\/\//i.test(u) ? u : '');
const HUES = [222, 168, 28, 285, 340];
export const tile = (company: string, id: number): string => {
  const h = HUES[id % 5];
  return `<span class="mono-tile" style="--tile-bg:hsl(${h} 70% 92%);--tile-fg:hsl(${h} 55% 30%)" aria-hidden="true">${esc(company.trim()[0]?.toUpperCase() ?? '?')}</span>`;
};
export const plural = (n: number, one: string, many = one + 's'): string => `${n} ${n === 1 ? one : many}`;
/** "2026-10-24T14:30" (local wall-clock time) -> "Sat, Oct 24, 2:30 PM" */
export const whenLocal = (iso: string, withWeekday = true): string => {
  const [d, t] = iso.split('T');
  const [y, m, day] = d.split('-').map(Number);
  const [hh, mm] = (t || '00:00').split(':').map(Number);
  return new Date(y, m - 1, day, hh, mm).toLocaleString(undefined, { ...(withWeekday ? { weekday: 'short' } : {}), month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};
/** The viewer's own time zone, e.g. "America/New_York"; empty if the browser does not say. */
export const userTimeZone = (): string => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { return ''; } };
/** "20261017T090000" read as wall-clock time where the viewer is -> "20261017T130000Z" (UTC). */
export const toUtcStamp = (local: string): string => {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(local);
  if (!m) return local; // already UTC (ends in Z) or not a time we understand: leave it alone
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  return d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
};
/** Google Calendar link with exact times: the start and end are converted to UTC (with a Z) using the viewer's
 *  own time zone, and that zone is added as ctz so Google shows the event in local time. Nothing is left for
 *  Google to guess, whatever zone the Google account itself is set to. */
export const calLink = (url: string | null | undefined): string => {
  if (!url || !/^https:\/\/calendar\.google\.com\//.test(url)) return '';
  const u = new URL(url);
  const dates = u.searchParams.get('dates');
  if (dates) u.searchParams.set('dates', dates.split('/').map(toUtcStamp).join('/'));
  const tz = userTimeZone();
  if (tz && !u.searchParams.has('ctz')) u.searchParams.set('ctz', tz);
  return u.toString();
};
