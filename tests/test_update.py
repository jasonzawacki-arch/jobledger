"""Browser tests for the in-app update prompt (Issue #26): the app checks the public
GitHub Releases feed at most once a day on launch and shows a slim, dismissible banner
when a newer applicable release exists. The feed is mocked with Playwright route
interception on the api.github.com releases URL — the repo currently has zero releases,
so an empty or unreachable feed must stay silent (the designed behavior today).
Skipped when Playwright or the built files are missing."""
import functools, http.server, json, re, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAY = ROOT / "dist" / "play"
# A regex route pattern, not a glob: the glob form did not match the feed URL in
# this environment (the unmocked request escaped to the network and failed).
RELEASES_RE = re.compile(r"api\.github\.com/repos/jasonzawacki-arch/jobledger/releases")
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


def release(tag, prerelease=False, with_asset=True):
    return {
        "tag_name": tag,
        "name": f"JobLedger {tag}",
        "html_url": f"https://github.com/jasonzawacki-arch/jobledger/releases/tag/{tag}",
        "body": f"Release notes for {tag}.",
        "prerelease": prerelease,
        "draft": False,
        "assets": ([{"name": "jobledger.apk",
                     "browser_download_url":
                     f"https://github.com/jasonzawacki-arch/jobledger/releases/download/{tag}/jobledger.apk"}]
                   if with_asset else []),
    }


@unittest.skipUnless(sync_playwright and (PLAY / "index.html").exists(), "needs playwright and `npm run build`")
class UpdatePrompt(unittest.TestCase):
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
        self.feed = []          # mutable: a test can swap what the mocked feed returns
        self.feed_hits = 0
        self.feed_mode = "json"  # "json" | "http500" | "abort"

    def tearDown(self):
        self.ctx.close()

    def open(self):
        pg = self.ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))

        def handle(route):
            self.feed_hits += 1
            if self.feed_mode == "abort":
                route.abort()
            elif self.feed_mode == "http500":
                route.fulfill(status=500, content_type="application/json", body="{}")
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(self.feed))

        pg.route(RELEASES_RE, handle)
        pg.goto(self.site.base)
        pg.wait_for_selector(".board")
        return pg

    def assert_silent(self, pg):
        pg.wait_for_timeout(900)  # the launch check has had ample time to (not) prompt
        self.assertIsNone(pg.query_selector(".update-banner"), "no update banner should be shown")
        self.assertFalse(self.errors, self.errors)

    def test_banner_appears_for_newer_release(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".update-banner", timeout=5000)
        text = pg.inner_text(".update-banner")
        self.assertIn("Update available", text)
        self.assertIn("0.2.0", text)
        info = pg.get_attribute("[data-update-info]", "href")
        self.assertEqual(info, "https://github.com/jasonzawacki-arch/jobledger/releases/tag/v0.2.0")
        go = pg.get_attribute("[data-update-go]", "href")
        self.assertEqual(go, "https://github.com/jasonzawacki-arch/jobledger/releases/download/v0.2.0/jobledger.apk")
        self.assertIsNotNone(pg.query_selector("[data-update-dismiss]"), "banner needs a dismiss control")
        self.assertGreaterEqual(self.feed_hits, 1)
        self.assertFalse(self.errors, self.errors)

    def test_update_button_falls_back_to_release_page_without_assets(self):
        self.feed = [release("v0.2.0", with_asset=False)]
        pg = self.open()
        pg.wait_for_selector(".update-banner", timeout=5000)
        go = pg.get_attribute("[data-update-go]", "href")
        self.assertEqual(go, "https://github.com/jasonzawacki-arch/jobledger/releases/tag/v0.2.0")
        self.assertFalse(self.errors, self.errors)

    def test_dismiss_snoozes_that_version(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".update-banner", timeout=5000)
        pg.click("[data-update-dismiss]")
        pg.wait_for_selector(".update-banner", state="detached", timeout=5000)
        # Simulate the next day (past the daily-check gate): the snooze alone must
        # suppress the same version even though the feed is checked again.
        pg.evaluate("localStorage.removeItem('jobledger.update.lastCheck')")
        pg.reload(); pg.wait_for_selector(".board")
        self.assertGreaterEqual(self.feed_hits, 1)
        self.assert_silent(pg)
        self.assertEqual(pg.evaluate("localStorage.getItem('jobledger.update.snoozed')"), "0.2.0")

    def test_newer_version_reprompts_after_snooze(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".update-banner", timeout=5000)
        pg.click("[data-update-dismiss]")
        pg.wait_for_selector(".update-banner", state="detached", timeout=5000)
        self.feed = [release("v0.3.0"), release("v0.2.0")]
        pg.evaluate("localStorage.removeItem('jobledger.update.lastCheck')")
        pg.reload(); pg.wait_for_selector(".board")
        pg.wait_for_selector(".update-banner", timeout=5000)
        self.assertIn("0.3.0", pg.inner_text(".update-banner"))
        self.assertFalse(self.errors, self.errors)

    def test_check_runs_at_most_once_a_day(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".update-banner", timeout=5000)
        hits_after_first = self.feed_hits
        self.assertGreaterEqual(hits_after_first, 1)
        pg.reload(); pg.wait_for_selector(".board")
        pg.wait_for_timeout(900)
        self.assertEqual(self.feed_hits, hits_after_first,
                         "a second launch the same day must not re-fetch the feed")

    def test_empty_feed_stays_silent(self):
        self.feed = []  # the repo's real state today: zero releases
        pg = self.open()
        self.assert_silent(pg)
        self.assertGreaterEqual(self.feed_hits, 1, "the check should still have run")

    def test_failed_feed_stays_silent(self):
        self.feed_mode = "http500"
        pg = self.open()
        self.assert_silent(pg)
        self.assertGreaterEqual(self.feed_hits, 1)

    def test_unreachable_feed_stays_silent(self):
        self.feed_mode = "abort"  # offline / unreachable
        pg = self.open()
        self.assert_silent(pg)
        self.assertGreaterEqual(self.feed_hits, 1)

    def test_same_and_older_releases_stay_silent(self):
        self.feed = [release("v0.1.0"), release("v0.0.9")]  # current version is 0.1.0 (package.json)
        pg = self.open()
        self.assert_silent(pg)

    def test_stable_build_ignores_prereleases(self):
        # The shipped version 0.1.0 has no prerelease suffix, so it is the stable
        # channel: a newer pre-release alone must not prompt...
        self.feed = [release("v0.2.0-beta.1", prerelease=True)]
        pg = self.open()
        self.assert_silent(pg)

    def test_stable_build_offers_newest_stable_not_newer_prerelease(self):
        self.feed = [release("v0.3.0-beta.1", prerelease=True), release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".update-banner", timeout=5000)
        text = pg.inner_text(".update-banner")
        self.assertIn("0.2.0", text)
        self.assertNotIn("0.3.0", text)
        self.assertFalse(self.errors, self.errors)

    def test_prerelease_suffix_versions_compare_correctly(self):
        # 0.2.0-beta.2 is newer than 0.2.0-beta.1 but both are pre-releases, so on the
        # stable channel neither prompts; a stable 0.2.0 outranks its own betas.
        self.feed = [release("v0.2.0-beta.2", prerelease=True), release("v0.2.0-beta.1", prerelease=True)]
        pg = self.open()
        self.assert_silent(pg)


if __name__ == "__main__":
    unittest.main()
