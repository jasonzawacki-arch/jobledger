// TypeScript twin of jobledger/matcher.py, used when JobLedger runs on the phone with no server.
// It must give the same answers as the Python version: tests/test_matcher_parity.py runs every labeled case
// through both and fails on any difference. Change both files together.
//
// Guardrail: this never writes new resume wording. Evidence is always a line copied verbatim from the resume.
import type { Match, MatchItem } from '../types';

const STOP = new Set(`a an and are as at be been by can do does for from has have in into is it its of on or our over per so such that the their them they this to
us we will with you your who what when where which while within without about across also any all both each more most other some than then these those
using use used work working works team teams role roles job company help helps ensure including include includes strong ability able must should plus
experience years year related knowledge understanding familiarity proficiency excellent good great new etc e.g i.e candidate responsibilities
requirements qualifications preferred required looking join seeking develop developing
skill skills quickly accurately proven record hands-on handson successfully effectively various variety`.split(/\s+/));

const SYNONYMS: string[][] = [
  ['javascript', 'js'], ['typescript', 'ts'], ['kubernetes', 'k8s'], ['postgresql', 'postgres', 'psql'], ['python', 'python3'],
  ['ci', 'ci/cd', 'cicd'], ['amazon web services', 'aws'], ['google cloud', 'gcp'], ['machine learning', 'ml'],
  ['natural language processing', 'nlp'], ['rest', 'restful'], ['node', 'nodejs', 'node.js'], ['react', 'reactjs', 'react.js'],
  ['sql', 'sqlite', 'mysql', 'postgresql'], ['api', 'apis'], ['test', 'testing', 'tests', 'tested', 'unit test'],
  ['dashboard', 'dashboards', 'reporting', 'reports', 'report'], ['analytics', 'analysis', 'analyze', 'analyzed', 'analyst'],
  ['demo', 'demos', 'demonstration', 'demonstrations'], ['customer', 'customers', 'client', 'clients'],
  ['docs', 'documentation', 'documented', 'document', 'documents'], ['kb', 'wiki'],
];
const IMPLIES: Record<string, string[]> = {
  sqlite: ['databas'], sql: ['databas'], postgresql: ['databas'], oracl: ['databas'], mongodb: ['databas'],
  salesforc: ['crm'], hubspot: ['crm'], zendesk: ['ticket', 'helpdesk'], jira: ['ticket'],
  terraform: ['iac'], cloudform: ['iac'], jenkin: ['ci'], github: ['git'],
  docker: ['contain'], kubernetes: ['contain'], 'amazon web services': ['cloud'], azur: ['cloud'], 'google cloud': ['cloud'],
};
const STEMFIX: Record<string, string> = { container: 'contain' };
const CANON = new Map<string, string>();
for (const g of SYNONYMS) for (const w of g) if (!CANON.has(w)) CANON.set(w, g[0]);

