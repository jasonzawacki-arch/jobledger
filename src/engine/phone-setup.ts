// Set-up for the installable phone app. Data is kept on the phone by default; if you connect the app to your
// own JobLedger server (on a computer or in the cloud), its /api requests go there instead.
import { Engine, install } from './local';
import { keepData, load, save } from './store';

const SERVER_KEY = 'jobledger.server';
const w = window as unknown as { __JL_MODE__: string; __JL_REMOTE__?: string };
w.__JL_MODE__ = 'phone';
const realFetch = window.fetch.bind(window);
let remote = '';
try { remote = localStorage.getItem(SERVER_KEY) || ''; } catch { /* storage unavailable: stay on the phone */ }

if (remote) {
  w.__JL_REMOTE__ = remote;
  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href);
    if (url.origin === location.origin && url.pathname.includes('/api/')) {
      return realFetch(remote + url.pathname.slice(url.pathname.indexOf('/api/')) + url.search, init);
    }
    return realFetch(input, init);
  };
} else {
  install(load().then((d) => new Engine(d, { save: (x) => { void save(x); keepData(); }, fetch: realFetch })));
}

/** Checks that a JobLedger server answers at this address, then uses it from now on (after a reload). */
export async function connectServer(address: string): Promise<void> {
  let url: URL;
  try { url = new URL(address.trim()); } catch { throw new Error('Enter the full address, starting with https://'); }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) throw new Error('The address must start with https://, which Tailscale or your cloud host provides.');
  const base = url.origin + url.pathname.replace(/\/+$/, '');
  let ok = false;
  try {
    const r = await realFetch(base + '/api/health', { cache: 'no-store' });
    const j = (await r.json()) as { ok?: boolean };
    ok = r.ok && j.ok === true;
  } catch { ok = false; }
  if (!ok) throw new Error(`No JobLedger server answered at ${base}. Check that it is running with JL_TOKEN set and with JL_ORIGINS=${location.origin}, and that this phone can reach it.`);
  localStorage.setItem(SERVER_KEY, base);
}
/** Goes back to keeping data on the phone. Nothing on the server or the phone is deleted. */
export function useThisPhone(): void {
  try { localStorage.removeItem(SERVER_KEY); } catch { /* nothing stored */ }
}

(window as unknown as { __JL_CONNECT__: unknown }).__JL_CONNECT__ = { connectServer, useThisPhone };
