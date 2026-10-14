"""Follow-up reminders as calendar events, so they reach you when JobLedger is closed.

Two ways out, both built from the same reminder date (the follow-up date you set, or the day the idle
nudge will fire): an .ics file any calendar app can import, and a Google Calendar "add event" link.
Events are at 09:00 in the calendar's own time zone ("floating" time), 15 minutes long, with an alarm.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional
from urllib.parse import urlencode

REMIND_AT = dt.time(9, 0)
LENGTH = dt.timedelta(minutes=15)


def _times(day: str) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(dt.date.fromisoformat(day), REMIND_AT)
    return start, start + LENGTH


def summary(job: dict) -> str:
    return f"Follow up: {job['title']} at {job['company']}"


def details(job: dict) -> str:
    lines = [f"Stage: {job['stage_name']}."]
    if job.get("reminder_is_nudge"):
        lines.append("No follow-up date was set; this is when JobLedger's idle nudge fires if nothing happens first.")
    for c in job.get("contacts") or []:
        lines.append("Contact: " + ", ".join(x for x in (c["name"], c["role"], c["email"]) if x) + ".")
    if job.get("url", "").startswith(("http://", "https://")):
        lines.append(f"Posting: {job['url']}")
    lines.append("From JobLedger.")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines over 75 octets continue on the next line after a space. Never split a UTF-8 character."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


def _interview_times(iv: dict) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.fromisoformat(iv["starts_at"])
    return start, start + dt.timedelta(minutes=iv["minutes"])


def interview_summary(iv: dict, job: dict) -> str:
    return f"{iv.get('label') or 'Interview'}: {job['title']} at {job['company']}"


def interview_details(iv: dict, job: dict) -> str:
    lines = []
    if iv.get("place"):
        lines.append(f"Where: {iv['place']}")
    if str(job.get("url", "")).startswith(("http://", "https://")):
        lines.append(f"Posting: {job['url']}")
    lines.append("From JobLedger.")
    return "\n".join(lines)


def interview_link(iv: dict, job: dict) -> str:
    start, end = _interview_times(iv)
    q = {"action": "TEMPLATE", "text": interview_summary(iv, job), "dates": f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}",
         "details": interview_details(iv, job)}
    if iv.get("place"):
        q["location"] = iv["place"]
    return "https://calendar.google.com/calendar/render?" + urlencode(q)


def _stamp(local: dt.datetime, tz: Optional[dt.tzinfo]) -> str:
    """Wall-clock time as an iCalendar value: in UTC with a Z when the zone is known, otherwise "floating"
    (the calendar app then uses its own zone)."""
    if tz is None:
        return f"{local:%Y%m%dT%H%M%S}"
    return f"{local.replace(tzinfo=tz).astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}"


def ics(jobs: Iterable[dict], stamp: float, interviews: Iterable[dict] = (), tz: Optional[dt.tzinfo] = None) -> str:
    """One VEVENT per job that has a reminder date, plus one per upcoming interview (alert an hour before).
    UIDs are stable, so importing again updates events in calendars that honor UIDs instead of adding copies."""
    now = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//JobLedger//Follow-ups//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:JobLedger follow-ups"]
    for j in jobs:
        if not j.get("reminder_on"):
            continue
        start, end = _times(j["reminder_on"])
        lines += ["BEGIN:VEVENT", f"UID:jobledger-job-{j['id']}@jobledger.local", f"DTSTAMP:{now}",
                  f"DTSTART:{_stamp(start, tz)}", f"DTEND:{_stamp(end, tz)}",
                  f"SUMMARY:{_esc(summary(j))}", f"DESCRIPTION:{_esc(details(j))}",
                  "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(summary(j))}", "TRIGGER:-PT0M", "END:VALARM",
                  "END:VEVENT"]
    for iv in interviews:
        start, end = _interview_times(iv)
        job = {"company": iv["company"], "title": iv["title"], "url": iv.get("url", "")}
        lines += ["BEGIN:VEVENT", f"UID:jobledger-interview-{iv['id']}@jobledger.local", f"DTSTAMP:{now}",
                  f"DTSTART:{_stamp(start, tz)}", f"DTEND:{_stamp(end, tz)}",
                  f"SUMMARY:{_esc(interview_summary(iv, job))}", f"DESCRIPTION:{_esc(interview_details(iv, job))}"]
        if iv.get("place"):
            lines.append(f"LOCATION:{_esc(iv['place'])}")
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(interview_summary(iv, job))}", "TRIGGER:-PT60M", "END:VALARM",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def google_link(job: dict) -> Optional[str]:
    """A link that opens Google Calendar (app or web) with the event filled in; the user taps Save.
    Nothing is sent until they open it."""
    if not job.get("reminder_on"):
        return None
    start, end = _times(job["reminder_on"])
    q = {"action": "TEMPLATE", "text": summary(job), "dates": f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}",
         "details": details(job)}
    return "https://calendar.google.com/calendar/render?" + urlencode(q)
