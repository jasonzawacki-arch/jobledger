// The phone engine: answers the app's /api requests inside the page, with the same rules as the Python
// server (jobledger/service.py). Used by the installable phone app (data saved on the phone) and by the
// demo preview (sample data, nothing saved). The data is kept in the same shape as a backup file, so a
// backup moves freely between the phone and the server.
import { match as runMatch } from './matcher';
import { DEFAULT_MODEL, SYSTEM, diffParts, guard } from './rewrite';

export interface JobRow {
  id: number; company: string; title: string; location: string; pay: string; url: string; posted_on: string; description: string; source: string;
  stage: number; max_stage: number; closed_reason: string | null; follow_up_on: string | null; stage_changed_at: number; created_at: number; updated_at: number;
}
export interface EventRow { id: number; job_id: number; at: number; kind: string; text: string }
export interface ContactRow { id: number; job_id: number; name: string; role: string; email: string; last_contacted_on: string }
export interface PrepRow { id: number; job_id: number; text: string; done: boolean | number; position: number }
export interface InterviewRow { id: number; job_id: number; starts_at: string; minutes: number; label: string; place: string }
export interface Data {
  app: 'jobledger'; version: 1; exported_at?: number; jobs: JobRow[]; events: EventRow[]; contacts: ContactRow[]; prep: PrepRow[];
  interviews: InterviewRow[]; resume: string;
}
export const emptyData = (): Data => ({ app: 'jobledger', version: 1, jobs: [], events: [], contacts: [], prep: [], interviews: [], resume: '' });

export interface Reply { status: number; json?: unknown; text?: string; type?: string; filename?: string }
export interface Options {
  /** Demo: sample data in a sandboxed page, so downloads, imports and outside requests are explained instead. */
  demo?: boolean;
  /** Called after every change with the whole data set, to save it. */
  save?: (d: Data) => void;
  now?: () => number;
  /** For wording suggestions: the page's own fetch, before the engine replaced it. */
  fetch?: typeof fetch;
  /** True while Google Drive sync is on: new records get ids that cannot clash with another device's. */
  uniqueIds?: () => boolean;
}

const DAY = 86400, STALE = 10;
export const STAGES = ['Saved', 'Applied', 'Screen', 'Interview', 'Offer'];
const CLOSED = ['rejected', 'withdrawn', 'ghosted'];
const CLOSED_TEXT: Record<string, string> = { rejected: 'Rejected.', withdrawn: 'Withdrew the application.', ghosted: 'No response. Marked as ghosted.' };
const LIMITS: Record<string, number> = { company: 120, title: 160, location: 120, pay: 80, url: 500, posted_on: 10, description: 60000, source: 80 };
const EDITABLE = ['company', 'title', 'location', 'pay', 'url', 'posted_on', 'description', 'source'] as const;
type Editable = typeof EDITABLE[number];
const RESUME_LIMIT = 40000, IMPORT_MAX_ROWS = 2000, IMPORT_MAX_BYTES = 2_000_000;
export const DEFAULT_PREP = [
  'Re-read the posting and note three requirements to speak to',
  'Prepare a two-minute summary of your background',
  'Pick two or three stories that fit the main requirements',
  'Look up the company and the people you will meet',
  'Write down questions to ask them',
  'Plan how you will say you are returning to work after a break',
];
const STAGE_MOVE = /^Moved from (\w+) to (\w+)\.$/;

class Bad extends Error { status = 400; }
class Missing extends Error { status = 404; constructor(m = 'Not found') { super(m); } }

// ---------- dates (local wall-clock, like the server with its own time zone)
const pad = (n: number): string => (n < 10 ? '0' : '') + n;
const isoDate = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const parseLocal = (s: string): Date => {
  const [d, t = '00:00'] = s.split('T');
  const [y, m, day] = d.split('-').map(Number), [hh, mm] = t.split(':').map(Number);
  return new Date(y, m - 1, day, hh, mm);
};
const isDate = (s: string): boolean => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split('-').map(Number), x = new Date(y, m - 1, d);
  return x.getFullYear() === y && x.getMonth() === m - 1 && x.getDate() === d;
};
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const when = (s: string): string => {
  const d = parseLocal(s), h = d.getHours() % 12 || 12;
  return `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]} ${MON[d.getMonth()]} ${d.getDate()}, ${h}:${pad(d.getMinutes())} ${d.getHours() < 12 ? 'AM' : 'PM'}`;
};
const compact = (d: Date): string => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
const utcStamp = (d: Date): string => d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');

// ---------- calendar
function gcal(text: string, start: Date, end: Date, details: string, location?: string): string {
  const q = new URLSearchParams({ action: 'TEMPLATE', text, dates: `${compact(start)}/${compact(end)}`, details });
  if (location) q.set('location', location);
  return 'https://calendar.google.com/calendar/render?' + q.toString();
}
function icsEsc(s: string): string { return s.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r\n/g, '\n').replace(/\n/g, '\\n'); }
function fold(line: string): string {
  const enc = new TextEncoder(), out: string[] = [];
  let cur = '', size = 0;
  for (const ch of line) {
    const n = enc.encode(ch).length;
    if (size + n > (out.length ? 74 : 75)) { out.push(cur); cur = ''; size = 0; }
    cur += ch; size += n;
  }
  out.push(cur);
  return out.join('\r\n ');
}
const csvSafe = (v: string): string => (/^[=+\-@]/.test(v) ? "'" + v : v);
const csvCell = (v: string | number): string => { const s = String(v); return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };

