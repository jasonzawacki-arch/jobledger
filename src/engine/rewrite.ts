// TypeScript twin of the checks in jobledger/rewrite.py, for wording suggestions made from the phone.
// The model only ever sees one resume line and one requirement; its answer is checked by guard() before
// it is shown, and the UI always shows the original next to it with changes highlighted.

export const SYSTEM =
  'You reword one line from a person\'s resume so it reads more clearly for one job requirement. ' +
  'Rules: use only facts already in the line. Do not add numbers, tools, technologies, employers, titles, results or years. ' +
  "Do not make the work sound bigger than it was. Keep any note such as '(side project)'. Keep it to one sentence, at most 35 words. " +
  'If the line cannot be improved, return it unchanged. Reply with the sentence only.';
export const DEFAULT_MODEL = 'claude-haiku-5-5';

const WORD = /[A-Za-z0-9][A-Za-z0-9+#./'-]*/g;
const strip = (w: string): string => w.toLowerCase().replace(/^[.'\-/]+|[.'\-/]+$/g, '');
const tokens = (s: string): Set<string> => new Set((s.match(WORD) ?? []).map(strip));

/** [ok, reason]: rejects suggestions that add numbers, names or tools, or that are not one plain sentence. */
export function guard(source: string, rewrite: string): [boolean, string] {
  rewrite = rewrite.trim();
  if (!rewrite) return [false, 'The suggestion was empty.'];
  if (rewrite.includes('\n') || rewrite.length > Math.max(240, Math.floor(source.length * 1.6))) return [false, 'The suggestion was too long or not a single sentence.'];
  const src = tokens(source);
  const words = rewrite.match(WORD) ?? [];
  for (const w of words) {
    if (/\d/.test(w) && !src.has(strip(w))) return [false, `The suggestion added a number or version (${w}) that is not in your line.`];
  }
  for (const w of words.slice(1)) {
    const upper = w[0] !== w[0].toLowerCase() || (w === w.toUpperCase() && w !== w.toLowerCase() && w.length > 1);
    if (upper && !src.has(strip(w))) return [false, `The suggestion added a name or tool (${w}) that is not in your line.`];
  }
  return [true, ''];
}

/** [text, changed] pieces of the rewrite, marking words that are not in the source (longest common subsequence). */
export function diffParts(source: string, rewrite: string): [string, boolean][] {
  const a = source.split(/\s+/).filter(Boolean), b = rewrite.split(/\s+/).filter(Boolean);
  const key = (w: string): string => w.toLowerCase().replace(/^[.,;]+|[.,;]+$/g, '');
  const n = a.length, m = b.length;
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = key(a[i]) === key(b[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const same: boolean[] = new Array(m).fill(false);
  for (let i = 0, j = 0; i < n && j < m;) {
    if (key(a[i]) === key(b[j])) { same[j] = true; i++; j++; } else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
  }
  const out: [string, boolean][] = [];
  b.forEach((w, j) => {
    const changed = !same[j];
    if (out.length && out[out.length - 1][1] === changed) out[out.length - 1][0] += ' ' + w;
    else out.push([w, changed]);
  });
  return out.map(([t, c], i) => [t + (i < out.length - 1 ? ' ' : ''), c]);
}
