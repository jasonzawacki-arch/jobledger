import csv, datetime as dt, io, json, unittest, zoneinfo
from jobledger.db import Database
from jobledger.service import DAY, NotFound, Tracker, ValidationError

T0 = 1_800_000_000.0  # fixed start


class Clock:
    def __init__(self): self.t = T0
    def __call__(self): return self.t
    def advance(self, days): self.t += days * DAY


OPEN = []


def make(tz=dt.timezone.utc):
    c = Clock()
    OPEN.append(Database())
    return Tracker(OPEN[-1], c, tz=tz), c


def tearDownModule():
    while OPEN: OPEN.pop().close()


class Rules(unittest.TestCase):
    def test_required_fields_and_url(self):
        t, _ = make()
        with self.assertRaises(ValidationError): t.add_job({"company": "", "title": "x"})
        with self.assertRaises(ValidationError): t.add_job({"company": "A", "title": "x", "url": "javascript:alert(1)"})
        with self.assertRaises(ValidationError): t.add_job({"company": "A", "title": "x", "posted_on": "tomorrow"})

    def test_stage_moves_log_events_and_track_max(self):
        t, c = make()
        j = t.add_job({"company": "A", "title": "B"})
        c.advance(2); t.move(j["id"], 3)
        c.advance(1); t.move(j["id"], 1)
        got = t.get_job(j["id"])
        self.assertEqual((got["stage"], got["max_stage"]), (1, 3))
        self.assertEqual([e["kind"] for e in got["events"]], ["stage", "stage", "created"])
        with self.assertRaises(ValidationError): t.move(j["id"], 5)
        with self.assertRaises(ValidationError): t.move(j["id"], True)

    def test_close_and_reopen(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "B", "stage": 1})
        t.close(j["id"], "ghosted")
        self.assertEqual(t.list_jobs(), [])
        self.assertEqual(len(t.list_jobs(include_closed=True)), 1)
        t.reopen(j["id"])
        self.assertIsNone(t.get_job(j["id"])["closed_reason"])
        with self.assertRaises(ValidationError): t.close(j["id"], "bored")

    def test_follow_up_rules(self):
        t, c = make()
        a = t.add_job({"company": "A", "title": "x", "stage": 1})
        saved = t.add_job({"company": "S", "title": "x"})
        offer = t.add_job({"company": "O", "title": "x", "stage": 1}); t.move(offer["id"], 4)
        c.advance(9)
        due = lambda: {j["id"] for j in t.list_jobs() if j["follow_up_due"]}
        self.assertEqual(due(), set())
        c.advance(1)
        self.assertEqual(due(), {a["id"]})  # idle 10 days; saved and offer never nag
        t.add_note(a["id"], "emailed recruiter")
        self.assertEqual(due(), set())  # activity resets idle
        t.update_job(a["id"], {"follow_up_on": "2027-01-01"})
        c.advance(60)
        self.assertEqual(due(), {a["id"]})  # explicit date wins
        t.close(a["id"], "rejected")
        self.assertEqual(due(), set())

    def test_pulse_funnel(self):
        t, _ = make()
        a = t.add_job({"company": "A", "title": "x", "stage": 1}); t.move(a["id"], 3); t.close(a["id"], "rejected")
        t.add_job({"company": "B", "title": "x", "stage": 1})
        p = t.pulse()
        self.assertEqual(p["reached"], [2, 2, 1, 1, 0])
        self.assertEqual((p["active"], p["total"], p["closed"]["rejected"]), (1, 2, 1))
        self.assertEqual(p["reached_screen_pct"], 50)

    def test_notes(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})
        with self.assertRaises(ValidationError): t.add_note(j["id"], "  ")
        with self.assertRaises(ValidationError): t.add_note(j["id"], "x" * 2001)
        with self.assertRaises(NotFound): t.add_note(999, "hi")

    def test_delete_cascades(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})
        t.delete_job(j["id"])
        n = t.db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(n, 0)

    def test_csv_formula_guard(self):
        t, _ = make()
        t.add_job({"company": "=HYPERLINK(\"http://x\")", "title": "+cmd", "stage": 1})
        rows = list(csv.reader(io.StringIO(t.export_csv())))
        self.assertTrue(rows[1][rows[0].index("company")].startswith("'="))
        self.assertTrue(rows[1][rows[0].index("title")].startswith("'+"))

    def test_backup_round_trip(self):
        t, c = make()
        j = t.add_job({"company": "A", "title": "x", "stage": 1}); t.move(j["id"], 2); t.add_note(j["id"], "n")
        t.close(j["id"], "withdrawn")
        snap = json.loads(json.dumps(t.backup()))
        t2, _ = make()
        t2.add_job({"company": "old", "title": "gone"})
        t2.restore(snap)
        self.assertEqual(t2.backup()["jobs"], snap["jobs"])
        self.assertEqual(len(t2.list_jobs(include_closed=True)), 1)

    def test_restore_rejects_bad_data_and_keeps_existing(self):
        t, _ = make()
        t.add_job({"company": "keep", "title": "me"})
        good = t.backup()
        for bad in ([], {"app": "other"}, {**good, "version": 99}, {**good, "jobs": [{"company": "x"}]}, {**good, "jobs": [{**good["jobs"][0], "stage": 9}]}):
            with self.assertRaises(ValidationError): t.restore(bad)
        self.assertEqual(t.list_jobs()[0]["company"], "keep")

    def test_migrate_refuses_newer_schema(self):
        with Database() as d:
            d.conn.execute("PRAGMA user_version = 99")
            with self.assertRaises(Exception): d.migrate()

    def test_newer_database_file_is_refused_and_released(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.sqlite3")
            with Database(path) as d: d.conn.execute("PRAGMA user_version = 99")
            with self.assertRaises(RuntimeError): Database(path)

    def test_follow_up_date_uses_local_day(self):
        # T0 is 08:00 UTC, which is 03:00 the same day in New York. At 02:00 UTC the next day it is
        # still the evening before in New York, so a follow-up for that next day is not due yet there.
        ny = zoneinfo.ZoneInfo("America/New_York")
        for tz, expect in ((dt.timezone.utc, True), (ny, False)):
            t, c = make(tz)
            j = t.add_job({"company": "A", "title": "x", "stage": 1})
            c.advance(18 / 24)  # 02:00 UTC next day
            tomorrow_utc = dt.datetime.fromtimestamp(c(), dt.timezone.utc).date().isoformat()
            t.update_job(j["id"], {"follow_up_on": tomorrow_utc})
            self.assertEqual(t.get_job(j["id"])["follow_up_due"], expect, tz)


class Search(unittest.TestCase):
    def test_matches_fields_notes_and_contacts(self):
        t, _ = make()
        a = t.add_job({"company": "Acme Corp", "title": "Data Analyst", "location": "Boston", "description": "Needs Tableau"})
        b = t.add_job({"company": "Globex", "title": "Analyst", "stage": 1})
        t.add_note(b["id"], "Spoke with Priya about SQL")
        t.add_contact(b["id"], {"name": "Dana Ruiz", "role": "Recruiter", "email": "dana@globex.example"})
        t.close(a["id"], "rejected")
        self.assertEqual(t.search("acme"), [a["id"]])  # closed jobs are found too
        self.assertEqual(t.search("ANALYST"), [a["id"], b["id"]])  # case-insensitive
        self.assertEqual(t.search("tableau"), [a["id"]])  # posting text
        self.assertEqual(t.search("priya"), [b["id"]])  # notes
        self.assertEqual(t.search("recruiter dana"), [b["id"]])  # contacts, every word must match
        self.assertEqual(t.search("analyst boston"), [a["id"]])
        self.assertEqual(t.search("analyst nowhere"), [])
        self.assertEqual(t.search("   "), [])
        with self.assertRaises(ValidationError): t.search("x" * 201)
        with self.assertRaises(ValidationError): t.search(None)

    def test_like_wildcards_are_plain_text(self):
        t, _ = make()
        t.add_job({"company": "A", "title": "x"})
        self.assertEqual(t.search("%"), [])
        self.assertEqual(t.search("_"), [])


class Reminders(unittest.TestCase):
    def test_reminder_dates(self):
        t, c = make()
        saved = t.add_job({"company": "S", "title": "x"})
        applied = t.add_job({"company": "A", "title": "x", "stage": 1})
        dated = t.add_job({"company": "D", "title": "x"})
        t.update_job(dated["id"], {"follow_up_on": "2027-02-01"})
        g = lambda j: t.get_job(j["id"])
        self.assertIsNone(g(saved)["reminder_on"])  # saved, no date: never nags
        self.assertEqual(g(applied)["reminder_on"], "2027-01-25")  # T0 is 2027-01-15, plus 10 idle days
        self.assertTrue(g(applied)["reminder_is_nudge"])
        self.assertEqual(g(dated)["reminder_on"], "2027-02-01")
        self.assertFalse(g(dated)["reminder_is_nudge"])
        c.advance(3); t.add_note(applied["id"], "nudged")
        self.assertEqual(g(applied)["reminder_on"], "2027-01-28")  # activity pushes the nudge back
        t.move(applied["id"], 4)
        self.assertIsNone(g(applied)["reminder_on"])  # offers never nag
        t.close(dated["id"], "withdrawn")
        self.assertIsNone(g(dated)["reminder_on"])

    def test_calendar_file(self):
        t, _ = make()
        a = t.add_job({"company": "Acme, Inc.", "title": "Analyst; Data \\ Ops", "stage": 1, "url": "https://example.com/job"})
        t.add_contact(a["id"], {"name": "Zoë Ångström-Nakamura", "role": "Recruiter, Talent", "email": "z@example.com"})
        t.add_job({"company": "Saved Co", "title": "x"})  # no reminder, left out
        text = t.calendar()
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(text.count("BEGIN:VEVENT"), 1)
        self.assertIn("DTSTART:20270125T090000\r\n", text)
        self.assertIn("DTEND:20270125T091500\r\n", text)
        self.assertIn("UID:jobledger-job-1@jobledger.local", text)
        self.assertIn("TRIGGER:-PT0M", text)
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75, line)
        unfolded = text.replace("\r\n ", "")
        self.assertIn("SUMMARY:Follow up: Analyst\\; Data \\\\ Ops at Acme\\, Inc.", unfolded)
        self.assertIn("Contact: Zoë Ångström-Nakamura\\, Recruiter\\, Talent\\, z@example.com.", unfolded)
        self.assertEqual(t.calendar(a["id"]).count("BEGIN:VEVENT"), 1)
        with self.assertRaises(ValidationError): t.calendar(2)  # saved job with no date
        with self.assertRaises(NotFound): t.calendar(99)

    def test_google_link(self):
        from urllib.parse import parse_qs, urlsplit
        t, _ = make()
        a = t.add_job({"company": "Acme", "title": "Analyst", "stage": 1})
        link = t.get_job(a["id"])["calendar_link"]
        u = urlsplit(link)
        self.assertEqual((u.scheme, u.netloc), ("https", "calendar.google.com"))
        q = parse_qs(u.query)
        self.assertEqual(q["dates"], ["20270125T090000/20270125T091500"])
        self.assertEqual(q["text"], ["Follow up: Analyst at Acme"])
        self.assertIsNone(t.get_job(t.add_job({"company": "S", "title": "x"})["id"])["calendar_link"])


