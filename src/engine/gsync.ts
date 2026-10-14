// Google Drive sync for local-first apps. The same file is in Daily (src/gsync.ts) and JobLedger (src/engine/gsync.ts).
//
// Each app keeps its data on the device and describes it here as items: key -> { t: last change time, v: value }
// (v === null means deleted). A sync downloads the app's file from the user's own Google Drive (the hidden app
// folder, scope drive.appdata), merges item by item (the newer change wins), saves what changed on the device and
// uploads the result. No server of ours is involved; the browser talks to Google directly.
//
// A browser app without a server gets Google access for about an hour at a time. While it lasts, changes sync on
// their own; after that the app shows a Sync button, because asking Google again needs a tap.

export type Items = Record<string, { t: number; v: unknown }>;
export interface SyncFile { app: string; version: 1; at: number; items: Items }

/** Merges two sets of items: per key, the later change wins; on a tie the remote copy wins. */
export function mergeItems(local: Items, remote: Items): { merged: Items; toLocal: string[]; toRemote: boolean } {
  const merged: Items = {}, toLocal: string[] = [];
  let toRemote = false;
  for (const k of new Set([...Object.keys(local), ...Object.keys(remote)])) {
    const a = local[k], b = remote[k];
    const pick = !b ? a : !a ? b : a.t > b.t ? a : b;
    merged[k] = pick;
    if (!a || (pick === b && JSON.stringify(a.v) !== JSON.stringify(b.v))) toLocal.push(k);
    if (!b || (pick === a && JSON.stringify(a.v) !== JSON.stringify(b.v))) toRemote = true;
  }
  return { merged, toLocal, toRemote };
}

const SCOPE = 'https://www.googleapis.com/auth/drive.appdata openid email';
const API = 'https://www.googleapis.com';

declare const google: any;

/** Google sign-in through Google Identity Services (token model): no server, about an hour of access per sign-in. */
class GoogleAuth {
  private client: any = null;
  private ready: Promise<void> | null = null;
  constructor(private clientId: string) {}
  load(): Promise<void> {
    if (this.ready) return this.ready;
    this.ready = new Promise((resolve, reject) => {
      if ((window as any).google?.accounts?.oauth2) return resolve();
      const s = document.createElement('script');
      s.src = 'https://accounts.google.com/gsi/client';
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => {
        this.ready = null;
        reject(new Error('Could not reach Google. Check the connection and try again.'));
      };
      document.head.appendChild(s);
    });
    return this.ready;
  }
  /** Must run from a tap: Google opens its own window. */
  async token(hint: string, consent: boolean): Promise<{ token: string; exp: number }> {
    await this.load();
    return new Promise((resolve, reject) => {
      this.client = google.accounts.oauth2.initTokenClient({
        client_id: this.clientId,
        scope: SCOPE,
        callback: (r: any) => {
          if (r && r.access_token) resolve({ token: r.access_token, exp: Date.now() + (Number(r.expires_in) || 3600) * 1000 - 60000 });
          else reject(new Error(r && r.error === 'access_denied' ? 'Google sign-in was cancelled.' : 'Google sign-in failed.'));
        },
        error_callback: (e: any) => reject(new Error(e && e.type === 'popup_closed' ? 'Google sign-in was closed.' : 'Google sign-in did not open. Allow pop-ups for this page and try again.')),
      });
      this.client.requestAccessToken({ prompt: consent ? 'consent' : '', ...(hint ? { login_hint: hint } : {}) });
    });
  }
}

