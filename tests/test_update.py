"""Browser tests for the in-app update prompt (2026-10-22 cross-app addendum, §6): the shared app
shell checks this repo's GitHub Releases feed on launch and shows a slim, dismissible banner for a
newer release. The feed is mocked at the network layer; nothing reaches GitHub. Covers: a newer
release shows the banner, dismiss snoozes that version, a newer version re-prompts, an empty feed and
an offline check both stay silent, and the Play build (which defers to Play's updater) never checks.
Skipped when Playwright or the built files are missing."""
import json, os, unittest
from pathlib import Path

from tests.test_play import Static

ROOT = Path(__file__).resolve().parent.parent
PHONE = ROOT / "dist" / "app"
PLAY = ROOT / "dist" / "play"
FEED = "https://api.github.com/repos/jasonzawacki-arch/jobledger/releases**"
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def release(tag, prerelease=False, apk=True):
    url = f"https://github.com/jasonzawacki-arch/jobledger/releases/tag/{tag}"
    assets = [{"name": f"jobledger-{tag}.apk",
               "browser_download_url": f"https://github.com/jasonzawacki-arch/jobledger/releases/download/{tag}/jobledger-{tag}.apk"}] if apk else []
    return {"tag_name": tag, "draft": False, "prerelease": prerelease, "html_url": url, "assets": assets}


@unittest.skipUnless(sync_playwright and (PHONE / "index.html").exists(), "needs playwright and `npm run build`")
class UpdatePrompt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        cls.site = Static(PHONE)

    @classmethod
    def tearDownClass(cls):
        cls.site.stop(); cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.errors, self.hits, self.feed = [], [], []
        # service workers blocked so every request goes through the route below
        self.ctx = self.browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        self.ctx.route(FEED, self.serve_feed)

    def tearDown(self):
        self.ctx.close()

    def serve_feed(self, route):
        self.hits.append(route.request)
        if self.feed is None:
            route.abort("internetdisconnected")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.feed),
                          headers={"Access-Control-Allow-Origin": "*"})

    def open(self, base=None):
        pg = self.ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(base or self.site.base)
        pg.wait_for_selector(".board")
        return pg

    def settle(self, pg):
        pg.wait_for_timeout(600)

    def banner(self, pg):
        return pg.query_selector(".banner.update")

    def test_newer_release_shows_banner(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".banner.update")
        el = self.banner(pg)
        self.assertIn("0.2.0", el.inner_text())
        self.assertIn("Update available", el.inner_text())
        self.assertTrue(pg.get_attribute("[data-update-notes]", "href").endswith("/releases/tag/v0.2.0"))
        self.assertTrue(pg.get_attribute("[data-update-get]", "href").endswith("jobledger-v0.2.0.apk"))
        # no user data in the request: no cookies/credentials, no referrer
        req = self.hits[0]
        self.assertNotIn("cookie", {k.lower() for k in req.headers})
        self.assertNotIn("referer", {k.lower() for k in req.headers})
        # non-blocking: the board still works underneath
        self.assertTrue(pg.is_visible(".board"))
        self.assertFalse(self.errors, self.errors)

    def test_dismiss_snoozes_version_and_newer_reprompts(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".banner.update")
        pg.click("[data-update-dismiss]")
        self.assertIsNone(self.banner(pg))
        # same version, fresh daily check: stays snoozed
        pg.evaluate("localStorage.removeItem('jobledger.update.checked')")
        pg.reload(); pg.wait_for_selector(".board"); self.settle(pg)
        self.assertEqual(len(self.hits), 2)
        self.assertIsNone(self.banner(pg))
        # a newer release prompts again
        self.feed = [release("v0.3.0"), release("v0.2.0")]
        pg.evaluate("localStorage.removeItem('jobledger.update.checked')")
        pg.reload(); pg.wait_for_selector(".banner.update")
        self.assertIn("0.3.0", self.banner(pg).inner_text())
        self.assertFalse(self.errors, self.errors)

    def test_checks_at_most_daily(self):
        self.feed = [release("v0.2.0")]
        pg = self.open()
        pg.wait_for_selector(".banner.update")
        pg.reload(); pg.wait_for_selector(".banner.update")  # shown again from the cached answer
        self.assertEqual(len(self.hits), 1, "a second launch the same day must not hit the feed")

    def test_stable_build_ignores_prereleases(self):
        self.feed = [release("v0.3.0-beta.1", prerelease=True)]
        pg = self.open(); self.settle(pg)
        self.assertEqual(len(self.hits), 1)
        self.assertIsNone(self.banner(pg))

    def test_empty_feed_is_silent(self):
        self.feed = []
        pg = self.open(); self.settle(pg)
        self.assertEqual(len(self.hits), 1)
        self.assertIsNone(self.banner(pg))
        self.assertFalse(self.errors, self.errors)

    def test_offline_is_silent(self):
        self.feed = None
        pg = self.open(); self.settle(pg)
        self.assertIsNone(self.banner(pg))
        self.assertFalse(self.errors, self.errors)

    def test_play_build_never_checks(self):
        self.feed = [release("v0.2.0")]
        site = Static(PLAY)
        try:
            pg = self.open(site.base); self.settle(pg)
            self.assertEqual(self.hits, [], "the Play build defers to Play's updater")
            self.assertIsNone(self.banner(pg))
        finally:
            site.stop()


if __name__ == "__main__":
    unittest.main()
