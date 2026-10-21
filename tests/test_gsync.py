"""Google Drive sync in the phone app, against a stand-in for Google (sign-in and Drive are faked in the browser):
not offered without a client ID or in the demo, turn on, jobs sync between devices both ways, deletes travel,
records from two devices with the same id are renumbered on the first Combine, conflicting edits resolve
per-record last-writer-wins (ties go to the remote copy, as mergeItems() implements), and expired access shows
a Sync button. Skipped when Playwright or the built files are missing.

Standalone-run prerequisite: the demo check in test_not_offered_without_client_id_or_in_demo loads
`demo/` served next to the phone app, and that folder only exists after `python preview/build_preview.py`
(which itself needs `npm run build` first). Run `npm run build && python preview/build_preview.py` before
running this module on its own. CI's browser job does exactly that, then runs this module with the others."""
import json, os, re, unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from tests.test_phone import APP, ROOT, Static, sync_playwright

FAKE_GSI = """window.google = { accounts: { oauth2: {
  initTokenClient(cfg) { return { requestAccessToken(o) { window.__gsi = (window.__gsi || 0) + 1;
    setTimeout(() => cfg.callback({ access_token: 'tok' + Date.now(), expires_in: window.__expIn || 3600 }), 20); } }; },
  revoke(t, cb) { cb && cb(); } } } };"""
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "authorization, content-type", "access-control-allow-methods": "GET, POST, PATCH, OPTIONS"}


class FakeDrive:
    def __init__(self):
        self.files = {}

    def items(self):
        f = [x for x in self.files.values() if x["name"] == "jobledger-sync.json"]
        return json.loads(f[0]["body"])["items"] if f else {}

    def route(self, route):
        req = route.request
        u = urlparse(req.url)
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        if not (req.headers.get("authorization") or "").startswith("Bearer tok"):
            return route.fulfill(status=401, headers=CORS, body="{}")
        j = lambda o: route.fulfill(status=200, headers={**CORS, "content-type": "application/json"}, body=json.dumps(o))
        if u.path == "/oauth2/v3/userinfo":
            return j({"email": "tester@example.com"})
        if u.path == "/drive/v3/files" and req.method == "GET":
            name = re.search(r"name='([^']+)'", parse_qs(u.query)["q"][0]).group(1)
            return j({"files": [{"id": i} for i, f in self.files.items() if f["name"] == name]})
        m = re.match(r"^/drive/v3/files/([^/]+)$", u.path)
        if m and req.method == "GET":
            return route.fulfill(status=200, headers={**CORS, "content-type": "application/json"}, body=self.files[m.group(1)]["body"])
        m = re.match(r"^/upload/drive/v3/files/([^/]+)$", u.path)
        if m and req.method == "PATCH":
            self.files[m.group(1)]["body"] = req.post_data
            return j({"id": m.group(1)})
        if u.path == "/upload/drive/v3/files" and req.method == "POST":
            b = re.search(r"boundary=(\S+)", req.headers["content-type"]).group(1)
            parts = [p for p in req.post_data.split("--" + b) if p.strip() and p.strip() != "--"]
            meta = json.loads(parts[0].split("\r\n\r\n", 1)[1].strip())
            assert meta["parents"] == ["appDataFolder"], meta
            fid = "f%d" % (len(self.files) + 1)
            self.files[fid] = {"name": meta["name"], "body": parts[1].split("\r\n\r\n", 1)[1].rstrip("\r\n")}
            return j({"id": fid})
        return route.fulfill(status=404, headers=CORS, body="{}")


