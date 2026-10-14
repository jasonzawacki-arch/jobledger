"""All tracker rules live here, independent of HTTP. The clock is injected so tests can move time."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import time
from typing import Callable, Optional

from . import reminders

from . import matcher
from .db import Database
from .stages import CLOSED_REASONS, STAGES

DAY = 86400
RESUME_LIMIT = 40000
IMPORT_MAX_ROWS = 2000
IMPORT_MAX_BYTES = 2_000_000
DEFAULT_PREP = [
    "Re-read the posting and note three requirements to speak to",
    "Prepare a two-minute summary of your background",
    "Pick two or three stories that fit the main requirements",
    "Look up the company and the people you will meet",
    "Write down questions to ask them",
    "Plan how you will say you are returning to work after a break",
]
STALE_DAYS = 10  # a job in progress with no activity for this long needs a nudge
FIELD_LIMITS = {"company": 120, "title": 160, "location": 120, "pay": 80, "url": 500, "posted_on": 10, "description": 60000, "source": 80}
EDITABLE = ("company", "title", "location", "pay", "url", "posted_on", "description", "source")
SOURCES = ("LinkedIn", "Indeed", "Company site", "Referral", "Recruiter", "Job board", "Other")  # suggestions; any text is allowed
STAGE_MOVE = re.compile(r"^Moved from (\w+) to (\w+)\.$")


class ValidationError(ValueError):
    pass


class NotFound(KeyError):
    pass


def _clean(field: str, value) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    value = value.strip()
    if len(value) > FIELD_LIMITS[field]:
        raise ValidationError(f"{field} is too long (limit {FIELD_LIMITS[field]} characters)")
    if field in ("company", "title") and not value:
        raise ValidationError(f"{field} is required")
    if field == "url" and value and not value.lower().startswith(("http://", "https://")):
        raise ValidationError("url must start with http:// or https://")
    if field == "posted_on" and value:
        _date(value, "posted_on")
    return value


def _date(value: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a date like 2026-10-24") from None


class Tracker:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time, stale_days: int = STALE_DAYS,
                 tz: Optional[dt.tzinfo] = None):
        """tz decides what "today" is for follow-up dates. None means this computer's own time zone."""
        self.db, self.clock, self.stale_days, self.tz = db, clock, stale_days, tz

    # ---- helpers
    def _today(self) -> dt.date:
        return dt.datetime.fromtimestamp(self.clock(), self.tz).date()

    def _event(self, job_id: int, kind: str, text: str) -> None:
        self.db.conn.execute("INSERT INTO events (job_id, at, kind, text) VALUES (?, ?, ?, ?)", (job_id, self.clock(), kind, text))

    def _row(self, job_id: int):
        r = self.db.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None:
            raise NotFound("No such job")
        return r

    def _view(self, r, last_at: Optional[float] = None) -> dict:
        now = self.clock()
        if last_at is None:
            row = self.db.conn.execute("SELECT MAX(at) FROM events WHERE job_id = ?", (r["id"],)).fetchone()
            last_at = row[0] or r["created_at"]
        closed = r["closed_reason"] is not None
        idle_days = int((now - last_at) // DAY)
        due, remind = False, None
        nxt = self._next_interview(r["id"]) if not closed else None
        if nxt and not r["follow_up_on"]:
            pass  # an interview is coming up, so there is nothing to chase
        elif not closed and r["stage"] != 4:
            if r["follow_up_on"]:
                remind = r["follow_up_on"]
                due = dt.date.fromisoformat(r["follow_up_on"]) <= self._today()
            elif r["stage"] in (1, 2, 3):
                due = idle_days >= self.stale_days
                # the day the idle nudge will fire if nothing happens before then
                remind = dt.datetime.fromtimestamp(last_at + self.stale_days * DAY, self.tz).date().isoformat()
        return {
            "id": r["id"], "company": r["company"], "title": r["title"], "location": r["location"], "pay": r["pay"],
            "url": r["url"], "posted_on": r["posted_on"], "description": r["description"], "source": r["source"],
            "stage": r["stage"], "stage_name": STAGES[r["stage"]], "max_stage": r["max_stage"],
            "closed_reason": r["closed_reason"], "follow_up_on": r["follow_up_on"],
            "reminder_on": remind, "reminder_is_nudge": bool(remind) and not r["follow_up_on"], "next_interview": nxt,
            "days_in_stage": int((now - r["stage_changed_at"]) // DAY), "idle_days": idle_days, "follow_up_due": due,
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }

    def _touch(self, job_id: int) -> None:
        self.db.conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (self.clock(), job_id))

    # ---- jobs
    def add_job(self, data: dict) -> dict:
        vals = {f: _clean(f, data.get(f)) for f in EDITABLE}
        stage = data.get("stage", 0)
        if not isinstance(stage, int) or isinstance(stage, bool) or stage not in (0, 1):
            raise ValidationError("A new job starts as Saved or Applied")
        follow = self._follow(data.get("follow_up_on"))
        now = self.clock()
        with self.db.lock:
            c = self.db.conn
            c.execute("BEGIN")
            try:
                cur = c.execute(
                    f"INSERT INTO jobs ({', '.join(EDITABLE)}, stage, max_stage, follow_up_on, stage_changed_at, created_at, updated_at)"
                    f" VALUES ({', '.join('?' * (len(EDITABLE) + 6))})",
                    (*[vals[f] for f in EDITABLE], stage, stage, follow, now, now, now),
                )
                jid = cur.lastrowid
                self._event(jid, "created", "Saved the posting." if stage == 0 else "Applied.")
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return self.get_job(jid)

    @staticmethod
    def _follow(v) -> Optional[str]:
        if v in (None, ""):
            return None
        if not isinstance(v, str):
            raise ValidationError("follow_up_on must be a date like 2026-10-24")
        _date(v, "follow_up_on")
        return v

    def get_job(self, job_id: int) -> dict:
        with self.db.lock:
            out = self._view(self._row(job_id))
            ev = self.db.conn.execute("SELECT id, at, kind, text FROM events WHERE job_id = ? ORDER BY at DESC, id DESC", (job_id,)).fetchall()
            out["events"] = [dict(e) for e in ev]
            out["contacts"] = [dict(r) for r in self.db.conn.execute("SELECT id, name, role, email, last_contacted_on FROM contacts WHERE job_id = ? ORDER BY id", (job_id,))]
            out["prep"] = [dict(r) | {"done": bool(r["done"])} for r in self.db.conn.execute("SELECT id, text, done FROM prep_items WHERE job_id = ? ORDER BY position, id", (job_id,))]
            out["calendar_link"] = reminders.google_link(out)
            out["interviews"] = [self._interview_view(dict(r), out) for r in self.db.conn.execute(
                "SELECT id, starts_at, minutes, label, place FROM interviews WHERE job_id = ? ORDER BY starts_at, id", (job_id,))]
            return out

    def calendar(self, job_id: Optional[int] = None) -> str:
        """An .ics file with the follow-up reminder for one job, or for every open job that has one."""
        with self.db.lock:
            if job_id is not None:
                job = self.get_job(job_id)
                interviews = [i for i in self.upcoming_interviews(days=3650) if i["job_id"] == job_id]
                jobs = [job] if job["reminder_on"] else []
                if not jobs and not interviews:
                    raise ValidationError("This job has no follow-up or upcoming interview to remind you about")
            else:
                jobs = [self.get_job(j["id"]) for j in self.list_jobs() if j["reminder_on"]]
                interviews = self.upcoming_interviews(days=3650)
        return reminders.ics(jobs, self.clock(), interviews)

    # ---- interviews
    def _next_interview(self, job_id: int) -> Optional[str]:
        """Start of the job's next interview that has not ended yet, as local "YYYY-MM-DDTHH:MM"."""
        now = self._now_local()
        for r in self.db.conn.execute("SELECT starts_at, minutes FROM interviews WHERE job_id = ? AND starts_at >= ? ORDER BY starts_at",
                                      (job_id, (now - dt.timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M"))):
            if dt.datetime.fromisoformat(r["starts_at"]) + dt.timedelta(minutes=r["minutes"]) >= now:
                return r["starts_at"]
        return None

    def _now_local(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.clock(), self.tz).replace(tzinfo=None)

    @staticmethod
    def _interview_fields(d, partial: bool = False) -> dict:
        if not isinstance(d, dict):
            raise ValidationError("Expected an object")
        out = {}
        if "starts_at" in d or not partial:
            v = d.get("starts_at")
            if not isinstance(v, str):
                raise ValidationError("Give the interview a date and time")
            try:
                when = dt.datetime.fromisoformat(v.strip())
            except ValueError:
                raise ValidationError("starts_at must be a date and time like 2026-10-24T14:30") from None
            if when.tzinfo is not None or not 2000 <= when.year <= 2100:
                raise ValidationError("starts_at must be a local date and time like 2026-10-24T14:30")
            out["starts_at"] = when.strftime("%Y-%m-%dT%H:%M")
        if "minutes" in d:
            m = d["minutes"]
            if isinstance(m, bool) or not isinstance(m, int) or not 5 <= m <= 600:
                raise ValidationError("minutes must be a whole number from 5 to 600")
            out["minutes"] = m
        for k, limit in (("label", 120), ("place", 300)):
            if k in d:
                v = d[k] if d[k] is not None else ""
                if not isinstance(v, str):
                    raise ValidationError(f"{k} must be text")
                if len(v.strip()) > limit:
                    raise ValidationError(f"{k} is too long (limit {limit} characters)")
                out[k] = v.strip()
        return out

    def _interview_view(self, r: dict, job: dict) -> dict:
        r["past"] = dt.datetime.fromisoformat(r["starts_at"]) + dt.timedelta(minutes=r["minutes"]) < self._now_local()
        r["calendar_link"] = reminders.interview_link(r, job)
        return r

    @staticmethod
    def _when(starts_at: str) -> str:
        t = dt.datetime.fromisoformat(starts_at)
        return f"{t:%a %b} {t.day}, {t:%I:%M %p}".replace(" 0", " ")

    def add_interview(self, job_id: int, d) -> dict:
        f = self._interview_fields(d)
        with self.db.lock:
            self._row(job_id)
            self.db.conn.execute("INSERT INTO interviews (job_id, starts_at, minutes, label, place) VALUES (?, ?, ?, ?, ?)",
                                 (job_id, f["starts_at"], f.get("minutes", 60), f.get("label", ""), f.get("place", "")))
            what = f.get("label") or "Interview"
            self._event(job_id, "interview", f"{what} scheduled for {self._when(f['starts_at'])}.")
            self._touch(job_id)
            return self.get_job(job_id)

    def update_interview(self, job_id: int, interview_id: int, d) -> dict:
        f = self._interview_fields(d, partial=True)
        with self.db.lock:
            if not self.db.conn.execute("SELECT 1 FROM interviews WHERE id = ? AND job_id = ?", (interview_id, job_id)).fetchone():
                raise NotFound("No such interview")
            for k, v in f.items():
                self.db.conn.execute(f"UPDATE interviews SET {k} = ? WHERE id = ?", (v, interview_id))
            if "starts_at" in f:
                self._event(job_id, "interview", f"Interview moved to {self._when(f['starts_at'])}.")
            self._touch(job_id)
            return self.get_job(job_id)

    def delete_interview(self, job_id: int, interview_id: int) -> dict:
        with self.db.lock:
            r = self.db.conn.execute("SELECT starts_at, label FROM interviews WHERE id = ? AND job_id = ?", (interview_id, job_id)).fetchone()
            if not r:
                raise NotFound("No such interview")
            self.db.conn.execute("DELETE FROM interviews WHERE id = ?", (interview_id,))
            self._event(job_id, "interview", f"{r['label'] or 'Interview'} on {self._when(r['starts_at'])} removed.")
            self._touch(job_id)
            return self.get_job(job_id)

    def upcoming_interviews(self, days: int = 14) -> list[dict]:
        """Interviews for open jobs that have not ended yet and start within the next `days` days, soonest first."""
        now = self._now_local()
        until = (now + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT i.id, i.job_id, i.starts_at, i.minutes, i.label, i.place, j.company, j.title, j.url, j.stage"
                " FROM interviews i JOIN jobs j ON j.id = i.job_id WHERE j.closed_reason IS NULL AND i.starts_at <= ?"
                " ORDER BY i.starts_at, i.id", (until,)).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            if dt.datetime.fromisoformat(r["starts_at"]) + dt.timedelta(minutes=r["minutes"]) < now:
                continue
            job = {"company": r["company"], "title": r["title"], "url": r["url"], "contacts": []}
            r["calendar_link"] = reminders.interview_link(r, job)
            out.append(r)
        return out

    def list_jobs(self, include_closed: bool = False) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT j.*, (SELECT MAX(at) FROM events e WHERE e.job_id = j.id) AS last_at FROM jobs j"
                + ("" if include_closed else " WHERE closed_reason IS NULL")
                + " ORDER BY stage, stage_changed_at DESC, id DESC"
            ).fetchall()
            return [self._view(r, r["last_at"] or r["created_at"]) for r in rows]

    def search(self, query) -> list[int]:
        """Ids of jobs (open or closed) where every word of the query appears somewhere: the job's own
        fields, its notes, or its contacts. Case-insensitive."""
        if not isinstance(query, str) or len(query) > 200:
            raise ValidationError("Search for up to 200 characters")
        words = query.casefold().split()
        if not words:
            return []
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT j.id, j.company || ' ' || j.title || ' ' || j.location || ' ' || j.pay || ' ' || j.description"
                " || ' ' || COALESCE((SELECT group_concat(text, ' ') FROM events e WHERE e.job_id = j.id AND e.kind = 'note'), '')"
                " || ' ' || COALESCE((SELECT group_concat(name || ' ' || role || ' ' || email, ' ') FROM contacts c WHERE c.job_id = j.id), '')"
                " || ' ' || j.source || ' ' || COALESCE((SELECT group_concat(label || ' ' || place, ' ') FROM interviews i WHERE i.job_id = j.id), '')"
                " AS hay FROM jobs j ORDER BY j.id"
            ).fetchall()
        return [r["id"] for r in rows if all(w in r["hay"].casefold() for w in words)]

    def update_job(self, job_id: int, patch: dict) -> dict:
        with self.db.lock:
            self._row(job_id)
            sets, args = [], []
            for f in EDITABLE:
                if f in patch:
                    sets.append(f"{f} = ?")
                    args.append(_clean(f, patch[f]))
            if "follow_up_on" in patch:
                new = self._follow(patch["follow_up_on"])
                sets.append("follow_up_on = ?")
                args.append(new)
            if sets:
                sets.append("updated_at = ?")
                args += [self.clock(), job_id]
                self.db.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
                if "follow_up_on" in patch:
                    new = self._follow(patch["follow_up_on"])
                    self._event(job_id, "follow_up", f"Follow-up set for {new}." if new else "Follow-up date cleared.")
            if "stage" in patch:
                self.move(job_id, patch["stage"])
            return self.get_job(job_id)

    def move(self, job_id: int, stage) -> dict:
        if not isinstance(stage, int) or isinstance(stage, bool) or not 0 <= stage <= 4:
            raise ValidationError("stage must be a number from 0 to 4")
        with self.db.lock:
            r = self._row(job_id)
            if r["stage"] == stage and r["closed_reason"] is None:
                return self.get_job(job_id)
            now = self.clock()
            self.db.conn.execute(
                "UPDATE jobs SET stage = ?, max_stage = MAX(max_stage, ?), closed_reason = NULL, stage_changed_at = ?, updated_at = ? WHERE id = ?",
                (stage, stage, now, now, job_id),
            )
            if r["closed_reason"] is not None:
                self._event(job_id, "reopened", f"Reopened at {STAGES[stage]}.")
            else:
                self._event(job_id, "stage", f"Moved from {STAGES[r['stage']]} to {STAGES[stage]}.")
            return self.get_job(job_id)

    def close(self, job_id: int, reason: str) -> dict:
        if reason not in CLOSED_REASONS:
            raise ValidationError("reason must be rejected, withdrawn or ghosted")
        with self.db.lock:
            r = self._row(job_id)
            if r["closed_reason"] == reason:
                return self.get_job(job_id)
            self.db.conn.execute("UPDATE jobs SET closed_reason = ?, updated_at = ? WHERE id = ?", (reason, self.clock(), job_id))
            self._event(job_id, "closed", {"rejected": "Rejected.", "withdrawn": "Withdrew the application.", "ghosted": "No response. Marked as ghosted."}[reason])
            return self.get_job(job_id)

    def reopen(self, job_id: int) -> dict:
        with self.db.lock:
            r = self._row(job_id)
            if r["closed_reason"] is None:
                return self.get_job(job_id)
            return self.move(job_id, r["stage"])

    def add_note(self, job_id: int, text) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("A note cannot be empty")
        if len(text) > 2000:
            raise ValidationError("A note can be up to 2000 characters")
        with self.db.lock:
            self._row(job_id)
            self._event(job_id, "note", text.strip())
            self._touch(job_id)
            return self.get_job(job_id)

    def delete_job(self, job_id: int) -> None:
        with self.db.lock:
            self._row(job_id)
            self.db.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    # ---- summaries
    def pulse(self) -> dict:
        with self.db.lock:
            jobs = self.list_jobs(include_closed=True)
        active = [j for j in jobs if j["closed_reason"] is None]
        reached = [sum(1 for j in jobs if j["max_stage"] >= i) for i in range(5)]
        applied = reached[1]
        closed = {r: sum(1 for j in jobs if j["closed_reason"] == r) for r in CLOSED_REASONS}
        due = [j for j in active if j["follow_up_due"]]
        in_flight = [j for j in active if j["stage"] > 0]
        return {
            "active": len(active), "total": len(jobs), "reached": reached, "closed": closed,
            "reached_screen_pct": round(reached[2] / applied * 100) if applied else None,
            "avg_days_in_stage": round(sum(j["days_in_stage"] for j in active) / len(active)) if active else None,
            "in_flight": len(in_flight),
            "follow_ups_due": [{"id": j["id"], "company": j["company"], "title": j["title"], "stage_name": j["stage_name"], "days_in_stage": j["days_in_stage"], "idle_days": j["idle_days"], "follow_up_on": j["follow_up_on"]} for j in due],
            "stale_days": self.stale_days,
            **self._response_stats(jobs),
            "upcoming_interviews": self.upcoming_interviews(),
        }

    def _response_stats(self, jobs: list[dict]) -> dict:
        """When each job was applied to and when it first heard back, worked out from its history.

        Applied: the job was created at Applied, or first moved to Applied or beyond.
        Heard back: after applying, it first moved to Screen or beyond, or was closed as rejected.
        """
        idx = {n: i for i, n in enumerate(STAGES)}
        with self.db.lock:
            evs = self.db.conn.execute("SELECT job_id, at, kind, text FROM events ORDER BY at, id").fetchall()
        applied, heard = {}, {}
        for e in evs:
            j = e["job_id"]
            if j not in applied:
                if e["kind"] == "created" and e["text"] == "Applied.":
                    applied[j] = e["at"]
                elif e["kind"] == "stage":
                    m = STAGE_MOVE.match(e["text"])
                    if m and idx.get(m.group(2), 0) >= 1:
                        applied[j] = e["at"]
                        if idx.get(m.group(2), 0) >= 2:  # moved straight from Saved to Screen or later
                            heard[j] = e["at"]
                continue
            if j in heard:
                continue
            if e["kind"] == "stage":
                m = STAGE_MOVE.match(e["text"])
                if m and idx.get(m.group(2), 0) >= 2:
                    heard[j] = e["at"]
            elif e["kind"] == "closed" and e["text"] == "Rejected.":
                heard[j] = e["at"]
        by_id = {j["id"]: j for j in jobs}
        applied = {j: t for j, t in applied.items() if j in by_id}
        waits = sorted((heard[j] - applied[j]) / DAY for j in applied if j in heard)
        median = None
        if waits:
            mid = len(waits) // 2
            median = round(waits[mid] if len(waits) % 2 else (waits[mid - 1] + waits[mid]) / 2)

        # applications per week for the last 8 weeks, weeks starting on Monday in the user's time zone
        today = self._today()
        start = today - dt.timedelta(days=today.weekday() + 7 * 7)
        weeks = [{"week_of": (start + dt.timedelta(weeks=i)).isoformat(), "applied": 0} for i in range(8)]
        for t in applied.values():
            d = dt.datetime.fromtimestamp(t, self.tz).date()
            i = (d - start).days // 7
            if 0 <= i < 8:
                weeks[i]["applied"] += 1

        sources: dict[str, dict] = {}
        for j, t in applied.items():
            src = by_id[j]["source"] or "Not set"
            row = sources.setdefault(src, {"source": src, "applied": 0, "heard_back": 0})
            row["applied"] += 1
            row["heard_back"] += j in heard
        return {
            "applied_total": len(applied),
            "heard_back": sum(1 for j in applied if j in heard),
            "response_rate_pct": round(sum(1 for j in applied if j in heard) / len(applied) * 100) if applied else None,
            "median_days_to_reply": median,
            "weekly_applied": weeks,
            "by_source": sorted(sources.values(), key=lambda r: (-r["applied"], r["source"] == "Not set", r["source"])),
        }

    # ---- contacts
    @staticmethod
    def _contact_fields(d, partial: bool = False) -> dict:
        if not isinstance(d, dict):
            raise ValidationError("Expected an object")
        out = {}
        for k, limit in (("name", 120), ("role", 120), ("email", 200), ("last_contacted_on", 10)):
            if k not in d:
                if k == "name" and not partial:
                    raise ValidationError("A contact needs a name")
                continue
            v = d[k] if d[k] is not None else ""
            if not isinstance(v, str):
                raise ValidationError(f"{k} must be text")
            v = v.strip()
            if len(v) > limit:
                raise ValidationError(f"{k} is too long (limit {limit} characters)")
            if k == "name" and not v:
                raise ValidationError("A contact needs a name")
            if k == "email" and v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
                raise ValidationError("That email address does not look right")
            if k == "last_contacted_on" and v:
                _date(v, "last_contacted_on")
            out[k] = v
        return out

    def add_contact(self, job_id: int, d) -> dict:
        f = self._contact_fields(d)
        with self.db.lock:
            self._row(job_id)
            self.db.conn.execute("INSERT INTO contacts (job_id, name, role, email, last_contacted_on) VALUES (?, ?, ?, ?, ?)",
                                 (job_id, f["name"], f.get("role", ""), f.get("email", ""), f.get("last_contacted_on", "")))
            self._touch(job_id)
            return self.get_job(job_id)

    def update_contact(self, job_id: int, contact_id: int, d) -> dict:
        f = self._contact_fields(d, partial=True)
        with self.db.lock:
            if not self.db.conn.execute("SELECT 1 FROM contacts WHERE id = ? AND job_id = ?", (contact_id, job_id)).fetchone():
                raise NotFound("No such contact")
            for k, v in f.items():
                self.db.conn.execute(f"UPDATE contacts SET {k} = ? WHERE id = ?", (v, contact_id))
            self._touch(job_id)
            return self.get_job(job_id)

    def delete_contact(self, job_id: int, contact_id: int) -> dict:
        with self.db.lock:
            cur = self.db.conn.execute("DELETE FROM contacts WHERE id = ? AND job_id = ?", (contact_id, job_id))
            if cur.rowcount == 0:
                raise NotFound("No such contact")
            return self.get_job(job_id)

    # ---- interview prep checklist
    def add_prep(self, job_id: int, text) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("A checklist item cannot be empty")
        if len(text.strip()) > 200:
            raise ValidationError("A checklist item can be up to 200 characters")
        with self.db.lock:
            self._row(job_id)
            pos = self.db.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM prep_items WHERE job_id = ?", (job_id,)).fetchone()[0]
            self.db.conn.execute("INSERT INTO prep_items (job_id, text, done, position) VALUES (?, ?, 0, ?)", (job_id, text.strip(), pos))
            return self.get_job(job_id)

    def add_default_prep(self, job_id: int) -> dict:
        with self.db.lock:
            self._row(job_id)
            have = {r[0] for r in self.db.conn.execute("SELECT text FROM prep_items WHERE job_id = ?", (job_id,))}
            for t in DEFAULT_PREP:
                if t not in have:
                    self.add_prep(job_id, t)
            return self.get_job(job_id)

    def set_prep_done(self, job_id: int, item_id: int, done) -> dict:
        if not isinstance(done, bool):
            raise ValidationError("done must be true or false")
        with self.db.lock:
            cur = self.db.conn.execute("UPDATE prep_items SET done = ? WHERE id = ? AND job_id = ?", (int(done), item_id, job_id))
            if cur.rowcount == 0:
                raise NotFound("No such checklist item")
            return self.get_job(job_id)

    def delete_prep(self, job_id: int, item_id: int) -> dict:
        with self.db.lock:
            cur = self.db.conn.execute("DELETE FROM prep_items WHERE id = ? AND job_id = ?", (item_id, job_id))
            if cur.rowcount == 0:
                raise NotFound("No such checklist item")
            return self.get_job(job_id)

    # ---- CSV import
    def import_csv(self, text) -> dict:
        """Adds jobs from a spreadsheet. Understands this app's own export and simple company/title sheets. All or nothing."""
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("The file is empty")
        if len(text.encode("utf-8", "ignore")) > IMPORT_MAX_BYTES:
            raise ValidationError("The file is too large")
        text = text.lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValidationError("The file has no header row")
        norm = {h: h.strip().lower().replace(" ", "_") for h in reader.fieldnames if h}
        if "company" not in norm.values() or "title" not in norm.values():
            raise ValidationError("The first row must include company and title columns")
        rows = []
        for n, raw in enumerate(reader, start=2):
            if n - 1 > IMPORT_MAX_ROWS:
                raise ValidationError(f"Too many rows (limit {IMPORT_MAX_ROWS})")
            r = {norm[k]: (v or "").strip().removeprefix("'") for k, v in raw.items() if k in norm}
            if not any(r.values()):
                continue
            try:
                vals = {f: _clean(f, r.get(f, "")) for f in EDITABLE}
                follow = self._follow(r.get("follow_up_on", "") or None)
                label = (r.get("stage") or "").strip().lower()
                stages = [s.lower() for s in STAGES]
                stage = stages.index(label) if label in stages else 0
                status = (r.get("status") or "active").strip().lower()
                if status not in ("active", *CLOSED_REASONS):
                    raise ValidationError("status must be active, rejected, withdrawn or ghosted")
            except ValidationError as e:
                raise ValidationError(f"Row {n}: {e}") from None
            rows.append((vals, follow, stage, status))
        if not rows:
            raise ValidationError("No jobs found in the file")
        for vals, follow, stage, status in rows:
            j = self.add_job({**vals, "stage": min(stage, 1), "follow_up_on": follow})
            if stage > 1:
                self.move(j["id"], stage)
            if status != "active":
                self.close(j["id"], status)
        return {"imported": len(rows)}

    # ---- resume and matching
    def get_resume(self) -> dict:
        with self.db.lock:
            r = self.db.conn.execute("SELECT text, updated_at FROM resume WHERE id = 1").fetchone()
        return {"text": r["text"], "updated_at": r["updated_at"]} if r else {"text": "", "updated_at": None}

    def set_resume(self, text) -> dict:
        if not isinstance(text, str):
            raise ValidationError("The resume must be text")
        if len(text) > RESUME_LIMIT:
            raise ValidationError(f"The resume is too long (limit {RESUME_LIMIT} characters)")
        text = text.strip()
        with self.db.lock:
            if text:
                self.db.conn.execute("INSERT INTO resume (id, text, updated_at) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at", (text, self.clock()))
            else:
                self.db.conn.execute("DELETE FROM resume")
        return self.get_resume()

    def match_job(self, job_id: int) -> dict:
        with self.db.lock:
            r = self._row(job_id)
        return matcher.match(r["description"], self.get_resume()["text"])

    # ---- export, backup
    def export_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["company", "title", "location", "pay", "url", "posted_on", "source", "stage", "furthest_stage", "status", "follow_up_on", "days_in_stage", "created_utc"])
        for j in self.list_jobs(include_closed=True):
            w.writerow([_csv_safe(j["company"]), _csv_safe(j["title"]), _csv_safe(j["location"]), _csv_safe(j["pay"]), _csv_safe(j["url"]), j["posted_on"], _csv_safe(j["source"]), j["stage_name"], STAGES[j["max_stage"]],
                        j["closed_reason"] or "active", j["follow_up_on"] or "", j["days_in_stage"], dt.datetime.fromtimestamp(j["created_at"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M")])
        return buf.getvalue()

    def backup(self) -> dict:
        with self.db.lock:
            jobs = [dict(r) for r in self.db.conn.execute("SELECT * FROM jobs ORDER BY id")]
            events = [dict(r) for r in self.db.conn.execute("SELECT * FROM events ORDER BY id")]
            contacts = [dict(r) for r in self.db.conn.execute("SELECT * FROM contacts ORDER BY id")]
            prep = [dict(r) for r in self.db.conn.execute("SELECT * FROM prep_items ORDER BY id")]
            interviews = [dict(r) for r in self.db.conn.execute("SELECT * FROM interviews ORDER BY id")]
        return {"app": "jobledger", "version": 1, "exported_at": self.clock(), "jobs": jobs, "events": events, "resume": self.get_resume()["text"],
                "contacts": contacts, "prep": prep, "interviews": interviews}

    def restore(self, data) -> dict:
        """Replaces everything with a backup. Validated first, applied in one transaction."""
        if not isinstance(data, dict) or data.get("app") != "jobledger" or data.get("version") != 1:
            raise ValidationError("That file is not a JobLedger backup")
        jobs, events = data.get("jobs"), data.get("events")
        if not isinstance(jobs, list) or not isinstance(events, list):
            raise ValidationError("The backup is missing its jobs or events")
        resume = data.get("resume", "")  # older backups have none
        if not isinstance(resume, str) or len(resume) > RESUME_LIMIT:
            raise ValidationError("The resume in the backup is invalid")
        contacts, prep = data.get("contacts", []), data.get("prep", [])
        if not isinstance(contacts, list) or not isinstance(prep, list) or len(contacts) > 20000 or len(prep) > 50000:
            raise ValidationError("The contacts or checklists in the backup are invalid")
        interviews = data.get("interviews", [])  # older backups have none
        if not isinstance(interviews, list) or len(interviews) > 20000:
            raise ValidationError("The interviews in the backup are invalid")
        if len(jobs) > 5000 or len(events) > 100000:
            raise ValidationError("The backup is too large")
        cols = ["id", "company", "title", "location", "pay", "url", "posted_on", "description", "stage", "max_stage", "closed_reason", "follow_up_on", "stage_changed_at", "created_at", "updated_at", "source"]
        ids = set()
        for j in jobs:
            if isinstance(j, dict):
                j.setdefault("source", "")  # older backups have no source
            if not isinstance(j, dict) or set(cols) - set(j):
                raise ValidationError("A job in the backup is incomplete")
            if not isinstance(j["id"], int) or j["id"] in ids:
                raise ValidationError("The backup has a duplicate or invalid job id")
            ids.add(j["id"])
            for f in EDITABLE:
                _clean(f, j[f])
            if j["stage"] not in range(5) or j["max_stage"] not in range(5) or j["max_stage"] < j["stage"]:
                raise ValidationError("A job in the backup has an invalid stage")
            if j["closed_reason"] is not None and j["closed_reason"] not in CLOSED_REASONS:
                raise ValidationError("A job in the backup has an invalid status")
            self._follow(j["follow_up_on"])
            for f in ("stage_changed_at", "created_at", "updated_at"):
                if not isinstance(j[f], (int, float)):
                    raise ValidationError("A job in the backup has an invalid date")
        for e in events:
            if not isinstance(e, dict) or e.get("job_id") not in ids or e.get("kind") not in ("created", "stage", "note", "closed", "reopened", "follow_up", "interview") \
                    or not isinstance(e.get("text"), str) or not isinstance(e.get("at"), (int, float)):
                raise ValidationError("An event in the backup is invalid")
        for ct in contacts:
            if not isinstance(ct, dict) or ct.get("job_id") not in ids:
                raise ValidationError("A contact in the backup is invalid")
            self._contact_fields({k: ct.get(k, "") for k in ("name", "role", "email", "last_contacted_on")})
        for iv in interviews:
            if not isinstance(iv, dict) or iv.get("job_id") not in ids:
                raise ValidationError("An interview in the backup is invalid")
            iv.update(self._interview_fields({k: iv.get(k) for k in ("starts_at", "minutes", "label", "place") if k in iv}))
        for it in prep:
            if not isinstance(it, dict) or it.get("job_id") not in ids or not isinstance(it.get("text"), str) or it.get("done") not in (0, 1, True, False):
                raise ValidationError("A checklist item in the backup is invalid")
        with self.db.lock:
            c = self.db.conn
            c.execute("BEGIN")
            try:
                c.execute("DELETE FROM contacts")
                c.execute("DELETE FROM prep_items")
                c.execute("DELETE FROM interviews")
                c.execute("DELETE FROM events")
                c.execute("DELETE FROM jobs")
                c.execute("DELETE FROM resume")
                if resume.strip():
                    c.execute("INSERT INTO resume (id, text, updated_at) VALUES (1, ?, ?)", (resume.strip(), self.clock()))
                for j in jobs:
                    c.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [j[k] for k in cols])
                for e in events:
                    c.execute("INSERT INTO events (job_id, at, kind, text) VALUES (?, ?, ?, ?)", (e["job_id"], e["at"], e["kind"], e["text"][:2000]))
                for ct in contacts:
                    c.execute("INSERT INTO contacts (job_id, name, role, email, last_contacted_on) VALUES (?, ?, ?, ?, ?)",
                              (ct["job_id"], ct["name"].strip(), ct.get("role", ""), ct.get("email", ""), ct.get("last_contacted_on", "")))
                for iv in interviews:
                    c.execute("INSERT INTO interviews (job_id, starts_at, minutes, label, place) VALUES (?, ?, ?, ?, ?)",
                              (iv["job_id"], iv["starts_at"], iv.get("minutes", 60), iv.get("label", ""), iv.get("place", "")))
                for n, it in enumerate(prep):
                    c.execute("INSERT INTO prep_items (job_id, text, done, position) VALUES (?, ?, ?, ?)", (it["job_id"], it["text"][:200], int(bool(it["done"])), it.get("position", n)))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return {"jobs": len(jobs), "events": len(events)}


def _csv_safe(v: str) -> str:
    """Stops spreadsheet programs from running a cell that starts with = + - or @ as a formula."""
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v
