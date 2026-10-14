"""Builds dist/preview.html: the real JobLedger interface with demo data and an in-page stand-in for the server.

Run after `npm run build`:   python preview/build_preview.py
The demo data and matcher results come from the real Python code, so the preview shows what the app shows.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobledger.__main__ import load_demo  # noqa: E402
from jobledger.db import Database  # noqa: E402
from jobledger.service import DEFAULT_PREP, Tracker  # noqa: E402


def snapshot() -> dict:
    t = Tracker(Database())
    load_demo(t)
    b = t.backup()
    return {"made_at": time.time(), "backup": b, "resume": b["resume"], "default_prep": DEFAULT_PREP,
            "matches": {str(j["id"]): t.match_job(j["id"]) for j in b["jobs"]}}


BANNER = """<div class="pv" role="note"><b>Preview</b><span>Sample data. Changes stay on this page until you reload. Reading links, downloads and wording suggestions need the full app.</span></div>"""

PREVIEW_CSS = """
/* preview only */
body { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.pv { max-width: 1200px; margin: 10px auto 0; display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline;
  background: var(--accent-soft); color: var(--ink); border: 1px solid var(--line); border-radius: 12px; padding: 8px 12px; font-size: 13px; }
.pv b { color: var(--accent); font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.06em; text-transform: uppercase; }
"""

# Keep the viewer's explicit light/dark choice: the app's own theme setting defaults to "system" and would clear it.
THEME_SHIM = """<script>(function(){try{var t=document.documentElement.getAttribute('data-theme');
if((t==='light'||t==='dark')&&!localStorage.getItem('jobledger.theme'))localStorage.setItem('jobledger.theme',t);}catch(e){}})();</script>"""


def build() -> Path:
    html = (ROOT / "dist" / "index.html").read_text("utf-8")
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    app_js = re.search(r"<script>(.*)</script>", body, re.S).group(1)
    body_markup = body[: body.index("<script>")]
    body_markup = body_markup.replace('<div class="wrap">', BANNER + '\n<div class="wrap">', 1)
    data = json.dumps(snapshot()).replace("</", "<\\/")
    mock = (ROOT / "preview" / "mock.js").read_text("utf-8")
    page = f"""<title>JobLedger Preview</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="color-scheme" content="light dark">
<style>{style}{PREVIEW_CSS}</style>
{THEME_SHIM}
{body_markup}
<script>window.__JL_SNAPSHOT__ = {data};</script>
<script>{mock}</script>
<script>{app_js}</script>
"""
    out = ROOT / "dist" / "preview.html"
    out.write_text(page, "utf-8")
    return out


if __name__ == "__main__":
    p = build()
    print(p, f"{p.stat().st_size / 1024:.0f} KB")
