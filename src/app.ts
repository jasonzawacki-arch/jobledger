import { api, ApiError, getAiKey, getToken, setAiKey, setToken } from './api';
import { CLOSED_LABEL, Job, Match, Pulse, Rewrite, SOURCES, STAGES } from './types';
import { esc, isoDay, plural, safeUrl, shortDate, tile, whenLocal } from './util';

type View = 'board' | 'job' | 'pulse' | 'data';
interface State {
  view: View; jobs: Job[]; sel: number | null; detail: Job | null; pulse: Pulse | null; match: Match | null; resume: string;
  showClosed: boolean; form: 'add' | 'edit' | null; confirmDelete: boolean; pendingRestore: any | null;
  needToken: boolean; error: string; loaded: boolean;
  rewrites: Record<string, Rewrite | 'busy'>; draft: boolean;
  query: string; hits: Set<number> | null;
}
const S: State = {
  view: 'board', jobs: [], sel: null, detail: null, pulse: null, match: null, resume: '', showClosed: false, form: null,
  confirmDelete: false, pendingRestore: null, needToken: false, error: '', loaded: false, rewrites: {}, draft: false,
  query: '', hits: null,
};
const viewEl = document.getElementById('view') as HTMLElement;
const stripEl = document.getElementById('strip') as HTMLElement;
const tabsEl = document.getElementById('tabs') as HTMLElement;
const toastEl = document.getElementById('toast') as HTMLElement;
const stColor = (i: number) => `var(--st${i})`;
let toastTimer: number | undefined;
function toast(msg: string): void {
  toastEl.textContent = msg;
  toastEl.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toastEl.classList.remove("on"), Math.max(2200, msg.length * 45));
}
const active = () => S.jobs.filter((j) => !j.closed_reason);
const closed = () => S.jobs.filter((j) => j.closed_reason);

async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
  try {
    S.error = '';
    return await fn();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { S.needToken = true; render(); return undefined; }
    S.error = e instanceof Error ? e.message : String(e);
    toast(S.error);
    render();
    return undefined;
  }
}
async function refresh(): Promise<void> {
  const q = S.query.trim();
  const [jobs, pulse, resume, hits] = await Promise.all([api.jobs(), api.pulse(), api.resume(), q ? api.search(q) : Promise.resolve(null)]);
  S.jobs = jobs; S.pulse = pulse; S.resume = resume.text; S.loaded = true;
  if (q === S.query.trim()) S.hits = hits ? new Set(hits.ids) : null;
  if (S.sel !== null) {
    if (!jobs.some((j) => j.id === S.sel)) { S.sel = null; S.detail = null; S.match = null; if (S.view === 'job') S.view = 'board'; }
    else [S.detail, S.match] = await Promise.all([api.job(S.sel), api.match(S.sel)]);
  }
}
async function reload(): Promise<void> { await guard(refresh); render(); }
function setDetail(j: Job): void { S.detail = j; S.sel = j.id; }