/** RFC 4180 CSV, as Python's csv module reads it. */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], cell = '', i = 0, quoted = false, started = false;
  while (i < text.length) {
    const c = text[i];
    if (quoted) {
      if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i += 2; continue; } quoted = false; i++; continue; }
      cell += c; i++; continue;
    }
    if (c === '"' && !started) { quoted = true; started = true; i++; continue; }
    if (c === ',') { row.push(cell); cell = ''; started = false; i++; continue; }
    if (c === '\r' || c === '\n') {
      row.push(cell); rows.push(row); row = []; cell = ''; started = false;
      i += c === '\r' && text[i + 1] === '\n' ? 2 : 1; continue;
    }
    cell += c; started = true; i++;
  }
  if (started || cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

export class Engine {
  d: Data;
  private demo: boolean;
  private save: (d: Data) => void;
  private now: () => number;
  private outside?: typeof fetch;
  private uniqueIds: () => boolean;
  /** Set by the /api/restore route on the save that persists a restore, so the host (phone setup) can
   * tell the sync tracker the restored records are historical, not fresh edits. Consumed by changed(). */
  restoredSync = false;

  constructor(data: Data, opts: Options = {}) {
    this.d = data;
    this.demo = !!opts.demo;
    this.save = opts.save ?? (() => undefined);
    this.now = opts.now ?? (() => Date.now() / 1000);
    this.outside = opts.fetch;
    this.uniqueIds = opts.uniqueIds ?? (() => false);
  }

  // ---------- helpers
  private nextId(list: { id: number }[]): number {
    const next = list.reduce((m, x) => Math.max(m, x.id), 0) + 1;
    // With sync on, two devices may add records before they next sync: time plus a random part keeps ids apart.
    return this.uniqueIds() ? Math.max(next, Date.now() * 100 + Math.floor(Math.random() * 100)) : next;
  }
  private row(id: number): JobRow { const r = this.d.jobs.find((j) => j.id === id); if (!r) throw new Missing(); return r; }
  private event(jobId: number, kind: string, text: string): void { this.d.events.push({ id: this.nextId(this.d.events), job_id: jobId, at: this.now(), kind, text }); }
  private touch(r: JobRow): void { r.updated_at = this.now(); }
  private today(): string { return isoDate(new Date(this.now() * 1000)); }
  private nowDate(): Date { return new Date(this.now() * 1000); }

  private clean(f: Editable, v: unknown): string {
    if (v === null || v === undefined) v = '';
    if (typeof v !== 'string') throw new Bad(`${f} must be text`);
    const s = v.trim();
    if (s.length > LIMITS[f]) throw new Bad(`${f} is too long (limit ${LIMITS[f]} characters)`);
    if ((f === 'company' || f === 'title') && !s) throw new Bad(`${f} is required`);
    if (f === 'url' && s && !/^https?:\/\//i.test(s)) throw new Bad('url must start with http:// or https://');
    if (f === 'posted_on' && s && !isDate(s)) throw new Bad('posted_on must be a date like 2026-10-24');
    return s;
  }
  private follow(v: unknown): string | null {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v !== 'string' || !isDate(v)) throw new Bad('follow_up_on must be a date like 2026-10-24');
    return v;
  }

  // ---------- views (same fields as Tracker._view and get_job)
  private nextInterview(jobId: number): string | null {
    const t = this.nowDate();
    let best: string | null = null;
    for (const i of this.d.interviews) {
      if (i.job_id !== jobId) continue;
      if (new Date(parseLocal(i.starts_at).getTime() + i.minutes * 60000) >= t && (best === null || i.starts_at < best)) best = i.starts_at;
    }
    return best;
  }
  private view(r: JobRow) {
    const n = this.now();
    let last = r.created_at;
    for (const e of this.d.events) if (e.job_id === r.id && e.at > last) last = e.at;
    const closed = r.closed_reason !== null;
    const idle = Math.floor((n - last) / DAY);
    let due = false, remind: string | null = null;
    const nxt = closed ? null : this.nextInterview(r.id);
    if (nxt && !r.follow_up_on) { /* an interview is coming up, so there is nothing to chase */ }
    else if (!closed && r.stage !== 4) {
      if (r.follow_up_on) { remind = r.follow_up_on; due = r.follow_up_on <= this.today(); }
      else if (r.stage >= 1 && r.stage <= 3) { due = idle >= STALE; remind = isoDate(new Date((last + STALE * DAY) * 1000)); }
    }
    return {
      id: r.id, company: r.company, title: r.title, location: r.location, pay: r.pay, url: r.url, posted_on: r.posted_on,
      description: r.description, source: r.source, stage: r.stage, stage_name: STAGES[r.stage], max_stage: r.max_stage,
      closed_reason: r.closed_reason, follow_up_on: r.follow_up_on, reminder_on: remind, reminder_is_nudge: !!remind && !r.follow_up_on,
      next_interview: nxt, days_in_stage: Math.floor((n - r.stage_changed_at) / DAY), idle_days: idle, follow_up_due: due,
      created_at: r.created_at, updated_at: r.updated_at,
    };
  }
  private followDetails(j: { stage_name: string; reminder_is_nudge: boolean; url: string }, contacts: ContactRow[]): string {
    const lines = [`Stage: ${j.stage_name}.`];
    if (j.reminder_is_nudge) lines.push("No follow-up date was set; this is when JobLedger's idle nudge fires if nothing happens first.");
    for (const c of contacts) lines.push('Contact: ' + [c.name, c.role, c.email].filter(Boolean).join(', ') + '.');
    if (/^https?:\/\//.test(j.url)) lines.push(`Posting: ${j.url}`);
    lines.push('From JobLedger.');
    return lines.join('\n');
  }
  private interviewDetails(i: InterviewRow, url: string): string {
    const lines: string[] = [];
    if (i.place) lines.push(`Where: ${i.place}`);
    if (/^https?:\/\//.test(url)) lines.push(`Posting: ${url}`);
    lines.push('From JobLedger.');
    return lines.join('\n');
  }
  private interviewLink(i: InterviewRow, j: { title: string; company: string; url: string }): string {
    const s = parseLocal(i.starts_at);
    return gcal(`${i.label || 'Interview'}: ${j.title} at ${j.company}`, s, new Date(s.getTime() + i.minutes * 60000), this.interviewDetails(i, j.url), i.place || undefined);
  }
  getJob(id: number) {
    const r = this.row(id), v = this.view(r);
    const contacts = this.d.contacts.filter((c) => c.job_id === id);
    const t = this.nowDate();
    const out = {
      ...v,
      events: this.d.events.filter((e) => e.job_id === id).sort((a, b) => b.at - a.at || b.id - a.id).map(({ id: eid, at, kind, text }) => ({ id: eid, at, kind, text })),
      contacts: contacts.map(({ id: cid, name, role, email, last_contacted_on }) => ({ id: cid, name, role, email, last_contacted_on })),
      prep: this.d.prep.filter((p) => p.job_id === id).sort((a, b) => a.position - b.position || a.id - b.id).map((p) => ({ id: p.id, text: p.text, done: !!p.done })),
      calendar_link: null as string | null,
      interviews: this.d.interviews.filter((i) => i.job_id === id).sort((a, b) => (a.starts_at < b.starts_at ? -1 : a.starts_at > b.starts_at ? 1 : a.id - b.id))
        .map((i) => ({ id: i.id, starts_at: i.starts_at, minutes: i.minutes, label: i.label, place: i.place,
          past: new Date(parseLocal(i.starts_at).getTime() + i.minutes * 60000) < t, calendar_link: this.interviewLink(i, r) })),
    };
    if (v.reminder_on) {
      const s = parseLocal(v.reminder_on + 'T09:00');
      out.calendar_link = gcal(`Follow up: ${v.title} at ${v.company}`, s, new Date(s.getTime() + 15 * 60000), this.followDetails(v, contacts));
    }
    return out;
  }
  listJobs(includeClosed: boolean) {
    return this.d.jobs.filter((j) => includeClosed || j.closed_reason === null)
      .sort((a, b) => a.stage - b.stage || b.stage_changed_at - a.stage_changed_at || b.id - a.id).map((r) => this.view(r));
  }

  // ---------- jobs
  addJob(d: unknown) {
    if (!d || typeof d !== 'object') throw new Bad('Expected an object');
    const o = d as Record<string, unknown>;
    const vals = {} as Record<Editable, string>;
    for (const f of EDITABLE) vals[f] = this.clean(f, o[f]);
    const stage = o.stage === undefined ? 0 : o.stage;
    if (stage !== 0 && stage !== 1) throw new Bad('A new job starts as Saved or Applied');
    const follow = this.follow(o.follow_up_on);
    const n = this.now(), id = this.nextId(this.d.jobs);
    this.d.jobs.push({ ...vals, id, stage, max_stage: stage, closed_reason: null, follow_up_on: follow, stage_changed_at: n, created_at: n, updated_at: n });
    this.event(id, 'created', stage === 0 ? 'Saved the posting.' : 'Applied.');
    return this.getJob(id);
  }
  move(id: number, stage: unknown) {
    if (typeof stage !== 'number' || !Number.isInteger(stage) || stage < 0 || stage > 4) throw new Bad('stage must be a number from 0 to 4');
    const r = this.row(id);
    if (r.stage === stage && r.closed_reason === null) return this.getJob(id);
    const wasClosed = r.closed_reason !== null, from = r.stage;
    r.stage = stage; r.max_stage = Math.max(r.max_stage, stage); r.closed_reason = null; r.stage_changed_at = r.updated_at = this.now();
    this.event(id, wasClosed ? 'reopened' : 'stage', wasClosed ? `Reopened at ${STAGES[stage]}.` : `Moved from ${STAGES[from]} to ${STAGES[stage]}.`);
    return this.getJob(id);
  }
  updateJob(id: number, p: unknown) {
    if (!p || typeof p !== 'object') throw new Bad('Expected an object');
    const o = p as Record<string, unknown>, r = this.row(id);
    const next: Partial<Record<Editable, string>> = {};
    let changed = false;
    for (const f of EDITABLE) if (f in o) { next[f] = this.clean(f, o[f]); changed = true; }
    let fu: string | null = null;
    if ('follow_up_on' in o) { fu = this.follow(o.follow_up_on); changed = true; }
    Object.assign(r, next);
    if ('follow_up_on' in o) r.follow_up_on = fu;
    if (changed) { this.touch(r); if ('follow_up_on' in o) this.event(id, 'follow_up', fu ? `Follow-up set for ${fu}.` : 'Follow-up date cleared.'); }
    if ('stage' in o) this.move(id, o.stage);
    return this.getJob(id);
  }
  close(id: number, reason: unknown) {
    if (typeof reason !== 'string' || !CLOSED.includes(reason)) throw new Bad('reason must be rejected, withdrawn or ghosted');
    const r = this.row(id);
    if (r.closed_reason !== reason) { r.closed_reason = reason; this.touch(r); this.event(id, 'closed', CLOSED_TEXT[reason]); }
    return this.getJob(id);
  }
  deleteJob(id: number): void {
    this.row(id);
    this.d.jobs = this.d.jobs.filter((j) => j.id !== id);
    this.d.events = this.d.events.filter((x) => x.job_id !== id);
    this.d.contacts = this.d.contacts.filter((x) => x.job_id !== id);
    this.d.prep = this.d.prep.filter((x) => x.job_id !== id);
    this.d.interviews = this.d.interviews.filter((x) => x.job_id !== id);
  }
  addNote(id: number, text: unknown) {
    if (typeof text !== 'string' || !text.trim()) throw new Bad('A note cannot be empty');
    if (text.length > 2000) throw new Bad('A note can be up to 2000 characters');
    const r = this.row(id);
    this.event(id, 'note', text.trim()); this.touch(r);
    return this.getJob(id);
  }

  // ---------- contacts, checklist, interviews
  private contactFields(d: unknown, partial: boolean): Partial<ContactRow> {
    if (!d || typeof d !== 'object') throw new Bad('Expected an object');
    const o = d as Record<string, unknown>, out: Record<string, string> = {};
    for (const [k, limit] of [['name', 120], ['role', 120], ['email', 200], ['last_contacted_on', 10]] as [string, number][]) {
      if (!(k in o)) { if (k === 'name' && !partial) throw new Bad('A contact needs a name'); continue; }
      let v = o[k] ?? '';
      if (typeof v !== 'string') throw new Bad(`${k} must be text`);
      v = v.trim();
      if ((v as string).length > limit) throw new Bad(`${k} is too long (limit ${limit} characters)`);
      if (k === 'name' && !v) throw new Bad('A contact needs a name');
      if (k === 'email' && v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v as string)) throw new Bad('That email address does not look right');
      if (k === 'last_contacted_on' && v && !isDate(v as string)) throw new Bad('last_contacted_on must be a date like 2026-10-24');
      out[k] = v as string;
    }
    return out;
  }
  private addPrep(id: number, text: unknown): void {
    if (typeof text !== 'string' || !text.trim()) throw new Bad('A checklist item cannot be empty');
    if (text.trim().length > 200) throw new Bad('A checklist item can be up to 200 characters');
    this.row(id);
    const pos = 1 + this.d.prep.filter((p) => p.job_id === id).reduce((m, p) => Math.max(m, p.position), 0);
    this.d.prep.push({ id: this.nextId(this.d.prep), job_id: id, text: text.trim(), done: false, position: pos });
  }
  private interviewFields(d: unknown, partial: boolean): Partial<InterviewRow> {
    if (!d || typeof d !== 'object') throw new Bad('Expected an object');
    const o = d as Record<string, unknown>, out: Partial<InterviewRow> = {};
    if ('starts_at' in o || !partial) {
      const v = o.starts_at;
      if (typeof v !== 'string' || !v.trim()) throw new Bad('Give the interview a date and time');
      const s = v.trim();
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s) || !isDate(s.slice(0, 10))) throw new Bad('starts_at must be a date and time like 2026-10-24T14:30');
      const y = Number(s.slice(0, 4));
      if (y < 2000 || y > 2100) throw new Bad('starts_at must be a local date and time like 2026-10-24T14:30');
      out.starts_at = s.slice(0, 16);
    }
    if ('minutes' in o) {
      const m = o.minutes;
      if (typeof m !== 'number' || !Number.isInteger(m) || m < 5 || m > 600) throw new Bad('minutes must be a whole number from 5 to 600');
      out.minutes = m;
    }
    for (const [k, limit] of [['label', 120], ['place', 300]] as ['label' | 'place', number][]) {
      if (!(k in o)) continue;
      const v = o[k] ?? '';
      if (typeof v !== 'string') throw new Bad(`${k} must be text`);
      if (v.trim().length > limit) throw new Bad(`${k} is too long (limit ${limit} characters)`);
      out[k] = v.trim();
    }
    return out;
  }
  private sub<T extends { id: number; job_id: number }>(list: T[], jobId: number, sid: number, what: string): T {
    const x = list.find((y) => y.id === sid && y.job_id === jobId);
    if (!x) throw new Missing(`No such ${what}`);
    return x;
  }
  upcoming(days: number) {
    const t = this.nowDate(), until = new Date(t.getTime() + days * DAY * 1000);
    const out = [];
    for (const i of this.d.interviews) {
      const j = this.d.jobs.find((x) => x.id === i.job_id);
      if (!j || j.closed_reason !== null) continue;
      const s = parseLocal(i.starts_at);
      if (s > until || new Date(s.getTime() + i.minutes * 60000) < t) continue;
      out.push({ id: i.id, job_id: j.id, starts_at: i.starts_at, minutes: i.minutes, label: i.label, place: i.place, company: j.company, title: j.title,
        url: j.url, stage: j.stage, calendar_link: this.interviewLink(i, j) });
    }
    return out.sort((a, b) => (a.starts_at < b.starts_at ? -1 : a.starts_at > b.starts_at ? 1 : a.id - b.id));
  }

  // ---------- search and summaries
  search(q: unknown): number[] {
    if (typeof q !== 'string' || q.length > 200) throw new Bad('Search for up to 200 characters');
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return [...this.d.jobs].sort((a, b) => a.id - b.id).filter((j) => {
      const hay = [j.company, j.title, j.location, j.pay, j.description, j.source,
        ...this.d.events.filter((e) => e.job_id === j.id && e.kind === 'note').map((e) => e.text),
        ...this.d.contacts.filter((c) => c.job_id === j.id).map((c) => `${c.name} ${c.role} ${c.email}`),
        ...this.d.interviews.filter((i) => i.job_id === j.id).map((i) => `${i.label} ${i.place}`)].join(' ').toLowerCase();
      return words.every((w) => hay.includes(w));
    }).map((j) => j.id);
  }
  pulse() {
    const jobs = this.listJobs(true), active = jobs.filter((j) => j.closed_reason === null);
    const reached = [0, 1, 2, 3, 4].map((i) => jobs.filter((j) => j.max_stage >= i).length);
    const closed: Record<string, number> = { rejected: 0, withdrawn: 0, ghosted: 0 };
    for (const j of jobs) if (j.closed_reason) closed[j.closed_reason]++;
    const due = active.filter((j) => j.follow_up_due);
    // when each job was applied to and first heard back (same rules as Tracker._response_stats)
    const applied = new Map<number, number>(), heard = new Map<number, number>();
    for (const e of [...this.d.events].sort((a, b) => a.at - b.at || a.id - b.id)) {
      const m = e.kind === 'stage' ? STAGE_MOVE.exec(e.text) : null, to = m ? STAGES.indexOf(m[2]) : -1;
      if (!applied.has(e.job_id)) {
        if (e.kind === 'created' && e.text === 'Applied.') applied.set(e.job_id, e.at);
        else if (m && to >= 1) { applied.set(e.job_id, e.at); if (to >= 2) heard.set(e.job_id, e.at); }
        continue;
      }
      if (heard.has(e.job_id)) continue;
      if (m && to >= 2) heard.set(e.job_id, e.at);
      else if (e.kind === 'closed' && e.text === 'Rejected.') heard.set(e.job_id, e.at);
    }
    const byId = new Map(jobs.map((j) => [j.id, j]));
    const ids = [...applied.keys()].filter((k) => byId.has(k));
    const waits = ids.filter((k) => heard.has(k)).map((k) => (heard.get(k)! - applied.get(k)!) / DAY).sort((a, b) => a - b);
    let median: number | null = null;
    if (waits.length) {
      const mid = Math.floor(waits.length / 2);
      const x = waits.length % 2 ? waits[mid] : (waits[mid - 1] + waits[mid]) / 2;
      const r = Math.round(x); median = Math.abs(x % 1) === 0.5 && r % 2 ? r - 1 : r; // Python rounds halves to even
    }
    const t = this.nowDate(); t.setHours(0, 0, 0, 0);
    const start = new Date(t); start.setDate(t.getDate() - ((t.getDay() + 6) % 7) - 49);
    const weeks = Array.from({ length: 8 }, (_, w) => { const d = new Date(start); d.setDate(start.getDate() + 7 * w); return { week_of: isoDate(d), applied: 0 }; });
    for (const k of ids) {
      const d = new Date(applied.get(k)! * 1000); d.setHours(0, 0, 0, 0);
      const i = Math.floor(Math.round((d.getTime() - start.getTime()) / 86400000) / 7);
      if (i >= 0 && i < 8) weeks[i].applied++;
    }
    const src = new Map<string, { source: string; applied: number; heard_back: number }>();
    for (const k of ids) {
      const s = byId.get(k)!.source || 'Not set';
      const row = src.get(s) ?? { source: s, applied: 0, heard_back: 0 };
      row.applied++; if (heard.has(k)) row.heard_back++;
      src.set(s, row);
    }
    const heardN = ids.filter((k) => heard.has(k)).length;
    const pct = (a: number, b: number): number => { const x = (a / b) * 100, r = Math.round(x); return Math.abs(x % 1) === 0.5 && r % 2 ? r - 1 : r; };
    return {
      active: active.length, total: jobs.length, reached, closed,
      reached_screen_pct: reached[1] ? pct(reached[2], reached[1]) : null,
      avg_days_in_stage: active.length ? Math.round(active.reduce((s, j) => s + j.days_in_stage, 0) / active.length) : null,
      in_flight: active.filter((j) => j.stage > 0).length,
      follow_ups_due: due.map((j) => ({ id: j.id, company: j.company, title: j.title, stage_name: j.stage_name, days_in_stage: j.days_in_stage, idle_days: j.idle_days, follow_up_on: j.follow_up_on })),
      stale_days: STALE, applied_total: ids.length, heard_back: heardN, response_rate_pct: ids.length ? pct(heardN, ids.length) : null,
      median_days_to_reply: median, weekly_applied: weeks,
      by_source: [...src.values()].sort((a, b) => b.applied - a.applied || Number(a.source === 'Not set') - Number(b.source === 'Not set') || (a.source < b.source ? -1 : a.source > b.source ? 1 : 0)),
      upcoming_interviews: this.upcoming(14),
    };
  }
  matchJob(id: number) { return runMatch(this.row(id).description, this.d.resume); }

  // ---------- files
  calendar(jobId: number | null): string {
    let jobs: ReturnType<Engine['getJob']>[], interviews: ReturnType<Engine['upcoming']>;
    if (jobId !== null) {
      const job = this.getJob(jobId);
      interviews = this.upcoming(3650).filter((i) => i.job_id === jobId);
      jobs = job.reminder_on ? [job] : [];
      if (!jobs.length && !interviews.length) throw new Bad('This job has no follow-up or upcoming interview to remind you about');
    } else {
      jobs = this.listJobs(false).filter((j) => j.reminder_on).map((j) => this.getJob(j.id));
      interviews = this.upcoming(3650);
    }
    const stamp = utcStamp(this.nowDate());
    const L = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//JobLedger//Follow-ups//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:JobLedger follow-ups'];
    for (const j of jobs) {
      const s = parseLocal(j.reminder_on + 'T09:00'), e = new Date(s.getTime() + 15 * 60000), title = `Follow up: ${j.title} at ${j.company}`;
      L.push('BEGIN:VEVENT', `UID:jobledger-job-${j.id}@jobledger.local`, `DTSTAMP:${stamp}`, `DTSTART:${utcStamp(s)}`, `DTEND:${utcStamp(e)}`,
        `SUMMARY:${icsEsc(title)}`, `DESCRIPTION:${icsEsc(this.followDetails(j, this.d.contacts.filter((c) => c.job_id === j.id)))}`,
        'BEGIN:VALARM', 'ACTION:DISPLAY', `DESCRIPTION:${icsEsc(title)}`, 'TRIGGER:-PT0M', 'END:VALARM', 'END:VEVENT');
    }
    for (const iv of interviews) {
      const s = parseLocal(iv.starts_at), e = new Date(s.getTime() + iv.minutes * 60000), title = `${iv.label || 'Interview'}: ${iv.title} at ${iv.company}`;
      L.push('BEGIN:VEVENT', `UID:jobledger-interview-${iv.id}@jobledger.local`, `DTSTAMP:${stamp}`, `DTSTART:${utcStamp(s)}`, `DTEND:${utcStamp(e)}`,
        `SUMMARY:${icsEsc(title)}`, `DESCRIPTION:${icsEsc(this.interviewDetails(iv as unknown as InterviewRow, iv.url))}`);
      if (iv.place) L.push(`LOCATION:${icsEsc(iv.place)}`);
      L.push('BEGIN:VALARM', 'ACTION:DISPLAY', `DESCRIPTION:${icsEsc(title)}`, 'TRIGGER:-PT60M', 'END:VALARM', 'END:VEVENT');
    }
    L.push('END:VCALENDAR');
    return L.map(fold).join('\r\n') + '\r\n';
  }
  exportCsv(): string {
    const rows = [['company', 'title', 'location', 'pay', 'url', 'posted_on', 'source', 'stage', 'furthest_stage', 'status', 'follow_up_on', 'days_in_stage', 'created_utc']];
    for (const j of this.listJobs(true)) {
      rows.push([csvSafe(j.company), csvSafe(j.title), csvSafe(j.location), csvSafe(j.pay), csvSafe(j.url), j.posted_on, csvSafe(j.source), j.stage_name,
        STAGES[j.max_stage], j.closed_reason || 'active', j.follow_up_on || '', String(j.days_in_stage),
        new Date(j.created_at * 1000).toISOString().slice(0, 16).replace('T', ' ')]);
    }
    return rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
  }
  importCsv(text: unknown): { imported: number } {
    if (typeof text !== 'string' || !text.trim()) throw new Bad('The file is empty');
    if (new TextEncoder().encode(text).length > IMPORT_MAX_BYTES) throw new Bad('The file is too large');
    const table = parseCsv(text.replace(/^\ufeff/, ''));
    const header = table.shift();
    if (!header || !header.some((h) => h)) throw new Bad('The file has no header row');
    const norm = header.map((h) => h.trim().toLowerCase().replace(/ /g, '_'));
    if (!norm.includes('company') || !norm.includes('title')) throw new Bad('The first row must include company and title columns');
    const rows: { vals: Record<Editable, string>; follow: string | null; stage: number; status: string }[] = [];
    let n = 1;
    for (const raw of table) {
      n++;
      if (n - 1 > IMPORT_MAX_ROWS) throw new Bad(`Too many rows (limit ${IMPORT_MAX_ROWS})`);
      const r: Record<string, string> = {};
      norm.forEach((k, i) => { if (header[i]) r[k] = (raw[i] ?? '').trim().replace(/^'/, ''); });
      if (!Object.values(r).some((v) => v)) continue;
      try {
        const vals = {} as Record<Editable, string>;
        for (const f of EDITABLE) vals[f] = this.clean(f, r[f] ?? '');
        const follow = this.follow(r.follow_up_on || null);
        const label = (r.stage || '').trim().toLowerCase(), si = STAGES.map((s) => s.toLowerCase()).indexOf(label);
        const status = (r.status || 'active').trim().toLowerCase();
        if (status !== 'active' && !CLOSED.includes(status)) throw new Bad('status must be active, rejected, withdrawn or ghosted');
        rows.push({ vals, follow, stage: si < 0 ? 0 : si, status });
      } catch (e) {
        if (e instanceof Bad) throw new Bad(`Row ${n}: ${e.message}`);
        throw e;
      }
    }
    if (!rows.length) throw new Bad('No jobs found in the file');
    for (const { vals, follow, stage, status } of rows) {
      const j = this.addJob({ ...vals, stage: Math.min(stage, 1), follow_up_on: follow });
      if (stage > 1) this.move(j.id, stage);
      if (status !== 'active') this.close(j.id, status);
    }
    return { imported: rows.length };
  }
  backup(): Data {
    return JSON.parse(JSON.stringify({ ...this.d, app: 'jobledger', version: 1, exported_at: this.now(),
      prep: this.d.prep.map((p) => ({ ...p, done: p.done ? 1 : 0 })) }));
  }
  /** Replaces everything with a backup (from the phone or the server). Validated first; all or nothing. */
  restore(data: unknown): { jobs: number; events: number } {
    const o = data as Partial<Data> | null;
    if (!o || typeof o !== 'object' || o.app !== 'jobledger' || o.version !== 1) throw new Bad('That file is not a JobLedger backup');
    const { jobs, events } = o;
    if (!Array.isArray(jobs) || !Array.isArray(events)) throw new Bad('The backup is missing its jobs or events');
    const resume = o.resume ?? '';
    if (typeof resume !== 'string' || resume.length > RESUME_LIMIT) throw new Bad('The resume in the backup is invalid');
    const contacts = o.contacts ?? [], prep = o.prep ?? [], interviews = o.interviews ?? [];
    if (!Array.isArray(contacts) || !Array.isArray(prep) || contacts.length > 20000 || prep.length > 50000) throw new Bad('The contacts or checklists in the backup are invalid');
    if (!Array.isArray(interviews) || interviews.length > 20000) throw new Bad('The interviews in the backup are invalid');
    if (jobs.length > 5000 || events.length > 100000) throw new Bad('The backup is too large');
    const cols = ['id', 'company', 'title', 'location', 'pay', 'url', 'posted_on', 'description', 'stage', 'max_stage', 'closed_reason', 'follow_up_on', 'stage_changed_at', 'created_at', 'updated_at'];
    const ids = new Set<number>(), outJobs: JobRow[] = [];
    for (const j0 of jobs as unknown[]) {
      if (!j0 || typeof j0 !== 'object') throw new Bad('A job in the backup is incomplete');
      const j = { source: '', ...(j0 as Record<string, unknown>) } as Record<string, unknown>;
      if (cols.some((c) => !(c in j))) throw new Bad('A job in the backup is incomplete');
      if (typeof j.id !== 'number' || !Number.isInteger(j.id) || ids.has(j.id)) throw new Bad('The backup has a duplicate or invalid job id');
      ids.add(j.id);
      for (const f of EDITABLE) this.clean(f, j[f]);
      if (![0, 1, 2, 3, 4].includes(j.stage as number) || ![0, 1, 2, 3, 4].includes(j.max_stage as number) || (j.max_stage as number) < (j.stage as number)) throw new Bad('A job in the backup has an invalid stage');
      if (j.closed_reason !== null && !CLOSED.includes(j.closed_reason as string)) throw new Bad('A job in the backup has an invalid status');
      this.follow(j.follow_up_on);
      for (const f of ['stage_changed_at', 'created_at', 'updated_at']) if (typeof j[f] !== 'number') throw new Bad('A job in the backup has an invalid date');
      const row = {} as Record<string, unknown>;
      for (const c of [...cols, 'source']) row[c] = j[c];
      for (const f of EDITABLE) row[f] = this.clean(f, j[f]);
      row.follow_up_on = this.follow(j.follow_up_on);
      outJobs.push(row as unknown as JobRow);
    }
    const kinds = ['created', 'stage', 'note', 'closed', 'reopened', 'follow_up', 'interview'];
    const outEvents: EventRow[] = (events as unknown as Record<string, unknown>[]).map((e, i) => {
      if (!e || typeof e !== 'object' || !ids.has(e.job_id as number) || !kinds.includes(e.kind as string) || typeof e.text !== 'string' || typeof e.at !== 'number') throw new Bad('An event in the backup is invalid');
      return { id: i + 1, job_id: e.job_id as number, at: e.at, kind: e.kind as string, text: (e.text as string).slice(0, 2000) };
    });
    const outContacts: ContactRow[] = (contacts as unknown as Record<string, unknown>[]).map((c, i) => {
      if (!c || typeof c !== 'object' || !ids.has(c.job_id as number)) throw new Bad('A contact in the backup is invalid');
      const f = this.contactFields({ name: c.name ?? '', role: c.role ?? '', email: c.email ?? '', last_contacted_on: c.last_contacted_on ?? '' }, false);
      return { id: i + 1, job_id: c.job_id as number, name: f.name!, role: f.role ?? '', email: f.email ?? '', last_contacted_on: f.last_contacted_on ?? '' };
    });
    const outInterviews: InterviewRow[] = (interviews as unknown as Record<string, unknown>[]).map((v, i) => {
      if (!v || typeof v !== 'object' || !ids.has(v.job_id as number)) throw new Bad('An interview in the backup is invalid');
      const pick: Record<string, unknown> = {};
      for (const k of ['starts_at', 'minutes', 'label', 'place']) if (k in v) pick[k] = v[k];
      const f = this.interviewFields(pick, false);
      return { id: i + 1, job_id: v.job_id as number, starts_at: f.starts_at!, minutes: f.minutes ?? 60, label: f.label ?? '', place: f.place ?? '' };
    });
    const outPrep: PrepRow[] = (prep as unknown as Record<string, unknown>[]).map((p, i) => {
      if (!p || typeof p !== 'object' || !ids.has(p.job_id as number) || typeof p.text !== 'string' || ![0, 1, true, false].includes(p.done as number | boolean)) throw new Bad('A checklist item in the backup is invalid');
      return { id: i + 1, job_id: p.job_id as number, text: (p.text as string).slice(0, 200), done: !!p.done, position: typeof p.position === 'number' ? p.position : i };
    });
    this.d = { app: 'jobledger', version: 1, jobs: outJobs, events: outEvents, contacts: outContacts, prep: outPrep, interviews: outInterviews, resume: resume.trim() };
    return { jobs: outJobs.length, events: outEvents.length };
  }

  private async rewrite(id: number, key: string, body: unknown) {
    if (!key.startsWith('sk-ant-') || key.length > 300) throw new Bad('Add your Anthropic API key on the Data tab to use suggestions');
    const want = (body as { requirement?: unknown } | null)?.requirement;
    if (typeof want !== 'string') throw new Bad('Say which requirement to reword');
    const item = this.matchJob(id).requirements.find((x) => x.requirement === want);
    if (!item || !item.evidence) throw new Bad('There is no resume line to reword for that requirement');
    const source = item.evidence.line; // always taken from the stored resume, never from the request
    if (!this.outside) throw new Bad('Wording suggestions are not available here');
    let res: Response;
    try {
      res = await this.outside('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json', 'anthropic-dangerous-direct-browser-access': 'true' },
        body: JSON.stringify({ model: DEFAULT_MODEL, max_tokens: 200, system: SYSTEM, messages: [{ role: 'user', content: `Job requirement: ${want}\n\nResume line: ${source}` }] }),
      });
    } catch {
      return { status: 502, json: { error: 'Could not reach Anthropic. Check your connection.' } };
    }
    if (res.status === 401 || res.status === 403) return { status: 502, json: { error: 'Anthropic did not accept that API key.' } };
    if (res.status === 429) return { status: 502, json: { error: 'Anthropic says you are over your rate limit. Try again in a minute.' } };
    if (!res.ok) return { status: 502, json: { error: `Anthropic returned an error (${res.status}).` } };
    let suggestion = '';
    try {
      const data = await res.json() as { content: { type: string; text?: string }[] };
      suggestion = data.content.filter((b) => b.type === 'text').map((b) => b.text ?? '').join('').trim();
    } catch {
      return { status: 502, json: { error: 'Anthropic sent a reply this app could not read.' } };
    }
    const [ok, reason] = guard(source, suggestion);
    const out: Record<string, unknown> = { source, ok, reason };
    if (ok) Object.assign(out, { suggestion, parts: diffParts(source, suggestion), unchanged: suggestion === source });
    return { status: 200, json: out };
  }

  // ---------- routing (same paths and answers as jobledger/api.py)
  async handle(method: string, path: string, query: URLSearchParams, body: unknown, headers: Headers): Promise<Reply> {
    try {
      const r = await this.route(method, path, query, body, headers);
      return r;
    } catch (e) {
      if (e instanceof Bad || e instanceof Missing) return { status: e.status, json: { error: e.message } };
      console.error(e);
      return { status: 500, json: { error: 'Something went wrong. Your last change may not have been saved.' } };
    }
  }

  private changed<T>(value: T, status = 200): Reply { this.save(this.d); this.restoredSync = false; return { status, json: value }; }

  private async route(method: string, path: string, q: URLSearchParams, body: unknown, headers: Headers): Promise<Reply> {
    const sandbox = (what: string): never => { throw new Bad(`${what} needs the full app. In the preview, try the Google Calendar buttons instead.`); };
    if (path === '/api/health') return { status: 200, json: { ok: true, auth: false, mode: this.demo ? 'demo' : 'phone' } };
    if (path === '/api/jobs') return method === 'POST' ? this.changed(this.addJob(body), 201) : { status: 200, json: this.listJobs(q.get('closed') === '1') };
    if (path === '/api/pulse') return { status: 200, json: this.pulse() };
    if (path === '/api/search') return { status: 200, json: { ids: this.search(q.get('q') ?? '') } };
    if (path === '/api/resume') {
      if (method === 'PUT') {
        const t = (body as { text?: unknown } | null)?.text;
        if (typeof t !== 'string') throw new Bad('The resume must be text');
        if (t.length > RESUME_LIMIT) throw new Bad(`The resume is too long (limit ${RESUME_LIMIT} characters)`);
        this.d.resume = t.trim();
        return this.changed({ text: this.d.resume, updated_at: null });
      }
      return { status: 200, json: { text: this.d.resume, updated_at: null } };
    }
    if (path === '/api/posting') throw new Bad('Fill from link needs the JobLedger server on your computer, because a phone app cannot read other websites. Paste the posting text instead.');
    if (path === '/api/import') { if (this.demo) sandbox('Importing a file'); const t = (body as { csv?: unknown } | null)?.csv; return this.changed(this.importCsv(t), 201); }
    if (path === '/api/restore') { if (this.demo) sandbox('Restoring a backup'); const c = this.restore(body); this.restoredSync = true; return this.changed(c); }
    if (path === '/api/export.csv') { if (this.demo) sandbox('Downloading files'); return { status: 200, text: this.exportCsv(), type: 'text/csv', filename: 'jobledger.csv' }; }
    if (path === '/api/backup') { if (this.demo) sandbox('Downloading files'); return { status: 200, text: JSON.stringify(this.backup()), type: 'application/json', filename: 'jobledger-backup.json' }; }
    if (path === '/api/calendar.ics') { if (this.demo) sandbox('Downloading files'); return { status: 200, text: this.calendar(null), type: 'text/calendar', filename: 'jobledger-follow-ups.ics' }; }
    const m = /^\/api\/jobs\/(\d+)(\/.*)?$/.exec(path);
    if (!m) throw new Missing();
    const id = Number(m[1]), rest = m[2] ?? '';
    let s: RegExpExecArray | null;
    if (!rest) {
      if (method === 'DELETE') { this.deleteJob(id); this.save(this.d); return { status: 204 }; }
      return method === 'PATCH' ? this.changed(this.updateJob(id, body)) : { status: 200, json: this.getJob(id) };
    }
    if (rest === '/notes') return this.changed(this.addNote(id, (body as { text?: unknown } | null)?.text), 201);
    if (rest === '/move') return this.changed(this.move(id, (body as { stage?: unknown } | null)?.stage));
    if (rest === '/close') return this.changed(this.close(id, (body as { reason?: unknown } | null)?.reason));
    if (rest === '/reopen') { const r = this.row(id); return this.changed(r.closed_reason !== null ? this.move(id, r.stage) : this.getJob(id)); }
    if (rest === '/match') return { status: 200, json: this.matchJob(id) };
    if (rest === '/calendar.ics') { if (this.demo) sandbox('Downloading files'); return { status: 200, text: this.calendar(id), type: 'text/calendar', filename: `jobledger-follow-up-${id}.ics` }; }
    if (rest === '/rewrite') {
      if (this.demo) throw new Bad('Wording suggestions need the phone app or the full app, with your own API key.');
      return this.rewrite(id, (headers.get('x-anthropic-key') ?? '').trim(), body);
    }
    if (rest === '/contacts') {
      const f = this.contactFields(body, false); this.row(id);
      this.d.contacts.push({ id: this.nextId(this.d.contacts), job_id: id, name: f.name!, role: f.role ?? '', email: f.email ?? '', last_contacted_on: f.last_contacted_on ?? '' });
      this.touch(this.row(id));
      return this.changed(this.getJob(id), 201);
    }
    if ((s = /^\/contacts\/(\d+)$/.exec(rest))) {
      const c = this.sub(this.d.contacts, id, Number(s[1]), 'contact');
      if (method === 'DELETE') this.d.contacts = this.d.contacts.filter((x) => x !== c);
      else { Object.assign(c, this.contactFields(body, true)); this.touch(this.row(id)); }
      return this.changed(this.getJob(id));
    }
    if (rest === '/prep') { this.addPrep(id, (body as { text?: unknown } | null)?.text); return this.changed(this.getJob(id), 201); }
    if (rest === '/prep/defaults') {
      this.row(id);
      const have = new Set(this.d.prep.filter((p) => p.job_id === id).map((p) => p.text));
      for (const t of DEFAULT_PREP) if (!have.has(t)) this.addPrep(id, t);
      return this.changed(this.getJob(id));
    }
    if ((s = /^\/prep\/(\d+)$/.exec(rest))) {
      const it = this.sub(this.d.prep, id, Number(s[1]), 'checklist item');
      if (method === 'DELETE') this.d.prep = this.d.prep.filter((x) => x !== it);
      else { const done = (body as { done?: unknown } | null)?.done; if (typeof done !== 'boolean') throw new Bad('done must be true or false'); it.done = done; }
      return this.changed(this.getJob(id));
    }
    if (rest === '/interviews') {
      const f = this.interviewFields(body, false); this.row(id);
      this.d.interviews.push({ id: this.nextId(this.d.interviews), job_id: id, starts_at: f.starts_at!, minutes: f.minutes ?? 60, label: f.label ?? '', place: f.place ?? '' });
      this.event(id, 'interview', `${f.label || 'Interview'} scheduled for ${when(f.starts_at!)}.`); this.touch(this.row(id));
      return this.changed(this.getJob(id), 201);
    }
    if ((s = /^\/interviews\/(\d+)$/.exec(rest))) {
      const iv = this.sub(this.d.interviews, id, Number(s[1]), 'interview');
      if (method === 'DELETE') {
        this.d.interviews = this.d.interviews.filter((x) => x !== iv);
        this.event(id, 'interview', `${iv.label || 'Interview'} on ${when(iv.starts_at)} removed.`);
      } else {
        const f = this.interviewFields(body, true); Object.assign(iv, f);
        if (f.starts_at) this.event(id, 'interview', `Interview moved to ${when(f.starts_at)}.`);
      }
      this.touch(this.row(id));
      return this.changed(this.getJob(id));
    }
    throw new Missing();
  }
}

/** Replaces window.fetch for /api/ paths so the unchanged app talks to the engine instead of a server. */
export function install(ready: Promise<Engine>): void {
  const real = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.includes('/api/')) return real(input, init);
    const path = url.pathname.slice(url.pathname.indexOf('/api/'));
    const engine = await ready;
    let body: unknown = null;
    try { body = init?.body ? JSON.parse(String(init.body)) : null; } catch { body = null; }
    const r = await engine.handle((init?.method ?? 'GET').toUpperCase(), path, url.searchParams, body, new Headers(init?.headers));
    if (r.text !== undefined) return new Response(r.text, { status: r.status, headers: { 'Content-Type': (r.type ?? 'text/plain') + '; charset=utf-8' } });
    return new Response(r.status === 204 ? null : JSON.stringify(r.json), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  };
}
