"""Browser tests for the Google Play build (dist/play): no service worker is registered, the
server-connect / install-instructions / API-key / sync panels are hidden (N-5, Req. 6), and the
free-tier Pro gate fires at addJob in the Play build only (Req. 5). Skipped when Playwright or
the built files are missing."""
import functools, http.server, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAY = ROOT / "dist" / "play"
PHONE = ROOT / "dist" / "app"
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


class Static:
    """Serves a folder like a Capacitor host would: plain files, nothing else (no /api)."""

    def __init__(self, folder):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
        handler.log_message = lambda *a: None
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()


def add_jobs(pg, n, start=0):
    """Add n jobs through the in-page engine (same /api the UI uses)."""
    return pg.evaluate("""([n, start]) => Promise.all(
      Array.from({length: n}, (_, i) => fetch('/api/jobs', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({company: 'Co ' + (start + i), title: 'Role'})})
        .then(r => r.json().then(j => ({status: r.status, job: j}))))
    )""", [n, start])


def job_count(pg):
    return pg.evaluate("fetch('/api/jobs?closed=1').then(r => r.json()).then(j => j.length)")


def import_csv(pg, csv_text):
    """POST a CSV to /api/import through the in-page engine; returns {status, body}."""
    return pg.evaluate("""csv => fetch('/api/import', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({csv})})
      .then(r => r.json().then(j => ({status: r.status, body: j})))""", csv_text)


def backup_counts(pg):
    """Job/event counts and company names from the app's own backup (the persisted dataset)."""
    return pg.evaluate("""fetch('/api/backup').then(r => r.text()).then(t => {
      const b = JSON.parse(t);
      return {jobs: b.jobs.length, events: b.events.length,
              companies: b.jobs.map(j => j.company)}; })""")


def csv_of(n, prefix="Imp"):
    return "company,title\n" + "".join(f"{prefix} Co {i},Role {i}\n" for i in range(n))