/** The app's one file in the hidden app folder of the user's Drive. */
class DriveFile {
  constructor(private name: string, private tok: () => string) {}
  private async call(url: string, init: RequestInit = {}): Promise<Response> {
    const r = await fetch(url, { ...init, headers: { Authorization: 'Bearer ' + this.tok(), ...(init.headers || {}) } });
    if (r.status === 401) throw Object.assign(new Error('Google access expired.'), { expired: true });
    if (!r.ok) throw new Error('Google Drive answered ' + r.status + '.');
    return r;
  }
  async find(): Promise<string | null> {
    const q = encodeURIComponent(`name='${this.name}'`);
    const r = await (await this.call(`${API}/drive/v3/files?spaces=appDataFolder&q=${q}&fields=files(id)&orderBy=modifiedTime desc`)).json();
    return (r.files && r.files[0] && r.files[0].id) || null;
  }
  async read(id: string): Promise<SyncFile | null> {
    const r = await this.call(`${API}/drive/v3/files/${id}?alt=media`);
    try {
      return (await r.json()) as SyncFile;
    } catch (e) {
      return null;
    }
  }
  async write(id: string | null, body: SyncFile): Promise<string> {
    const json = JSON.stringify(body);
    if (id) {
      await this.call(`${API}/upload/drive/v3/files/${id}?uploadType=media`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: json });
      return id;
    }
    const b = 'gsync' + Math.random().toString(36).slice(2);
    const meta = JSON.stringify({ name: this.name, parents: ['appDataFolder'], mimeType: 'application/json' });
    const multipart = `--${b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${meta}\r\n--${b}\r\nContent-Type: application/json\r\n\r\n${json}\r\n--${b}--`;
    const r = await (await this.call(`${API}/upload/drive/v3/files?uploadType=multipart&fields=id`, { method: 'POST', headers: { 'Content-Type': 'multipart/related; boundary=' + b }, body: multipart })).json();
    return r.id;
  }
  async email(): Promise<string> {
    try {
      return ((await (await this.call(`${API}/oauth2/v3/userinfo`)).json()).email as string) || '';
    } catch (e) {
      return '';
    }
  }
}

export interface Adapter {
  /** Every item on this device, with change times (0 when a change time is not known). */
  items(): Promise<Items>;
  /** Saves the merged values for these keys on this device (null: delete). */
  apply(items: Items, keys: string[]): Promise<void>;
  /** True when this device has data worth keeping (not only sample or empty data). */
  hasData(): boolean;
  /** Before the first combine: lets the app rename local records whose keys mean something else in the remote copy. */
  prepare?(remote: Items): Promise<void>;
}

export interface SyncState {
  /** Sync is set up on this device (a Google account is linked). */
  on: boolean;
  email: string;
  /** Last successful sync (ms), 0 if never. */
  last: number;
  busy: boolean;
  /** Google access has expired: a tap on Sync is needed. */
  tap: boolean;
  /** First sync on a device that already has data, with data in Drive too: the user picks what to do. */
  choose: boolean;
  msg: string;
}

