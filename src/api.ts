import type { Job, Match, Pulse, Rewrite } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
const KEY = 'jobledger.token';
export const getToken = (): string => { try { return localStorage.getItem(KEY) || ''; } catch { return ''; } };
export const setToken = (t: string): void => { try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch { /* storage unavailable */ } };

const AKEY = 'jobledger.anthropic-key';
export const getAiKey = (): string => { try { return localStorage.getItem(AKEY) || ''; } catch { return ''; } };
export const setAiKey = (k: string): void => { try { k ? localStorage.setItem(AKEY, k) : localStorage.removeItem(AKEY); } catch { /* storage unavailable */ } };

async function raw(method: string, path: string, body?: unknown, extra: Record<string, string> = {}): Promise<Response> {
  const headers: Record<string, string> = { ...extra };
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Is it running?');
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try { msg = (await res.json()).error || msg; } catch { /* not json */ }
    throw new ApiError(res.status, msg);
  }
  return res;
}
async function json<T>(method: string, path: string, body?: unknown, extra: Record<string, string> = {}): Promise<T> {
  const res = await raw(method, path, body, extra);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  health: () => json<{ ok: boolean; auth: boolean }>('GET', '/api/health'),
  jobs: () => json<Job[]>('GET', '/api/jobs?closed=1'),
  job: (id: number) => json<Job>('GET', `/api/jobs/${id}`),
  add: (d: Record<string, unknown>) => json<Job>('POST', '/api/jobs', d),
  update: (id: number, d: Record<string, unknown>) => json<Job>('PATCH', `/api/jobs/${id}`, d),
  remove: (id: number) => json<void>('DELETE', `/api/jobs/${id}`),
  note: (id: number, text: string) => json<Job>('POST', `/api/jobs/${id}/notes`, { text }),
  move: (id: number, stage: number) => json<Job>('POST', `/api/jobs/${id}/move`, { stage }),
  close: (id: number, reason: string) => json<Job>('POST', `/api/jobs/${id}/close`, { reason }),
  reopen: (id: number) => json<Job>('POST', `/api/jobs/${id}/reopen`),
  match: (id: number) => json<Match>('GET', `/api/jobs/${id}/match`),
  resume: () => json<{ text: string }>('GET', '/api/resume'),
  saveResume: (text: string) => json<{ text: string }>('PUT', '/api/resume', { text }),
  addContact: (id: number, d: Record<string, unknown>) => json<Job>('POST', `/api/jobs/${id}/contacts`, d),
  updateContact: (id: number, cid: number, d: Record<string, unknown>) => json<Job>('PATCH', `/api/jobs/${id}/contacts/${cid}`, d),
  delContact: (id: number, cid: number) => json<Job>('DELETE', `/api/jobs/${id}/contacts/${cid}`),
  addPrep: (id: number, text: string) => json<Job>('POST', `/api/jobs/${id}/prep`, { text }),
  defaultPrep: (id: number) => json<Job>('POST', `/api/jobs/${id}/prep/defaults`),
  setPrep: (id: number, pid: number, done: boolean) => json<Job>('PATCH', `/api/jobs/${id}/prep/${pid}`, { done }),
  delPrep: (id: number, pid: number) => json<Job>('DELETE', `/api/jobs/${id}/prep/${pid}`),
  importCsv: (csv: string) => json<{ imported: number }>('POST', '/api/import', { csv }),
  rewrite: (id: number, requirement: string) => json<Rewrite>('POST', `/api/jobs/${id}/rewrite`, { requirement }, { 'X-Anthropic-Key': getAiKey() }),
  pulse: () => json<Pulse>('GET', '/api/pulse'),
  restore: (data: unknown) => json<{ jobs: number }>('POST', '/api/restore', data),
  async download(path: string, filename: string): Promise<void> {
    const blob = await (await raw('GET', path)).blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  },
};
