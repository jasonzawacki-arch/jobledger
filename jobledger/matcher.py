"""Compare a job posting with the user's own resume lines. Free, local, and deliberately simple.

Guardrail: this module never writes new resume wording. Every piece of evidence it returns is a line copied
verbatim from the resume, so nothing can be claimed that the resume does not already say.
"""
from __future__ import annotations

import re

STOP = set("""a an and are as at be been by can do does for from has have in into is it its of on or our over per so such that the their them they this to
us we will with you your who what when where which while within without about across also any all both each more most other some than then these those
using use used work working works team teams role roles job company help helps ensure including include includes strong ability able must should plus
experience years year related knowledge understanding familiarity proficiency excellent good great new etc e.g i.e candidate responsibilities
requirements qualifications preferred required looking join seeking develop developing
skill skills quickly accurately proven record hands-on handson successfully effectively various variety""".split())

# Different spellings of the same skill. Each group maps to its first entry.
SYNONYMS = [
    ["javascript", "js"], ["typescript", "ts"], ["kubernetes", "k8s"], ["postgresql", "postgres", "psql"], ["python", "python3"],
    ["ci", "ci/cd", "cicd"], ["amazon web services", "aws"], ["google cloud", "gcp"], ["machine learning", "ml"],
    ["natural language processing", "nlp"], ["rest", "restful"], ["node", "nodejs", "node.js"], ["react", "reactjs", "react.js"],
    ["sql", "sqlite", "mysql", "postgresql"], ["api", "apis"], ["test", "testing", "tests", "tested", "unit test"],
    ["dashboard", "dashboards", "reporting", "reports", "report"], ["analytics", "analysis", "analyze", "analyzed", "analyst"],
    ["demo", "demos", "demonstration", "demonstrations"], ["customer", "customers", "client", "clients"],
    ["docs", "documentation", "documented", "document", "documents"], ["kb", "wiki"],
]

# A resume line that names a specific tool also shows the general skill it belongs to: SQLite is a database,
# Salesforce is a CRM. Only the resume side is widened, so a posting that asks for Salesforce still needs Salesforce.
IMPLIES = {
    "sqlite": ["databas"], "sql": ["databas"], "postgresql": ["databas"], "oracl": ["databas"], "mongodb": ["databas"],
    "salesforc": ["crm"], "hubspot": ["crm"], "zendesk": ["ticket", "helpdesk"], "jira": ["ticket"],
    "terraform": ["iac"], "cloudform": ["iac"], "jenkin": ["ci"], "github": ["git"],
    "docker": ["contain"], "kubernetes": ["contain"], "amazon web services": ["cloud"], "azur": ["cloud"], "google cloud": ["cloud"],
}
# Stems that the Porter rules leave apart although they are the same word.
STEMFIX = {"container": "contain"}
_CANON: dict[str, str] = {}
for g in SYNONYMS:
    for w in g:
        _CANON.setdefault(w, g[0])

WORD = re.compile(r"[a-z0-9][a-z0-9+#./-]*[a-z0-9+#]|[a-z0-9]")
BULLET = re.compile(r"^\s*(?:[-*•●▪–]|\d+[.)])\s+")
CUES = re.compile(r"\b(experience|proficien|knowledge|familiar|ability|skilled?|required|must|strong|understanding|degree|certif|expertise|responsible|you will|you'll|we need|looking for|preferred|plus)\b", re.I)
HEAD = re.compile(r"^(about|who we are|what you|responsibilit|requirement|qualification|benefits|perks|compensation|salary|our |why |equal|how to|nice to have|preferred|the role|overview)", re.I)

MATCH_AT, PARTIAL_AT = 0.6, 0.3
# Everyday verbs say little about fit ("manage", "run"), so they count half as much as the skill words around them.
WEAK = {"run", "manag", "maintain", "deliv", "write", "build", "lead", "creat", "perform", "provid", "handl", "own",
        "drive", "work", "make", "get", "keep", "set", "take"}


def _w(t: str) -> float:
    return 0.5 if t in WEAK else 1.0


IRREGULAR = {"wrote": "write", "written": "write", "built": "build", "ran": "run", "led": "lead", "made": "make", "taught": "teach",
             "kept": "keep", "sold": "sell", "began": "begin", "chose": "choose", "drove": "drive", "grew": "grow", "won": "win"}
