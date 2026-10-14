# JobLedger

A local-first job application tracker. Your data lives in one SQLite file on your own machine. It costs nothing to run, needs no account and sends nothing anywhere.

![Board, light](docs/board-light.png)

## What it does

- **Board** with five stages (Saved, Applied, Screen, Interview, Offer). Drag cards between columns or use the stage track on a job.
- **Closing a job** as rejected, withdrawn or no response keeps it in your history and funnel numbers but off the board. It can be reopened.
- **History timeline** for every job: stage moves, notes, follow-up dates and closures, each with a date.
- **Follow-up nudges.** A job is flagged when the date you set arrives. With no date, jobs at Applied, Screen or Interview are flagged after 10 idle days (any note or stage change resets the clock). Saved, Offer and closed jobs are never flagged.
- **Pulse** dashboard: active jobs, how far applications get (counted by the furthest stage each one ever reached, so a rejected interview still counts), share that reached a screen, average days in stage, and what needs a follow-up.
- **Your data stays yours:** CSV export, JSON backup and validated restore (all or nothing).
- **Resume matching.** Paste your resume once (Data tab) and a job's posting text (Edit details). The Job view lists each requirement as Match, Partial or Gap, with the resume line that supports it and the shared words highlighted. It works by comparing words and common spellings (k8s/Kubernetes, Postgres/PostgreSQL), runs locally and costs nothing.
- **People and prep.** Keep the recruiter or hiring manager on each job (with a one-tap "Contacted today") and an interview checklist, with an optional standard list.
- **Follow-up email drafts.** A plain template filled in from the job and contact. Edit it, copy it, or open it in your email app. Nothing is sent from the app.
- **CSV import** from a simple spreadsheet (company and title columns, a few optional ones) or from your own export. All or nothing, with the row number in any error.
- **Optional wording suggestions** (off by default, bring your own Anthropic API key). See below.
- **Installable on Android** (Chrome's Install app) when served over HTTPS.
- Liquid-glass interface in light and dark, usable at phone width.

| Job, dark | Pulse, phone |
|---|---|
| ![](docs/job-dark.png) | ![](docs/pulse-phone.png) |

## Run it

```bash
pip install -r requirements.txt
npm install && npm run build      # builds dist/index.html (needs Node 18+)
python -m jobledger --demo        # --demo loads made-up sample jobs into an empty database
```

Open http://127.0.0.1:8000. Settings come from environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `JL_DB` | `jobledger.sqlite3` | database file |
| `JL_HOST` / `JL_PORT` | `127.0.0.1` / `8000` | where to listen |
| `JL_TOKEN` | unset | if set, every API call needs it (the UI asks once) |
| `JL_HOSTS` | unset | extra host names to answer on without a token, comma-separated (e.g. `laptop.local`) |
| `JL_TZ` | this computer's zone | time zone for follow-up dates, e.g. `America/New_York` |

## Wording suggestions (optional, your own key)

Add an Anthropic API key on the Data tab and a "Suggest wording" button appears under each matched requirement. It sends that one resume line and that one requirement to Claude and shows the reply next to your original with every changed word highlighted.

- The line is always read from your stored resume on the server, never from the browser request.
- The key stays in your browser's local storage and travels only in a header on those requests. The server uses it once and does not store or log it.
- Each suggestion is a short request on your own account (the default model is `claude-haiku-5-5`, change it with `JL_MODEL`). Without a key, nothing is ever sent anywhere.
- A check rejects replies that add numbers, names or tools that are not in the original line. This catches the obvious ways to invent things. It is not a proof, so read each suggestion before you use it.

## How accurate is the matcher?

`python eval/run.py` scores it against hand-labeled requirements: 33 used while tuning (88% agree) and 16 written afterward and never used to adjust it (75% agree). It has never called a true gap a match in either set; its misses lean cautious (calling a match "partial"). Sixteen to thirty-three labels is a small sample, so treat the percentages as a rough guide, not a guarantee. Tests fail if false matches appear or accuracy drops below a floor.

## Tests

```bash
python -m unittest discover -s tests -t .   # rules, API over real HTTP, matcher accuracy, and browser flows
npm run typecheck
```

Rule tests use an injected clock, so "10 days idle" is tested without waiting. Browser tests (Playwright) cover add, drag, notes, close, delete confirmation, backup and restore, escaping of hostile text, phone width, themes and the token prompt. They skip themselves if Playwright or the built UI is missing. The Anthropic client is tested against a local fake; the real API is never called in tests.

## Design notes

- `jobledger/service.py` holds every rule and knows nothing about HTTP. `api.py` is a thin JSON layer.
- Schema is versioned with `PRAGMA user_version`; the app refuses to open a database from a newer version.
- CSV cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so spreadsheets do not run them as formulas.
- The UI escapes all text before rendering and only links http(s) addresses.
- The UI is TypeScript compiled by a small dependency-free `build.mjs` into one HTML file.
- Other websites open in your browser cannot use the API. Writes from another origin are refused, request bodies must be `application/json` (which a plain form cannot send), and without a token the server only answers to `localhost`, `127.0.0.1` or names in `JL_HOSTS`, which blocks DNS rebinding.
- The token is accepted only in the `Authorization` header, never in the URL, so it does not end up in logs or history.
- The database is closed cleanly when the server stops.

## Honest limits

- **Phone use needs HTTPS.** Android only offers Install app on a secure address. On your home network that means putting the server behind something like a Tailscale or Cloudflare tunnel. Set `JL_TOKEN` before you expose it.
- **Single user.** One token at most, no accounts. If you expose it beyond your own machine, put it behind HTTPS and set `JL_TOKEN` (without one, it refuses any host name other than localhost and `JL_HOSTS`). The token is sent as a bearer header and stored in the browser's local storage.
- **Time zone.** Follow-up dates follow the server computer's time zone (or `JL_TZ`). The CSV export's `created_utc` column stays in UTC.
- **No automatic updates from email or job sites.** You enter and move jobs yourself.
- **Matching is word-based, not understanding.** It can miss a requirement your resume covers in different words, and a "Match" only means shared wording. It never rewrites your resume or adds claims: evidence is always a line copied from it. Suggested rewording, if added later, would be optional and use your own Claude API key.
- Backups include your resume and restore everything at once; there is no merge.
