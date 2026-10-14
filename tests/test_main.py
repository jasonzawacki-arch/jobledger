"""Phone setup through Tailscale, with the tailscale command faked."""
import json, subprocess, unittest
from jobledger.__main__ import SetupError, tailscale_up


def fake(status=None, status_rc=0, serve_rc=0, serve_err=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "status":
            return subprocess.CompletedProcess(cmd, status_rc, json.dumps(status) if status is not None else "", "")
        return subprocess.CompletedProcess(cmd, serve_rc, "", serve_err)
    return run, calls


RUNNING = {"BackendState": "Running", "Self": {"DNSName": "Laptop.tail1234.ts.net."}}
which = lambda name: "/usr/bin/tailscale"


class Tailscale(unittest.TestCase):
    def test_success(self):
        run, calls = fake(RUNNING)
        name, off = tailscale_up(8123, run=run, which=which)
        self.assertEqual(name, "laptop.tail1234.ts.net")
        self.assertEqual(calls[-1], ["/usr/bin/tailscale", "serve", "--bg", "--https=443", "http://127.0.0.1:8123"])
        self.assertEqual(off, calls[-1] + ["off"])

    def test_not_installed(self):
        run, calls = fake(RUNNING)
        with self.assertRaisesRegex(SetupError, "not installed"):
            tailscale_up(8000, run=run, which=lambda n: None)
        self.assertEqual(calls, [])

    def test_not_connected(self):
        for st, rc in (({"BackendState": "NeedsLogin"}, 0), (None, 1)):
            run, calls = fake(st, status_rc=rc)
            with self.assertRaisesRegex(SetupError, "not connected"):
                tailscale_up(8000, run=run, which=which)
            self.assertEqual(len(calls), 1)  # never tried to serve

    def test_no_dns_name(self):
        run, _ = fake({"BackendState": "Running", "Self": {}})
        with self.assertRaisesRegex(SetupError, "MagicDNS"):
            tailscale_up(8000, run=run, which=which)

    def test_serve_fails_shows_why(self):
        run, _ = fake(RUNNING, serve_rc=1, serve_err="HTTPS is not enabled for your tailnet")
        with self.assertRaisesRegex(SetupError, "(?s)turn on HTTPS.*not enabled for your tailnet"):
            tailscale_up(8000, run=run, which=which)


if __name__ == "__main__":
    unittest.main()