/* ---------- views */
function renderTabs(): void {
  const t: [View, string][] = [['board', 'Board'], ['job', 'Job'], ['pulse', 'Pulse'], ['data', 'Data']];
  tabsEl.innerHTML = t.map(([v, n]) => `<button class="tab" role="tab" data-view="${v}" aria-selected="${S.view === v}">${n}</button>`).join('');
}
function renderStrip(): void {
  stripEl.innerHTML = STAGES.map((n, i) => `<div style="--c:${stColor(i)}"><span>${n}</span><b>${active().filter((j) => j.stage === i).length}</b></div>`).join('');
}
function card(j: Job, draggable: boolean): string {
  const tag = j.closed_reason ? `<span class="pill">${CLOSED_LABEL[j.closed_reason]}</span>`
    : j.next_interview ? `<span class="pill good" title="Next interview">${esc(whenLocal(j.next_interview))}</span>`
    : j.follow_up_due ? '<span class="pill warn">Follow-up due</span>' : '';
  return `<button class="card" ${draggable ? 'draggable="true"' : ''} data-open="${j.id}"><span class="ch">${tile(j.company, j.id)}<span><b>${esc(j.company)}</b><small>${esc(j.title)}</small></span></span>
    <span class="row"><span class="mono mute">${j.closed_reason ? 'closed' : j.days_in_stage + 'd here'}</span>${tag}</span></button>`;
}
const shown = (j: Job) => S.hits === null || S.hits.has(j.id);
function boardResults(): string {
  const searching = S.hits !== null;
  const cols = STAGES.map((n, i) => {
    const list = active().filter((j) => j.stage === i && shown(j));
    const none = searching ? 'No matches.' : i === 4 ? 'No offers yet.' : 'Drop a job here.';
    return `<section class="col" data-col="${i}" aria-label="${n}"><h3><span class="dot" style="--c:${stColor(i)}"></span>${n}<span class="mono mute">${list.length}</span></h3>${
      list.map((j) => card(j, true)).join('') || `<div class="empty">${none}</div>`}</section>`;
  }).join('');
  const c = closed().filter(shown);
  const open = S.showClosed;
  const closedSec = c.length
    ? `<div class="sect"><button class="btn" data-toggle-closed aria-expanded="${open}">${open ? 'Hide' : 'Show'} closed (${c.length})</button>${open ? `<div class="closed-list">${c.map((j) => card(j, false)).join('')}</div>` : ''}</div>` : '';
  const count = searching
    ? `<p class="hint search-count" role="status">${S.hits!.size ? `${plural(S.hits!.size, 'job')} match \u201c${esc(S.query.trim())}\u201d${c.length && !S.showClosed ? ', including closed ones' : ''}.` : `Nothing matches \u201c${esc(S.query.trim())}\u201d.`} <button class="linkbtn" data-search-clear>Clear search</button></p>`
    : '';
  return `${count}<div class="board">${cols}</div>${closedSec}`;
}
function board(): string {
  const search = S.jobs.length
    ? `<label class="search"><span class="sr">Search jobs</span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="m20 20-4-4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg><input type="search" data-search placeholder="Search company, title, notes, people" value="${esc(S.query)}" autocomplete="off" enterkeyhint="search"></label>` : '';
  return `<div class="row toolbar">${search}<button class="btn pri" data-add>Add job</button></div>
    <p class="hint" style="margin:0 0 10px">Drag a card to another column to move it, or tap it to open.</p>${S.form === 'add' ? jobForm(null) : ''}
    ${S.jobs.length === 0 && S.loaded ? '<div class="empty" style="margin-bottom:12px">Nothing tracked yet. Add your first job to get started.</div>' : ''}
    <div data-results>${boardResults()}</div>`;
}
function jobForm(j: Job | null): string {
  const v = (k: keyof Job) => esc(j ? (j[k] as string) ?? '' : '');
  return `<form class="panel" data-form="${j ? 'edit' : 'add'}" style="margin-bottom:14px"><h3>${j ? 'Edit job' : 'Add a job'}</h3>
    <div class="grid2">
      <label class="f">Company<input name="company" required maxlength="120" value="${v('company')}"></label>
      <label class="f">Title<input name="title" required maxlength="160" value="${v('title')}"></label>
      <label class="f">Location<input name="location" maxlength="120" value="${v('location')}"></label>
      <label class="f">Pay<input name="pay" maxlength="80" value="${v('pay')}"></label>
      <label class="f">Posting link<span class="inline"><input name="url" type="url" maxlength="500" placeholder="https://" value="${v('url')}"><button class="btn" type="button" data-fill-link>Fill from link</button></span></label>
      <label class="f">Posted on<input name="posted_on" type="date" value="${v('posted_on')}"></label>
      <label class="f">Where you found it<input name="source" maxlength="80" list="sources" placeholder="LinkedIn, referral…" value="${v('source')}"><datalist id="sources">${SOURCES.map((x) => `<option value="${esc(x)}">`).join('')}</datalist></label>
      ${j ? '' : `<label class="f">Starting stage<select name="stage">${STAGES.slice(0, 2).map((n, i) => `<option value="${i}">${n}</option>`).join('')}</select></label>`}
    </div>
    <label class="f">Posting text or notes about the role<textarea name="description" maxlength="60000">${v('description')}</textarea></label>
    ${S.error ? `<p class="err" role="alert">${esc(S.error)}</p>` : ''}
    <div class="btns"><button class="btn pri" type="submit">${j ? 'Save changes' : 'Add job'}</button><button class="btn" type="button" data-cancel-form>Cancel</button></div></form>`;
}
function contactsPanel(j: Job): string {
  const list = (j.contacts ?? []).map((c) => `<div class="fu"><span><b>${esc(c.name)}</b>${c.role ? ` <span class="mute">· ${esc(c.role)}</span>` : ''}<br><small class="mute">${c.email ? `<a class="job-link" href="mailto:${esc(c.email)}">${esc(c.email)}</a>` : 'no email'} · ${c.last_contacted_on ? 'last contact ' + esc(isoDay(c.last_contacted_on)) : 'not contacted yet'}</small></span>
    <span class="btns"><button class="btn" data-contacted="${c.id}">Contacted today</button><button class="btn danger" data-del-contact="${c.id}" aria-label="Remove ${esc(c.name)}">Remove</button></span></div>`).join('');
  return `<section class="panel"><h3>People</h3>${list || '<div class="empty">Add the recruiter or hiring manager so you can find them later.</div>'}
    <form data-contact-form class="grid2"><label class="f">Name<input name="name" required maxlength="120"></label><label class="f">Role<input name="role" maxlength="120" placeholder="Recruiter"></label>
      <label class="f">Email<input name="email" type="email" maxlength="200"></label><label class="f">Last contacted<input name="last_contacted_on" type="date"></label>
      <div class="btns" style="grid-column:1/-1"><button class="btn pri" type="submit">Add person</button></div></form></section>`;
}
function interviewsPanel(j: Job): string {
  const list = (j.interviews ?? []).map((iv) => {
    const place = iv.place ? (safeUrl(iv.place) ? `<a class="job-link" href="${esc(iv.place)}" target="_blank" rel="noopener noreferrer">Join link</a>` : esc(iv.place)) : '';
    const cal = !iv.past && safeUrl(iv.calendar_link) ? `<a class="btn" href="${esc(iv.calendar_link)}" target="_blank" rel="noopener noreferrer">Add to Google Calendar</a>` : '';
    return `<div class="fu${iv.past ? ' past' : ''}"><span><b>${esc(iv.label || 'Interview')}</b><br><small class="mute">${esc(whenLocal(iv.starts_at))} · ${iv.minutes} min${place ? ' · ' + place : ''}${iv.past ? ' · done' : ''}</small></span>
      <span class="btns">${cal}<button class="btn danger" data-del-interview="${iv.id}" aria-label="Remove ${esc(iv.label || 'interview')}">Remove</button></span></div>`;
  }).join('');
  return `<section class="panel"><h3>Interviews</h3>${list || '<div class="empty">No interviews scheduled yet.</div>'}
    <form data-interview-form class="grid2"><label class="f">Date and time<input name="starts_at" type="datetime-local" required></label>
      <label class="f">Length<select name="minutes">${[30, 45, 60, 90, 120].map((m) => `<option value="${m}" ${m === 60 ? 'selected' : ''}>${m} min</option>`).join('')}</select></label>
      <label class="f">What<input name="label" maxlength="120" placeholder="Phone screen with Dana"></label>
      <label class="f">Where<input name="place" maxlength="300" placeholder="Video link or address"></label>
      <div class="btns" style="grid-column:1/-1"><button class="btn pri" type="submit">Add interview</button></div></form>
    <span class="mute" style="font-size:12.5px">While an interview is coming up, this job is not flagged for a follow-up. The Data tab's calendar file includes interviews, with an alert an hour before.</span></section>`;
}
function prepPanel(j: Job): string {
  const items = (j.prep ?? []).map((p) => `<div class="fu"><label style="display:flex;gap:10px;align-items:center;min-width:0"><input type="checkbox" style="width:auto" data-prep="${p.id}" ${p.done ? 'checked' : ''}><span style="${p.done ? 'text-decoration:line-through;color:var(--mute)' : ''}">${esc(p.text)}</span></label><button class="btn danger" data-del-prep="${p.id}" aria-label="Remove item">Remove</button></div>`).join('');
  const done = (j.prep ?? []).filter((p) => p.done).length;
  return `<section class="panel"><h3>Interview prep${j.prep?.length ? ` <span class="mono mute">${done}/${j.prep.length}</span>` : ''}</h3>${items || '<div class="empty">No checklist yet.</div>'}
    <form data-prep-form class="btns" style="flex-wrap:nowrap"><label class="f" style="flex:1"><span class="sr">New checklist item</span><input name="text" maxlength="200" placeholder="Add an item" required></label><button class="btn" type="submit">Add</button></form>
    <div class="btns"><button class="btn" data-prep-defaults>Add a standard checklist</button></div></section>`;
}
function draftText(j: Job): { subject: string; body: string; to: string } {
  const c = (j.contacts ?? []).find((x) => x.email) ?? (j.contacts ?? [])[0];
  const first = c?.name.split(/\s+/)[0];
  const opener = j.stage === 1 ? `I applied for the ${j.title} role about ${plural(j.days_in_stage, 'day')} ago and wanted to check on the status.`
    : j.stage === 2 ? `Thank you for the conversation about the ${j.title} role. I wanted to check on next steps.`
    : j.stage === 3 ? `Thank you for the time you spent interviewing me for the ${j.title} role. I wanted to check on where things stand.`
    : `I wanted to check in about the ${j.title} role.`;
  return {
    to: c?.email ?? '',
    subject: j.stage === 3 ? `Thank you: ${j.title} interview` : `Following up on ${j.title} at ${j.company}`,
    body: `Hi ${first || 'there'},\n\n${opener} I remain very interested and I am happy to share anything else that would help.\n\nThank you,\n[Your name]`,
  };
}
function draftPanel(j: Job): string {
  if (!S.draft) return '<div class="btns"><button class="btn" data-draft>Draft follow-up email</button></div>';
  const d = draftText(j);
  const mail = `mailto:${encodeURIComponent(d.to).replace('%40', '@')}?subject=${encodeURIComponent(d.subject)}&body=${encodeURIComponent(d.body)}`;
  return `<div class="sug"><label class="f">Subject<input data-draft-subject value="${esc(d.subject)}"></label><label class="f">Message<textarea data-draft-body rows="8">${esc(d.body)}</textarea></label>
    <div class="btns"><button class="btn pri" data-copy-draft>Copy to clipboard</button><a class="btn job-link" href="${esc(mail)}">Open in email app</a><button class="btn" data-draft-close>Close</button></div>
    <span class="mute" style="font-size:12.5px">A plain template. Edit it, then copy it or open it in your email app. Nothing is sent from here.</span></div>`;
}
function reminderPanel(j: Job): string {
  if (!j.reminder_on) return '';
  const when = `${isoDay(j.reminder_on)}, 9:00 AM`;
  const link = j.calendar_link && safeUrl(j.calendar_link);
  return `<div class="remind"><span class="mute" style="font-size:13px">Get reminded even when JobLedger is closed: put ${j.reminder_is_nudge ? 'the nudge day' : 'this date'} (<b>${esc(when)}</b>) in your calendar. If the date changes, add it again.</span>
    <div class="btns">${link ? `<a class="btn" href="${esc(link)}" target="_blank" rel="noopener noreferrer" data-gcal>Add to Google Calendar</a>` : ''}<button class="btn" data-ics>Download .ics</button></div></div>`;
}
function jobView(): string {
  const j = S.detail;
  if (!j) return `<div class="empty">Pick a job from the board to see its details.</div>`;
  const url = safeUrl(j.url);
  const reopenBar = j.closed_reason
    ? `<div class="banner"><span>${CLOSED_LABEL[j.closed_reason]}. This job is off your board.</span><button class="btn" data-reopen>Reopen</button></div>` : '';
  const closeBtns = j.closed_reason ? '' : `<div class="btns"><span class="mute" style="font-size:13px">Close as:</span>${['rejected', 'withdrawn', 'ghosted'].map((r) => `<button class="btn" data-close="${r}">${CLOSED_LABEL[r]}</button>`).join('')}</div>`;
  const del = S.confirmDelete
    ? `<div class="confirm" role="alert"><span>Delete this job and its whole history? This cannot be undone.</span><button class="btn danger" data-delete-yes>Delete</button><button class="btn" data-delete-no>Keep it</button></div>`
    : `<button class="btn danger" data-delete>Delete job</button>`;
  const events = (j.events ?? []).map((e) => `<li><span class="mono mute">${shortDate(e.at)}</span><span class="pt"></span><span>${esc(e.text)}${e.kind === 'note' ? ' <span class="mono mute kind">note</span>' : ''}</span></li>`).join('');
  return `<button class="back" data-go="board">&larr; Back to board</button>
  ${S.form === 'edit' ? jobForm(j) : ''}
  <section class="hero">
    <div class="hero-top"><div class="ch" style="gap:14px">${tile(j.company, j.id)}<span><span class="mono mute">${esc(j.company)}</span><h2>${esc(j.title)}</h2></span></div>
      <div class="chips">${j.location ? `<span class="chip">${esc(j.location)}</span>` : ''}${j.pay ? `<span class="chip">${esc(j.pay)}</span>` : ''}${j.posted_on ? `<span class="chip">Posted ${esc(isoDay(j.posted_on))}</span>` : ''}${j.source ? `<span class="chip">via ${esc(j.source)}</span>` : ''}${j.follow_up_due ? '<span class="pill warn">Follow-up due</span>' : ''}</div></div>
    ${reopenBar}
    <div class="track" role="group" aria-label="Stage">${STAGES.map((n, i) => `<button class="step ${i < j.stage ? 'done' : ''}" style="--c:${stColor(i)}" data-stage="${i}" aria-pressed="${j.stage === i}"><i></i>${n}</button>`).join('')}</div>
    <div class="btns"><button class="btn" data-edit>Edit details</button>${url ? `<a class="btn job-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open posting</a>` : ''}</div>
  </section>
  ${matchPanel()}
  <div class="bench">
    <section class="panel"><h3>Follow-up</h3>
      <p class="hint" style="margin:0">${j.closed_reason ? 'Closed jobs are never flagged.' : j.stage === 4 ? 'Offers are not flagged. Take your time.' : j.follow_up_on ? 'You set a date. It is flagged once that day arrives.' : `Without a date, applied-to-interview jobs are flagged after ${S.pulse?.stale_days ?? 10} idle days. You have been quiet for ${plural(j.idle_days, 'day')}.`}</p>
      <div class="btns"><label class="f" style="flex:1;min-width:150px"><span class="sr">Follow-up date</span><input type="date" data-fu value="${esc(j.follow_up_on ?? '')}" ${j.closed_reason ? 'disabled' : ''}></label>${j.follow_up_on ? '<button class="btn" data-fu-clear>Clear date</button>' : ''}</div>
      ${reminderPanel(j)}
      ${draftPanel(j)}
      ${closeBtns}
      ${j.description ? `<h3>Posting</h3><div class="desc">${esc(j.description)}</div>` : ''}
      ${del}
    </section>
    <section class="panel"><h3>History and notes</h3>
      <form data-note-form class="btns" style="flex-wrap:nowrap;align-items:flex-end"><label class="f" style="flex:1"><span class="sr">New note</span><textarea name="text" rows="2" maxlength="2000" placeholder="Add a note: who you spoke to, what comes next"></textarea></label><button class="btn pri" type="submit">Add note</button></form>
      <ul class="tl">${events}</ul>
    </section>
  </div>
  <div class="bench" style="margin-top:14px">${interviewsPanel(j)}${prepPanel(j)}</div>
  <div class="bench" style="margin-top:14px">${contactsPanel(j)}</div>`;
}
const MATCH_BADGE: Record<string, [string, string]> = { match: ['good', 'Match'], partial: ['warn', 'Partial'], gap: ['bad', 'Gap'] };
function ring(score: number): string {
  const r = 34, c = 2 * Math.PI * r, col = score >= 70 ? 'var(--good)' : score >= 40 ? 'var(--accent)' : 'var(--warn)';
  return `<svg class="ring" viewBox="0 0 84 84" role="img" aria-label="${score} percent covered"><circle cx="42" cy="42" r="${r}" fill="none" stroke="var(--line)" stroke-width="8"/><circle cx="42" cy="42" r="${r}" fill="none" stroke="${col}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${(c * score) / 100} ${c}" transform="rotate(-90 42 42)"/><text x="42" y="49" text-anchor="middle">${score}</text></svg>`;
}
function suggestUi(req: string): string {
  const rw = S.rewrites[req];
  const key = `data-suggest="${esc(req)}"`;
  if (rw === 'busy') return '<span class="why mute">Asking for a suggestion…</span>';
  if (!rw) return `<span class="why"><button class="btn" ${key}>Suggest wording</button></span>`;
  if (!rw.ok) return `<span class="why"><span class="pill warn">Suggestion blocked</span> ${esc(rw.reason)} <button class="btn" ${key}>Try again</button></span>`;
  if (rw.unchanged) return `<span class="why"><span class="pill good">Already reads well</span> No change suggested for this line.</span>`;
  return `<div class="why sug"><div class="src"><span class="mono">Your line</span><br>${esc(rw.source)}</div>
    <div class="new"><span class="mono mute">Suggested, changes highlighted</span><br>${(rw.parts ?? []).map(([t, c]) => (c ? `<mark>${esc(t)}</mark>` : esc(t))).join('')}</div>
    <div class="btns"><button class="btn pri" data-copy-suggestion="${esc(req)}">Copy suggestion</button><button class="btn" ${key}>Another</button></div>
    <span class="mute" style="font-size:12.5px">Check it says only what is true before you use it.</span></div>`;
}
function matchPanel(): string {
  const m = S.match;
  const shield = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>';
  const aiHint = getAiKey() ? '' : ' Optional: add your own Anthropic API key on the Data tab to get wording suggestions.';
  const note = `<div class="guard">${shield}<span>Matching compares words only. It shows lines copied from your own resume as evidence and never writes or adds anything, so it cannot invent experience. A "Match" means shared wording, not a promise you qualify.${aiHint}</span></div>`;
  let inner: string;
  if (!m || !m.has_posting) inner = '<div class="empty">Paste the posting text with <b>Edit details</b> to see how it lines up with your resume.</div>';
  else if (!m.has_resume) inner = '<div class="empty">Add your resume first. <button class="btn" data-view="data">Open Data</button></div>';
  else if (!m.requirements.length) inner = '<div class="empty">No requirement lines were found in that posting. Bulleted requirements work best.</div>';
  else {
    inner = `<div class="score">${ring(m.score ?? 0)}<div><b>${m.counts.match} of ${m.requirements.length} requirements covered</b><div class="mute" style="font-size:13px">${plural(m.counts.gap, 'gap')} to be ready to talk about</div></div></div>
      <div>${m.requirements.map((r) => {
        const [tone, label] = MATCH_BADGE[r.status];
        const ev = r.evidence ? `<span class="why"><span class="mono">Your resume</span><br>${r.evidence.parts.map(([t, hit]) => (hit ? `<mark>${esc(t)}</mark>` : esc(t))).join('')}${r.status === 'partial' && r.missing.length ? `<br><span class="mute">Not seen in that line: ${esc(r.missing.slice(0, 5).join(', '))}</span>` : ''}</span>`
          : '<span class="why">Not on your resume. If it comes up, say so plainly.</span>';
        return `<div class="req"><span class="pill ${tone}">${label}</span><b>${esc(r.requirement)}</b>${ev}${r.evidence && getAiKey() ? suggestUi(r.requirement) : ''}</div>`;
      }).join('')}</div>`;
  }
  return `<section class="panel" style="margin-bottom:14px"><h3>How the posting matches your resume</h3>${inner}${note}</section>`;
}
const shortDay = (iso: string): string => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' }); };
function pulseView(): string {
  const p = S.pulse;
  if (!p) return '';
  const max = Math.max(...p.reached, 1);
  const closedTotal = p.closed.rejected + p.closed.withdrawn + p.closed.ghosted;
  const due = p.follow_ups_due.map((d) => `<div class="fu"><span><b>${esc(d.company)}</b><br><small class="mute">${esc(d.title)} · ${esc(d.stage_name)} · ${d.follow_up_on ? 'follow-up ' + esc(isoDay(d.follow_up_on)) : plural(d.idle_days, 'day') + ' quiet'}</small></span><button class="btn" data-open="${d.id}">Open</button></div>`).join('');
  const soon = p.upcoming_interviews.map((u) => `<div class="fu"><span><b>${esc(whenLocal(u.starts_at))}</b><br><small class="mute">${esc(u.label || 'Interview')} · ${esc(u.company)} · ${esc(u.title)}</small></span><span class="btns">${safeUrl(u.calendar_link) ? `<a class="btn" href="${esc(u.calendar_link)}" target="_blank" rel="noopener noreferrer">Add to calendar</a>` : ''}<button class="btn" data-open="${u.job_id}">Open</button></span></div>`).join('');
  const wmax = Math.max(...p.weekly_applied.map((w) => w.applied), 1);
  const weeks = p.weekly_applied.map((w, i) => `<div class="wk" title="Week of ${esc(isoDay(w.week_of))}: ${plural(w.applied, 'application')}"><b>${w.applied || ''}</b><span style="height:calc((100% - 40px) * ${(w.applied / wmax).toFixed(3)})"></span><small>${i === p.weekly_applied.length - 1 ? 'Now' : esc(shortDay(w.week_of))}</small></div>`).join('');
  const src = p.by_source.map((r) => `<tr><td>${esc(r.source)}</td><td class="num">${r.applied}</td><td class="num">${r.heard_back}</td><td class="num">${Math.round((r.heard_back / r.applied) * 100)}%</td></tr>`).join('');
  return `<div class="tiles">
    <div class="tile"><div class="mono mute">Active</div><div class="v">${p.active}</div><small>${plural(p.in_flight, 'application')} in flight</small></div>
    <div class="tile"><div class="mono mute">Heard back</div><div class="v">${p.response_rate_pct === null ? '–' : p.response_rate_pct + '%'}</div><small>${p.heard_back} of ${plural(p.applied_total, 'application')}</small></div>
    <div class="tile"><div class="mono mute">Days to a reply</div><div class="v">${p.median_days_to_reply ?? '–'}</div><small>median, applied to first answer</small></div>
    <div class="tile"><div class="mono mute">Follow-ups due</div><div class="v">${p.follow_ups_due.length}</div><small>waiting ${p.stale_days}+ days or past your date</small></div></div>
  <div class="two">
    <section class="panel"><h3>Coming up</h3>${soon || '<div class="empty">No interviews in the next two weeks.</div>'}</section>
    <section class="panel"><h3>Follow-ups due</h3>${due || '<div class="empty">Nothing is waiting. Nice.</div>'}</section>
    <section class="panel"><h3>Applications per week</h3><div class="weeks" role="img" aria-label="Applications per week, last 8 weeks: ${p.weekly_applied.map((w) => w.applied).join(', ')}">${weeks}</div></section>
    <section class="panel"><h3>How far applications get</h3><div class="mono mute">Jobs that ever reached this stage</div>
      ${STAGES.map((n, i) => `<div class="bar"><span>${n}</span><span class="trk"><i style="--c:${stColor(i)};width:${(p.reached[i] / max) * 100}%"></i></span><b>${p.reached[i]}</b></div>`).join('')}
      <p class="hint" style="margin:0">${closedTotal ? `Closed: ${p.closed.rejected} rejected, ${p.closed.ghosted} no response, ${p.closed.withdrawn} withdrawn.` : 'Nothing closed yet.'}</p>
    </section>
    <section class="panel"><h3>Where your applications came from</h3>${src ? `<table class="srct"><thead><tr><th>Source</th><th class="num">Applied</th><th class="num">Heard back</th><th class="num">Rate</th></tr></thead><tbody>${src}</tbody></table>
      <p class="hint" style="margin:0">"Heard back" means it reached a screen or later, or was rejected. Set the source with Edit details.</p>` : '<div class="empty">Apply to a few jobs to see this.</div>'}</section>
  </div>`;
}
function dataView(): string {
  const theme = (() => { try { return localStorage.getItem('jobledger.theme') || 'system'; } catch { return 'system'; } })();
  const pr = S.pendingRestore;
  return `<div class="two">
    <section class="panel"><h3>Your data</h3>
      <p class="hint" style="margin:0">Everything lives in a SQLite file on the machine running the server. Nothing is sent anywhere.</p>
      <div class="btns"><button class="btn" data-export-csv>Export CSV</button><button class="btn" data-backup>Download backup (JSON)</button></div>
      <h3>Follow-up reminders</h3>
      <p class="hint" style="margin:0">One calendar file with a 9:00 AM event and alert for every open job's follow-up (your date, or the day its idle nudge fires). Import it into Google Calendar on the web, Outlook or Apple Calendar so reminders reach you while JobLedger is closed. Importing again updates events in calendars that support it.</p>
      <div class="btns"><button class="btn" data-ics-all>Download all follow-ups (.ics)</button></div>
      <h3>Import from a spreadsheet</h3>
      <p class="hint" style="margin:0">A CSV with company and title columns. Optional columns: location, pay, url, posted_on, stage, status, follow_up_on. Your own export works too.</p>
      <label class="f"><span class="sr">CSV file</span><input type="file" accept=".csv,text/csv" data-import-file></label>
      <h3>Restore from a backup</h3>
      <p class="hint" style="margin:0">Restoring replaces all current jobs and history with the file's contents.</p>
      <label class="f"><span class="sr">Backup file</span><input type="file" accept="application/json,.json" data-restore-file></label>
      ${pr ? `<div class="confirm" role="alert"><span>Replace everything with this backup (${pr.jobs.length} jobs)?</span><button class="btn danger" data-restore-yes>Replace my data</button><button class="btn" data-restore-no>Cancel</button></div>` : ''}
      ${S.error ? `<p class="err" role="alert">${esc(S.error)}</p>` : ''}
    </section>
    <form class="panel" data-resume-form><h3>Your resume</h3>
      <p class="hint" style="margin:0">Paste your resume, one accomplishment per line (bullets are fine). It is stored only in your database and is used for matching on each job.</p>
      <label class="f"><span class="sr">Resume text</span><textarea name="text" rows="9" maxlength="40000">${esc(S.resume)}</textarea></label>
      <div class="btns"><button class="btn pri" type="submit">Save resume</button></div></form>
    <form class="panel" data-key-form><h3>Wording suggestions (optional)</h3>
      <p class="hint" style="margin:0">Off by default and free. If you add your own Anthropic API key, a button appears under each matched requirement that asks Claude to reword that one resume line. Each suggestion costs a fraction of a cent on your own account. The key is kept in this browser and sent only with those requests.</p>
      <label class="f">API key<input name="key" type="password" autocomplete="off" placeholder="${getAiKey() ? 'A key is saved in this browser' : 'sk-ant-…'}"></label>
      <div class="btns"><button class="btn pri" type="submit">Save key</button>${getAiKey() ? '<button class="btn" type="button" data-key-remove>Remove key</button>' : ''}</div></form>
    <section class="panel"><h3>Appearance</h3>
      <label class="f">Theme<select data-theme>${['system', 'light', 'dark'].map((t) => `<option value="${t}" ${t === theme ? 'selected' : ''}>${t[0].toUpperCase() + t.slice(1)}</option>`).join('')}</select></label>
      ${getToken() ? '<div class="btns"><button class="btn" data-signout>Forget access token</button></div>' : ''}
      <h3>Install on your phone</h3>
      <p class="hint" style="margin:0">When this page is opened over HTTPS, Chrome on Android offers Install app, which gives JobLedger its own icon. The easy way: install Tailscale on your computer and phone, then start JobLedger with <code>python -m jobledger --tailscale</code>. It prints a private https address only your own devices can open.</p>
    </section></div>`;
}
function gate(): string {
  return `<form class="panel gate" data-token-form><h3>Access token needed</h3><p class="hint" style="margin:0">This server is protected. Enter the token that was set as JL_TOKEN.</p>
    <label class="f">Token<input name="token" type="password" autocomplete="current-password" required></label>
    ${getToken() ? '<p class="err" role="alert">That token was not accepted.</p>' : ''}
    <div class="btns"><button class="btn pri" type="submit">Continue</button></div></form>`;
}
function render(): void {
  renderTabs();
  if (S.needToken) { stripEl.innerHTML = ''; viewEl.innerHTML = gate(); return; }
  renderStrip();
  viewEl.innerHTML = S.view === 'board' ? board() : S.view === 'job' ? jobView() : S.view === 'pulse' ? pulseView() : dataView();
}

/* ---------- actions */
function applyTheme(t: string): void {
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  else document.documentElement.removeAttribute('data-theme');
}
async function open(id: number): Promise<void> {
  S.sel = id; S.confirmDelete = false; S.form = null; S.view = 'job'; S.rewrites = {}; S.draft = false;
  const j = await guard(() => api.job(id));
  if (j) { setDetail(j); S.match = (await guard(() => api.match(id))) ?? null; }
  render();
}
async function moveTo(id: number, stage: number): Promise<void> {
  const j = await guard(() => api.move(id, stage));
  if (j) { setDetail(j); toast(`Moved to ${STAGES[stage]}.`); }
  await reload();
}
function formData(f: HTMLFormElement): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  new FormData(f).forEach((v, k) => { o[k] = k === 'stage' ? Number(v) : String(v); });
  return o;
}

document.addEventListener('click', async (ev) => {
  const t = (ev.target as HTMLElement).closest<HTMLElement>('[data-view],[data-open],[data-go],[data-add],[data-cancel-form],[data-toggle-closed],[data-stage],[data-close],[data-reopen],[data-edit],[data-delete],[data-delete-yes],[data-delete-no],[data-fu-clear],[data-export-csv],[data-backup],[data-restore-yes],[data-restore-no],[data-signout],[data-contacted],[data-del-contact],[data-del-prep],[data-prep-defaults],[data-draft],[data-draft-close],[data-copy-draft],[data-suggest],[data-copy-suggestion],[data-key-remove],[data-search-clear],[data-ics],[data-ics-all],[data-fill-link],[data-del-interview]');
  if (!t) return;
  const d = t.dataset;
  if (d.view) {
    S.view = d.view as View; S.form = null; S.error = '';
    if (S.view === 'job' && !S.detail && S.jobs.length) { await open((active()[0] ?? S.jobs[0]).id); return; }
    if (S.view === 'pulse') await guard(refresh);
  } else if (d.open) { await open(Number(d.open)); return; }
  else if (d.go) { S.view = d.go as View; S.form = null; }
  else if (d.add !== undefined) { S.form = S.form === 'add' ? null : 'add'; S.error = ''; }
  else if (d.cancelForm !== undefined) { S.form = null; S.error = ''; }
  else if (d.toggleClosed !== undefined) S.showClosed = !S.showClosed;
  else if (d.searchClear !== undefined) { S.query = ''; S.hits = null; render(); document.querySelector<HTMLInputElement>('[data-search]')?.focus(); return; }
  else if (d.stage !== undefined && S.sel !== null) { await moveTo(S.sel, Number(d.stage)); return; }
  else if (d.close && S.sel !== null) { const j = await guard(() => api.close(S.sel!, d.close!)); if (j) { setDetail(j); toast('Moved off your board.'); } await reload(); return; }
  else if (d.reopen !== undefined && S.sel !== null) { const j = await guard(() => api.reopen(S.sel!)); if (j) { setDetail(j); toast('Back on your board.'); } await reload(); return; }
  else if (d.edit !== undefined) { S.form = 'edit'; S.error = ''; }
  else if (d.delete !== undefined) S.confirmDelete = true;
  else if (d.deleteNo !== undefined) S.confirmDelete = false;
  else if (d.deleteYes !== undefined && S.sel !== null) {
    await guard(() => api.remove(S.sel!));
    S.sel = null; S.detail = null; S.confirmDelete = false; S.view = 'board'; toast('Job deleted.');
    await reload(); return;
  } else if (d.fuClear !== undefined && S.sel !== null) { const j = await guard(() => api.update(S.sel!, { follow_up_on: null })); if (j) setDetail(j); await reload(); return; }
  else if (d.contacted && S.sel !== null) {
    const today = new Date(); const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    const j = await guard(() => api.updateContact(S.sel!, Number(d.contacted), { last_contacted_on: iso })); if (j) setDetail(j); await reload(); return;
  }
  else if (d.delContact && S.sel !== null) { const j = await guard(() => api.delContact(S.sel!, Number(d.delContact))); if (j) setDetail(j); await reload(); return; }
  else if (d.delPrep && S.sel !== null) { const j = await guard(() => api.delPrep(S.sel!, Number(d.delPrep))); if (j) setDetail(j); render(); return; }
  else if (d.prepDefaults !== undefined && S.sel !== null) { const j = await guard(() => api.defaultPrep(S.sel!)); if (j) setDetail(j); render(); return; }
  else if (d.draft !== undefined) S.draft = true;
  else if (d.draftClose !== undefined) S.draft = false;
  else if (d.copyDraft !== undefined) {
    const text = `${(document.querySelector('[data-draft-subject]') as HTMLInputElement).value}\n\n${(document.querySelector('[data-draft-body]') as HTMLTextAreaElement).value}`;
    try { await navigator.clipboard.writeText(text); toast('Copied.'); } catch { (document.querySelector('[data-draft-body]') as HTMLTextAreaElement).select(); toast('Press Ctrl+C to copy the selected text.'); }
    return;
  }
  else if (d.suggest && S.sel !== null) {
    const req = d.suggest, id = S.sel;
    S.rewrites[req] = 'busy'; render();
    const r = await guard(() => api.rewrite(id, req));
    if (r) S.rewrites[req] = r; else delete S.rewrites[req];
    render(); return;
  }
  else if (d.copySuggestion) {
    const r = S.rewrites[d.copySuggestion];
    if (r && r !== 'busy' && r.suggestion) { try { await navigator.clipboard.writeText(r.suggestion); toast('Copied.'); } catch { toast('Could not copy. Select the text and copy it.'); } }
    return;
  }
  else if (d.keyRemove !== undefined) { setAiKey(''); S.rewrites = {}; toast('Key removed.'); }
  else if (d.exportCsv !== undefined) { await guard(() => api.download('/api/export.csv', 'jobledger.csv')); return; }
  else if (d.delInterview && S.sel !== null) { const j = await guard(() => api.delInterview(S.sel!, Number(d.delInterview))); if (j) { setDetail(j); toast('Interview removed.'); await reload(); } return; }
  else if (d.fillLink !== undefined) { await fillFromLink(t as HTMLButtonElement); return; }
  else if (d.ics !== undefined && S.sel !== null) { await guard(() => api.download(`/api/jobs/${S.sel}/calendar.ics`, `jobledger-follow-up-${S.sel}.ics`)); return; }
  else if (d.icsAll !== undefined) {
    if (!S.jobs.some((j) => !j.closed_reason && j.reminder_on)) { toast('No open jobs have a follow-up yet.'); return; }
    await guard(() => api.download('/api/calendar.ics', 'jobledger-follow-ups.ics')); toast('Calendar file downloaded.'); return;
  }
  else if (d.backup !== undefined) { await guard(() => api.download('/api/backup', 'jobledger-backup.json')); toast('Backup downloaded.'); return; }
  else if (d.restoreNo !== undefined) S.pendingRestore = null;
  else if (d.restoreYes !== undefined && S.pendingRestore) {
    const r = await guard(() => api.restore(S.pendingRestore));
    S.pendingRestore = null; S.sel = null; S.detail = null;
    if (r) toast(`Restored ${plural(r.jobs, 'job')}.`);
    await reload(); return;
  } else if (d.signout !== undefined) { setToken(''); S.needToken = true; }
  render();
});

document.addEventListener('submit', async (ev) => {
  const f = ev.target as HTMLFormElement;
  ev.preventDefault();
  if (f.matches('[data-token-form]')) {
    setToken(String(new FormData(f).get('token') || ''));
    S.needToken = false;
    await reload();
    return;
  }
  if (f.matches('[data-contact-form]') && S.sel !== null) {
    const j = await guard(() => api.addContact(S.sel!, formData(f)));
    if (j) { setDetail(j); toast('Person added.'); } else return;
    await reload(); return;
  }
  if (f.matches('[data-interview-form]') && S.sel !== null) {
    const fd = new FormData(f);
    const d = { starts_at: String(fd.get('starts_at') || ''), minutes: Number(fd.get('minutes') || 60), label: String(fd.get('label') || ''), place: String(fd.get('place') || '') };
    const j = await guard(() => api.addInterview(S.sel!, d));
    if (j) { setDetail(j); toast('Interview added.'); } else return;
    await reload(); return;
  }
  if (f.matches('[data-prep-form]') && S.sel !== null) {
    const j = await guard(() => api.addPrep(S.sel!, String(new FormData(f).get('text') || '')));
    if (j) setDetail(j);
    render(); return;
  }
  if (f.matches('[data-key-form]')) {
    const k = String(new FormData(f).get('key') || '').trim();
    if (!k.startsWith('sk-ant-')) { S.error = 'That does not look like an Anthropic API key (it starts with sk-ant-).'; render(); return; }
    setAiKey(k); S.error = ''; toast('Key saved in this browser.'); render(); return;
  }
  if (f.matches('[data-resume-form]')) {
    const r = await guard(() => api.saveResume(String(new FormData(f).get('text') || '')));
    if (r) { S.resume = r.text; toast('Resume saved.'); }
    await reload();
    return;
  }
  if (f.matches('[data-note-form]') && S.sel !== null) {
    const text = String(new FormData(f).get('text') || '');
    const j = await guard(() => api.note(S.sel!, text));
    if (j) { setDetail(j); toast('Note added.'); }
    await reload();
    return;
  }
  if (f.dataset.form === 'add') {
    const j = await guard(() => api.add(formData(f)));
    if (j) { S.form = null; toast('Job added.'); await open(j.id); await guard(refresh); render(); } else render();
  } else if (f.dataset.form === 'edit' && S.sel !== null) {
    const j = await guard(() => api.update(S.sel!, formData(f)));
    if (j) { setDetail(j); S.form = null; toast('Saved.'); await reload(); } else render();
  }
});

document.addEventListener('change', async (ev) => {
  const t = ev.target as HTMLInputElement;
  if (t.matches('[data-fu]') && S.sel !== null) {
    const j = await guard(() => api.update(S.sel!, { follow_up_on: t.value || null }));
    if (j) { setDetail(j); toast(t.value ? 'Follow-up date set.' : 'Follow-up date cleared.'); }
    await reload();
  } else if (t.matches('[data-theme]')) {
    try { localStorage.setItem('jobledger.theme', t.value); } catch { /* ignore */ }
    applyTheme(t.value);
  } else if (t.matches('[data-prep]') && S.sel !== null) {
    const j = await guard(() => api.setPrep(S.sel!, Number(t.dataset.prep), t.checked));
    if (j) setDetail(j);
    render();
  } else if (t.matches('[data-import-file]') && t.files?.[0]) {
    const text = await t.files[0].text();
    const r = await guard(() => api.importCsv(text));
    if (r) { toast(`Imported ${plural(r.imported, 'job')}.`); await reload(); } else render();
  } else if (t.matches('[data-restore-file]') && t.files?.[0]) {
    try {
      const data = JSON.parse(await t.files[0].text());
      if (!data || data.app !== 'jobledger' || !Array.isArray(data.jobs)) throw new Error('That file is not a JobLedger backup.');
      S.pendingRestore = data; S.error = '';
    } catch (e) { S.pendingRestore = null; S.error = e instanceof Error && e.message.startsWith('That') ? e.message : 'That file could not be read as a backup.'; }
    render();
  }
});

/* fill the add/edit form from a posting link; fields the user already typed are kept */
const FILL_LABEL: Record<string, string> = { company: 'company', title: 'title', location: 'location', pay: 'pay', posted_on: 'posted date', description: 'posting text' };
async function fillFromLink(btn: HTMLButtonElement): Promise<void> {
  const f = btn.closest('form') as HTMLFormElement;
  const url = (f.elements.namedItem('url') as HTMLInputElement).value.trim();
  if (!url) { toast('Paste the posting link first.'); (f.elements.namedItem('url') as HTMLInputElement).focus(); return; }
  btn.disabled = true; const label = btn.textContent; btn.textContent = 'Reading…';
  try {
    const r = await api.posting(url);
    const filled: string[] = [], kept: string[] = [];
    for (const [k, v] of Object.entries(r.fields)) {
      const el = f.elements.namedItem(k) as HTMLInputElement | HTMLTextAreaElement | null;
      if (!el || k === 'url' || !v) continue;
      if (el.value.trim()) { if (el.value.trim() !== v) kept.push(FILL_LABEL[k] ?? k); continue; }
      el.value = v; filled.push(FILL_LABEL[k] ?? k);
    }
    S.error = '';
    toast(filled.length ? `Filled ${filled.join(', ')}${kept.length ? `; kept what you typed for ${kept.join(', ')}` : ''}.${r.source === 'page' ? ' This page had no job data, so check the details.' : ''}`
      : 'Nothing new to fill: those fields already have text.');
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e));
  } finally { btn.disabled = false; btn.textContent = label; }
}

/* search: only the results are redrawn, so the box keeps focus and cursor while typing */
let searchTimer: number | undefined;
let searchSeq = 0;
function showResults(): void {
  const el = document.querySelector<HTMLElement>('[data-results]');
  if (el) el.innerHTML = boardResults(); else render();
}
async function runSearch(): Promise<void> {
  const q = S.query.trim();
  const seq = ++searchSeq;
  if (!q) { S.hits = null; showResults(); return; }
  const r = await guard(() => api.search(q));
  if (seq !== searchSeq || !r) return; // a newer search has started, or it failed (guard showed why)
  S.hits = new Set(r.ids);
  if (closed().some((j) => S.hits!.has(j.id))) S.showClosed = true;
  showResults();
}
document.addEventListener('input', (ev) => {
  const t = ev.target as HTMLInputElement;
  if (!t.matches('[data-search]')) return;
  S.query = t.value;
  clearTimeout(searchTimer);
  searchTimer = window.setTimeout(runSearch, 150);
});
document.addEventListener('keydown', (ev) => {
  const t = ev.target as HTMLInputElement;
  if (ev.key === 'Escape' && t.matches('[data-search]') && t.value) { t.value = ''; S.query = ''; clearTimeout(searchTimer); void runSearch(); }
});

/* drag and drop between columns */
let dragId: number | null = null;
document.addEventListener('dragstart', (ev) => {
  const c = (ev.target as HTMLElement).closest<HTMLElement>('[data-open][draggable]');
  if (!c) return;
  dragId = Number(c.dataset.open); c.classList.add('drag');
  ev.dataTransfer?.setData('text/plain', String(dragId));
});
document.addEventListener('dragend', () => { dragId = null; document.querySelectorAll('.drag,.over').forEach((n) => n.classList.remove('drag', 'over')); });
document.addEventListener('dragover', (ev) => {
  const col = (ev.target as HTMLElement).closest<HTMLElement>('[data-col]');
  if (col && dragId !== null) { ev.preventDefault(); document.querySelectorAll('.over').forEach((n) => n !== col && n.classList.remove('over')); col.classList.add('over'); }
});
document.addEventListener('drop', async (ev) => {
  const col = (ev.target as HTMLElement).closest<HTMLElement>('[data-col]');
  if (!col || dragId === null) return;
  ev.preventDefault();
  const id = dragId; dragId = null;
  const stage = Number(col.dataset.col);
  if (S.jobs.find((j) => j.id === id)?.stage !== stage) await moveTo(id, stage); else render();
});

/* ---------- start */
if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
  navigator.serviceWorker.register('/sw.js').catch(() => { /* optional */ });
}
(async () => {
  try { applyTheme(localStorage.getItem('jobledger.theme') || 'system'); } catch { /* ignore */ }
  render();
  await guard(refresh);
  render();
})();