@unittest.skipUnless(sync_playwright and (APP / "index.html").exists(), "needs playwright and `npm run build`")
class GoogleDriveSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        cls.site = Static(APP)

    @classmethod
    def tearDownClass(cls):
        cls.site.stop(); cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.errors, self.ctxs, self.drive = [], [], FakeDrive()

    def tearDown(self):
        for c in self.ctxs:
            c.close()

    def device(self, client=True, exp=3600, base=None, pin=None):
        ctx = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.ctxs.append(ctx)
        init = f"window.GSYNC_CLIENT_ID='test-client';window.__expIn={exp};" if client else "window.GSYNC_CLIENT_ID='';"
        if pin is not None:
            init = f"window.__pin={pin};Date.now=()=>window.__pin;" + init  # frozen clock, advanced by hand
        ctx.add_init_script(init)
        ctx.route("https://accounts.google.com/gsi/client", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_GSI))
        ctx.route(re.compile(r"^https://www\.googleapis\.com/.*"), self.drive.route)
        ctx.route("**/api/**", lambda r: r.abort())  # never a JobLedger server
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(base or self.site.base)
        pg.wait_for_selector(".pv" if base else ".board")
        return pg

    def add(self, pg, company):
        pg.click("[data-view=board]")
        pg.click("[data-add]")
        pg.fill("input[name=company]", company); pg.fill("input[name=title]", "Analyst")
        pg.click("form[data-form=add] button[type=submit]")
        pg.wait_for_selector(".hero h2")
        pg.click("[data-go=board]")

    def board(self, pg):
        pg.click("[data-view=board]"); pg.wait_for_timeout(200)
        return pg.inner_text(".board")

    def turn_on(self, pg):
        pg.click("[data-view=data]")
        pg.click("[data-gs-on]"); pg.wait_for_timeout(1200)

    def sync_now(self, pg):
        pg.click("[data-view=data]")
        pg.click("[data-gs-now]"); pg.wait_for_timeout(1200)

    def set_remote(self, key, t, v):
        """Rewrite one record in the fake Drive file, as if another device had synced it."""
        for f in self.drive.files.values():
            if f["name"] == "jobledger-sync.json":
                doc = json.loads(f["body"])
                doc["items"][key] = {"t": t, "v": v}
                f["body"] = json.dumps(doc)
                return
        raise AssertionError("no sync file in the fake Drive")

    def test_not_offered_without_client_id_or_in_demo(self):
        pg = self.device(client=False)
        pg.click("[data-view=data]")
        self.assertEqual(pg.locator("[data-gs-on]").count(), 0)
        demo = self.device(base=self.site.base + "demo/")  # the hosted demo, next to the phone app
        demo.wait_for_selector(".board"); demo.click("[data-view=data]")
        self.assertEqual(demo.locator("[data-gs-on]").count(), 0)  # the demo never asks anyone to sign in
        self.assertEqual(self.errors, [])

    def test_two_devices(self):
        a = self.device()
        self.add(a, "Acme"); self.add(a, "Zeta")
        self.turn_on(a)
        panel = a.inner_text("section:has(h3:text('Sync with Google Drive'))")
        self.assertIn("tester@example.com", panel)
        self.assertEqual(sorted(k for k in self.drive.items() if k.startswith("jobs:")), ["jobs:1", "jobs:2"])

        b = self.device()
        self.turn_on(b)
        self.assertEqual(b.locator("[data-gs-ch]").count(), 0)  # nothing on B yet: no question
        self.assertIn("Acme", self.board(b)); self.assertIn("Zeta", self.board(b))

        # B adds a job: unique id, syncs on its own; A picks it up with Sync now
        self.add(b, "Beta"); b.wait_for_timeout(5500)
        ids = [int(k.split(":")[1]) for k in self.drive.items() if k.startswith("jobs:")]
        self.assertTrue(max(ids) > 10**12, ids)
        self.sync_now(a)
        self.assertIn("Beta", self.board(a))

        # A deletes Acme; B loses it too
        a.click(".card >> text=Acme"); a.click("[data-delete]"); a.click("[data-delete-yes]"); a.wait_for_timeout(5500)
        self.assertIsNone(self.drive.items()["jobs:1"]["v"])
        self.sync_now(b)
        self.assertNotIn("Acme", self.board(b))
        self.assertIn("Zeta", self.board(b))

        # C already has jobs with ids 1 and 2: asked first, Combine renumbers them so nothing is overwritten
        c = self.device()
        self.add(c, "Gamma"); self.add(c, "Eta")
        self.turn_on(c)
        self.assertEqual(c.locator("[data-gs-ch]").count(), 3)
        c.click("[data-gs-ch=combine]"); c.wait_for_timeout(1500)
        board = self.board(c)
        for name in ["Gamma", "Eta", "Zeta", "Beta"]:
            self.assertIn(name, board)
        self.assertNotIn("Acme", board)
        names = sorted(v["v"]["company"] for k, v in self.drive.items().items() if k.startswith("jobs:") and v["v"])
        self.assertEqual(names, ["Beta", "Eta", "Gamma", "Zeta"])
        # Gamma's own history moved with it
        c.click(".card >> text=Gamma"); c.wait_for_selector(".tl")
        self.assertIn("Saved the posting", c.inner_text(".tl"))
        self.assertEqual(self.errors, [])

    def test_restore_does_not_clobber_newer_synced_changes(self):
        a = self.device()
        self.add(a, "Acme"); self.add(a, "Zeta")
        self.turn_on(a)
        b = self.device()
        self.turn_on(b)
        bboard = self.board(b)
        self.assertIn("Acme", bboard); self.assertIn("Zeta", bboard)
        # an older backup of A, taken before B's changes
        a.click("[data-view=data]")
        with a.expect_download() as dl:
            a.click("[data-backup]")
        old_backup = Path(dl.value.path())

        # B edits Acme and deletes Zeta, then syncs
        b.click(".card >> text=Acme"); b.click("[data-edit]")
        b.fill("textarea[name=description]", "B's newer posting text")
        b.click("form[data-form=edit] button[type=submit]")
        b.wait_for_selector(".hero h2")
        b.click("[data-go=board]")
        b.click(".card >> text=Zeta"); b.click("[data-delete]"); b.click("[data-delete-yes]")
        b.wait_for_timeout(5500)
        self.assertIn("B's newer posting text", json.dumps(self.drive.items()["jobs:1"]))
        self.assertIsNone(self.drive.items()["jobs:2"]["v"])  # Zeta's deletion reached Drive

        # A restores the older backup with sync on: the confirm must warn about reconciliation
        a.click("[data-view=data]")
        a.set_input_files("[data-restore-file]", str(old_backup))
        confirm = a.inner_text("div.confirm")
        self.assertIn("Sync is on", confirm)
        self.assertIn("newer changes from your other devices win", confirm)
        a.click("[data-restore-yes]")
        a.wait_for_selector("#toast >> text=Restored")
        # the restore schedules no sync and pushes nothing: Drive is untouched
        self.assertIn("B's newer posting text", json.dumps(self.drive.items()["jobs:1"]))
        self.assertIsNone(self.drive.items()["jobs:2"]["v"])

        # A syncs: per-record last-writer-wins — B's newer Acme wins, Zeta stays deleted
        self.sync_now(a)
        self.assertIn("B's newer posting text", json.dumps(self.drive.items()["jobs:1"]))
        self.assertIsNone(self.drive.items()["jobs:2"]["v"])
        board = self.board(a)
        self.assertIn("Acme", board)
        self.assertNotIn("Zeta", board)
        self.assertEqual(self.errors, [])

    def test_conflicting_edits_resolve_per_record_lww_tie_goes_remote(self):
        PIN1, PIN2 = 2000000000000, 2000000060000
        a = self.device(pin=PIN1)
        self.add(a, "Acme")
        self.turn_on(a)
        a.wait_for_timeout(1500)  # first sync uploads the starting records
        a.click("[data-view=board]")  # turn_on leaves the page on the Data view
        a.evaluate(f"window.__pin={PIN2}")  # time moves on, so the edit below out-ranks the upload
        a.click(".card >> text=Acme"); a.click("[data-edit]")
        a.fill("textarea[name=description]", "local text")
        a.click("form[data-form=edit] button[type=submit]")
        a.wait_for_timeout(5500)  # auto-sync pushes the edit; its change time is PIN2
        rec = self.drive.items()["jobs:1"]
        self.assertEqual(rec["t"], PIN2)
        self.assertEqual(rec["v"]["description"], "local text")

        # The other device synced an OLDER edit to the same record: this device wins,
        # and the merged (local) version is pushed back up to Drive.
        old = dict(rec["v"]); old["description"] = "remote older text"
        self.set_remote("jobs:1", PIN2 - 30000, old)
        self.sync_now(a)
        rec = self.drive.items()["jobs:1"]
        self.assertEqual(rec["t"], PIN2)
        self.assertEqual(rec["v"]["description"], "local text")

        # A tie on the change time: the remote copy wins, and this device applies it locally.
        tie = dict(self.drive.items()["jobs:1"]["v"]); tie["description"] = "remote tie text"
        self.set_remote("jobs:1", PIN2, tie)
        self.sync_now(a)
        a.click("[data-view=board]")  # sync_now leaves the page on the Data view
        a.click(".card >> text=Acme"); a.click("[data-edit]")
        self.assertEqual(a.input_value("textarea[name=description]"), "remote tie text")
        self.assertEqual(self.errors, [])

    def test_expired_access_shows_sync_button(self):
        a = self.device(exp=30)  # 30 s minus the 60 s margin: expired at once
        self.turn_on(a)
        self.add(a, "Initech"); a.wait_for_timeout(500)
        chip = a.locator("#gsc")
        self.assertTrue(chip.is_visible())
        before = a.evaluate("window.__gsi")
        chip.click(); a.wait_for_timeout(1200)
        self.assertEqual(a.evaluate("window.__gsi"), before + 1)
        self.assertIn("Initech", [v["v"]["company"] for k, v in self.drive.items().items() if k.startswith("jobs:") and v["v"]])
        self.assertNotIn("tok", a.evaluate("JSON.stringify(localStorage)"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
