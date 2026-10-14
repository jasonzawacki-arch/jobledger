import json, threading, time, unittest, urllib.error, urllib.request
import uvicorn
from jobledger.api import make_app


class Server:
    def __init__(self, **kw):
        self.app = make_app(**kw)
        self.srv = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error"))
        self.th = threading.Thread(target=self.srv.run, daemon=True); self.th.start()
        while not self.srv.started: time.sleep(0.02)
        self.base = f"http://127.0.0.1:{self.srv.servers[0].sockets[0].getsockname()[1]}"

    def stop(self): self.srv.should_exit = True; self.th.join(5)

    def call(self, method, path, body=None, token=None, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, method=method, data=data,
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r: raw = r.read(); return r.status, raw
        except urllib.error.HTTPError as e: return e.code, e.read()


def j(res): return res[0], json.loads(res[1] or b"null")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.s = Server()
    @classmethod
    def tearDownClass(cls): cls.s.stop()

    def test_flow(self):
        c = self.s.call
        st, job = j(c("POST", "/api/jobs", {"company": "Acme", "title": "Analyst", "stage": 1}))
        self.assertEqual(st, 201)
        i = job["id"]
        self.assertEqual(j(c("POST", f"/api/jobs/{i}/move", {"stage": 2}))[1]["stage"], 2)
        self.assertEqual(j(c("POST", f"/api/jobs/{i}/notes", {"text": "hello"}))[0], 201)
        self.assertEqual(j(c("PATCH", f"/api/jobs/{i}", {"follow_up_on": "2027-02-02"}))[1]["follow_up_on"], "2027-02-02")
        self.assertEqual(j(c("POST", f"/api/jobs/{i}/close", {"reason": "rejected"}))[1]["closed_reason"], "rejected")
        self.assertNotIn(i, [x["id"] for x in j(c("GET", "/api/jobs"))[1]])  # closed jobs leave the default list
        self.assertEqual(j(c("POST", f"/api/jobs/{i}/reopen"))[1]["closed_reason"], None)
        self.assertEqual(j(c("GET", "/api/pulse"))[1]["active"] >= 1, True)
        self.assertEqual(c("GET", "/api/export.csv")[0], 200)
        snap = j(c("GET", "/api/backup"))[1]
        self.assertEqual(j(c("POST", "/api/restore", snap))[0], 200)
        self.assertEqual(c("DELETE", f"/api/jobs/{i}")[0], 204)
        self.assertEqual(c("GET", f"/api/jobs/{i}")[0], 404)

    def test_resume_and_match(self):
        c = self.s.call
        self.assertEqual(j(c("PUT", "/api/resume", {"text": "- Built Python APIs with SQLite and tests"}))[0], 200)
        i = j(c("POST", "/api/jobs", {"company": "M", "title": "T", "description": "- Experience building Python APIs\n- Kubernetes operations"}))[1]["id"]
        st, m = j(c("GET", f"/api/jobs/{i}/match"))
        self.assertEqual((st, m["counts"]["match"], m["counts"]["gap"]), (200, 1, 1))
        self.assertEqual(j(c("PUT", "/api/resume", {"text": 7}))[0], 400)
        self.assertEqual(c("GET", "/api/jobs/9999/match")[0], 404)

    def test_contacts_prep_import(self):
        c = self.s.call
        i = j(c("POST", "/api/jobs", {"company": "X", "title": "Y"}))[1]["id"]
        st, job = j(c("POST", f"/api/jobs/{i}/contacts", {"name": "Dana", "email": "d@example.com"}))
        self.assertEqual((st, job["contacts"][0]["name"]), (201, "Dana"))
        cid = job["contacts"][0]["id"]
        self.assertEqual(j(c("PATCH", f"/api/jobs/{i}/contacts/{cid}", {"role": "Recruiter"}))[1]["contacts"][0]["role"], "Recruiter")
        self.assertEqual(j(c("POST", f"/api/jobs/{i}/contacts", {"name": "Bad", "email": "nope"}))[0], 400)
        self.assertEqual(j(c("DELETE", f"/api/jobs/{i}/contacts/{cid}"))[1]["contacts"], [])
        job = j(c("POST", f"/api/jobs/{i}/prep/defaults"))[1]
        pid = job["prep"][0]["id"]
        self.assertTrue(j(c("PATCH", f"/api/jobs/{i}/prep/{pid}", {"done": True}))[1]["prep"][0]["done"])
        self.assertEqual(j(c("POST", f"/api/jobs/{i}/prep", {"text": "One more"}))[0], 201)
        self.assertEqual(c("DELETE", f"/api/jobs/{i}/prep/99999")[0], 404)
        self.assertEqual(j(c("POST", "/api/import", {"csv": "company,title\nImported Co,Analyst\n"}))[1], {"imported": 1})
        self.assertEqual(j(c("POST", "/api/import", {"csv": "nope"}))[0], 400)

    def test_errors(self):
        c = self.s.call
        self.assertEqual(j(c("POST", "/api/jobs", {"company": "", "title": "x"}))[0], 400)
        self.assertEqual(j(c("POST", "/api/jobs", [1]))[0], 400)
        self.assertEqual(c("GET", "/api/jobs/abc")[0], 404)
        self.assertEqual(c("GET", "/api/jobs/9999")[0], 404)
        req = urllib.request.Request(self.s.base + "/api/jobs", method="POST", data=b"{nope", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

    def test_ui_missing_is_clear(self):
        st, raw = self.s.call("GET", "/")
        self.assertIn(st, (200, 404))


class Auth(unittest.TestCase):
    def test_token(self):
        s = Server(token="s3cret")
        try:
            self.assertEqual(s.call("GET", "/api/health")[0], 200)
            self.assertEqual(s.call("GET", "/api/jobs")[0], 401)
            self.assertEqual(s.call("GET", "/api/jobs", token="wrong")[0], 401)
            self.assertEqual(s.call("GET", "/api/jobs", token="s3cret")[0], 200)
            self.assertEqual(s.call("GET", "/api/jobs?token=s3cret")[0], 401)  # never in the URL
            # With a token, any host name is fine (e.g. behind an HTTPS tunnel), but other sites still cannot write.
            self.assertEqual(s.call("GET", "/api/jobs", token="s3cret", headers={"Host": "me.example.net"})[0], 200)
            self.assertEqual(s.call("POST", "/api/jobs", {"company": "A", "title": "x"}, token="s3cret",
                                    headers={"Host": "me.example.net", "Origin": "https://me.example.net"})[0], 201)
            self.assertEqual(s.call("POST", "/api/jobs", {"company": "A", "title": "x"}, token="s3cret",
                                    headers={"Host": "me.example.net", "Origin": "https://evil.example"})[0], 403)
        finally:
            s.stop()


class CrossSite(unittest.TestCase):
    """Another website open in the same browser must not be able to change or read your data."""

    @classmethod
    def setUpClass(cls): cls.s = Server()
    @classmethod
    def tearDownClass(cls): cls.s.stop()

    def jobs(self):
        return len(json.loads(self.s.call("GET", "/api/jobs?closed=1")[1]))

    def test_other_origin_cannot_write(self):
        before = self.jobs()
        for path, body in (("/api/jobs", {"company": "Evil", "title": "x"}), ("/api/restore", {"app": "jobledger"})):
            st, _ = self.s.call("POST", path, body, headers={"Origin": "https://evil.example"})
            self.assertEqual(st, 403, path)
        self.assertEqual(self.s.call("POST", "/api/jobs", {"company": "Evil", "title": "x"}, headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.s.call("POST", "/api/jobs", {"company": "Evil", "title": "x"}, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.s.call("DELETE", "/api/jobs/1", headers={"Origin": "http://localhost:1"})[0], 403)  # another port is another site
        self.assertEqual(self.jobs(), before)

    def test_simple_form_body_is_rejected(self):
        # A plain form or no-cors fetch can only send text/plain, form or multipart bodies.
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
            st, _ = self.s.call("POST", "/api/jobs", raw=b'{"company":"Evil","title":"x"}', headers={"Content-Type": ctype})
            self.assertEqual(st, 400, ctype)

    def test_same_origin_works(self):
        origin = self.s.base  # what the app's own page sends
        self.assertEqual(self.s.call("POST", "/api/jobs", {"company": "Mine", "title": "x"}, headers={"Origin": origin})[0], 201)
        self.assertEqual(self.s.call("POST", "/api/jobs", {"company": "Mine", "title": "x"},
                                     headers={"Origin": origin.replace("127.0.0.1", "localhost"), "Host": origin.split("//")[1].replace("127.0.0.1", "localhost")})[0], 201)

    def test_rebound_host_name_is_refused(self):
        # DNS rebinding: evil.example made to resolve to 127.0.0.1 is same-origin to the browser, so check the Host.
        self.assertEqual(self.s.call("GET", "/api/jobs", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.s.call("GET", "/api/backup", headers={"Host": "evil.example:8000"})[0], 403)

    def test_listed_host_is_allowed(self):
        s = Server(hosts=["laptop.local"])
        try:
            self.assertEqual(s.call("GET", "/api/jobs", headers={"Host": "laptop.local:8000"})[0], 200)
            self.assertEqual(s.call("GET", "/api/jobs", headers={"Host": "evil.example"})[0], 403)
        finally:
            s.stop()


class Shutdown(unittest.TestCase):
    def test_database_closed_on_stop(self):
        import sqlite3
        s = Server()
        s.stop()
        with self.assertRaises(sqlite3.ProgrammingError):
            s.app.state.db.conn.execute("SELECT 1")


class SearchAndCalendar(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.s = Server()
    @classmethod
    def tearDownClass(cls): cls.s.stop()

    def test_search_and_ics(self):
        c = self.s.call
        _, a = j(c("POST", "/api/jobs", {"company": "Initech", "title": "Analyst", "stage": 1}))
        _, b = j(c("POST", "/api/jobs", {"company": "Hooli", "title": "PM"}))
        self.assertEqual(j(c("GET", "/api/search?q=initech"))[1], {"ids": [a["id"]]})
        self.assertEqual(c("GET", "/api/search?q=" + "x" * 201)[0], 400)
        req = urllib.request.Request(self.s.base + f"/api/jobs/{a['id']}/calendar.ics")
        with urllib.request.urlopen(req) as r:
            self.assertTrue(r.headers["Content-Type"].startswith("text/calendar"))
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertIn(b"BEGIN:VEVENT", r.read())
        self.assertEqual(c("GET", f"/api/jobs/{b['id']}/calendar.ics")[0], 400)  # saved, no date
        st, raw = c("GET", "/api/calendar.ics")
        self.assertEqual(st, 200)
        self.assertIn(b"Initech", raw); self.assertNotIn(b"Hooli", raw)


class PostingAndInterviews(unittest.TestCase):
    def test_posting_endpoint_and_interviews(self):
        from jobledger import posting
        def fake(url):
            if "bad" in url:
                raise posting.PostingError("Could not load that page")
            return {"source": "posting", "fields": {"company": "Initech", "title": "Analyst", "url": url}}
        s = Server(read_posting=fake)
        try:
            st, r = j(s.call("POST", "/api/posting", {"url": "https://jobs.example/1"}))
            self.assertEqual((st, r["fields"]["company"]), (200, "Initech"))
            st, r = j(s.call("POST", "/api/posting", {"url": "https://bad.example/"}))
            self.assertEqual((st, r["error"]), (400, "Could not load that page"))
            self.assertEqual(s.call("POST", "/api/posting", {"url": "https://jobs.example/1"}, headers={"Origin": "https://evil.example"})[0], 403)
            _, job = j(s.call("POST", "/api/jobs", {"company": "A", "title": "x"}))
            st, g = j(s.call("POST", f"/api/jobs/{job['id']}/interviews", {"starts_at": "2031-05-06T10:00", "label": "Panel"}))
            self.assertEqual((st, g["interviews"][0]["label"]), (201, "Panel"))
            iid = g["interviews"][0]["id"]
            self.assertEqual(j(s.call("PATCH", f"/api/jobs/{job['id']}/interviews/{iid}", {"minutes": 30}))[1]["interviews"][0]["minutes"], 30)
            self.assertEqual(j(s.call("GET", "/api/pulse"))[1]["upcoming_interviews"], [])  # more than two weeks away
            self.assertEqual(j(s.call("DELETE", f"/api/jobs/{job['id']}/interviews/{iid}"))[1]["interviews"], [])
            self.assertEqual(s.call("DELETE", f"/api/jobs/{job['id']}/interviews/{iid}")[0], 404)
        finally:
            s.stop()

    def test_real_reader_refuses_local_addresses(self):
        s = Server()
        try:
            st, r = j(s.call("POST", "/api/posting", {"url": s.base + "/api/backup"}))
            self.assertEqual(st, 400)
            self.assertIn("local network", r["error"])
        finally:
            s.stop()


class PhoneAppOrigins(unittest.TestCase):
    def test_listed_phone_app_can_connect_with_the_token(self):
        app_site = "https://me.github.io"
        s = Server(token="pw", origins=[app_site + "/"])
        try:
            h = {"Origin": app_site}
            req = urllib.request.Request(s.base + "/api/jobs", method="OPTIONS",
                                         headers={**h, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 204)
                self.assertEqual(r.headers["Access-Control-Allow-Origin"], app_site)
                self.assertIn("authorization", r.headers["Access-Control-Allow-Headers"])
            st, _ = s.call("POST", "/api/jobs", {"company": "A", "title": "x"}, token="pw", headers={**h, "Sec-Fetch-Site": "cross-site"})
            self.assertEqual(st, 201)
            self.assertEqual(s.call("GET", "/api/jobs", headers=h)[0], 401)  # still needs the password
            self.assertEqual(s.call("POST", "/api/jobs", {"company": "B", "title": "x"}, token="pw", headers={"Origin": "https://evil.example"})[0], 403)
            req = urllib.request.Request(s.base + "/api/health", headers={"Origin": "https://evil.example"})
            with urllib.request.urlopen(req) as r:
                self.assertIsNone(r.headers["Access-Control-Allow-Origin"])  # other sites cannot read answers
        finally:
            s.stop()

    def test_origins_need_a_token(self):
        with self.assertRaisesRegex(ValueError, "JL_TOKEN"):
            make_app(origins=["https://me.github.io"])


if __name__ == "__main__":
    unittest.main()