# Multi-word skills are folded into one token before matching, so "continuous integration" equals "CI/CD".
PHRASES = {"continuous integration": "ci", "continuous delivery": "ci", "continuous deployment": "ci", "machine learning": "ml",
           "natural language processing": "nlp", "amazon web services": "aws", "google cloud": "gcp", "unit test": "test", "unit tests": "test",
           "unit testing": "test", "power bi": "powerbi", "project management": "projectmanagement",
           "knowledge base": "kb", "help center": "kb", "help desk": "helpdesk", "service desk": "helpdesk",
           "infrastructure as code": "iac", "version control": "git"}
_PHRASE_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in PHRASES), key=len, reverse=True)) + r")\b", re.I)


_HYPHEN = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")


def _fold(text: str) -> str:
    """Splits hyphenated words into their parts, then folds multi-word skills into one token."""
    split = re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)+", lambda m: m.group(0) if m.group(0).lower() in STOP else _HYPHEN.sub(" ", m.group(0)), text)
    return _PHRASE_RE.sub(lambda m: PHRASES[m.group(1).lower()], split)


def _porter(w: str) -> str:
    """The classic Porter (1980) stemmer, so "deliver", "delivering" and "delivered" all become "deliv"."""
    if len(w) <= 2:
        return w
    V = "aeiou"

    def cons(word, i):
        ch = word[i]
        if ch in V:
            return False
        if ch == "y":
            return i == 0 or not cons(word, i - 1)
        return True

    def m(stem):
        n, i, L = 0, 0, len(stem)
        while i < L and cons(stem, i):
            i += 1
        while i < L:
            while i < L and not cons(stem, i):
                i += 1
            if i >= L:
                break
            n += 1
            while i < L and cons(stem, i):
                i += 1
        return n

    def has_v(stem):
        return any(not cons(stem, i) for i in range(len(stem)))

    def dbl(word):
        return len(word) > 1 and word[-1] == word[-2] and cons(word, len(word) - 1)

    def cvc(word):
        return (len(word) >= 3 and cons(word, len(word) - 3) and not cons(word, len(word) - 2)
                and cons(word, len(word) - 1) and word[-1] not in "wxy")

    def rep(word, pairs, cond):
        for suf, new in pairs:
            if word.endswith(suf):
                stem = word[: -len(suf)]
                return (stem + new) if cond(stem) else word, True
        return word, False

    # 1a
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]
    # 1b
    flag = False
    if w.endswith("eed"):
        if m(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed") and has_v(w[:-2]):
        w, flag = w[:-2], True
    elif w.endswith("ing") and has_v(w[:-3]):
        w, flag = w[:-3], True
    if flag:
        if w.endswith(("at", "bl", "iz")):
            w += "e"
        elif dbl(w) and w[-1] not in "lsz":
            w = w[:-1]
        elif m(w) == 1 and cvc(w):
            w += "e"
    # 1c
    if w.endswith("y") and has_v(w[:-1]):
        w = w[:-1] + "i"
    # 2
    w, _ = rep(w, [("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"), ("izer", "ize"), ("abli", "able"),
                   ("alli", "al"), ("entli", "ent"), ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
                   ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous"), ("aliti", "al"),
                   ("iviti", "ive"), ("biliti", "ble")], lambda st: m(st) > 0)
    # 3
    w, _ = rep(w, [("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"), ("ical", "ic"), ("ful", ""), ("ness", "")],
               lambda st: m(st) > 0)
    # 4
    for suf in ("al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion", "ou", "ism", "ate",
                "iti", "ous", "ive", "ize"):
        if w.endswith(suf):
            st = w[: -len(suf)]
            if m(st) > 1 and (suf != "ion" or st.endswith(("s", "t"))):
                w = st
            break
    # 5
    if w.endswith("e"):
        st = w[:-1]
        if m(st) > 1 or (m(st) == 1 and not cvc(st)):
            w = st
    if m(w) > 1 and dbl(w) and w.endswith("l"):
        w = w[:-1]
    return w


def _stem(w: str) -> str:
    w = IRREGULAR.get(w, w)
    if not w.isalpha():
        return w  # leave tokens like "c++", "ci/cd" or "s3" alone
    w = _porter(w)
    return STEMFIX.get(w, w)


def terms(text: str) -> list[str]:
    """Important words in a line, lowercased, stemmed, with synonyms folded together."""
    out: list[str] = []
    for w in WORD.findall(_fold(text).lower()):
        w = w.strip(".-/")
        if not w or w in STOP or (len(w) < 2 and w not in ("c", "r")) or re.fullmatch(r"\d+\+?", w):
            continue
        w = _CANON.get(w) or _CANON.get(_stem(w)) or _stem(w)
        if w not in out:
            out.append(w)
    return out


def _originals(text: str) -> dict[str, str]:
    """Maps each folded term back to the word as written, so the UI can say 'business' instead of 'busines'."""
    out: dict[str, str] = {}
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9+#./-]*", text):
        w = m.group(0).lower().strip(".-/")
        out.setdefault(_CANON.get(w) or _CANON.get(_stem(w)) or _stem(w), m.group(0).strip(".-/"))
    return out


def _pairs(text: str) -> set[str]:
    ws = [w for w in WORD.findall(_fold(text).lower())]
    return {f"{a} {b}" for a, b in zip(ws, ws[1:]) if a not in STOP and b not in STOP}


def resume_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = BULLET.sub("", raw).strip()
        if len(line) >= 8:
            lines.append(line)
    return lines


def requirements(posting: str, limit: int = 12) -> list[str]:
    """Pick the lines of a posting that read like asks. Bullets win; otherwise sentences with requirement cues."""
    cands: list[str] = []
    bullets = [BULLET.sub("", l).strip() for l in posting.splitlines() if BULLET.match(l)]
    pool = bullets or [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", posting)]
    for line in pool:
        if len(line) < 8 or len(line) > 260 or HEAD.match(line):
            continue
        if bullets or CUES.search(line):
            if len(terms(line)) >= (1 if bullets else 2):
                cands.append(line.rstrip(" .;"))
    seen, out = set(), []
    for c in cands:
        k = " ".join(sorted(terms(c)))
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out[:limit]


def _highlight(line: str, matched: set[str]) -> list[list]:
    """Splits a resume line into [text, is_match] pieces so the UI can mark shared words."""
    parts, pos = [], 0
    spans = [(m.start(), m.end()) for m in _PHRASE_RE.finditer(line) if PHRASES[m.group(1).lower()] in matched]
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9+#./-]*", line):
        if any(a <= m.start() < b for a, b in spans):
            if m.start() > pos:
                parts.append([line[pos:m.start()], False])
            parts.append([m.group(0), True])
            pos = m.end()
            continue
        w = m.group(0).lower().strip(".-/")
        key = _CANON.get(w) or _CANON.get(_stem(w)) or _stem(w)
        if key in matched:
            if m.start() > pos:
                parts.append([line[pos:m.start()], False])
            parts.append([m.group(0), True])
            pos = m.end()
    if pos < len(line):
        parts.append([line[pos:], False])
    return parts or [[line, False]]


def match(posting: str, resume: str) -> dict:
    lines = resume_lines(resume)
    line_terms = []
    for l in lines:
        lt = set(terms(l))
        lt |= {x for t in lt for x in IMPLIES.get(t, ())}  # SQLite also shows "database", and so on
        line_terms.append((l, lt, _pairs(l)))
    reqs = requirements(posting)
    out = []
    for r in reqs:
        rt = terms(r)
        rp = _pairs(r)
        orig = _originals(r)
        best, best_score, best_hit = None, 0.0, set()
        for line, lt, lp in line_terms:
            hit = {t for t in rt if t in lt}
            # shared two-word phrases count extra: "unit testing" beats "unit" and "testing" apart
            bonus = 0.15 * len(rp & lp)
            score = (sum(_w(t) for t in hit) / sum(_w(t) for t in rt) if rt else 0) + min(bonus, 0.3)
            if score > best_score:
                best, best_score, best_hit = line, score, hit
        score = min(best_score, 1.0)
        status = "match" if score >= MATCH_AT else "partial" if score >= PARTIAL_AT else "gap"
        item = {"requirement": r, "status": status, "score": round(score, 2), "missing": [orig.get(t, t) for t in rt if t not in best_hit] if best else [orig.get(t, t) for t in rt]}
        if best is not None and status != "gap":
            item["evidence"] = {"line": best, "parts": _highlight(best, best_hit)}
        out.append(item)
    n = len(out)
    covered = sum(1 for o in out if o["status"] == "match") + 0.5 * sum(1 for o in out if o["status"] == "partial")
    return {
        "has_resume": bool(lines), "has_posting": bool(posting.strip()), "requirements": out,
        "score": round(covered / n * 100) if n and lines else None,
        "counts": {k: sum(1 for o in out if o["status"] == k) for k in ("match", "partial", "gap")},
    }
