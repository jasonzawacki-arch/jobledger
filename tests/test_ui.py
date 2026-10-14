"""Browser tests: drive the real UI against a real server. Skipped when Playwright or the built UI is missing."""
import os, tempfile, unittest
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist" / "index.html"
SHOTS = os.environ.get("JL_SHOTS")
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from tests.test_api import Server


@unittest.skipUnless(sync_playwright and DIST.exists(), "needs playwright and `npm run build`")
class UI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.s = Server()
        self.errors = []

    def tearDown(self):
        self.s.stop()

    def page(self, w=1280, h=900, scheme="light"):
        ctx = self.browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, accept_downloads=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(self.s.base + "/")
        pg.wait_for_selector(".board")
        return pg

    def add(self, pg, company, title, stage="Applied"):
        pg.click("[data-add]")
        pg.fill("input[name=company]", company); pg.fill("input[name=title]", title)
        pg.select_option("select[name=stage]", label=stage)
        pg.click("form[data-form=add] button[type=submit]")
        pg.wait_for_selector(".hero h2")
        pg.click("[data-go=board]")

    def test_add_move_note_close_delete(self):
        pg = self.page()
        self.add(pg, "Acme Corp", "Data Analyst")
        self.assertEqual(pg.locator("[data-col='1'] .card").count(), 1)
        # open, move by stage track, add note
        pg.click(".card >> text=Acme Corp")
        pg.click("[data-stage='2']")
        pg.wait_for_selector(".tl >> text=Moved from Applied to Screen")
        pg.fill("textarea[name=text]", "Spoke with Dana, send portfolio")
        pg.click("[data-note-form] button")
        pg.wait_for_selector(".tl >> text=Spoke with Dana")
        self.assertIn("Moved from Applied to Screen", pg.inner_text(".tl"))
        # follow-up date
        pg.fill("[data-fu]", "2026-12-01")
        pg.wait_for_selector(".tl >> text=Follow-up set")
        # close
        pg.click("[data-close=ghosted]")
        pg.wait_for_selector(".banner")
        pg.click("[data-go=board]")
        self.assertEqual(pg.locator(".col .card").count(), 0)
        pg.click("[data-toggle-closed]")
        self.assertEqual(pg.locator(".closed-list .card").count(), 1)
        pg.click(".closed-list .card"); pg.click("[data-reopen]")
        pg.wait_for_selector(".banner", state="detached")
        # delete needs confirmation in the page
        pg.click("[data-delete]"); pg.click("[data-delete-no]")
        self.assertTrue(pg.locator("[data-delete]").is_visible())
        pg.click("[data-delete]"); pg.click("[data-delete-yes]")
        pg.wait_for_selector(".board")
        self.assertEqual(pg.locator(".card").count(), 0)
        self.assertEqual(self.errors, [])

    def test_resume_match_flow(self):
        pg = self.page()
        pg.click("[data-add]")
        pg.fill("input[name=company]", "Matchco"); pg.fill("input[name=title]", "Engineer")
        pg.fill("textarea[name=description]", "- Experience building Python APIs\n- Experience with Kubernetes in production")
        pg.click("form[data-form=add] button[type=submit]")
        pg.wait_for_selector(".hero h2")
        self.assertIn("Add your resume first", pg.inner_text(".panel >> text=matches your resume >> xpath=.."))
        pg.click("[data-view=data]")
        pg.fill("[data-resume-form] textarea", "- Built Python APIs with SQLite storage and tests")
        pg.click("[data-resume-form] button")
        pg.wait_for_selector(".toast.on")
        pg.click("[data-view=board]"); pg.click(".card >> text=Matchco")
        pg.wait_for_selector(".req")
        self.assertEqual(pg.locator(".req .pill.good").count(), 1)
        self.assertEqual(pg.locator(".req .pill.bad").count(), 1)
        self.assertIn("Built Python APIs", pg.inner_text(".req .why"))
        self.assertGreaterEqual(pg.locator(".req mark").count(), 1)
        self.assertEqual(self.errors, [])

    def test_contacts_prep_and_draft(self):
        pg = self.page()
        self.add(pg, "Peopleco", "Analyst")
        pg.click(".card >> text=Peopleco"); pg.wait_for_selector("[data-contact-form]")
        pg.fill("[data-contact-form] input[name=name]", "Dana Lee"); pg.fill("[data-contact-form] input[name=email]", "dana@example.com")
        pg.click("[data-contact-form] button")
        pg.wait_for_selector("text=dana@example.com")
        self.assertEqual(pg.locator("a[href='mailto:dana@example.com']").count(), 1)
        pg.click("[data-contacted]"); pg.wait_for_selector("text=last contact")
        pg.click("[data-prep-defaults]"); pg.wait_for_selector("[data-prep]")
        n = pg.locator("[data-prep]").count(); self.assertGreater(n, 3)
        pg.check("[data-prep] >> nth=0")
        pg.wait_for_selector("h3:has-text('Interview prep') .mono:has-text('1/')")
        pg.fill("[data-prep-form] input", "Bring references"); pg.click("[data-prep-form] button")
        pg.wait_for_selector("text=Bring references")
        pg.click("[data-draft]")
        body = pg.input_value("[data-draft-body]")
        self.assertIn("Hi Dana", body); self.assertIn("Peopleco", pg.input_value("[data-draft-subject]"))
        self.assertIn("mailto:dana@example.com", pg.get_attribute("a:has-text('Open in email app')", "href"))
        pg.click("[data-draft-close]")
        self.assertEqual(self.errors, [])

    def test_csv_import(self):
        pg = self.page()
        f = Path(tempfile.mkdtemp()) / "jobs.csv"; f.write_text("company,title,stage\nCsvco,Analyst,Screen\nOther Inc,Engineer,\n")
        pg.click("[data-view=data]"); pg.set_input_files("[data-import-file]", str(f))
        pg.wait_for_selector(".toast.on")
        pg.click("[data-view=board]")
        self.assertEqual(pg.locator("[data-col='2'] .card >> text=Csvco").count(), 1)
        self.assertEqual(pg.locator("[data-col='0'] .card >> text=Other Inc").count(), 1)

    def test_suggest_wording_with_user_key(self):
        self.s.stop()
        seen = []
        def fake(key, source, req):
            seen.append(key); return "Built a Python REST API backed by SQLite, with an audit log (side project)"
        self.s = Server(rewriter=fake)
        pg = self.page()
        pg.click("[data-add]")
        pg.fill("input[name=company]", "Aico"); pg.fill("input[name=title]", "Dev")
        pg.fill("textarea[name=description]", "- Experience building Python REST APIs")
        pg.click("form[data-form=add] button[type=submit]"); pg.wait_for_selector(".hero h2")
        pg.click("[data-view=data]")
        pg.fill("[data-resume-form] textarea", "- Built a Python REST API with SQLite storage and an audit log (side project)")
        pg.click("[data-resume-form] button"); pg.wait_for_selector(".toast.on")
        pg.click("[data-view=board]"); pg.click(".card >> text=Aico"); pg.wait_for_selector(".req")
        self.assertEqual(pg.locator("[data-suggest]").count(), 0)  # no key, no button
        pg.click("[data-view=data]")
        pg.fill("[data-key-form] input", "not-a-key"); pg.click("[data-key-form] button"); pg.wait_for_selector(".err, .toast.on")
        pg.fill("[data-key-form] input", "sk-ant-test123"); pg.click("[data-key-form] button")
        pg.click("[data-view=board]"); pg.click(".card >> text=Aico"); pg.wait_for_selector("[data-suggest]")
        pg.click("[data-suggest]")
        pg.wait_for_selector("[data-copy-suggestion]")
        self.assertEqual(seen, ["sk-ant-test123"])
        self.assertIn("your line", pg.inner_text(".req .sug").lower())
        self.assertGreaterEqual(pg.locator(".req .sug mark").count(), 1)
        self.assertEqual(self.errors, [])

    def test_installable_files_served(self):
        import urllib.request, json as _j
        m = _j.loads(urllib.request.urlopen(self.s.base + "/manifest.webmanifest").read())
        self.assertEqual(m["display"], "standalone")
        for icon in m["icons"]:
            r = urllib.request.urlopen(self.s.base + icon["src"]); self.assertEqual(r.headers["Content-Type"], "image/png")
        sw = urllib.request.urlopen(self.s.base + "/sw.js"); self.assertIn("javascript", sw.headers["Content-Type"])
        self.assertNotIn("/api/", sw.read().decode().split("startsWith")[0])  # never caches data
        with self.assertRaises(Exception): urllib.request.urlopen(self.s.base + "/secret.txt")

    def test_drag_and_drop(self):
        pg = self.page()
        self.add(pg, "Dragco", "Analyst")
        pg.drag_and_drop(".card >> text=Dragco", "[data-col='3']")
        pg.wait_for_selector("[data-col='3'] .card")
        self.assertEqual(pg.locator("[data-col='3'] .card").count(), 1)

    def test_validation_error_shown(self):
        pg = self.page()
        pg.click("[data-add]")
        pg.fill("input[name=company]", "X"); pg.fill("input[name=title]", "Y"); pg.fill("input[name=posted_on]", "2026-01-01")
        pg.evaluate("document.querySelector('input[name=url]').type='text'")
        pg.fill("input[name=url]", "ftp://nope")
        pg.click("form[data-form=add] button[type=submit]")
        pg.wait_for_selector(".err")
        self.assertIn("http", pg.inner_text(".err"))

    def test_markup_in_text_is_escaped(self):
        pg = self.page()
        self.add(pg, "<img src=x onerror=window.__pwned=1>", "<b>t</b>")
        self.assertIsNone(pg.evaluate("window.__pwned"))
        self.assertIn("<img", pg.inner_text(".card"))

    def test_pulse_and_backup_restore(self):
        pg = self.page()
        self.add(pg, "Alpha", "A"); self.add(pg, "Beta", "B")
        pg.click("[data-view=pulse]")
        pg.wait_for_selector(".tiles")
        self.assertIn("2", pg.inner_text(".tiles"))
        pg.click("[data-view=data]")
        with pg.expect_download() as dl:
            pg.click("[data-backup]")
        path = dl.value.path()
        pg.click("[data-view=board]")
        pg.click(".card >> text=Alpha"); pg.click("[data-delete]"); pg.click("[data-delete-yes]")
        pg.wait_for_selector(".board")
        self.assertEqual(pg.locator(".card").count(), 1)
        pg.click("[data-view=data]")
        pg.set_input_files("[data-restore-file]", path)
        pg.click("[data-restore-yes]")
        pg.wait_for_selector(".toast.on")
        pg.click("[data-view=board]")
        self.assertEqual(pg.locator(".card").count(), 2)
        pg.click("[data-view=data]")
        with pg.expect_download() as dl:
            pg.click("[data-export-csv]")
        self.assertTrue(Path(dl.value.path()).read_text().startswith("company"))

    def test_bad_backup_file_rejected(self):
        pg = self.page()
        pg.click("[data-view=data]")
        bad = Path(tempfile.mkdtemp()) / "x.json"; bad.write_text('{"hello": 1}')
        pg.set_input_files("[data-restore-file]", str(bad))
        pg.wait_for_selector(".err")
        self.assertEqual(pg.locator("[data-restore-yes]").count(), 0)

    def test_phone_width_has_no_sideways_page_scroll(self):
        pg = self.page(w=390, h=800)
        self.add(pg, "Phone Co", "Role")
        for v in ("board", "job", "pulse", "data"):
            pg.click(f"[data-view={v}]")
            self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), 391, v)
        self.assertEqual(self.errors, [])

    def test_dark_theme_applies(self):
        pg = self.page(scheme="dark")
        bg = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()")
        self.assertEqual(bg, "#0c111b")
        pg.click("[data-view=data]"); pg.select_option("[data-theme]", "light")
        self.assertEqual(pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()"), "#e9edf4")

    def test_token_gate(self):
        self.s.stop()
        self.s = Server(token="pw")
        ctx = self.browser.new_context()
        pg = ctx.new_page()
        pg.goto(self.s.base + "/")
        pg.wait_for_selector("[data-token-form]")
        pg.fill("input[name=token]", "pw"); pg.click("[data-token-form] button")
        pg.wait_for_selector(".board")

    @unittest.skipUnless(SHOTS, "set JL_SHOTS=dir to save screenshots")
    def test_screenshots(self):
        from jobledger.__main__ import load_demo
        load_demo(self.s.app.state.tracker)
        for name, w, h, sch in (("desktop-light", 1280, 900, "light"), ("desktop-dark", 1280, 900, "dark"), ("phone-dark", 390, 844, "dark")):
            pg = self.page(w, h, sch)
            pg.screenshot(path=f"{SHOTS}/{name}-board.png")
            pg.click("[data-open='1']"); pg.wait_for_selector(".hero")
            pg.screenshot(path=f"{SHOTS}/{name}-job.png", full_page=True)
            pg.click("[data-view=pulse]")
            pg.screenshot(path=f"{SHOTS}/{name}-pulse.png", full_page=True)


if __name__ == "__main__":
    unittest.main()
