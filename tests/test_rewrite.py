import json, unittest
from jobledger import rewrite as rw
from tests.test_api import Server, j

SRC = "Built a Python REST API with SQLite storage and an audit log (side project)"


class Guard(unittest.TestCase):
    def test_accepts_plain_rewording(self):
        ok, _ = rw.guard(SRC, "Built a Python REST API backed by SQLite, with an audit log (side project)")
        self.assertTrue(ok)

    def test_rejects_invented_content(self):
        for bad, why in (
            ("Built a Python REST API serving 10,000 users with an audit log (side project)", "number"),
            ("Built a Python REST API with SQLite on AWS (side project)", "tool"),
            ("Led a team that built a Python REST API at Google", "name"),
            ("Built a Python REST API with 99 percent uptime", "number"),
            ("", "empty"),
            ("Built it.\nAlso did more.", "multiline"),
        ):
            self.assertFalse(rw.guard(SRC, bad)[0], why)

    def test_numbers_already_in_source_are_fine(self):
        self.assertTrue(rw.guard("Wrote 53 automated tests", "Wrote 53 automated tests for the server")[0])

    def test_diff_marks_changes(self):
        parts = rw.diff_parts("Wrote automated tests", "Wrote automated server tests")
        self.assertEqual("".join(p[0] for p in parts).strip(), "Wrote automated server tests")
        self.assertTrue(any(c and "server" in t for t, c in parts))


class Client(unittest.TestCase):
    """The real HTTP client, pointed at a local fake of the Anthropic API (the real one is never called in tests)."""
    def run_fake(self, status, payload):
        import http.server, threading
        seen = {}
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen["headers"] = {k.lower(): v for k, v in self.headers.items()}; seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = rw.API_URL; rw.API_URL = f"http://127.0.0.1:{srv.server_port}/v1/messages"
        try:
            return seen, lambda: rw.anthropic_rewrite("sk-ant-x", SRC, "Python APIs", model="test-model")
        finally:
            self._restore = (old, srv)

    def tearDown(self):
        if hasattr(self, "_restore"):
            rw.API_URL = self._restore[0]; self._restore[1].shutdown()

    def test_request_shape_and_parse(self):
        seen, call = self.run_fake(200, {"content": [{"type": "text", "text": " Reworded line. "}]})
        self.assertEqual(call(), "Reworded line.")
        self.assertEqual(seen["headers"]["x-api-key"], "sk-ant-x")
        self.assertEqual(seen["body"]["model"], "test-model")
        self.assertIn(SRC, seen["body"]["messages"][0]["content"])
        self.assertIn("Do not add numbers", seen["body"]["system"])

    def test_error_codes(self):
        for status, word in ((401, "API key"), (429, "rate limit"), (500, "error")):
            _, call = self.run_fake(status, {"error": {}})
            with self.assertRaises(rw.RewriteError) as cm: call()
            self.assertIn(word, str(cm.exception))
            self.tearDown(); del self._restore


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reply = "Built a Python REST API backed by SQLite, with an audit log (side project)"

        def fake(key, source, requirement):
            self.calls.append((key, source, requirement))
            if isinstance(self.reply, Exception): raise self.reply
            return self.reply
        self.s = Server(rewriter=fake)
        c = self.s.call
        c("PUT", "/api/resume", {"text": "- " + SRC})
        self.jid = j(c("POST", "/api/jobs", {"company": "A", "title": "B", "description": "- Experience building Python REST APIs"}))[1]["id"]
        self.req = "Experience building Python REST APIs"

    def tearDown(self): self.s.stop()

    def post(self, body, key="sk-ant-test"):
        import urllib.request, urllib.error
        r = urllib.request.Request(self.s.base + f"/api/jobs/{self.jid}/rewrite", method="POST", data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json", **({"X-Anthropic-Key": key} if key else {})})
        try:
            with urllib.request.urlopen(r) as resp: return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_success_uses_resume_line_not_client_text(self):
        st, out = self.post({"requirement": self.req})
        self.assertEqual((st, out["ok"], out["source"]), (200, True, SRC))
        self.assertEqual(self.calls[0][1], SRC)

    def test_guard_failure_is_reported_not_shown(self):
        self.reply = "Built a Python REST API used by 5,000 people"
        st, out = self.post({"requirement": self.req})
        self.assertEqual((st, out["ok"]), (200, False))
        self.assertNotIn("suggestion", out)

    def test_needs_key_and_known_requirement(self):
        self.assertEqual(self.post({"requirement": self.req}, key="")[0], 400)
        self.assertEqual(self.post({"requirement": self.req}, key="nope")[0], 400)
        self.assertEqual(self.post({"requirement": "something I made up"})[0], 400)
        self.assertEqual(self.calls, [])

    def test_model_errors_become_502(self):
        self.reply = rw.RewriteError("Anthropic did not accept that API key.")
        st, out = self.post({"requirement": self.req})
        self.assertEqual(st, 502); self.assertIn("API key", out["error"])


if __name__ == "__main__":
    unittest.main()
