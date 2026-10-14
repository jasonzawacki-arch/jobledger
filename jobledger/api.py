"""HTTP layer: thin JSON endpoints over Tracker, plus the built single-file UI."""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from . import posting
from . import rewrite as rw
from .db import Database
from .service import NotFound, Tracker, ValidationError

MAX_BODY = 5_000_000
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _hostname(value: str) -> str:
    """'Example.com:8000' -> 'example.com', '[::1]:8000' -> '::1'. Empty on anything unparseable."""
    try:
        return (urlsplit("//" + value).hostname or "").lower()
    except ValueError:
        return ""


def make_app(db_path: str = ":memory:", token: Optional[str] = None, static_dir: Optional[str] = None,
             clock: Callable[[], float] = time.time, rewriter: Optional[rw.Rewriter] = None,
             hosts: Iterable[str] = (), tz=None, read_posting: Optional[Callable[[str], dict]] = None) -> Starlette:
    db = Database(db_path)
    tracker = Tracker(db, clock, tz=tz)
    # Without a token, only answer to names that point at this machine, unless more are listed (JL_HOSTS).
    # This stops DNS rebinding: a hostile site whose domain is made to resolve to 127.0.0.1.
    allowed_hosts = LOOPBACK | {h.strip().lower() for h in hosts if h.strip()}
    index = Path(static_dir or Path(__file__).resolve().parent.parent / "dist") / "index.html"

    def err(msg: str, status: int) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=status)

    def authed(req: Request) -> bool:
        if not token:
            return True
        given = req.headers.get("authorization", "")
        # Header only: a token in the URL ends up in logs and browser history.
        given = given[7:] if given.lower().startswith("bearer ") else ""
        return hmac.compare_digest(given.encode(), token.encode())

    def foreign(req: Request) -> Optional[str]:
        """Why a request must be refused as coming from another site, or None if it is fine."""
        host = _hostname(req.headers.get("host", ""))
        if not token and host not in allowed_hosts:
            return "This server only answers on localhost. Set JL_TOKEN, or list the name in JL_HOSTS."
        if req.method in SAFE_METHODS:
            return None
        # Browsers always send Origin on cross-site writes; it must name this same host.
        origin = req.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc.lower() != req.headers.get("host", "").lower():
            return "Requests from other sites are not allowed"
        if req.headers.get("sec-fetch-site") == "cross-site":
            return "Requests from other sites are not allowed"
        return None

    async def body(req: Request):
        raw = await req.body()
        if len(raw) > MAX_BODY:
            raise ValidationError("Request is too large")
        # A plain HTML form or a no-cors fetch cannot send this type, so this also blocks cross-site writes.
        if raw and req.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise ValidationError("Request body must be sent as application/json")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ValidationError("Request body must be JSON") from None
        return data

    def api(fn):
        async def handler(req: Request):
            why = foreign(req)
            if why:
                return err(why, 403)
            if not authed(req):
                return err("A valid token is required", 401)
            try:
                return await fn(req)
            except ValidationError as e:
                return err(str(e), 400)
            except NotFound:
                return err("Not found", 404)
        return handler

    def jid(req: Request) -> int:
        try:
            return int(req.path_params["id"])
        except ValueError:
            raise NotFound() from None

    async def health(req):
        return JSONResponse({"ok": True, "auth": bool(token)})

    async def jobs(req):
        if req.method == "POST":
            d = await body(req)
            if not isinstance(d, dict):
                raise ValidationError("Expected an object")
            return JSONResponse(tracker.add_job(d), status_code=201)
        return JSONResponse(tracker.list_jobs(include_closed=req.query_params.get("closed") == "1"))

    async def job(req):
        i = jid(req)
        if req.method == "DELETE":
            tracker.delete_job(i)
            return Response(status_code=204)
        if req.method == "PATCH":
            d = await body(req)
            if not isinstance(d, dict):
                raise ValidationError("Expected an object")
            return JSONResponse(tracker.update_job(i, d))
        return JSONResponse(tracker.get_job(i))

    async def note(req):
        d = await body(req)
        return JSONResponse(tracker.add_note(jid(req), d.get("text") if isinstance(d, dict) else None), status_code=201)

    async def move(req):
        d = await body(req)
        return JSONResponse(tracker.move(jid(req), d.get("stage") if isinstance(d, dict) else None))

    async def close(req):
        d = await body(req)
        return JSONResponse(tracker.close(jid(req), d.get("reason") if isinstance(d, dict) else None))

    async def reopen(req):
        return JSONResponse(tracker.reopen(jid(req)))

    async def posting_from_url(req):
        d = await body(req)
        url = d.get("url") if isinstance(d, dict) else None
        try:
            # urllib blocks, so run it in a worker thread and keep the server responsive
            r = await run_in_threadpool(read_posting or posting.from_url, url)
        except posting.PostingError as e:
            raise ValidationError(str(e)) from None
        return JSONResponse(r)

    async def interviews(req):
        return JSONResponse(tracker.add_interview(jid(req), await body(req)), status_code=201)

    async def interview(req):
        if req.method == "DELETE":
            return JSONResponse(tracker.delete_interview(jid(req), sub(req)))
        return JSONResponse(tracker.update_interview(jid(req), sub(req), await body(req)))

    async def search(req):
        return JSONResponse({"ids": tracker.search(req.query_params.get("q", ""))})

    def ics(text: str, name: str) -> Response:
        return Response(text, media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def job_calendar(req):
        return ics(tracker.calendar(jid(req)), f"jobledger-follow-up-{jid(req)}.ics")

    async def all_calendar(req):
        return ics(tracker.calendar(), "jobledger-follow-ups.ics")

    async def pulse(req):
        return JSONResponse(tracker.pulse())

    async def export_csv(req):
        return PlainTextResponse(tracker.export_csv(), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="jobledger.csv"'})

    async def backup(req):
        return JSONResponse(tracker.backup(), headers={"Content-Disposition": 'attachment; filename="jobledger-backup.json"'})

    async def restore(req):
        return JSONResponse(tracker.restore(await body(req)))

    def sub(req) -> int:
        try:
            return int(req.path_params["sid"])
        except ValueError:
            raise NotFound() from None

    async def contacts(req):
        return JSONResponse(tracker.add_contact(jid(req), await body(req)), status_code=201)

    async def contact(req):
        if req.method == "DELETE":
            return JSONResponse(tracker.delete_contact(jid(req), sub(req)))
        return JSONResponse(tracker.update_contact(jid(req), sub(req), await body(req)))

    async def prep(req):
        d = await body(req)
        return JSONResponse(tracker.add_prep(jid(req), d.get("text") if isinstance(d, dict) else None), status_code=201)

    async def prep_defaults(req):
        return JSONResponse(tracker.add_default_prep(jid(req)))

    async def prep_item(req):
        if req.method == "DELETE":
            return JSONResponse(tracker.delete_prep(jid(req), sub(req)))
        d = await body(req)
        return JSONResponse(tracker.set_prep_done(jid(req), sub(req), d.get("done") if isinstance(d, dict) else None))

    async def import_csv(req):
        d = await body(req)
        return JSONResponse(tracker.import_csv(d.get("csv") if isinstance(d, dict) else None), status_code=201)

    async def rewrite_line(req):
        key = req.headers.get("x-anthropic-key", "").strip()
        if not key.startswith("sk-ant-") or len(key) > 300:
            raise ValidationError("Add your Anthropic API key on the Data tab to use suggestions")
        d = await body(req)
        want = d.get("requirement") if isinstance(d, dict) else None
        if not isinstance(want, str):
            raise ValidationError("Say which requirement to reword")
        item = next((x for x in tracker.match_job(jid(req))["requirements"] if x["requirement"] == want), None)
        if not item or "evidence" not in item:
            raise ValidationError("There is no resume line to reword for that requirement")
        source = item["evidence"]["line"]  # always taken from the stored resume, never from the request
        try:
            suggestion = (rewriter or rw.anthropic_rewrite)(key, source, want).strip()
        except rw.RewriteError as e:
            return err(str(e), 502)
        ok, reason = rw.guard(source, suggestion)
        out = {"source": source, "ok": ok, "reason": reason}
        if ok:
            out.update(suggestion=suggestion, parts=rw.diff_parts(source, suggestion), unchanged=suggestion == source)
        return JSONResponse(out)

    async def resume(req):
        if req.method == "PUT":
            d = await body(req)
            return JSONResponse(tracker.set_resume(d.get("text") if isinstance(d, dict) else None))
        return JSONResponse(tracker.get_resume())

    async def match(req):
        return JSONResponse(tracker.match_job(jid(req)))

    ASSETS = {"manifest.webmanifest": "application/manifest+json", "sw.js": "text/javascript", "icon-192.png": "image/png",
              "icon-512.png": "image/png", "icon-maskable-512.png": "image/png"}

    async def asset(req):
        name = req.path_params["name"]
        f = index.parent / name
        if name not in ASSETS or not f.exists():
            return PlainTextResponse("Not found", status_code=404)
        return Response(f.read_bytes(), media_type=ASSETS[name], headers={"Cache-Control": "no-cache"})

    async def home(req):
        if index.exists():
            return HTMLResponse(index.read_text("utf-8"))
        return PlainTextResponse("UI not built. Run `npm run build`.", status_code=404)

    routes = [
        Route("/", home),
        Route("/api/health", health),
        Route("/{name}", asset),
        Route("/api/jobs", api(jobs), methods=["GET", "POST"]),
        Route("/api/jobs/{id}", api(job), methods=["GET", "PATCH", "DELETE"]),
        Route("/api/jobs/{id}/notes", api(note), methods=["POST"]),
        Route("/api/jobs/{id}/move", api(move), methods=["POST"]),
        Route("/api/jobs/{id}/close", api(close), methods=["POST"]),
        Route("/api/jobs/{id}/reopen", api(reopen), methods=["POST"]),
        Route("/api/jobs/{id}/match", api(match)),
        Route("/api/jobs/{id}/rewrite", api(rewrite_line), methods=["POST"]),
        Route("/api/jobs/{id}/contacts", api(contacts), methods=["POST"]),
        Route("/api/jobs/{id}/contacts/{sid}", api(contact), methods=["PATCH", "DELETE"]),
        Route("/api/jobs/{id}/prep", api(prep), methods=["POST"]),
        Route("/api/jobs/{id}/prep/defaults", api(prep_defaults), methods=["POST"]),
        Route("/api/jobs/{id}/prep/{sid}", api(prep_item), methods=["PATCH", "DELETE"]),
        Route("/api/import", api(import_csv), methods=["POST"]),
        Route("/api/resume", api(resume), methods=["GET", "PUT"]),
        Route("/api/pulse", api(pulse)),
        Route("/api/search", api(search)),
        Route("/api/posting", api(posting_from_url), methods=["POST"]),
        Route("/api/jobs/{id}/interviews", api(interviews), methods=["POST"]),
        Route("/api/jobs/{id}/interviews/{sid}", api(interview), methods=["PATCH", "DELETE"]),
        Route("/api/calendar.ics", api(all_calendar)),
        Route("/api/jobs/{id}/calendar.ics", api(job_calendar)),
        Route("/api/export.csv", api(export_csv)),
        Route("/api/backup", api(backup)),
        Route("/api/restore", api(restore), methods=["POST"]),
    ]
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            db.close()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.tracker = tracker
    app.state.db = db
    return app
