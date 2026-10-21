// Optional Google Drive sync for the phone app (data kept on the device). Each job, event, contact, prep item
// and interview is one sync item ("jobs:12"), plus the resume; a change time is kept per item by comparing each
// save with the one before. See gsync.ts for how a sync runs.
import type { Data, Engine } from './local';
import { Sync, type Items, type SyncState } from './gsync';
import { clientId } from './gsync-config';
import { loadMeta, save, saveMeta } from './store';

const TABLES = ['jobs', 'events', 'contacts', 'prep', 'interviews'] as const;
type Table = typeof TABLES[number];
type Rec = { id: number; job_id?: number; created_at?: number };

function snapshot(d: Data): Record<string, string> {
  const o: Record<string, string> = {};
  for (const t of TABLES) for (const r of d[t] as Rec[]) o[t + ':' + r.id] = JSON.stringify(r);
  o.resume = JSON.stringify(d.resume || '');
  return o;
}

/** Keeps change times (mt) up to date as the engine saves; tombstones (deleted items) keep their time too. */
export class Tracker {
  snap: Record<string, string> = {};
  mt: Record<string, number> = {};
  async start(d: Data): Promise<void> {
    this.mt = await loadMeta();
    this.snap = snapshot(d);
  }
  /** Called with every save: marks what changed since the last one. */
  saved(d: Data): boolean {
    const now = Date.now(), next = snapshot(d);
    let changed = false;
    for (const k of Object.keys(next)) if (next[k] !== this.snap[k]) { this.mt[k] = now; changed = true; }
    for (const k of Object.keys(this.snap)) if (!(k in next)) { this.mt[k] = now; changed = true; }
    this.snap = next;
    if (changed) void saveMeta(this.mt);
    return changed;
  }
  /** Called with the dataset right after a backup restore: the restored records are historical values,
   * not fresh edits, so their change times stay as they were. The next sync then reconciles per record
   * (the newer change wins) instead of letting the restored copy silently out-rank the other device. */
  restored(d: Data): void {
    this.snap = snapshot(d);
  }
  items(d: Data): Items {
    const it: Items = {};
    for (const t of TABLES) for (const r of d[t] as Rec[]) it[t + ':' + r.id] = { t: this.mt[t + ':' + r.id] || 0, v: r };
    it.resume = { t: this.mt.resume || 0, v: d.resume || '' };
    for (const k of Object.keys(this.mt)) if (!(k in it) && this.mt[k]) it[k] = { t: this.mt[k], v: null };
    return it;
  }
}

/** Puts merged items into the data set; returns nothing, the caller saves. */
function applyItems(d: Data, items: Items, keys: string[]): void {
  for (const k of keys) {
    const x = items[k];
    if (k === 'resume') { d.resume = (x.v as string) ?? ''; continue; }
    const [t, sid] = k.split(':') as [Table, string];
    if (!TABLES.includes(t)) continue;
    const id = Number(sid), list = d[t] as Rec[], i = list.findIndex((r) => r.id === id);
    if (x.v === null) { if (i >= 0) list.splice(i, 1); }
    else if (i >= 0) list[i] = x.v as Rec;
    else list.push(x.v as Rec);
  }
  for (const t of TABLES) (d[t] as Rec[]).sort((a, b) => a.id - b.id);
}

/** Before the first Combine: local records whose ids mean something else in Drive get new ids. */
function renumber(d: Data, remote: Items, tr: Tracker): void {
  const fresh = () => Date.now() * 100 + Math.floor(Math.random() * 100);
  const jobMap = new Map<number, number>();
  for (const t of TABLES) {
    for (const r of d[t] as Rec[]) {
      const k = t + ':' + r.id, other = remote[k];
      if (!other) continue;
      const o = other.v as Rec | null; // null: deleted in Drive, so this id is taken by another record
      const same = !!o && (t === 'jobs' ? o.created_at === r.created_at : JSON.stringify(o) === JSON.stringify(r));
      if (same) continue;
      const nid = fresh() + Math.floor(Math.random() * 1000);
      if (t === 'jobs') jobMap.set(r.id, nid);
      delete tr.snap[k]; // the old key now means the record from Drive: no delete mark for it
      delete tr.mt[k];
      r.id = nid;
    }
  }
  if (jobMap.size) for (const t of TABLES) if (t !== 'jobs') for (const r of d[t] as Rec[]) if (r.job_id != null && jobMap.has(r.job_id)) r.job_id = jobMap.get(r.job_id);
}

export function startSync(engine: Engine, tr: Tracker): Sync | null {
  if (!clientId()) return null;
  const chip = document.createElement('button');
  chip.id = 'gsc'; chip.type = 'button'; chip.hidden = true;
  let sync: Sync | null = null, applying = false;
  const shown = (s: SyncState) => {
    chip.hidden = !s.on || !(s.tap || s.busy);
    chip.textContent = s.busy ? 'Syncing...' : 'Sync';
    chip.disabled = s.busy;
    window.dispatchEvent(new CustomEvent('jobledger:sync'));
  };
  sync = new Sync('jobledger', clientId(), {
    items: async () => tr.items(engine.d),
    async apply(items, keys) {
      applying = true;
      applyItems(engine.d, items, keys);
      tr.snap = snapshot(engine.d);
      for (const k of keys) tr.mt[k] = items[k].t;
      await saveMeta(tr.mt);
      await save(engine.d);
      applying = false;
      window.dispatchEvent(new CustomEvent('jobledger:data'));
    },
    hasData: () => engine.d.jobs.length > 0,
    async prepare(remote) {
      renumber(engine.d, remote, tr);
      tr.saved(engine.d);
      await save(engine.d);
    },
  }, shown);
  chip.addEventListener('click', () => sync && sync.now(true));
  document.body.appendChild(chip);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && sync) void sync.now(false); });
  (window as unknown as { __JL_SYNC__: Sync }).__JL_SYNC__ = sync;
  // the engine's save callback calls this after each change
  (window as unknown as { __JL_SYNC_CHANGED__: () => void }).__JL_SYNC_CHANGED__ = () => { if (!applying && sync) sync.changed(); };
  shown(sync.state);
  return sync;
}
