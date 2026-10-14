"""Run with: python -m jobledger   (env: JL_DB, JL_TOKEN, JL_HOST, JL_PORT, JL_HOSTS, JL_TZ; flags: --demo, --tailscale)"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import zoneinfo

import uvicorn

from .api import make_app
from .seed import DAY, DEMO, DEMO_POSTINGS, DEMO_RESUME


def load_demo(tracker) -> None:
    if tracker.list_jobs(include_closed=True):
        print("Database is not empty; skipping demo data.")
        return
    real = tracker.clock
    now = real()
    for company, title, location, pay, stage, ago, closed, follow, note in DEMO:
        tracker.clock = lambda t=now - ago * DAY: t
        j = tracker.add_job({"company": company, "title": title, "location": location, "pay": pay, "stage": min(stage, 1), "description": DEMO_POSTINGS.get(company, "")})
        for s in range(2, stage + 1):
            tracker.clock = lambda t=now - ago * DAY + s * 3 * DAY: min(t, now)
            tracker.move(j["id"], s)
        if note:
            tracker.add_note(j["id"], note)
        if closed:
            tracker.close(j["id"], closed)
        if follow is not None:
            tracker.clock = real
            d = dt.datetime.fromtimestamp(now + follow * DAY, tracker.tz).date().isoformat()
            tracker.update_job(j["id"], {"follow_up_on": d})
    tracker.clock = real
    if not tracker.get_resume()["text"]:
        tracker.set_resume(DEMO_RESUME)


def time_zone():
    name = os.environ.get("JL_TZ", "").strip()
    if not name:
        return None  # this computer's own time zone
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        sys.exit(f"JL_TZ={name!r} is not a time zone name like America/New_York")


class SetupError(Exception):
    pass


def tailscale_up(port: int, run=subprocess.run, which=shutil.which) -> tuple[str, list[str]]:
    """Serve http://127.0.0.1:<port> over HTTPS to your own Tailscale devices.
    Returns the HTTPS host name and the command that turns it off again."""
    exe = which("tailscale")
    if not exe:
        raise SetupError("Tailscale is not installed on this computer. Get it at https://tailscale.com/download, sign in, and try again.")
    st = run([exe, "status", "--json"], capture_output=True, text=True)
    try:
        info = json.loads(st.stdout or "{}")
    except ValueError:
        info = {}
    if st.returncode != 0 or info.get("BackendState") != "Running":
        raise SetupError("Tailscale is not connected. Open Tailscale on this computer, sign in, and try again.")
    name = (info.get("Self") or {}).get("DNSName", "").rstrip(".").lower()
    if not name:
        raise SetupError("Tailscale did not report this computer's name. Turn on MagicDNS in the Tailscale admin console.")
    cmd = [exe, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"]
    sv = run(cmd, capture_output=True, text=True)
    if sv.returncode != 0:
        out = (sv.stderr or sv.stdout or "").strip()
        raise SetupError("Tailscale could not start HTTPS. If it mentions HTTPS certificates, turn on HTTPS in the "
                         "Tailscale admin console (DNS page) and try again." + (f"\n\n{out}" if out else ""))
    return name, cmd + ["off"]


def main() -> None:
    host, port = os.environ.get("JL_HOST", "127.0.0.1"), int(os.environ.get("JL_PORT", "8000"))
    token = os.environ.get("JL_TOKEN") or None
    hosts = os.environ.get("JL_HOSTS", "").split(",")
    stop = None
    if "--tailscale" in sys.argv:
        try:
            name, stop = tailscale_up(port)
        except SetupError as e:
            sys.exit(str(e))
        hosts.append(name)  # only your own Tailscale devices can reach this name
        print(f"Phone address: https://{name}")
        print("  On your phone: install Tailscale, sign in with the same account, open that address in Chrome,")
        print("  then tap the menu (three dots) and Install app.")
        if not token:
            print("  Tip: set JL_TOKEN too, so a shared or lost device on your tailnet still needs it.")
    app = make_app(os.environ.get("JL_DB", "jobledger.sqlite3"), token, hosts=hosts, tz=time_zone())
    if "--demo" in sys.argv:
        load_demo(app.state.tracker)
    print(f"JobLedger on http://{host}:{port}" + ("  (token required)" if token else ""))
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if stop:
            subprocess.run(stop, capture_output=True)  # stop serving HTTPS once JobLedger stops
            print("Stopped the Tailscale HTTPS address.")


if __name__ == "__main__":
    main()