/** Sync for one app. `onState` is called whenever something the page shows changes. */
export class Sync {
  state: SyncState;
  private auth: GoogleAuth;
  private file: DriveFile;
  private tok = '';
  private exp = 0;
  private fileId: string | null = null;
  private pendingRemote: SyncFile | null = null;
  private timer = 0;
  private LS: string;
  constructor(
    private app: string,
    clientId: string,
    private adapter: Adapter,
    private onState: (s: SyncState) => void,
  ) {
    this.LS = app + '.gsync';
    this.auth = new GoogleAuth(clientId);
    this.file = new DriveFile(app + '-sync.json', () => this.tok);
    let saved: any = {};
    try {
      saved = JSON.parse(localStorage.getItem(this.LS) || '{}');
    } catch (e) {}
    this.fileId = saved.fileId || null;
    this.state = { on: !!saved.on, email: saved.email || '', last: saved.last || 0, busy: false, tap: !!saved.on, choose: false, msg: '' };
  }
  private save() {
    try {
      localStorage.setItem(this.LS, JSON.stringify({ on: this.state.on, email: this.state.email, last: this.state.last, fileId: this.fileId }));
    } catch (e) {}
  }
  private set(p: Partial<SyncState>) {
    Object.assign(this.state, p);
    this.save();
    this.onState(this.state);
  }
  private live() {
    return !!this.tok && Date.now() < this.exp;
  }
  /** Turns sync on (from a tap): Google sign-in, then the first sync. */
  async link(): Promise<void> {
    if (this.state.busy) return;
    this.set({ busy: true, msg: 'Opening Google sign-in...' });
    try {
      const t = await this.auth.token('', true);
      this.tok = t.token;
      this.exp = t.exp;
      const email = await this.file.email();
      this.set({ on: true, email, msg: '' });
    } catch (e: any) {
      this.set({ busy: false, msg: e.message || String(e) });
      return;
    }
    this.state.busy = false;
    await this.run();
  }
  /** Sync now; `tap` is true when called from a tap, so Google may be asked again if access expired. */
  async now(tap = true): Promise<void> {
    if (!this.state.on || this.state.busy || this.state.choose) return;
    if (!this.live()) {
      if (!tap) return this.set({ tap: true });
      this.set({ busy: true, msg: '' });
      try {
        const t = await this.auth.token(this.state.email, false);
        this.tok = t.token;
        this.exp = t.exp;
      } catch (e: any) {
        return this.set({ busy: false, tap: true, msg: e.message || String(e) });
      }
      this.state.busy = false;
    }
    await this.run();
  }
  /** Call after every local change: syncs a few seconds later while Google access lasts. */
  changed(): void {
    if (!this.state.on) return;
    clearTimeout(this.timer);
    if (!this.live()) {
      if (!this.state.tap) this.set({ tap: true });
      return;
    }
    this.timer = window.setTimeout(() => this.now(false), 4000);
  }
  /** Turns sync off on this device. The copy in Drive stays. */
  off(): void {
    const t = this.tok;
    this.tok = '';
    this.exp = 0;
    this.fileId = null;
    this.pendingRemote = null;
    clearTimeout(this.timer);
    try {
      if (t && (window as any).google?.accounts?.oauth2) google.accounts.oauth2.revoke(t, () => {});
    } catch (e) {}
    this.set({ on: false, email: '', last: 0, tap: false, choose: false, busy: false, msg: 'Sync is off on this device. The copy in your Google Drive is still there.' });
  }
  /** Answers the first-sync question: combine both, take the Drive copy, or replace it with this device. */
  async choose(how: 'combine' | 'drive' | 'device'): Promise<void> {
    const remote = this.pendingRemote;
    if (!remote) return;
    this.pendingRemote = null;
    this.set({ choose: false, busy: true, msg: 'Syncing...' });
    try {
      if (how === 'device') {
        await this.upload(await this.adapter.items());
      } else if (how === 'drive') {
        const local = await this.adapter.items();
        const items: Items = { ...remote.items };
        for (const k of Object.keys(local)) if (!items[k]) items[k] = { t: Date.now(), v: null };
        await this.adapter.apply(items, Object.keys(items));
        await this.upload(items);
      } else {
        if (this.adapter.prepare) await this.adapter.prepare(remote.items);
        await this.merge(remote);
      }
      this.set({ busy: false, last: Date.now(), tap: false, msg: '' });
    } catch (e: any) {
      this.fail(e);
    }
  }
  private fail(e: any) {
    if (e && e.expired) {
      this.tok = '';
      return this.set({ busy: false, tap: true, msg: 'Google access expired. Tap Sync.' });
    }
    this.set({ busy: false, msg: 'Sync failed: ' + ((e && e.message) || e) });
  }
  private async upload(items: Items) {
    this.fileId = await this.file.write(this.fileId, { app: this.app, version: 1, at: Date.now(), items });
  }
  private async merge(remote: SyncFile | null) {
    const local = await this.adapter.items();
    const r = mergeItems(local, (remote && remote.items) || {});
    if (r.toLocal.length) await this.adapter.apply(r.merged, r.toLocal);
    if (r.toRemote || !remote) await this.upload(r.merged);
  }
  private async run() {
    this.set({ busy: true, msg: 'Syncing...' });
    try {
      if (!this.fileId) this.fileId = await this.file.find();
      let remote: SyncFile | null = null;
      if (this.fileId) {
        remote = await this.file.read(this.fileId);
        if (remote && remote.app !== this.app) throw new Error('The file in Drive belongs to another app.');
      }
      if (remote && !this.state.last && this.adapter.hasData() && Object.keys(remote.items || {}).length) {
        this.pendingRemote = remote;
        return this.set({ busy: false, choose: true, msg: '' });
      }
      await this.merge(remote);
      this.set({ busy: false, last: Date.now(), tap: false, msg: '' });
    } catch (e: any) {
      this.fail(e);
    }
  }
}
