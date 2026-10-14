"""Optional wording suggestions using the user's own Anthropic API key.

Off unless the user adds a key. The key is sent with each request, used once and never stored or logged by the server.
The model only ever sees one resume line plus one requirement, and its answer is checked by `guard` before it is shown.
The guard blocks the obvious ways a rewrite can invent things (new numbers, new names or tools). It is a safety net,
not a proof: the UI always shows the original line next to the suggestion with every change highlighted.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
SYSTEM = (
    "You reword one line from a person's resume so it reads more clearly for one job requirement. "
    "Rules: use only facts already in the line. Do not add numbers, tools, technologies, employers, titles, results or years. "
    "Do not make the work sound bigger than it was. Keep any note such as '(side project)'. Keep it to one sentence, at most 35 words. "
    "If the line cannot be improved, return it unchanged. Reply with the sentence only."
)

WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#./'-]*")


class RewriteError(Exception):
    """Something went wrong talking to the model; the message is safe to show."""


def _tokens(s: str) -> set[str]:
    return {w.lower().strip(".'-/") for w in WORD.findall(s)}


def guard(source: str, rewrite: str) -> tuple[bool, str]:
    """Returns (ok, reason). Rejects rewrites that add numbers, names or tools, or that are not one plain sentence."""
    rewrite = rewrite.strip()
    if not rewrite:
        return False, "The suggestion was empty."
    if "\n" in rewrite or len(rewrite) > max(240, int(len(source) * 1.6)):
        return False, "The suggestion was too long or not a single sentence."
    src = _tokens(source)
    for w in WORD.findall(rewrite):
        lw = w.lower().strip(".'-/")
        if any(ch.isdigit() for ch in w) and lw not in src:
            return False, f"The suggestion added a number or version ({w}) that is not in your line."
    # new capitalized words (names, tools, employers) that are not the first word and not already in the source
    words = WORD.findall(rewrite)
    for w in words[1:]:
        if (w[0].isupper() or (w.isupper() and len(w) > 1)) and w.lower().strip(".'-/") not in src:
            return False, f"The suggestion added a name or tool ({w}) that is not in your line."
    return True, ""


def diff_parts(source: str, rewrite: str) -> list[list]:
    """[text, changed] pieces of the rewrite, marking words that are not in the source line."""
    a, b = source.split(), rewrite.split()
    out, run = [], []
    sm = difflib.SequenceMatcher(a=[w.lower().strip(".,;") for w in a], b=[w.lower().strip(".,;") for w in b], autojunk=False)
    parts = []
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if j2 > j1:
            parts.append((" ".join(b[j1:j2]), tag != "equal"))
    for text, changed in parts:
        out.append([text, changed])
    # add spaces between pieces so the UI can concatenate them
    return [[t + (" " if i < len(out) - 1 else ""), c] for i, (t, c) in enumerate(out)]


def anthropic_rewrite(api_key: str, source: str, requirement: str, model: str | None = None, timeout: float = 30) -> str:
    body = {
        "model": model or os.environ.get("JL_MODEL", DEFAULT_MODEL),
        "max_tokens": 200,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": f"Job requirement: {requirement}\n\nResume line: {source}"}],
    }
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RewriteError("Anthropic did not accept that API key.") from None
        if e.code == 429:
            raise RewriteError("Anthropic says you are over your rate limit. Try again in a minute.") from None
        raise RewriteError(f"Anthropic returned an error ({e.code}).") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RewriteError("Could not reach Anthropic from the server.") from None
    try:
        return "".join(b["text"] for b in data["content"] if b.get("type") == "text").strip()
    except (KeyError, TypeError):
        raise RewriteError("Anthropic sent a reply this app could not read.") from None


Rewriter = Callable[[str, str, str], str]  # (api_key, source line, requirement) -> suggestion
