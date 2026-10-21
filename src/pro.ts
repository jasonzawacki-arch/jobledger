// Pro gate for the Play build (Req. 5; Req. 4's billing state machine will write the entitlement flag).
// Free tier: full pipeline, matching, backup/restore, calendar export — capped at 20 tracked
// applications, closed jobs counted. Jason's decisions, 2026-10-20.
export const FREE_JOB_CAP = 20;
const PRO_KEY = 'jobledger.pro';
/** Entitlement: true once Req. 4 (Play Billing) validates a receipt or restores a purchase. Until then, locked. */
export function isPro(): boolean {
  try { return localStorage.getItem(PRO_KEY) === '1'; } catch { return false; }
}
/** Pure gate check used by Engine.addJob. Returns the user-facing message when an add would exceed
 * the free tier in the Play build, otherwise null. Phone/PWA, server, and demo builds are never gated. */
export function gateMessage(mode: string, pro: boolean, jobCount: number): string | null {
  if (mode !== 'play' || pro || jobCount < FREE_JOB_CAP) return null;
  return `The free tier tracks ${FREE_JOB_CAP} applications (closed ones count too). Go Pro — $12.99 once, unlimited — to keep adding jobs.`;
}
