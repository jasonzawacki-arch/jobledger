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
