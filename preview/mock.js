// JobLedger preview: answers the app's /api calls inside the page, so the real interface can be tried
// without the Python server. It mirrors jobledger/service.py for everything the UI can do; things that need
// the server (reading links, downloads, import/restore, wording suggestions) say so instead.
// Changes live only in this page and are gone on reload. Demo data comes from window.__JL_SNAPSHOT__.
(function () {
  'use strict';
  var SNAP = window.__JL_SNAPSHOT__;
  var DAY = 86400, STALE = 10;
  var STAGES = ['Saved', 'Applied', 'Screen', 'Interview', 'Offer'];
  var CLOSED_TEXT = { rejected: 'Rejected.', withdrawn: 'Withdrew the application.', ghosted: 'No response. Marked as ghosted.' };
  var DEFAULT_PREP = SNAP.default_prep;
  var LIMITS = { company: 120, title: 160, location: 120, pay: 80, url: 500, posted_on: 10, description: 60000, source: 80 };
  var EDITABLE = ['company', 'title', 'location', 'pay', 'url', 'posted_on', 'description', 'source'];
  var FULL = 'needs the full app running on your computer';

  // Shift the demo so it always looks current: everything moves forward by whole days since it was made.
  var shiftDays = Math.max(0, Math.floor((Date.now() / 1000 - SNAP.made_at) / DAY));
  var shift = shiftDays * DAY;
  function shiftLocal(s) { var d = parseLocal(s); d.setDate(d.getDate() + shiftDays); return fmtLocal(d); }
  function shiftDate(s) { if (!s) return s; var p = s.split('-').map(Number); var d = new Date(p[0], p[1] - 1, p[2] + shiftDays); return isoDate(d); }

  var db = { jobs: [], events: [], contacts: [], prep: [], interviews: [], resume: SNAP.resume || '', seq: 1000 };
  SNAP.backup.jobs.forEach(function (j) {
    db.jobs.push(Object.assign({}, j, { stage_changed_at: j.stage_changed_at + shift, created_at: j.created_at + shift, updated_at: j.updated_at + shift,
      follow_up_on: j.follow_up_on ? shiftDate(j.follow_up_on) : null, posted_on: j.posted_on ? shiftDate(j.posted_on) : '' }));
  });
  SNAP.backup.events.forEach(function (e) { db.events.push(Object.assign({}, e, { at: e.at + shift })); });
  SNAP.backup.contacts.forEach(function (c) { db.contacts.push(Object.assign({}, c, { last_contacted_on: c.last_contacted_on ? shiftDate(c.last_contacted_on) : '' })); });
  SNAP.backup.prep.forEach(function (p) { db.prep.push(Object.assign({}, p, { done: !!p.done })); });
  (SNAP.backup.interviews || []).forEach(function (i) { db.interviews.push(Object.assign({}, i, { starts_at: shiftLocal(i.starts_at) })); });
  var matchFor = SNAP.matches; // job id -> match result, valid while the posting text and resume are unchanged
  var matchedText = {}; db.jobs.forEach(function (j) { matchedText[j.id] = j.description; });
  var matchedResume = db.resume;

  // ---------- helpers
  function now() { return Date.now() / 1000; }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function isoDate(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function fmtLocal(d) { return isoDate(d) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function parseLocal(s) { var a = s.split('T'), d = a[0].split('-').map(Number), t = (a[1] || '00:00').split(':').map(Number); return new Date(d[0], d[1] - 1, d[2], t[0], t[1]); }
  function today() { return isoDate(new Date()); }
  function dayOf(epoch) { return isoDate(new Date(epoch * 1000)); }
  function nextId() { return ++db.seq; }
  function Bad(msg) { this.status = 400; this.msg = msg; }
  function Missing(msg) { this.status = 404; this.msg = msg || 'Not found'; }
  function row(id) { var r = db.jobs.find(function (j) { return j.id === id; }); if (!r) throw new Missing(); return r; }
  function event(jobId, kind, text) { db.events.push({ id: nextId(), job_id: jobId, at: now(), kind: kind, text: text }); }
  function touch(r) { r.updated_at = now(); }
  function isDate(s) { return /^\d{4}-\d{2}-\d{2}$/.test(s) && !isNaN(new Date(s + 'T00:00').getTime()); }
  function clean(f, v) {
    if (v === null || v === undefined) v = '';
    if (typeof v !== 'string') throw new Bad(f + ' must be text');
    v = v.trim();
    if (v.length > LIMITS[f]) throw new Bad(f + ' is too long (limit ' + LIMITS[f] + ' characters)');
    if ((f === 'company' || f === 'title') && !v) throw new Bad(f + ' is required');
    if (f === 'url' && v && !/^https?:\/\//i.test(v)) throw new Bad('url must start with http:// or https://');
    if (f === 'posted_on' && v && !isDate(v)) throw new Bad('posted_on must be a date like 2026-10-24');
    return v;
  }
  function follow(v) {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v !== 'string' || !isDate(v)) throw new Bad('follow_up_on must be a date like 2026-10-24');
    return v;
  }
  function when(s) {
    var d = parseLocal(s), wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()], mo = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()];
    var h = d.getHours() % 12 || 12; return wd + ' ' + mo + ' ' + d.getDate() + ', ' + h + ':' + pad(d.getMinutes()) + ' ' + (d.getHours() < 12 ? 'AM' : 'PM');
  }
  function compact(d) { return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + 'T' + pad(d.getHours()) + pad(d.getMinutes()) + '00'; }
  function gcal(text, start, end, details, location) {
    var q = 'action=TEMPLATE&text=' + encodeURIComponent(text) + '&dates=' + compact(start) + '%2F' + compact(end) + '&details=' + encodeURIComponent(details);
    if (location) q += '&location=' + encodeURIComponent(location);
    return 'https://calendar.google.com/calendar/render?' + q;
  }

  // ---------- views (same fields as Tracker._view and get_job)
  function nextInterview(jobId) {
    var t = new Date(), best = null;
    db.interviews.forEach(function (i) {
      if (i.job_id !== jobId) return;
      var end = new Date(parseLocal(i.starts_at).getTime() + i.minutes * 60000);
      if (end >= t && (!best || i.starts_at < best)) best = i.starts_at;
    });
    return best;
  }
  function view(r) {
    var n = now();
    var last = r.created_at;
    db.events.forEach(function (e) { if (e.job_id === r.id && e.at > last) last = e.at; });
    var closed = r.closed_reason !== null && r.closed_reason !== undefined;
    var idle = Math.floor((n - last) / DAY);
    var due = false, remind = null, nxt = closed ? null : nextInterview(r.id);
    if (nxt && !r.follow_up_on) { /* an interview is coming up */ }
    else if (!closed && r.stage !== 4) {
      if (r.follow_up_on) { remind = r.follow_up_on; due = r.follow_up_on <= today(); }
      else if (r.stage >= 1 && r.stage <= 3) { due = idle >= STALE; remind = dayOf(last + STALE * DAY); }
    }
    return { id: r.id, company: r.company, title: r.title, location: r.location, pay: r.pay, url: r.url, posted_on: r.posted_on,
      description: r.description, source: r.source || '', stage: r.stage, stage_name: STAGES[r.stage], max_stage: r.max_stage,
      closed_reason: closed ? r.closed_reason : null, follow_up_on: r.follow_up_on || null, reminder_on: remind,
      reminder_is_nudge: !!remind && !r.follow_up_on, next_interview: nxt, days_in_stage: Math.floor((n - r.stage_changed_at) / DAY),
      idle_days: idle, follow_up_due: due, created_at: r.created_at, updated_at: r.updated_at };
  }
  function followLink(j) {
    if (!j.reminder_on) return null;
    var lines = ['Stage: ' + j.stage_name + '.'];
    if (j.reminder_is_nudge) lines.push("No follow-up date was set; this is when JobLedger's idle nudge fires if nothing happens first.");
    (j.contacts || []).forEach(function (c) { lines.push('Contact: ' + [c.name, c.role, c.email].filter(Boolean).join(', ') + '.'); });
    if (/^https?:\/\//.test(j.url || '')) lines.push('Posting: ' + j.url);
    lines.push('From JobLedger.');
    var s = parseLocal(j.reminder_on + 'T09:00');
    return gcal('Follow up: ' + j.title + ' at ' + j.company, s, new Date(s.getTime() + 15 * 60000), lines.join('\n'));
  }
  function interviewLink(i, j) {
    var s = parseLocal(i.starts_at), lines = [];
    if (i.place) lines.push('Where: ' + i.place);
    if (/^https?:\/\//.test(j.url || '')) lines.push('Posting: ' + j.url);
    lines.push('From JobLedger.');
    return gcal((i.label || 'Interview') + ': ' + j.title + ' at ' + j.company, s, new Date(s.getTime() + i.minutes * 60000), lines.join('\n'), i.place);
  }
  function getJob(id) {
    var r = row(id), out = view(r);
    out.events = db.events.filter(function (e) { return e.job_id === id; }).sort(function (a, b) { return b.at - a.at || b.id - a.id; })
      .map(function (e) { return { id: e.id, at: e.at, kind: e.kind, text: e.text }; });
    out.contacts = db.contacts.filter(function (c) { return c.job_id === id; }).map(function (c) { return { id: c.id, name: c.name, role: c.role, email: c.email, last_contacted_on: c.last_contacted_on }; });
    out.prep = db.prep.filter(function (p) { return p.job_id === id; }).sort(function (a, b) { return a.position - b.position || a.id - b.id; })
      .map(function (p) { return { id: p.id, text: p.text, done: !!p.done }; });
    out.calendar_link = followLink(out);
    var t = new Date();
    out.interviews = db.interviews.filter(function (i) { return i.job_id === id; }).sort(function (a, b) { return a.starts_at < b.starts_at ? -1 : a.starts_at > b.starts_at ? 1 : a.id - b.id; })
      .map(function (i) { return { id: i.id, starts_at: i.starts_at, minutes: i.minutes, label: i.label, place: i.place,
        past: new Date(parseLocal(i.starts_at).getTime() + i.minutes * 60000) < t, calendar_link: interviewLink(i, out) }; });
    return out;
  }
  function listJobs(includeClosed) {
    return db.jobs.filter(function (j) { return includeClosed || !j.closed_reason; })
      .sort(function (a, b) { return a.stage - b.stage || b.stage_changed_at - a.stage_changed_at || b.id - a.id; }).map(view);
  }

  // ---------- rules (same as Tracker)
  function addJob(d) {
    if (!d || typeof d !== 'object') throw new Bad('Expected an object');
    var vals = {}; EDITABLE.forEach(function (f) { vals[f] = clean(f, d[f]); });
    var stage = d.stage === undefined ? 0 : d.stage;
    if (stage !== 0 && stage !== 1) throw new Bad('A new job starts as Saved or Applied');
    var n = now(), id = nextId();
    db.jobs.push(Object.assign(vals, { id: id, stage: stage, max_stage: stage, closed_reason: null, follow_up_on: follow(d.follow_up_on), stage_changed_at: n, created_at: n, updated_at: n }));
    event(id, 'created', stage === 0 ? 'Saved the posting.' : 'Applied.');
    return getJob(id);
  }
  function move(id, stage) {
    if (typeof stage !== 'number' || stage % 1 || stage < 0 || stage > 4) throw new Bad('stage must be a number from 0 to 4');
    var r = row(id);
    if (r.stage === stage && !r.closed_reason) return getJob(id);
    var wasClosed = !!r.closed_reason, from = r.stage;
    r.stage = stage; r.max_stage = Math.max(r.max_stage, stage); r.closed_reason = null; r.stage_changed_at = r.updated_at = now();
    event(id, wasClosed ? 'reopened' : 'stage', wasClosed ? 'Reopened at ' + STAGES[stage] + '.' : 'Moved from ' + STAGES[from] + ' to ' + STAGES[stage] + '.');
    return getJob(id);
  }
  function updateJob(id, p) {
    if (!p || typeof p !== 'object') throw new Bad('Expected an object');
    var r = row(id), changed = false, next = {};
    EDITABLE.forEach(function (f) { if (f in p) { next[f] = clean(f, p[f]); changed = true; } });
    var fu; if ('follow_up_on' in p) { fu = follow(p.follow_up_on); changed = true; }
    Object.assign(r, next);
    if ('follow_up_on' in p) { r.follow_up_on = fu; }
    if (changed) { touch(r); if ('follow_up_on' in p) event(id, 'follow_up', fu ? 'Follow-up set for ' + fu + '.' : 'Follow-up date cleared.'); }
    if ('stage' in p) move(id, p.stage);
    return getJob(id);
  }
  function interviewFields(d, partial) {
    if (!d || typeof d !== 'object') throw new Bad('Expected an object');
    var out = {};
    if ('starts_at' in d || !partial) {
      var v = d.starts_at;
      if (typeof v !== 'string' || !v.trim()) throw new Bad('Give the interview a date and time');
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v.trim())) throw new Bad('starts_at must be a local date and time like 2026-10-24T14:30');
      out.starts_at = v.trim().slice(0, 16);
    }
    if ('minutes' in d) {
      if (typeof d.minutes !== 'number' || d.minutes % 1 || d.minutes < 5 || d.minutes > 600) throw new Bad('minutes must be a whole number from 5 to 600');
      out.minutes = d.minutes;
    }
    [['label', 120], ['place', 300]].forEach(function (x) {
      if (x[0] in d) { var s = d[x[0]] == null ? '' : d[x[0]]; if (typeof s !== 'string') throw new Bad(x[0] + ' must be text'); s = s.trim(); if (s.length > x[1]) throw new Bad(x[0] + ' is too long (limit ' + x[1] + ' characters)'); out[x[0]] = s; }
    });
    return out;
  }
  function contactFields(d, partial) {
    if (!d || typeof d !== 'object') throw new Bad('Expected an object');
    var out = {};
    [['name', 120], ['role', 120], ['email', 200], ['last_contacted_on', 10]].forEach(function (x) {
      var k = x[0];
      if (!(k in d)) { if (k === 'name' && !partial) throw new Bad('A contact needs a name'); return; }
      var v = d[k] == null ? '' : d[k];
      if (typeof v !== 'string') throw new Bad(k + ' must be text');
      v = v.trim();
      if (v.length > x[1]) throw new Bad(k + ' is too long (limit ' + x[1] + ' characters)');
      if (k === 'name' && !v) throw new Bad('A contact needs a name');
      if (k === 'email' && v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) throw new Bad('That email address does not look right');
      if (k === 'last_contacted_on' && v && !isDate(v)) throw new Bad('last_contacted_on must be a date like 2026-10-24');
      out[k] = v;
    });
    return out;
  }
  function addPrep(id, text) {
    if (typeof text !== 'string' || !text.trim()) throw new Bad('A checklist item cannot be empty');
    if (text.trim().length > 200) throw new Bad('A checklist item can be up to 200 characters');
    row(id);
    var pos = 1 + db.prep.filter(function (p) { return p.job_id === id; }).reduce(function (m, p) { return Math.max(m, p.position); }, 0);
    db.prep.push({ id: nextId(), job_id: id, text: text.trim(), done: false, position: pos });
  }
  function search(q) {
    if (typeof q !== 'string' || q.length > 200) throw new Bad('Search for up to 200 characters');
    var words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    return db.jobs.slice().sort(function (a, b) { return a.id - b.id; }).filter(function (j) {
      var hay = [j.company, j.title, j.location, j.pay, j.description, j.source]
        .concat(db.events.filter(function (e) { return e.job_id === j.id && e.kind === 'note'; }).map(function (e) { return e.text; }))
        .concat(db.contacts.filter(function (c) { return c.job_id === j.id; }).map(function (c) { return c.name + ' ' + c.role + ' ' + c.email; }))
        .concat(db.interviews.filter(function (i) { return i.job_id === j.id; }).map(function (i) { return i.label + ' ' + i.place; }))
        .join(' ').toLowerCase();
      return words.every(function (w) { return hay.indexOf(w) >= 0; });
    }).map(function (j) { return j.id; });
  }
  function upcoming(days) {
    var t = new Date(), until = new Date(t.getTime() + days * DAY * 1000);
    return db.interviews.map(function (i) {
      var j = db.jobs.find(function (x) { return x.id === i.job_id; });
      if (!j || j.closed_reason) return null;
      var s = parseLocal(i.starts_at);
      if (s > until || new Date(s.getTime() + i.minutes * 60000) < t) return null;
      return { id: i.id, job_id: j.id, starts_at: i.starts_at, minutes: i.minutes, label: i.label, place: i.place, company: j.company, title: j.title,
        url: j.url, stage: j.stage, calendar_link: interviewLink(i, j) };
    }).filter(Boolean).sort(function (a, b) { return a.starts_at < b.starts_at ? -1 : a.starts_at > b.starts_at ? 1 : a.id - b.id; });
  }
  function pulse() {
    var jobs = listJobs(true), active = jobs.filter(function (j) { return !j.closed_reason; });
    var reached = [0, 1, 2, 3, 4].map(function (i) { return jobs.filter(function (j) { return j.max_stage >= i; }).length; });
    var closedCount = { rejected: 0, withdrawn: 0, ghosted: 0 }; jobs.forEach(function (j) { if (j.closed_reason) closedCount[j.closed_reason]++; });
    var due = active.filter(function (j) { return j.follow_up_due; });
    // when each job was applied to and first heard back (same rules as Tracker._response_stats)
    var applied = {}, heard = {}, idx = {}; STAGES.forEach(function (s, i) { idx[s] = i; });
    db.events.slice().sort(function (a, b) { return a.at - b.at || a.id - b.id; }).forEach(function (e) {
      var j = e.job_id, m = e.kind === 'stage' ? /^Moved from (\w+) to (\w+)\.$/.exec(e.text) : null;
      if (!(j in applied)) {
        if (e.kind === 'created' && e.text === 'Applied.') applied[j] = e.at;
        else if (m && (idx[m[2]] || 0) >= 1) { applied[j] = e.at; if (idx[m[2]] >= 2) heard[j] = e.at; }
        return;
      }
      if (j in heard) return;
      if (m && (idx[m[2]] || 0) >= 2) heard[j] = e.at;
      else if (e.kind === 'closed' && e.text === 'Rejected.') heard[j] = e.at;
    });
    var byId = {}; jobs.forEach(function (j) { byId[j.id] = j; });
    var ids = Object.keys(applied).filter(function (k) { return byId[k]; });
    var waits = ids.filter(function (k) { return k in heard; }).map(function (k) { return (heard[k] - applied[k]) / DAY; }).sort(function (a, b) { return a - b; });
    var median = null;
    if (waits.length) { var mid = Math.floor(waits.length / 2); median = Math.round(waits.length % 2 ? waits[mid] : (waits[mid - 1] + waits[mid]) / 2); }
    var t = new Date(); t.setHours(0, 0, 0, 0);
    var start = new Date(t); start.setDate(t.getDate() - ((t.getDay() + 6) % 7) - 49);
    var weeks = []; for (var w = 0; w < 8; w++) { var d = new Date(start); d.setDate(start.getDate() + 7 * w); weeks.push({ week_of: isoDate(d), applied: 0 }); }
    ids.forEach(function (k) { var d = new Date(applied[k] * 1000); d.setHours(0, 0, 0, 0); var i = Math.floor(Math.round((d - start) / 86400000) / 7); if (i >= 0 && i < 8) weeks[i].applied++; });
    var src = {};
    ids.forEach(function (k) { var s = byId[k].source || 'Not set'; src[s] = src[s] || { source: s, applied: 0, heard_back: 0 }; src[s].applied++; if (k in heard) src[s].heard_back++; });
    var heardN = ids.filter(function (k) { return k in heard; }).length;
    return {
      active: active.length, total: jobs.length, reached: reached, closed: closedCount,
      reached_screen_pct: reached[1] ? Math.round(reached[2] / reached[1] * 100) : null,
      avg_days_in_stage: active.length ? Math.round(active.reduce(function (s, j) { return s + j.days_in_stage; }, 0) / active.length) : null,
      in_flight: active.filter(function (j) { return j.stage > 0; }).length,
      follow_ups_due: due.map(function (j) { return { id: j.id, company: j.company, title: j.title, stage_name: j.stage_name, days_in_stage: j.days_in_stage, idle_days: j.idle_days, follow_up_on: j.follow_up_on }; }),
      stale_days: STALE, applied_total: ids.length, heard_back: heardN, response_rate_pct: ids.length ? Math.round(heardN / ids.length * 100) : null,
      median_days_to_reply: median, weekly_applied: weeks,
      by_source: Object.keys(src).map(function (k) { return src[k]; }).sort(function (a, b) { return b.applied - a.applied || (a.source === 'Not set') - (b.source === 'Not set') || (a.source < b.source ? -1 : 1); }),
      upcoming_interviews: upcoming(14),
    };
  }
  function match(id) {
    var r = row(id);
    if (!r.description.trim()) return { has_resume: !!db.resume.trim(), has_posting: false, requirements: [], score: null, counts: { match: 0, partial: 0, gap: 0 } };
    if (matchFor[id] && matchedText[id] === r.description && matchedResume === db.resume) return matchFor[id];
    return { has_resume: !!db.resume.trim(), has_posting: true, requirements: [], score: null, counts: { match: 0, partial: 0, gap: 0 },
      note: 'In this preview, matching works on the sample jobs as they came. For new or edited postings it ' + FULL + '.' };
  }

  // ---------- routing
  function sub(list, jobId, sid, what) { var x = list.find(function (y) { return y.id === sid && y.job_id === jobId; }); if (!x) throw new Missing('No such ' + what); return x; }
  function route(method, path, q, body) {
    var m;
    if (path === '/api/health') return { ok: true, auth: false };
    if (path === '/api/jobs') return method === 'POST' ? [201, addJob(body)] : listJobs(q.get('closed') === '1');
    if (path === '/api/pulse') return pulse();
    if (path === '/api/search') return { ids: search(q.get('q') || '') };
    if (path === '/api/resume') {
      if (method === 'PUT') {
        var t = body && body.text; if (typeof t !== 'string') throw new Bad('The resume must be text');
        if (t.length > 40000) throw new Bad('The resume is too long (limit 40000 characters)');
        db.resume = t.trim();
      }
      return { text: db.resume, updated_at: null };
    }
    if (path === '/api/posting') throw new Bad('Reading a posting link ' + FULL + ' (the page cannot reach other sites). Paste the posting text instead.');
    if (path === '/api/import' || path === '/api/restore') throw new Bad('Importing files ' + FULL + '.');
    if (path === '/api/export.csv' || path === '/api/backup' || /calendar\.ics$/.test(path)) throw new Bad('Downloading files ' + FULL + '. In the preview, use the Google Calendar buttons instead.');
    if ((m = /^\/api\/jobs\/(\d+)(\/.*)?$/.exec(path))) {
      var id = Number(m[1]), rest = m[2] || '', s;
      if (!rest) {
        if (method === 'DELETE') {
          row(id);
          db.jobs = db.jobs.filter(function (j) { return j.id !== id; });
          ['events', 'contacts', 'prep', 'interviews'].forEach(function (k) { db[k] = db[k].filter(function (x) { return x.job_id !== id; }); });
          return [204, null];
        }
        return method === 'PATCH' ? updateJob(id, body) : getJob(id);
      }
      if (rest === '/notes') {
        var text = body && body.text;
        if (typeof text !== 'string' || !text.trim()) throw new Bad('A note cannot be empty');
        if (text.length > 2000) throw new Bad('A note can be up to 2000 characters');
        var r = row(id); event(id, 'note', text.trim()); touch(r); return [201, getJob(id)];
      }
      if (rest === '/move') return move(id, body && body.stage);
      if (rest === '/close') {
        var reason = body && body.reason; if (!CLOSED_TEXT[reason]) throw new Bad('reason must be rejected, withdrawn or ghosted');
        var c = row(id); if (c.closed_reason !== reason) { c.closed_reason = reason; touch(c); event(id, 'closed', CLOSED_TEXT[reason]); }
        return getJob(id);
      }
      if (rest === '/reopen') { var o = row(id); return o.closed_reason ? move(id, o.stage) : getJob(id); }
      if (rest === '/match') return match(id);
      if (rest === '/rewrite') throw new Bad('Getting wording suggestions ' + FULL + ', with your own API key.');
      if (rest === '/contacts') { var cf = contactFields(body); row(id); db.contacts.push(Object.assign({ id: nextId(), job_id: id, role: '', email: '', last_contacted_on: '' }, cf)); touch(row(id)); return [201, getJob(id)]; }
      if ((s = /^\/contacts\/(\d+)$/.exec(rest))) {
        var ct = sub(db.contacts, id, Number(s[1]), 'contact');
        if (method === 'DELETE') db.contacts = db.contacts.filter(function (x) { return x !== ct; });
        else { Object.assign(ct, contactFields(body, true)); touch(row(id)); }
        return getJob(id);
      }
      if (rest === '/prep') { addPrep(id, body && body.text); return [201, getJob(id)]; }
      if (rest === '/prep/defaults') {
        row(id); var have = db.prep.filter(function (p) { return p.job_id === id; }).map(function (p) { return p.text; });
        DEFAULT_PREP.forEach(function (t) { if (have.indexOf(t) < 0) addPrep(id, t); }); return getJob(id);
      }
      if ((s = /^\/prep\/(\d+)$/.exec(rest))) {
        var it = sub(db.prep, id, Number(s[1]), 'checklist item');
        if (method === 'DELETE') db.prep = db.prep.filter(function (x) { return x !== it; });
        else { if (typeof (body && body.done) !== 'boolean') throw new Bad('done must be true or false'); it.done = body.done; }
        return getJob(id);
      }
      if (rest === '/interviews') {
        var f = interviewFields(body, false); row(id);
        db.interviews.push({ id: nextId(), job_id: id, starts_at: f.starts_at, minutes: f.minutes || 60, label: f.label || '', place: f.place || '' });
        event(id, 'interview', (f.label || 'Interview') + ' scheduled for ' + when(f.starts_at) + '.'); touch(row(id));
        return [201, getJob(id)];
      }
      if ((s = /^\/interviews\/(\d+)$/.exec(rest))) {
        var iv = sub(db.interviews, id, Number(s[1]), 'interview');
        if (method === 'DELETE') { db.interviews = db.interviews.filter(function (x) { return x !== iv; }); event(id, 'interview', (iv.label || 'Interview') + ' on ' + when(iv.starts_at) + ' removed.'); }
        else { var g = interviewFields(body, true); Object.assign(iv, g); if (g.starts_at) event(id, 'interview', 'Interview moved to ' + when(g.starts_at) + '.'); }
        touch(row(id)); return getJob(id);
      }
    }
    throw new Missing();
  }

  var realFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (input, init) {
    var url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.pathname.indexOf('/api/') !== 0) return realFetch ? realFetch(input, init) : Promise.reject(new TypeError('offline'));
    var method = ((init && init.method) || 'GET').toUpperCase(), body = null;
    try { body = init && init.body ? JSON.parse(init.body) : null; } catch (e) { body = null; }
    var status = 200, data;
    try {
      data = route(method, url.pathname, url.searchParams, body);
      if (Array.isArray(data) && data.length === 2 && typeof data[0] === 'number') { status = data[0]; data = data[1]; }
    } catch (e) {
      if (!(e instanceof Bad || e instanceof Missing)) { console.error(e); e = { status: 500, msg: 'Something went wrong in the preview' }; }
      status = e.status; data = { error: e.msg };
    }
    var init2 = { status: status, headers: { 'Content-Type': 'application/json' } };
    return new Promise(function (res) { setTimeout(function () { res(new Response(status === 204 ? null : JSON.stringify(data), init2)); }, 60); });
  };
})();
