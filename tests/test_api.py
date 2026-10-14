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

    def call(self, method, path, body=None, token=None):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
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
            self.assertEqual(s.call("GET", "/api/jobs?token=s3cret")[0], 200)
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
