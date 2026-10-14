"""Run with: python -m jobledger   (env: JL_DB, JL_TOKEN, JL_HOST, JL_PORT; flag: --demo)"""
from __future__ import annotations

import os
import sys
import time

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
            d = time.strftime("%Y-%m-%d", time.gmtime(now + follow * DAY))
            tracker.update_job(j["id"], {"follow_up_on": d})
    tracker.clock = real
    if not tracker.get_resume()["text"]:
        tracker.set_resume(DEMO_RESUME)


def main() -> None:
    app = make_app(os.environ.get("JL_DB", "jobledger.sqlite3"), os.environ.get("JL_TOKEN") or None)
    if "--demo" in sys.argv:
        load_demo(app.state.tracker)
    host, port = os.environ.get("JL_HOST", "127.0.0.1"), int(os.environ.get("JL_PORT", "8000"))
    print(f"JobLedger on http://{host}:{port}" + ("  (token required)" if os.environ.get("JL_TOKEN") else ""))
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
