"""Reading a job from its link. The network is faked: tests never leave this machine."""
import json, unittest
from jobledger import posting as p

GREENHOUSE_LIKE = """<!doctype html><html><head><title>Data Analyst - Initech Careers</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Data Analyst",
"hiringOrganization":{"@type":"Organization","name":"Initech"},"datePosted":"2026-10-01T09:00:00Z",
"jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Arlington","addressRegion":"VA","addressCountry":"US"}}],
"baseSalary":{"@type":"MonetaryAmount","currency":"USD","value":{"@type":"QuantitativeValue","minValue":85000,"maxValue":100000,"unitText":"YEAR"}},
"description":"&lt;p&gt;About us&lt;/p&gt;&lt;h3&gt;Requirements&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;Strong SQL &amp;amp; Python&lt;/li&gt;&lt;li&gt;Tableau dashboards&lt;/li&gt;&lt;/ul&gt;"}
</script></head><body>ignored</body></html>"""

GRAPH_REMOTE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"WebSite","name":"Jobs"},
{"@type":["JobPosting"],"title":"Platform Engineer","hiringOrganization":"Hooli","jobLocationType":"TELECOMMUTE",
"baseSalary":{"currency":"USD","value":{"value":65,"unitText":"HOUR"}},"description":"<ul><li>Terraform</li><li>Kubernetes</li></ul>"}]}
</script></head></html>"""

NO_LD = """<html><head><title>  Senior PM   | Globex </title><meta property="og:site_name" content="Globex">
<meta name="description" content="Lead product for our &amp; billing team."></head><body></body></html>"""

PUBLIC = lambda host: ["93.184.216.34"]


def page(body, status=200, ctype="text/html; charset=utf-8", headers=None):
    return lambda url: (status, {"content-type": ctype, **(headers or {})}, body.encode())


class Read(unittest.TestCase):
    def test_job_posting_data(self):
        r = p.read(GREENHOUSE_LIKE, "https://boards.example.com/initech/1")
        f = r["fields"]
        self.assertEqual(r["source"], "posting")
        self.assertEqual((f["title"], f["company"], f["location"], f["pay"], f["posted_on"]),
                         ("Data Analyst", "Initech", "Arlington, VA", "$85k-100k/yr", "2026-10-01"))
        self.assertIn("- Strong SQL & Python\n- Tableau dashboards", f["description"])  # bullets kept for the matcher
        self.assertNotIn("<", f["description"])

    def test_graph_remote_and_hourly(self):
        f = p.read(GRAPH_REMOTE)["fields"]
        self.assertEqual((f["title"], f["company"], f["location"], f["pay"]), ("Platform Engineer", "Hooli", "Remote", "$65/hr"))
        self.assertEqual(f["description"], "- Terraform\n- Kubernetes")

    def test_falls_back_to_page_title(self):
        r = p.read(NO_LD)
        self.assertEqual(r["source"], "page")
        self.assertEqual(r["fields"]["title"], "Senior PM | Globex")
        self.assertEqual(r["fields"]["company"], "Globex")
        self.assertEqual(r["fields"]["description"], "Lead product for our & billing team.")

    def test_nothing_useful(self):
        with self.assertRaisesRegex(p.PostingError, "Paste the posting text"):
            p.read("<html><body>hi</body></html>")

    def test_broken_json_is_skipped(self):
        bad = '<script type="application/ld+json">{not json</script>' + GRAPH_REMOTE
        self.assertEqual(p.read(bad)["fields"]["title"], "Platform Engineer")

    def test_long_fields_are_trimmed(self):
        big = GRAPH_REMOTE.replace("Platform Engineer", "X" * 500)
        self.assertEqual(len(p.read(big)["fields"]["title"]), 160)


class Addresses(unittest.TestCase):
    def test_refuses_local_and_private(self):
        for url, resolved in (("http://localhost:8000/", ["127.0.0.1"]), ("http://router/", ["192.168.1.1"]),
                              ("http://10.0.0.5/", None), ("http://169.254.169.254/latest", None), ("http://[::1]/", None),
                              ("http://tail.example/", ["100.101.102.103"]), ("http://x.example/", ["::ffff:127.0.0.1"]),
                              ("http://mixed.example/", ["93.184.216.34", "10.1.1.1"])):
            with self.assertRaisesRegex(p.PostingError, "local network", msg=url):
                p.check_url(url, lambda h, r=resolved: r)

    def test_refuses_other_schemes_and_credentials(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "https://user:pw@example.com/", "", None):
            with self.assertRaises(p.PostingError, msg=url):
                p.check_url(url, PUBLIC)

    def test_accepts_public(self):
        self.assertEqual(p.check_url(" https://jobs.example.com/1 ", PUBLIC), "https://jobs.example.com/1")

    def test_redirect_to_private_is_refused(self):
        hops = {"https://short.example/a": (302, {"location": "http://192.168.0.1/admin"}, b"")}
        get = lambda url: hops[url]
        res = lambda h: ["192.168.0.1"] if h == "192.168.0.1" else ["93.184.216.34"]
        with self.assertRaisesRegex(p.PostingError, "local network"):
            p.fetch("https://short.example/a", get, res)

    def test_follows_public_redirects(self):
        hops = {"https://short.example/a": (301, {"location": "/b"}, b""),
                "https://short.example/b": (200, {"content-type": "text/html"}, GRAPH_REMOTE.encode())}
        url, html = p.fetch("https://short.example/a", lambda u: hops[u], PUBLIC)
        self.assertEqual(url, "https://short.example/b")

    def test_redirect_loop(self):
        with self.assertRaisesRegex(p.PostingError, "too many"):
            p.fetch("https://a.example/", lambda u: (302, {"location": "https://a.example/"}, b""), PUBLIC)

    def test_blocked_too_big_and_not_html(self):
        with self.assertRaisesRegex(p.PostingError, "Paste the posting text"):
            p.fetch("https://x.example/", page("", status=999), PUBLIC)  # LinkedIn's "go away"
        with self.assertRaisesRegex(p.PostingError, "too large"):
            p.fetch("https://x.example/", lambda u: (200, {"content-type": "text/html"}, b"x" * (p.MAX_BYTES + 1)), PUBLIC)
        with self.assertRaisesRegex(p.PostingError, "not a web page"):
            p.fetch("https://x.example/", page("%PDF", ctype="application/pdf"), PUBLIC)

    def test_network_failure(self):
        def boom(url):
            raise OSError("timed out")
        with self.assertRaisesRegex(p.PostingError, "Could not load"):
            p.fetch("https://x.example/", boom, PUBLIC)

    def test_from_url_keeps_the_pasted_link(self):
        r = p.from_url("https://jobs.example.com/1?ref=me", page(GREENHOUSE_LIKE), PUBLIC)
        self.assertEqual(r["fields"]["url"], "https://jobs.example.com/1?ref=me")
        self.assertEqual(r["fields"]["company"], "Initech")


if __name__ == "__main__":
    unittest.main()
