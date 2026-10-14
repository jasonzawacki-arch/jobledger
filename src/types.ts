export interface Job {
  id: number; company: string; title: string; location: string; pay: string; url: string; posted_on: string; description: string;
  stage: number; stage_name: string; max_stage: number; closed_reason: string | null; follow_up_on: string | null;
  days_in_stage: number; idle_days: number; follow_up_due: boolean; created_at: number; updated_at: number;
  reminder_on: string | null; reminder_is_nudge: boolean; calendar_link?: string | null; source: string; next_interview: string | null;
  interviews?: Interview[];
  events?: JobEvent[]; contacts?: Contact[]; prep?: PrepItem[];
}
export interface Interview { id: number; starts_at: string; minutes: number; label: string; place: string; past: boolean; calendar_link: string }
export interface Upcoming { id: number; job_id: number; starts_at: string; minutes: number; label: string; place: string; company: string; title: string; calendar_link: string }
export interface Contact { id: number; name: string; role: string; email: string; last_contacted_on: string }
export interface PrepItem { id: number; text: string; done: boolean }
export interface Rewrite { source: string; ok: boolean; reason: string; suggestion?: string; parts?: [string, boolean][]; unchanged?: boolean }
export interface JobEvent { id: number; at: number; kind: string; text: string }
export interface DueItem { id: number; company: string; title: string; stage_name: string; days_in_stage: number; idle_days: number; follow_up_on: string | null }
export interface Pulse {
  active: number; total: number; reached: number[]; closed: Record<string, number>; reached_screen_pct: number | null;
  avg_days_in_stage: number | null; in_flight: number; follow_ups_due: DueItem[]; stale_days: number;
  applied_total: number; heard_back: number; response_rate_pct: number | null; median_days_to_reply: number | null;
  weekly_applied: { week_of: string; applied: number }[]; by_source: { source: string; applied: number; heard_back: number }[];
  upcoming_interviews: Upcoming[];
}
export const SOURCES = ['LinkedIn', 'Indeed', 'Company site', 'Referral', 'Recruiter', 'Job board', 'Other'];
export const STAGES = ['Saved', 'Applied', 'Screen', 'Interview', 'Offer'];
export const CLOSED_LABEL: Record<string, string> = { rejected: 'Rejected', withdrawn: 'Withdrawn', ghosted: 'No response' };
export interface MatchItem {
  requirement: string; status: 'match' | 'partial' | 'gap'; score: number; missing: string[];
  evidence?: { line: string; parts: [string, boolean][] };
}
export interface Match {
  has_resume: boolean; has_posting: boolean; requirements: MatchItem[]; score: number | null;
  counts: { match: number; partial: number; gap: number };
  note?: string;
}