@unittest.skipUnless(sync_playwright and (PLAY / "index.html").exists(), "needs playwright and `npm run build`")
class Play(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=__import__("os").environ.get("CHROMIUM") or None)
        cls.site = Static(PLAY)

    @classmethod
    def tearDownClass(cls):
        cls.site.stop(); cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.errors = []
        self.ctx = self.browser.new_context(viewport={"width": 390, "height": 844}, service_workers="allow")

    def tearDown(self):
        self.ctx.close()

    def open(self):
        pg = self.ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(self.site.base)
        pg.wait_for_selector(".board")
        return pg

    def test_no_service_worker_registered(self):
        pg = self.open()
        pg.wait_for_timeout(500)
        regs = pg.evaluate("navigator.serviceWorker.getRegistrations().then(r => r.length)")
        self.assertEqual(regs, 0, "the Play build must not register a service worker")
        self.assertFalse(self.errors, self.errors)

    def test_consumer_panels_hidden(self):
        pg = self.open()
        pg.click("[data-view=data]")
        pg.wait_for_selector("text=Your data")
        body = pg.evaluate("document.body.innerText")
        for gone in ["Where your data is kept", "Wording suggestions", "Install on your phone",
                     "Sync with Google Drive", "Turn on sync"]:
            self.assertNotIn(gone, body, f"panel should be hidden in the Play build: {gone}")
        for kept in ["Your data", "Follow-up reminders", "Restore from a backup"]:
            self.assertIn(kept, body, f"panel should stay in the Play build: {kept}")
        self.assertFalse(self.errors, self.errors)

    def test_free_tier_cap_at_add_job(self):
        pg = self.open()
        res = add_jobs(pg, 20)
        self.assertTrue(all(r["status"] == 201 for r in res), res)
        # the 21st add is rejected with the free-tier message
        pg.click("[data-add]")
        pg.fill("input[name=company]", "Over the cap"); pg.fill("input[name=title]", "Role")
        pg.click("form[data-form=add] button[type=submit]")
        pg.wait_for_selector(".toast.on")
        self.assertIn("free tier", pg.evaluate("document.querySelector('.toast').textContent").lower())
        self.assertEqual(job_count(pg), 20)
        self.assertFalse(self.errors, self.errors)

    def test_cap_counts_closed_jobs(self):
        pg = self.open()
        res = add_jobs(pg, 20)
        first = res[0]["job"]["id"]
        closed = pg.evaluate("""id => fetch('/api/jobs/' + id + '/close',
          {method: 'POST', headers: {'Content-Type': 'application/json'},
           body: JSON.stringify({reason: 'rejected'})}).then(r => r.status)""", first)
        self.assertEqual(closed, 200)
        # 19 active, 20 tracked: closing does not free a slot
        denied = pg.evaluate("""fetch('/api/jobs', {method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({company: 'Sneaky', title: 'Role'})})
          .then(r => r.json().then(j => ({status: r.status, error: j.error})))""")
        self.assertEqual(denied["status"], 400)
        self.assertIn("free tier", denied["error"].lower())
        self.assertFalse(self.errors, self.errors)

    def test_board_shows_free_tier_counter(self):
        pg = self.open()
        add_jobs(pg, 3)
        pg.reload(); pg.wait_for_selector(".board")
        self.assertIn("free tier: 3/20", pg.evaluate("document.querySelector('.toolbar').innerText").lower())

    def test_csv_import_over_cap_is_atomic_19_plus_3(self):
        # Issue #24: 19 tracked + a 3-row import must reject BEFORE mutating anything.
        pg = self.open()
        self.assertTrue(all(r["status"] == 201 for r in add_jobs(pg, 19)))
        before = backup_counts(pg)
        self.assertEqual((before["jobs"], before["events"]), (19, 19))
        res = import_csv(pg, csv_of(3))
        self.assertEqual(res["status"], 400, res)
        self.assertIn("free tier", res["body"]["error"].lower())
        after = backup_counts(pg)
        self.assertEqual((after["jobs"], after["events"]), (19, 19),
                         "a rejected import must leave jobs and events untouched")
        self.assertFalse(any(c.startswith("Imp") for c in after["companies"]))
        # Nothing hidden in memory either: a reload and a later successful add
        # (the 20th slot) must not persist any of the rejected rows.
        pg.reload(); pg.wait_for_selector(".board")
        self.assertEqual(job_count(pg), 19)
        self.assertTrue(all(r["status"] == 201 for r in add_jobs(pg, 1, start=100)))
        after2 = backup_counts(pg)
        self.assertEqual(after2["jobs"], 20)
        self.assertFalse(any(c.startswith("Imp") for c in after2["companies"]))
        self.assertFalse(self.errors, self.errors)

    def test_csv_import_over_cap_is_atomic_from_zero_21(self):
        pg = self.open()
        res = import_csv(pg, csv_of(21))
        self.assertEqual(res["status"], 400, res)
        self.assertIn("free tier", res["body"]["error"].lower())
        counts = backup_counts(pg)
        self.assertEqual((counts["jobs"], counts["events"]), (0, 0))
        pg.reload(); pg.wait_for_selector(".board")
        self.assertEqual(job_count(pg), 0)
        self.assertFalse(self.errors, self.errors)

    def test_csv_import_exactly_remaining_capacity_succeeds(self):
        pg = self.open()
        self.assertTrue(all(r["status"] == 201 for r in add_jobs(pg, 19)))
        res = import_csv(pg, csv_of(1))
        self.assertEqual(res["status"], 201, res)
        self.assertEqual(res["body"]["imported"], 1)
        self.assertEqual(job_count(pg), 20)
        self.assertFalse(self.errors, self.errors)

    def test_csv_import_malformed_csv_changes_nothing(self):
        pg = self.open()
        self.assertTrue(all(r["status"] == 201 for r in add_jobs(pg, 2)))
        bad = "company,title,status\nGood Co,Role,active\nBad Co,Role,bogus\n"
        res = import_csv(pg, bad)
        self.assertEqual(res["status"], 400, res)
        counts = backup_counts(pg)
        self.assertEqual((counts["jobs"], counts["events"]), (2, 2))
        self.assertFalse(any(c == "Good Co" for c in counts["companies"]))
        self.assertFalse(self.errors, self.errors)

    def test_csv_import_unrestricted_in_phone_build(self):
        # The phone PWA shares the engine but is never gated: a 21-row import succeeds whole.
        site = Static(PHONE)
        try:
            pg = self.ctx.new_page()
            pg.on("pageerror", lambda e: self.errors.append(str(e)))
            pg.goto(site.base); pg.wait_for_selector(".board")
            res = import_csv(pg, csv_of(21))
            self.assertEqual(res["status"], 201, res)
            self.assertEqual(res["body"]["imported"], 21)
            self.assertEqual(job_count(pg), 21)
            self.assertFalse(self.errors, self.errors)
        finally:
            site.stop()

    def test_gate_is_play_only(self):
        # the phone PWA build shares the engine but is never gated
        site = Static(PHONE)
        try:
            pg = self.ctx.new_page()
            pg.on("pageerror", lambda e: self.errors.append(str(e)))
            pg.goto(site.base); pg.wait_for_selector(".board")
            res = add_jobs(pg, 21)
            self.assertTrue(all(r["status"] == 201 for r in res), res)
            self.assertEqual(job_count(pg), 21)
            self.assertNotIn("Free tier:", pg.evaluate("document.querySelector('.toolbar').innerText"))
            self.assertFalse(self.errors, self.errors)
        finally:
            site.stop()


PRIVACY = ROOT / "dist" / "app" / "privacy.html"


@unittest.skipUnless(PRIVACY.exists(), "needs `npm run build`")
class PrivacyPage(unittest.TestCase):
    """The Play listing's privacy policy must ship with the hosted phone app
    (Pages uploads dist/app, so it lands at /jobledger/privacy.html). §6 item 13."""

    def test_published_and_names_egress_paths(self):
        text = PRIVACY.read_text(encoding="utf-8")
        for claim in [
            "No ads, no analytics, no tracking",
            "stored on your phone",
            "Add to Google Calendar",   # the one user-initiated network egress in the Play build
            "no Google Drive sync",      # Req. 6: hidden in v1
            "no wording-suggestion requests",  # N-5: API-key panel removed from Play
        ]:
            self.assertIn(claim, text, f"privacy page must state: {claim}")
        self.assertNotIn("AI-powered", text)
