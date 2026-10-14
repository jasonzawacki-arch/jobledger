// Set-up for the demo preview: the engine, with sample data that is never saved.
import { Data, Engine, install } from './local';

declare global { interface Window { __JL_SNAPSHOT__?: { made_at: number; backup: Data }; __JL_MODE__?: string } }
window.__JL_MODE__ = 'demo';

/** Moves the sample data forward by whole days since it was made, so it always looks current. */
function fresh(): Data {
  const snap = window.__JL_SNAPSHOT__!;
  const days = Math.max(0, Math.floor((Date.now() / 1000 - snap.made_at) / 86400));
  const sec = days * 86400;
  const pad = (n: number): string => (n < 10 ? '0' : '') + n;
  const shiftDate = (s: string): string => { const [y, m, d] = s.split('-').map(Number); const x = new Date(y, m - 1, d + days); return `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`; };
  const b = JSON.parse(JSON.stringify(snap.backup)) as Data;
  for (const j of b.jobs) {
    j.stage_changed_at += sec; j.created_at += sec; j.updated_at += sec;
    if (j.follow_up_on) j.follow_up_on = shiftDate(j.follow_up_on);
    if (j.posted_on) j.posted_on = shiftDate(j.posted_on);
    j.source = j.source ?? '';
  }
  for (const e of b.events) e.at += sec;
  for (const c of b.contacts) if (c.last_contacted_on) c.last_contacted_on = shiftDate(c.last_contacted_on);
  for (const i of b.interviews ?? []) i.starts_at = shiftDate(i.starts_at.slice(0, 10)) + i.starts_at.slice(10);
  b.interviews = b.interviews ?? [];
  return b;
}
install(Promise.resolve(new Engine(fresh(), { demo: true })));