class Interviews(unittest.TestCase):
    def test_add_list_move_delete(self):
        t, c = make()  # T0 is 2027-01-15 08:00 UTC
        j = t.add_job({"company": "Acme", "title": "Analyst", "stage": 1})
        g = t.add_interview(j["id"], {"starts_at": "2027-01-18T14:30", "minutes": 45, "label": "Phone screen", "place": "https://meet.example/x"})
        iv = g["interviews"][0]
        self.assertEqual((iv["starts_at"], iv["minutes"], iv["label"], iv["past"]), ("2027-01-18T14:30", 45, "Phone screen", False))
        self.assertIn("dates=20270118T143000%2F20270118T151500", iv["calendar_link"])
        self.assertIn("Phone screen scheduled for Mon Jan 18, 2:30 PM.", [e["text"] for e in g["events"]])
        self.assertEqual(g["next_interview"], "2027-01-18T14:30")
        self.assertEqual(t.list_jobs()[0]["next_interview"], "2027-01-18T14:30")
        self.assertEqual([u["company"] for u in t.upcoming_interviews()], ["Acme"])
        t.update_interview(j["id"], iv["id"], {"starts_at": "2027-01-19T09:00"})
        self.assertEqual(t.get_job(j["id"])["interviews"][0]["starts_at"], "2027-01-19T09:00")
        c.advance(5)  # Jan 20: it is over
        self.assertTrue(t.get_job(j["id"])["interviews"][0]["past"])
        self.assertIsNone(t.get_job(j["id"])["next_interview"])
        self.assertEqual(t.upcoming_interviews(), [])
        t.delete_interview(j["id"], iv["id"])
        self.assertEqual(t.get_job(j["id"])["interviews"], [])
        with self.assertRaises(NotFound): t.delete_interview(j["id"], iv["id"])

    def test_validation(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})
        for bad in ({}, {"starts_at": "tomorrow"}, {"starts_at": "2027-01-18T14:30+05:00"}, {"starts_at": "2027-01-18T14:30", "minutes": 0},
                    {"starts_at": "2027-01-18T14:30", "minutes": True}, {"starts_at": "2027-01-18T14:30", "label": "x" * 121}, None):
            with self.assertRaises(ValidationError, msg=bad): t.add_interview(j["id"], bad)
        with self.assertRaises(NotFound): t.add_interview(99, {"starts_at": "2027-01-18T14:30"})

    def test_upcoming_interview_pauses_the_nudge(self):
        t, c = make()
        j = t.add_job({"company": "A", "title": "x", "stage": 1})
        t.add_interview(j["id"], {"starts_at": "2027-02-20T10:00"})
        c.advance(20)  # idle well past 10 days, but the interview is still ahead
        g = t.get_job(j["id"])
        self.assertFalse(g["follow_up_due"]); self.assertIsNone(g["reminder_on"])
        t.update_job(j["id"], {"follow_up_on": "2027-02-01"})  # a date you set still counts
        self.assertTrue(t.get_job(j["id"])["follow_up_due"])

    def test_interviews_in_calendar_backup_and_search(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})  # saved: no follow-up of its own
        t.add_interview(j["id"], {"starts_at": "2027-01-18T14:30", "label": "Panel", "place": "Room 4, 1 Main St"})
        one = t.calendar(j["id"]).replace("\r\n ", "")
        self.assertIn("UID:jobledger-interview-1@jobledger.local", one)
        self.assertIn("LOCATION:Room 4\\, 1 Main St", one)
        self.assertIn("TRIGGER:-PT60M", one)
        self.assertIn("jobledger-interview-1", t.calendar())
        self.assertEqual(t.search("panel"), [j["id"]])
        b = json.loads(json.dumps(t.backup()))
        t.delete_interview(j["id"], 1)
        t.restore(b)
        self.assertEqual(t.get_job(j["id"])["interviews"][0]["label"], "Panel")
        bad = json.loads(json.dumps(b)); bad["interviews"][0]["starts_at"] = "nope"
        with self.assertRaises(ValidationError): t.restore(bad)


