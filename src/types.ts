export interface Job {
  id: number; company: string; title: string; location: string; pay: string; url: string; posted_on: string; description: string;
  stage: number; stage_name: string; max_stage: number; closed_reason: string | null; follow_up_on: string | null;
  days_in_stage: number; idle_days: number; follow_up_due: boolean; created_at: number; updated_at: number;
  events?: JobEvent[]; contacts?: Contact[]; prep?: PrepItem[];
}
export interface Contact { id: number; name: string; role: string; email: string; last_contacted_on: string }
export interface PrepItem { id: number; text: string; done: boolean }
export interface Rewrite { source: string; ok: boolean; reason: string; suggestion?: string; parts?: [string, boolean][]; unchanged?: boolean }
export interface JobEvent { id: number; at: number; kind: string; text: string }
export interface DueItem { id: number; company: string; title: string; stage_name: string; days_in_stage: number; idle_days: number; follow_up_on: string | null }
export interface Pulse {
  active: number; total: number; reached: number[]; closed: Record<string, number>; reached_screen_pct: number | null;
  avg_days_in_stage: number | null; in_flight: number; follow_ups_due: DueItem[]; stale_days: number;
}
export const STAGES = ['Saved', 'Applied', 'Screen', 'Interview', 'Offer'];
export const CLOSED_LABEL: Record<string, string> = { rejected: 'Rejected', withdrawn: 'Withdrawn', ghosted: 'No response' };
export interface MatchItem {
  requirement: string; status: 'match' | 'partial' | 'gap'; score: number; missing: string[];
  evidence?: { line: string; parts: [string, boolean][] };
}
export interface Match {
  has_resume: boolean; has_posting: boolean; requirements: MatchItem[]; score: number | null;
  counts: { match: number; partial: number; gap: number };
}
