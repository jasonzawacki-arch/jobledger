import csv, io, json, unittest
from jobledger.db import Database
from jobledger.service import DAY, NotFound, Tracker, ValidationError

T0 = 1_800_000_000.0  # fixed start


class Clock:
    def __init__(self): self.t = T0
    def __call__(self): return self.t
    def advance(self, days): self.t += days * DAY


def make():
    c = Clock()
    return Tracker(Database(), c), c


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
        d = Database(); d.conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(Exception): d.migrate()


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