const WORD = /[a-z0-9][a-z0-9+#./-]*[a-z0-9+#]|[a-z0-9]/g;
const RAW = /[A-Za-z0-9][A-Za-z0-9+#./-]*/g;
const BULLET = /^\s*(?:[-*•●▪–]|\d+[.)])\s+/;
const CUES = /\b(experience|proficien|knowledge|familiar|ability|skilled?|required|must|strong|understanding|degree|certif|expertise|responsible|you will|you'll|we need|looking for|preferred|plus)\b/i;
const HEAD = /^(about|who we are|what you|responsibilit|requirement|qualification|benefits|perks|compensation|salary|our |why |equal|how to|nice to have|preferred|the role|overview)/i;
const MATCH_AT = 0.6, PARTIAL_AT = 0.3;
const WEAK = new Set(['run', 'manag', 'maintain', 'deliv', 'write', 'build', 'lead', 'creat', 'perform', 'provid', 'handl', 'own',
  'drive', 'work', 'make', 'get', 'keep', 'set', 'take']);
const weight = (t: string): number => (WEAK.has(t) ? 0.5 : 1.0);
const IRREGULAR: Record<string, string> = { wrote: 'write', written: 'write', built: 'build', ran: 'run', led: 'lead', made: 'make', taught: 'teach',
  kept: 'keep', sold: 'sell', began: 'begin', chose: 'choose', drove: 'drive', grew: 'grow', won: 'win' };
const PHRASES: Record<string, string> = { 'continuous integration': 'ci', 'continuous delivery': 'ci', 'continuous deployment': 'ci', 'machine learning': 'ml',
  'natural language processing': 'nlp', 'amazon web services': 'aws', 'google cloud': 'gcp', 'unit test': 'test', 'unit tests': 'test',
  'unit testing': 'test', 'power bi': 'powerbi', 'project management': 'projectmanagement',
  'knowledge base': 'kb', 'help center': 'kb', 'help desk': 'helpdesk', 'service desk': 'helpdesk',
  'infrastructure as code': 'iac', 'version control': 'git' };
const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const PHRASE_SRC = '\\b(' + Object.keys(PHRASES).sort((a, b) => b.length - a.length).map(escapeRe).join('|') + ')\\b';
const phraseRe = (): RegExp => new RegExp(PHRASE_SRC, 'gi');

/** Python's str.strip(".-/") */
const stripPunct = (w: string): string => w.replace(/^[.\-/]+|[.\-/]+$/g, '');
/** Python's str.splitlines() */
const LINE_BREAK = new RegExp('\\r\\n|[\\n\\r\\v\\f\\x1c-\\x1e\\x85\\u2028\\u2029]');
const splitlines = (s: string): string[] => { const out = s.split(LINE_BREAK); if (out.length && out[out.length - 1] === '') out.pop(); return out; };
/** Python's round(): halves go to the even neighbour. */
function pyRound(x: number, digits = 0): number {
  const f = 10 ** digits, y = x * f, r = Math.round(y);
  if (Math.abs(y % 1) === 0.5) return (r % 2 === 0 ? r : r - 1) / f;
  return r / f;
}
const findAll = (re: RegExp, s: string): string[] => s.match(re) ?? [];

function fold(text: string): string {
  const split = text.replace(/[A-Za-z]+(?:-[A-Za-z]+)+/g, (m) => (STOP.has(m.toLowerCase()) ? m : m.replace(/(?<=[A-Za-z])-(?=[A-Za-z])/g, ' ')));
  return split.replace(phraseRe(), (_m, g: string) => PHRASES[g.toLowerCase()]);
}

/** The classic Porter (1980) stemmer, same steps as _porter in matcher.py. */
function porter(w: string): string {
  if (w.length <= 2) return w;
  const cons = (word: string, i: number): boolean => {
    const ch = word[i];
    if ('aeiou'.includes(ch)) return false;
    if (ch === 'y') return i === 0 || !cons(word, i - 1);
    return true;
  };
  const m = (stem: string): number => {
    let n = 0, i = 0;
    const L = stem.length;
    while (i < L && cons(stem, i)) i++;
    while (i < L) {
      while (i < L && !cons(stem, i)) i++;
      if (i >= L) break;
      n++;
      while (i < L && cons(stem, i)) i++;
    }
    return n;
  };
  const hasV = (stem: string): boolean => { for (let i = 0; i < stem.length; i++) if (!cons(stem, i)) return true; return false; };
  const dbl = (word: string): boolean => word.length > 1 && word[word.length - 1] === word[word.length - 2] && cons(word, word.length - 1);
  const cvc = (word: string): boolean => word.length >= 3 && cons(word, word.length - 3) && !cons(word, word.length - 2)
    && cons(word, word.length - 1) && !'wxy'.includes(word[word.length - 1]);
  const rep = (word: string, pairs: [string, string][], cond: (st: string) => boolean): string => {
    for (const [suf, nw] of pairs) {
      if (word.endsWith(suf)) { const st = word.slice(0, -suf.length); return cond(st) ? st + nw : word; }
    }
    return word;
  };
  // 1a
  if (w.endsWith('sses')) w = w.slice(0, -2);
  else if (w.endsWith('ies')) w = w.slice(0, -2);
  else if (w.endsWith('ss')) { /* keep */ }
  else if (w.endsWith('s')) w = w.slice(0, -1);
  // 1b
  let flag = false;
  if (w.endsWith('eed')) { if (m(w.slice(0, -3)) > 0) w = w.slice(0, -1); }
  else if (w.endsWith('ed') && hasV(w.slice(0, -2))) { w = w.slice(0, -2); flag = true; }
  else if (w.endsWith('ing') && hasV(w.slice(0, -3))) { w = w.slice(0, -3); flag = true; }
  if (flag) {
    if (w.endsWith('at') || w.endsWith('bl') || w.endsWith('iz')) w += 'e';
    else if (dbl(w) && !'lsz'.includes(w[w.length - 1])) w = w.slice(0, -1);
    else if (m(w) === 1 && cvc(w)) w += 'e';
  }
  // 1c
  if (w.endsWith('y') && hasV(w.slice(0, -1))) w = w.slice(0, -1) + 'i';
  // 2
  w = rep(w, [['ational', 'ate'], ['tional', 'tion'], ['enci', 'ence'], ['anci', 'ance'], ['izer', 'ize'], ['abli', 'able'],
    ['alli', 'al'], ['entli', 'ent'], ['eli', 'e'], ['ousli', 'ous'], ['ization', 'ize'], ['ation', 'ate'],
    ['ator', 'ate'], ['alism', 'al'], ['iveness', 'ive'], ['fulness', 'ful'], ['ousness', 'ous'], ['aliti', 'al'],
    ['iviti', 'ive'], ['biliti', 'ble']], (st) => m(st) > 0);
  // 3
  w = rep(w, [['icate', 'ic'], ['ative', ''], ['alize', 'al'], ['iciti', 'ic'], ['ical', 'ic'], ['ful', ''], ['ness', '']], (st) => m(st) > 0);
  // 4
  for (const suf of ['al', 'ance', 'ence', 'er', 'ic', 'able', 'ible', 'ant', 'ement', 'ment', 'ent', 'ion', 'ou', 'ism', 'ate',
    'iti', 'ous', 'ive', 'ize']) {
    if (w.endsWith(suf)) {
      const st = w.slice(0, -suf.length);
      if (m(st) > 1 && (suf !== 'ion' || st.endsWith('s') || st.endsWith('t'))) w = st;
      break;
    }
  }
  // 5
  if (w.endsWith('e')) {
    const st = w.slice(0, -1);
    if (m(st) > 1 || (m(st) === 1 && !cvc(st))) w = st;
  }
  if (m(w) > 1 && dbl(w) && w.endsWith('l')) w = w.slice(0, -1);
  return w;
}

function stem(w: string): string {
  w = IRREGULAR[w] ?? w;
  if (!/^\p{L}+$/u.test(w)) return w; // leave tokens like "c++", "ci/cd" or "s3" alone
  w = porter(w);
  return STEMFIX[w] ?? w;
}
const canon = (w: string): string => CANON.get(w) || CANON.get(stem(w)) || stem(w);

/** Important words in a line, lowercased, stemmed, with synonyms folded together. */
export function terms(text: string): string[] {
  const out: string[] = [];
  for (let w of findAll(WORD, fold(text).toLowerCase())) {
    w = stripPunct(w);
    if (!w || STOP.has(w) || (w.length < 2 && w !== 'c' && w !== 'r') || /^\d+\+?$/.test(w)) continue;
    w = canon(w);
    if (!out.includes(w)) out.push(w);
  }
  return out;
}

function originals(text: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const raw of findAll(RAW, text)) {
    const key = canon(stripPunct(raw.toLowerCase()));
    if (!out.has(key)) out.set(key, stripPunct(raw));
  }
  return out;
}

function pairs(text: string): Set<string> {
  const ws = findAll(WORD, fold(text).toLowerCase());
  const out = new Set<string>();
  for (let i = 0; i + 1 < ws.length; i++) if (!STOP.has(ws[i]) && !STOP.has(ws[i + 1])) out.add(`${ws[i]} ${ws[i + 1]}`);
  return out;
}

export function resumeLines(text: string): string[] {
  const lines: string[] = [];
  for (const raw of splitlines(text)) {
    const line = raw.replace(BULLET, '').trim();
    if (line.length >= 8) lines.push(line);
  }
  return lines;
}

/** Python's str.strip() for the whitespace a posting can contain */
const pyStrip = (s: string): string => s.replace(/^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/g, '');

export function requirements(posting: string, limit = 12): string[] {
  const cands: string[] = [];
  const bullets = splitlines(posting).filter((l) => BULLET.test(l)).map((l) => pyStrip(l.replace(BULLET, '')));
  const pool = bullets.length ? bullets : posting.split(/(?<=[.!?])\s+|\n+/).map(pyStrip);
  for (const line of pool) {
    if (line.length < 8 || line.length > 260 || HEAD.test(line)) continue;
    if (bullets.length || CUES.test(line)) {
      if (terms(line).length >= (bullets.length ? 1 : 2)) cands.push(line.replace(/[ .;]+$/, ''));
    }
  }
  const seen = new Set<string>(), out: string[] = [];
  for (const c of cands) {
    const k = [...terms(c)].sort().join(' ');
    if (!seen.has(k)) { seen.add(k); out.push(c); }
  }
  return out.slice(0, limit);
}

function highlight(line: string, matched: Set<string>): [string, boolean][] {
  const parts: [string, boolean][] = [];
  let pos = 0;
  const spans: [number, number][] = [];
  for (const mm of line.matchAll(phraseRe())) if (matched.has(PHRASES[mm[1].toLowerCase()])) spans.push([mm.index!, mm.index! + mm[0].length]);
  for (const mm of line.matchAll(RAW)) {
    const start = mm.index!, end = start + mm[0].length;
    const inPhrase = spans.some(([a, b]) => a <= start && start < b);
    if (inPhrase || matched.has(canon(stripPunct(mm[0].toLowerCase())))) {
      if (start > pos) parts.push([line.slice(pos, start), false]);
      parts.push([mm[0], true]);
      pos = end;
    }
  }
  if (pos < line.length) parts.push([line.slice(pos), false]);
  return parts.length ? parts : [[line, false]];
}

export function match(posting: string, resume: string): Match {
  const lines = resumeLines(resume);
  const lineTerms = lines.map((l) => {
    const lt = new Set(terms(l));
    for (const t of [...lt]) for (const x of IMPLIES[t] ?? []) lt.add(x);
    return { line: l, lt, lp: pairs(l) };
  });
  const out: MatchItem[] = [];
  for (const r of requirements(posting)) {
    const rt = terms(r), rp = pairs(r), orig = originals(r);
    let best: string | null = null, bestScore = 0, bestHit = new Set<string>();
    for (const { line, lt, lp } of lineTerms) {
      const hit = new Set(rt.filter((t) => lt.has(t)));
      let shared = 0; rp.forEach((p) => { if (lp.has(p)) shared++; });
      const total = rt.reduce((s, t) => s + weight(t), 0);
      const score = (rt.length ? [...hit].reduce((s, t) => s + weight(t), 0) / total : 0) + Math.min(0.15 * shared, 0.3);
      if (score > bestScore) { best = line; bestScore = score; bestHit = hit; }
    }
    const score = Math.min(bestScore, 1.0);
    const status = score >= MATCH_AT ? 'match' : score >= PARTIAL_AT ? 'partial' : 'gap';
    const missing = (best !== null ? rt.filter((t) => !bestHit.has(t)) : rt).map((t) => orig.get(t) ?? t);
    const item: MatchItem = { requirement: r, status, score: pyRound(score, 2), missing };
    if (best !== null && status !== 'gap') item.evidence = { line: best, parts: highlight(best, bestHit) };
    out.push(item);
  }
  const n = out.length;
  const covered = out.filter((o) => o.status === 'match').length + 0.5 * out.filter((o) => o.status === 'partial').length;
  return {
    has_resume: lines.length > 0, has_posting: pyStrip(posting) !== '', requirements: out,
    score: n && lines.length ? pyRound((covered / n) * 100) : null,
    counts: { match: out.filter((o) => o.status === 'match').length, partial: out.filter((o) => o.status === 'partial').length, gap: out.filter((o) => o.status === 'gap').length },
  };
}