class Source(unittest.TestCase):
    def test_source_field_export_import_and_old_backups(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x", "source": "Referral"})
        self.assertEqual(j["source"], "Referral")
        self.assertEqual(t.update_job(j["id"], {"source": "LinkedIn"})["source"], "LinkedIn")
        with self.assertRaises(ValidationError): t.update_job(j["id"], {"source": "x" * 81})
        out = t.export_csv()
        self.assertIn("source", out.splitlines()[0])
        t2, _ = make(); t2.import_csv(out)
        self.assertEqual(t2.list_jobs()[0]["source"], "LinkedIn")
        old = json.loads(json.dumps(t.backup()))
        for jj in old["jobs"]: del jj["source"]
        del old["interviews"]
        t.restore(old)  # backups from before sources and interviews still restore
        self.assertEqual(t.list_jobs()[0]["source"], "")


class Pulse(unittest.TestCase):
    def test_response_rate_reply_time_weeks_and_sources(self):
        t, c = make()  # Friday 2027-01-15
        a = t.add_job({"company": "A", "title": "x", "stage": 1, "source": "LinkedIn"})
        b = t.add_job({"company": "B", "title": "y", "source": "Referral"})  # saved first
        d = t.add_job({"company": "D", "title": "z", "stage": 1, "source": "LinkedIn"})
        t.add_job({"company": "S", "title": "never applied"})
        c.advance(3); t.move(a["id"], 2)  # heard back after 3 days
        c.advance(1); t.move(b["id"], 1)  # applied on day 4
        c.advance(5); t.close(b["id"], "rejected")  # heard back (a no) after 5 days
        t.close(d["id"], "ghosted")  # silence is not a reply
        p = t.pulse()
        self.assertEqual((p["applied_total"], p["heard_back"], p["response_rate_pct"], p["median_days_to_reply"]), (3, 2, 67, 4))
        self.assertEqual(len(p["weekly_applied"]), 8)
        self.assertEqual([w["applied"] for w in p["weekly_applied"]][-2:], [2, 1])  # week of Jan 11: A, D; week of Jan 18: B
        self.assertEqual(p["weekly_applied"][-1]["week_of"], "2027-01-18")
        self.assertEqual(p["by_source"], [{"source": "LinkedIn", "applied": 2, "heard_back": 1}, {"source": "Referral", "applied": 1, "heard_back": 1}])

    def test_empty(self):
        t, _ = make()
        p = t.pulse()
        self.assertEqual((p["applied_total"], p["response_rate_pct"], p["median_days_to_reply"], p["by_source"]), (0, None, None, []))


if __name__ == "__main__":
    unittest.main()


class Matching(unittest.TestCase):
    RESUME = "- Built a Python REST API with SQLite storage and an audit log (side project)\n- Wrote 53 automated tests and a CI workflow on GitHub Actions\n"
    POST = "Requirements:\n- Python and REST APIs\n- Automated testing and CI/CD\n- Experience with Kubernetes in production\n"

    def test_statuses_and_evidence_are_verbatim_resume_lines(self):
        from jobledger import matcher
        r = matcher.match(self.POST, self.RESUME)
        by = {x["requirement"]: x for x in r["requirements"]}
        self.assertEqual(by["Python and REST APIs"]["status"], "match")
        self.assertEqual(by["Automated testing and CI/CD"]["status"], "match")
        self.assertEqual(by["Experience with Kubernetes in production"]["status"], "gap")
        lines = matcher.resume_lines(self.RESUME)
        for x in r["requirements"]:
            if "evidence" in x:  # the guardrail: nothing is invented
                self.assertIn(x["evidence"]["line"], lines)
                self.assertEqual("".join(p[0] for p in x["evidence"]["parts"]), x["evidence"]["line"])
            else:
                self.assertEqual(x["status"], "gap")

    def test_synonyms(self):
        from jobledger import matcher
        r = matcher.match("- Experience with k8s and Postgres\n", "- Ran Kubernetes clusters and PostgreSQL databases daily\n")
        self.assertEqual(r["requirements"][0]["status"], "match")

    def test_no_resume_or_no_posting(self):
        from jobledger import matcher
        self.assertIsNone(matcher.match(self.POST, "")["score"])
        self.assertEqual(matcher.match("", self.RESUME)["requirements"], [])

    def test_resume_storage_backup_and_old_backups(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x", "description": self.POST})
        self.assertEqual(t.get_resume()["text"], "")
        t.set_resume(self.RESUME)
        self.assertEqual(t.match_job(j["id"])["counts"]["gap"], 1)
        snap = json.loads(json.dumps(t.backup()))
        t2, _ = make(); t2.restore(snap)
        self.assertEqual(t2.get_resume()["text"], self.RESUME.strip())
        snap.pop("resume"); t2.restore(snap)  # backups made before resumes existed still restore
        self.assertEqual(t2.get_resume()["text"], "")
        with self.assertRaises(ValidationError): t.set_resume(5)
        with self.assertRaises(ValidationError): t.set_resume("x" * 40001)
        t.set_resume("  ")
        self.assertEqual(t.get_resume()["text"], "")


class Extras(unittest.TestCase):
    def test_contacts(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})
        with self.assertRaises(ValidationError): t.add_contact(j["id"], {"name": ""})
        with self.assertRaises(ValidationError): t.add_contact(j["id"], {"name": "Dana", "email": "not-an-email"})
        with self.assertRaises(ValidationError): t.add_contact(j["id"], {"name": "Dana", "last_contacted_on": "soon"})
        got = t.add_contact(j["id"], {"name": "Dana Lee", "role": "Recruiter", "email": "dana@example.com", "last_contacted_on": "2026-10-01"})
        cid = got["contacts"][0]["id"]
        got = t.update_contact(j["id"], cid, {"last_contacted_on": "2026-10-10"})
        self.assertEqual((got["contacts"][0]["name"], got["contacts"][0]["last_contacted_on"]), ("Dana Lee", "2026-10-10"))
        other = t.add_job({"company": "B", "title": "y"})
        with self.assertRaises(NotFound): t.delete_contact(other["id"], cid)  # cannot reach another job's contact
        self.assertEqual(t.delete_contact(j["id"], cid)["contacts"], [])

    def test_prep_checklist(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})
        got = t.add_default_prep(j["id"]); n = len(got["prep"])
        self.assertGreater(n, 3)
        self.assertEqual(len(t.add_default_prep(j["id"])["prep"]), n)  # no duplicates
        t.add_prep(j["id"], "Bring references")
        iid = t.get_job(j["id"])["prep"][0]["id"]
        self.assertTrue(t.set_prep_done(j["id"], iid, True)["prep"][0]["done"])
        with self.assertRaises(ValidationError): t.set_prep_done(j["id"], iid, "yes")
        with self.assertRaises(ValidationError): t.add_prep(j["id"], " ")
        self.assertEqual(len(t.delete_prep(j["id"], iid)["prep"]), n)

    def test_import_round_trip_and_errors(self):
        t, _ = make()
        a = t.add_job({"company": "=Evil()", "title": "Analyst", "stage": 1}); t.move(a["id"], 3); t.close(a["id"], "rejected")
        t.add_job({"company": "Plain", "title": "Role"})
        text = t.export_csv()
        t2, _ = make()
        self.assertEqual(t2.import_csv(text)["imported"], 2)
        jobs = {j["company"]: j for j in t2.list_jobs(include_closed=True)}
        self.assertEqual((jobs["=Evil()"]["stage"], jobs["=Evil()"]["closed_reason"]), (3, "rejected"))  # formula guard undone on import
        self.assertEqual(t2.import_csv("Company,Title\nAcme,Engineer\n")["imported"], 1)
        for bad in ("", "name,role\nA,B\n", "company,title\n,Engineer\n", "company,title,status\nA,B,maybe\n", "company,title,url\nA,B,javascript:x\n"):
            with self.assertRaises(ValidationError): t2.import_csv(bad)
        before = len(t2.list_jobs(include_closed=True))
        with self.assertRaises(ValidationError): t2.import_csv("company,title\nGood,Row\n,Bad\n")
        self.assertEqual(len(t2.list_jobs(include_closed=True)), before)  # all or nothing

    def test_backup_includes_contacts_and_prep(self):
        t, _ = make()
        j = t.add_job({"company": "A", "title": "x"})
        t.add_contact(j["id"], {"name": "Dana"}); t.add_default_prep(j["id"])
        snap = json.loads(json.dumps(t.backup()))
        t2, _ = make(); t2.restore(snap)
        got = t2.get_job(j["id"])
        self.assertEqual(got["contacts"][0]["name"], "Dana")
        self.assertEqual(len(got["prep"]), len(t.get_job(j["id"])["prep"]))
        snap["contacts"][0]["job_id"] = 999
        with self.assertRaises(ValidationError): t2.restore(snap)
