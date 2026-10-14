"""Fill in a job from its posting link.

Most job sites (Greenhouse, Lever, Workday, Indeed, company career pages) embed the posting as schema.org
JobPosting data for search engines. We read that first; otherwise we fall back to the page title and description.

The server fetches the page, so the address is checked first: only http(s), and never an address on this
computer or the local network (that would let a pasted link probe your router or other devices). Redirects
are checked the same way, the download is size-capped, and nothing is fetched unless you ask.
"""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 3_000_000
TIMEOUT = 12
MAX_REDIRECTS = 5
UA = "Mozilla/5.0 (JobLedger; personal job tracker) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"


class PostingError(Exception):
    """Something the user can act on: a bad link, a blocked address, or a page that could not be read."""


# ---------- address checks

Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})


def check_url(url: str, resolve: Resolver = _resolve) -> str:
    """Returns the URL if it is safe to fetch, otherwise raises PostingError saying why."""
    if not isinstance(url, str) or not url.strip():
        raise PostingError("Paste the posting's web address")
    url = url.strip()
    if len(url) > 2000:
        raise PostingError("That link is too long")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PostingError("Use a link that starts with http:// or https://")
    host = parts.hostname
    if not host:
        raise PostingError("That link has no site name")
    if parts.username or parts.password:
        raise PostingError("Links with a user name or password are not allowed")
    try:
        addrs = [host] if _is_ip(host) else resolve(host)
    except (socket.gaierror, UnicodeError, OSError):
        raise PostingError("Could not find that site. Check the link.") from None
    if not addrs:
        raise PostingError("Could not find that site. Check the link.")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise PostingError("That address is on this computer or your local network, so it is not fetched")
    return url


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# ---------- fetching

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # we follow redirects ourselves so each hop is checked


Fetcher = Callable[[str], tuple[int, dict, bytes]]


def _http_get(url: str) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                               "Accept-Language": "en-US,en;q=0.8"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            body = r.read(MAX_BYTES + 1)
            return r.status, {k.lower(): v for k, v in r.headers.items()}, body
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b""


