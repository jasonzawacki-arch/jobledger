"""Scores the matcher against hand-labeled cases. Run: python eval/run.py   (add --verbose to list every miss)

Labels were written by a person before the matcher was tuned against them. The number that matters most is
FALSE MATCHES: a requirement labeled gap that the matcher called a match, because that is the failure that
could mislead someone.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobledger import matcher  # noqa: E402

ORDER = ("gap", "partial", "match")


def run(path: Path = Path(__file__).with_name("cases.json")) -> dict:
    cases = json.loads(path.read_text())
    total = correct = false_match = missed_match = 0
    misses = []
    for c in cases:
        got = {x["requirement"]: x["status"] for x in matcher.match(c["posting"], c["resume"])["requirements"]}
        for req, want in c["expect"].items():
            have = got.get(req, "not-extracted")
            total += 1
            if have == want:
                correct += 1
                continue
            if want == "gap" and have == "match":
                false_match += 1
            if want == "match" and have == "gap":
                missed_match += 1
            misses.append((c["name"], req, want, have))
    return {"total": total, "correct": correct, "accuracy": correct / total, "false_matches": false_match, "missed_matches": missed_match, "misses": misses}


SETS = [
    ("cases.json", "tuning set 1 (used to build the matcher)"),
    ("holdout.json", "tuning set 2 (held out at first, then used for the October 2026 improvements)"),
    ("holdout2.json", "held-out set (written before the October 2026 changes, never used to adjust the matcher)"),
]

if __name__ == "__main__":
    for name, label in SETS:
        r = run(Path(__file__).with_name(name))
        print(label)
        print(f"  {r['correct']}/{r['total']} labels agree ({r['accuracy']:.0%}); false matches: {r['false_matches']}; missed matches: {r['missed_matches']}")
        for case, req, want, have in r["misses"]:
            print(f"    {case}: '{req}' labeled {want}, matcher said {have}")
