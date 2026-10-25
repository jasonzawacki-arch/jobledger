// Saves the phone app's data in the browser's own database (IndexedDB), with localStorage as a fallback.
// The whole data set is one record in backup format, written after every change.
import { Data, emptyData } from './local';

const DB = 'jobledger', STORE = 'state', KEY = 'data', LS = 'jobledger.data';

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function load(): Promise<Data> {
  try {
    const db = await open();
    const data = await new Promise<Data | undefined>((resolve, reject) => {
      const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve(req.result as Data | undefined);
      req.onerror = () => reject(req.error);
    });
    if (data) return data;
  } catch { /* fall through to localStorage */ }
  try { const raw = localStorage.getItem(LS); if (raw) return JSON.parse(raw) as Data; } catch { /* nothing saved */ }
  return emptyData();
}

let pending: Promise<void> = Promise.resolve();
/** Saves in order; a failed save is reported once on the page. */
export function save(d: Data): Promise<void> {
  const copy = JSON.parse(JSON.stringify(d)) as Data;
  pending = pending.then(async () => {
    try {
      const db = await open();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put(copy, KEY);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch {
      try { localStorage.setItem(LS, JSON.stringify(copy)); } catch { window.dispatchEvent(new CustomEvent('jobledger:save-failed')); }
    }
  });
  return pending;
}

/** Change times per record, for Google Drive sync (a second record next to the data). */
export async function loadMeta(): Promise<Record<string, number>> {
  try {
    const db = await open();
    return await new Promise((resolve, reject) => {
      const req = db.transaction(STORE, 'readonly').objectStore(STORE).get('mt');
      req.onsuccess = () => resolve((req.result as Record<string, number>) || {});
      req.onerror = () => reject(req.error);
    });
  } catch {
    try { return JSON.parse(localStorage.getItem(LS + '.mt') || '{}') as Record<string, number>; } catch { return {}; }
  }
}
let pendingMeta: Promise<void> = Promise.resolve();
/** Writes change times in order, like save(): a delayed earlier write (e.g. from just before a
 * backup restore) must not land after a later one and resurrect the old times. The copy is taken
 * at call time, and a failed write falls back to localStorage inside the serialized unit, so it
 * cannot poison later writes. */
export function saveMeta(mt: Record<string, number>): Promise<void> {
  const copy = { ...mt };
  pendingMeta = pendingMeta.then(async () => {
    try {
      const db = await open();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).put(copy, 'mt');
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch {
      try { localStorage.setItem(LS + '.mt', JSON.stringify(copy)); } catch { /* best effort */ }
    }
  });
  return pendingMeta;
}

/** Asks the browser not to clear the data when the phone runs low on space. */
export function keepData(): void {
  try { void navigator.storage?.persist?.(); } catch { /* not supported */ }
}
