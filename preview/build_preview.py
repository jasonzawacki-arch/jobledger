"""Builds dist/preview.html: the demo build of JobLedger (the phone engine in demo mode) with sample data.

Run after `npm run build`:   python preview/build_preview.py
The sample data comes from the real Python demo loader, so the preview shows what the app shows. The result is a
single page that runs anywhere, including as a hosted page that cannot download files or reach other sites.
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
from jobledger.service import Tracker  # noqa: E402


def snapshot() -> dict:
    t = Tracker(Database())
    load_demo(t)
    return {"made_at": time.time(), "backup": t.backup()}


BANNER = """<div class="pv" role="note"><b>Preview</b><span>Sample data. Changes stay on this page until you reload. Downloads, imports and wording suggestions need the phone app or the full app.</span></div>"""

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
    html = (ROOT / "dist" / "demo" / "index.html").read_text("utf-8")
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    app_js = re.search(r"<script>(.*)</script>", body, re.S).group(1)
    body_markup = body[: body.index("<script>")]
    body_markup = body_markup.replace('<div class="wrap">', BANNER + '\n<div class="wrap">', 1)
    data = json.dumps(snapshot()).replace("</", "<\\/")
    page = f"""<title>JobLedger Preview</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="color-scheme" content="light dark">
<style>{style}{PREVIEW_CSS}</style>
{THEME_SHIM}
{body_markup}
<script>window.__JL_SNAPSHOT__ = {data};</script>
<script>{app_js}</script>
"""
    out = ROOT / "dist" / "preview.html"
    out.write_text(page, "utf-8")
    # The same demo as a normal web page, for hosting next to the phone app (dist/app/demo/).
    site = ROOT / "dist" / "app" / "demo"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                                     '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                                     '<link rel="icon" href="../icon-192.png">\n</head>\n<body>\n' + page + '</body>\n</html>\n', "utf-8")
    return out


if __name__ == "__main__":
    p = build()
    print(p, f"{p.stat().st_size / 1024:.0f} KB")