def fetch(url: str, get: Fetcher = _http_get, resolve: Resolver = _resolve) -> tuple[str, str]:
    """Downloads a page, following up to five redirects and checking each address. Returns (final url, html)."""
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url, resolve)
        try:
            status, headers, body = get(url)
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            raise PostingError("Could not load that page. The site may be down or blocking automatic requests; "
                               "paste the posting text instead.") from None
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            url = urljoin(url, headers["location"])
            continue
        if status in (401, 403, 429, 999):
            raise PostingError("That site does not allow automatic reading (it asked for a sign-in or blocked the "
                               "request). Paste the posting text instead.")
        if status >= 400:
            raise PostingError(f"The site answered with an error ({status}). Check the link.")
        if len(body) > MAX_BYTES:
            raise PostingError("That page is too large to read")
        ctype = headers.get("content-type", "text/html").lower()
        if "html" not in ctype and "xml" not in ctype:
            raise PostingError("That link is not a web page (it may be a PDF or a download)")
        m = re.search(r"charset=([\w-]+)", ctype)
        try:
            text = body.decode(m.group(1) if m else "utf-8", errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return url, text
    raise PostingError("That link redirects too many times")


# ---------- reading the page

class _Page(HTMLParser):
    """Collects JSON-LD blocks, meta tags and the <title>; ignores everything else."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ld: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in: Optional[str] = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in, self._buf = "ld", []
        elif tag == "title" and not self.title:
            self._in, self._buf = "title", []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"])

    def handle_endtag(self, tag):
        if self._in == "ld" and tag == "script":
            self.ld.append("".join(self._buf))
            self._in = None
        elif self._in == "title" and tag == "title":
            self.title = " ".join("".join(self._buf).split())
            self._in = None

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)


class _Text(HTMLParser):
    """Turns posting HTML into plain text, keeping list items as "- " lines so the matcher sees them as bullets."""
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK or tag == "li":
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)

    def text(self) -> str:
        lines = [" ".join(l.split()) for l in "".join(self.out).splitlines()]
        lines = [l for l in lines if l and l != "-"]
        return "\n".join(lines)


def html_to_text(fragment: str) -> str:
    # JSON-LD descriptions are often HTML that was escaped once more (&lt;p&gt;), so unescape first if needed
    if "<" not in fragment and "&lt;" in fragment:
        fragment = html.unescape(fragment)
    p = _Text()
    p.feed(fragment)
    p.close()
    return p.text()


def _job_postings(blocks: list[str]):
    def walk(x):
        if isinstance(x, list):
            for y in x:
                yield from walk(y)
        elif isinstance(x, dict):
            t = x.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                yield x
            for k in ("@graph", "mainEntity", "itemListElement"):
                if k in x:
                    yield from walk(x[k])
    for b in blocks:
        b = b.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            data = json.loads(b)
        except ValueError:
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", b))  # stray raw newlines inside strings
            except ValueError:
                continue
        yield from walk(data)


def _s(v) -> str:
    if isinstance(v, dict):
        v = v.get("name") or v.get("@value") or ""
    if isinstance(v, list):
        v = next((_s(x) for x in v if _s(x)), "")
    return " ".join(html.unescape(str(v or "")).split())


def _location(jp: dict) -> str:
    places = []
    locs = jp.get("jobLocation") or []
    for loc in locs if isinstance(locs, list) else [locs]:
        addr = loc.get("address", loc) if isinstance(loc, dict) else {}
        if isinstance(addr, str):
            places.append(addr)
            continue
        city, region = _s(addr.get("addressLocality")), _s(addr.get("addressRegion"))
        country = _s(addr.get("addressCountry"))
        p = ", ".join(x for x in (city, region) if x) or country
        if p and p not in places:
            places.append(p)
    remote = "TELECOMMUTE" in str(jp.get("jobLocationType", "")).upper()
    text = "; ".join(places[:3]) + (" +" if len(places) > 3 else "")
    if remote:
        text = "Remote" + (f" ({text})" if text else "")
    return text


def _money(n: float) -> str:
    return f"{n / 1000:g}k" if n >= 1000 else f"{n:g}"


def _pay(jp: dict) -> str:
    bs = jp.get("baseSalary") or jp.get("estimatedSalary")
    if isinstance(bs, list):
        bs = bs[0] if bs else None
    if not isinstance(bs, dict):
        return ""
    cur = _s(bs.get("currency")) or "USD"
    sym = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "CA$", "AUD": "A$"}.get(cur.upper(), cur + " ")
    v = bs.get("value", bs)
    unit = ""
    if isinstance(v, dict):
        unit = _s(v.get("unitText"))
        lo, hi, one = v.get("minValue"), v.get("maxValue"), v.get("value")
    else:
        lo = hi = None
        one = v
    try:
        lo, hi, one = (float(x) if x not in (None, "") else None for x in (lo, hi, one))
    except (TypeError, ValueError):
        return ""
    per = {"HOUR": "/hr", "DAY": "/day", "WEEK": "/wk", "MONTH": "/mo", "YEAR": "/yr"}.get(unit.upper(), "")
    if lo and hi and lo != hi:
        return f"{sym}{_money(lo)}-{_money(hi)}{per}"
    n = lo or hi or one
    return f"{sym}{_money(n)}{per}" if n else ""


def _date(v) -> str:
    s = _s(v)[:10]
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


def read(page_html: str, url: str = "") -> dict:
    """Pulls the job fields out of a posting page. Only fields actually found are returned."""
    p = _Page()
    try:
        p.feed(page_html)
        p.close()
    except Exception:  # pragma: no cover - HTMLParser is forgiving, but never fail the request over markup
        pass
    jp = next(_job_postings(p.ld), None)
    out: dict = {}
    if jp:
        out["title"] = _s(jp.get("title"))
        org = jp.get("hiringOrganization")
        out["company"] = _s(org)
        out["location"] = _location(jp)
        out["pay"] = _pay(jp)
        out["posted_on"] = _date(jp.get("datePosted"))
        out["description"] = html_to_text(str(jp.get("description") or ""))
        source = "posting"
    else:
        title = p.meta.get("og:title") or p.title
        out["title"] = _s(title)
        out["company"] = _s(p.meta.get("og:site_name"))
        out["description"] = _s(p.meta.get("og:description") or p.meta.get("description"))
        source = "page"
    if url:
        out["url"] = url
    limits = {"company": 120, "title": 160, "location": 120, "pay": 80, "description": 60000, "url": 500}
    out = {k: (v[: limits[k]] if k in limits else v) for k, v in out.items() if v}
    if not out.get("title") and not out.get("description"):
        raise PostingError("Could not find a job posting on that page. Paste the posting text instead.")
    return {"source": source, "fields": out}


def from_url(url: str, get: Fetcher = _http_get, resolve: Resolver = _resolve) -> dict:
    final, page = fetch(url, get, resolve)
    r = read(page, final)
    r["fields"]["url"] = url.strip()[:500]  # keep the link as pasted; the final address may carry tracking
    return r
