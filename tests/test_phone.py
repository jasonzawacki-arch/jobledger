"""Browser tests for the phone app (dist/app, served as plain static files with no JobLedger server) and the
demo preview. Skipped when Playwright or the built files are missing."""
import functools, http.server, json, os, tempfile, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "app"
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from jobledger.db import Database
from jobledger.service import Tracker


class Static:
    """Serves a folder like GitHub Pages would: plain files, nothing else (no /api)."""

    def __init__(self, folder):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
        handler.log_message = lambda *a: None
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()


@unittest.skipUnless(sync_playwright and (APP / "index.html").exists(), "needs playwright and `npm run build`")
class Phone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        cls.site = Static(APP)

    @classmethod
    def tearDownClass(cls):
        cls.site.stop(); cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.errors = []
        self.ctx = self.browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True, service_workers="allow")
        self.api_calls = []
        # the phone app must never call a JobLedger server
        self.ctx.route("**/api/**", lambda route: (self.api_calls.append(route.request.url), route.abort()))

    def tearDown(self):
        self.ctx.close()

    def open(self):
        pg = self.ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(self.site.base)
        pg.wait_for_selector(".board")
        return pg

    def add(self, pg, company, title, stage="Applied"):
        pg.click("[data-add]")
        pg.fill("input[name=company]", company); pg.fill("input[name=title]", title)
        pg.select_option("select[name=stage]", label=stage)
        pg.click("form[data-form=add] button[type=submit]")
        pg.wait_for_selector(".hero h2")
        pg.click("[data-go=board]")

    def test_data_is_saved_on_the_phone(self):
        pg = self.open()
        pg.wait_for_selector("text=Nothing tracked yet")
        self.assertEqual(pg.locator("[data-fill-link]").count(), 0)  # needs the server
        self.add(pg, "Initech", "Data Analyst")
        pg.click(".card >> text=Initech")
        pg.fill("textarea[name=text]", "Spoke with Dana"); pg.click("[data-note-form] button")
        pg.wait_for_selector(".tl >> text=Spoke with Dana")
        pg.fill("[data-interview-form] input[name=starts_at]", "2031-05-06T14:30"); pg.fill("[data-interview-form] input[name=label]", "Panel")
        pg.click("[data-interview-form] button[type=submit]"); pg.wait_for_selector("text=Panel")
        pg.wait_for_timeout(300)  # let the save finish
        pg.reload(); pg.wait_for_selector(".board .card")
        self.assertIn("Initech", pg.inner_text(".board"))
        pg.click(".card >> text=Initech")
        pg.wait_for_selector(".tl >> text=Spoke with Dana")
        self.assertIn("Panel", pg.inner_text("section:has(h3:text('Interviews'))"))
        pg.click("[data-view=data]")
        self.assertIn("saved on this device", pg.inner_text("section:has(h3:text('Your data'))"))
        self.assertEqual((self.errors, self.api_calls), ([], []))

    def test_matcher_and_search_run_on_the_phone(self):
        pg = self.open()
        pg.click("[data-view=data]")
        pg.fill("textarea[name=text]", "- Built weekly sales dashboards in Excel and SQL\n- Wrote Python scripts to clean data")
        pg.click("[data-resume-form] button[type=submit]")
        pg.wait_for_selector("#toast >> text=Resume saved")
        pg.click("[data-view=board]")
        self.add(pg, "Initech", "Analyst")
        pg.click(".card >> text=Initech")
        pg.click("[data-edit]")
        pg.fill("textarea[name=description]", "Requirements:\n- Strong SQL skills\n- Experience with Tableau")
        pg.click("form[data-form=edit] button[type=submit]")
        pg.wait_for_selector(".req")
        reqs = pg.inner_text(".panel:has(h3:text('How the posting matches'))")
        self.assertIn("Strong SQL skills", reqs); self.assertIn("Match", reqs); self.assertIn("Gap", reqs)
        pg.click("[data-go=board]")
        pg.fill("[data-search]", "tableau")
        pg.wait_for_selector(".search-count >> text=1 job match")
        self.assertEqual((self.errors, self.api_calls), ([], []))

    def test_backup_moves_between_phone_and_server(self):
        pg = self.open()
        self.add(pg, "Initech", "Analyst")
        pg.click(".card >> text=Initech")
        pg.fill("[data-interview-form] input[name=starts_at]", "2031-05-06T14:30")
        pg.click("[data-interview-form] button[type=submit]"); pg.wait_for_selector("section:has(h3:text('Interviews')) .fu")
        pg.click("[data-view=data]")
        with pg.expect_download() as dl:
            pg.click("[data-backup]")
        backup = json.loads(Path(dl.value.path()).read_text())
        # the server can restore what the phone saved
        t = Tracker(Database())
        t.restore(backup)
        self.assertEqual([j["company"] for j in t.list_jobs()], ["Initech"])
        self.assertEqual(t.get_job(t.list_jobs()[0]["id"])["interviews"][0]["starts_at"], "2031-05-06T14:30")
        # and the phone can restore what the server saved
        t2 = Tracker(Database())
        j = t2.add_job({"company": "Hooli", "title": "PM", "stage": 1, "source": "Referral"})
        t2.add_contact(j["id"], {"name": "Dana", "email": "dana@hooli.example"})
        t2.add_default_prep(j["id"])
        f = Path(tempfile.mkdtemp()) / "server-backup.json"
        f.write_text(json.dumps(t2.backup()))
        pg.set_input_files("[data-restore-file]", str(f))
        pg.click("[data-restore-yes]")
        pg.click("[data-view=board]")
        pg.wait_for_selector(".card >> text=Hooli")
        self.assertNotIn("Initech", pg.inner_text(".board"))
        pg.click(".card >> text=Hooli")
        self.assertIn("Dana", pg.inner_text("section:has(h3:text('People'))"))
        self.assertIn("via Referral", pg.inner_text(".hero"))
        self.assertEqual((self.errors, self.api_calls), ([], []))

    def test_csv_and_calendar_files(self):
        pg = self.open()
        f = Path(tempfile.mkdtemp()) / "jobs.csv"
        f.write_text('company,title,stage,status,source\nAcme,"Analyst, Data",Interview,active,LinkedIn\nGlobex,PM,Applied,rejected,\n')
        pg.click("[data-view=data]")
        pg.set_input_files("[data-import-file]", str(f))
        pg.wait_for_selector("#toast >> text=Imported 2 jobs")
        with pg.expect_download() as dl:
            pg.click("[data-export-csv]")
        out = Path(dl.value.path()).read_text()
        self.assertIn('"Analyst, Data"', out); self.assertIn("rejected", out)
        t = Tracker(Database())  # the server reads the phone's export
        self.assertEqual(t.import_csv(out)["imported"], 2)
        with pg.expect_download() as dl:
            pg.click("[data-ics-all]")
        ics = Path(dl.value.path()).read_text()
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertRegex(ics, r"DTSTART:\d{8}T\d{6}Z")  # an exact moment, so any calendar app places it right
        self.assertEqual((self.errors, self.api_calls), ([], []))

    def test_wording_suggestion_goes_straight_to_anthropic(self):
        seen = {}

        def anthropic(route):
            seen["headers"] = route.request.headers
            seen["body"] = json.loads(route.request.post_data)
            route.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"},
                          body=json.dumps({"content": [{"type": "text", "text": "Built weekly dashboards in Excel and SQL for the sales team."}]}))
        self.ctx.route("https://api.anthropic.com/**", anthropic)
        pg = self.open()
        pg.click("[data-view=data]")
        pg.fill("textarea[name=text]", "- Built weekly sales dashboards in Excel and SQL")
        pg.click("[data-resume-form] button[type=submit]")
        pg.wait_for_selector("#toast >> text=Resume saved")
        pg.fill("[data-key-form] input[name=key]", "sk-ant-test-key"); pg.click("[data-key-form] button[type=submit]")
        pg.wait_for_selector("#toast >> text=Key saved")
        pg.click("[data-view=board]")
        self.add(pg, "Initech", "Analyst")
        pg.click(".card >> text=Initech"); pg.click("[data-edit]")
        pg.fill("textarea[name=description]", "Requirements:\n- Build dashboards in SQL")
        pg.click("form[data-form=edit] button[type=submit]")
        pg.click("[data-suggest]")
        pg.wait_for_selector(".sug >> text=for the sales team")
        self.assertEqual(seen["headers"]["x-api-key"], "sk-ant-test-key")
        self.assertIn("Resume line: Built weekly sales dashboards in Excel and SQL", seen["body"]["messages"][0]["content"])
        self.assertEqual((self.errors, self.api_calls), ([], []))

    def test_works_offline_after_first_visit(self):
        pg = self.open()
        self.add(pg, "Initech", "Analyst")
        pg.evaluate("navigator.serviceWorker.ready")
        pg.wait_for_function("navigator.serviceWorker.controller !== null || (location.reload(), false)", timeout=10000)
        pg.wait_for_timeout(300)
        self.ctx.set_offline(True)
        pg.reload(); pg.wait_for_selector(".card >> text=Initech")
        self.ctx.set_offline(False)
        self.assertEqual(self.api_calls, [])

    def test_connect_to_your_server_and_back(self):
        from tests.test_api import Server
        site = self.site.base.rstrip("/")
        srv = Server(token="pw", origins=[site])
        try:
            pg = self.open()
            self.ctx.unroute("**/api/**")  # this test talks to a real server on purpose
            self.add(pg, "OnPhone", "Kept locally")
            pg.click("[data-view=data]")
            self.assertIn(site, pg.inner_text("[data-where-form]"))  # tells you what to put in JL_ORIGINS
            pg.fill("[data-where-form] input[name=server]", "http://localhost:1")  # nothing there
            pg.click("[data-where-form] button[type=submit]")
            pg.wait_for_selector("[data-where-form] .err >> text=No JobLedger server answered")
            pg.fill("[data-where-form] input[name=server]", srv.base.replace("127.0.0.1", "localhost"))
            pg.click("[data-where-form] button[type=submit]")
            pg.wait_for_selector("[data-token-form]")  # the server asks for its password
            pg.fill("input[name=token]", "pw"); pg.click("[data-token-form] button[type=submit]")
            pg.wait_for_selector(".board")
            self.assertNotIn("OnPhone", pg.inner_text(".board"))
            pg.click("[data-add]")
            self.assertEqual(pg.locator("[data-fill-link]").count(), 1)  # the server can read links
            pg.click("[data-cancel-form]")
            self.add(pg, "OnServer", "Saved remotely")
            self.assertEqual([j["company"] for j in srv.app.state.tracker.list_jobs()], ["OnServer"])
            pg.click("[data-view=data]")
            self.assertIn("Using your JobLedger server", pg.inner_text("[data-where-form]"))
            pg.click("[data-use-phone]")
            pg.wait_for_selector(".card >> text=OnPhone")
            self.assertNotIn("OnServer", pg.inner_text(".board"))
            self.assertEqual(self.errors, [])
        finally:
            srv.stop()

    def test_hosted_demo_leaves_the_app_alone(self):
        if not (APP / "demo" / "index.html").exists():
            self.skipTest("run python preview/build_preview.py")
        pg = self.open()
        self.add(pg, "Initech", "Analyst")
        pg.evaluate("navigator.serviceWorker.ready")
        pg.goto(self.site.base + "demo/"); pg.wait_for_selector(".pv")
        self.assertIn("Northwind", pg.inner_text(".board"))  # sample data, not mine
        self.ctx.set_offline(True)
        pg.goto(self.site.base); pg.wait_for_selector(".card >> text=Initech")  # the offline copy is still the app
        self.ctx.set_offline(False)
        self.assertNotIn("Northwind", pg.inner_text(".board"))
        self.assertEqual(self.api_calls, [])

    def test_install_files(self):
        pg = self.open()
        manifest = pg.evaluate("fetch('manifest.webmanifest').then(r => r.json())")
        self.assertEqual((manifest["start_url"], manifest["scope"], manifest["display"]), ("./", "./", "standalone"))
        self.assertTrue(all(not i["src"].startswith("/") for i in manifest["icons"]))  # works under any folder


@unittest.skipUnless(sync_playwright and (ROOT / "dist" / "preview.html").exists(), "needs playwright and the preview build")
class Demo(unittest.TestCase):
    def test_demo_runs_from_a_single_file(self):
        html = (ROOT / "dist" / "preview.html").read_text()
        f = Path(tempfile.mkdtemp()) / "demo.html"
        f.write_text('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>' + html + "</body></html>")
        errors = []
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
            pg = b.new_page(viewport={"width": 390, "height": 844})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f.as_uri()); pg.wait_for_selector(".board .card")
            self.assertIn("Sample data", pg.inner_text(".pv"))
            pg.click(".card >> text=Northwind"); pg.wait_for_selector(".req")  # the matcher runs in the page
            pg.click("[data-edit]")
            pg.fill("textarea[name=description]", "Requirements:\n- Strong SQL\n- Kubernetes")
            pg.click("form[data-form=edit] button[type=submit]")
            pg.wait_for_selector(".req >> text=Kubernetes")  # edited postings are matched too
            pg.click("[data-view=data]"); pg.click("[data-backup]")
            pg.wait_for_selector("#toast >> text=needs the full app")
            self.assertEqual(pg.evaluate("document.documentElement.scrollWidth"), 390)
            b.close()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
