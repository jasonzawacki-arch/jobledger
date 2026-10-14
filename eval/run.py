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


if __name__ == "__main__":
    print("tuning set")
    r = run()
    print(f"{r['correct']}/{r['total']} labels agree ({r['accuracy']:.0%}); false matches: {r['false_matches']}; missed matches: {r['missed_matches']}")
    if "--verbose" in sys.argv or r["misses"]:
        for name, req, want, have in r["misses"]:
            print(f"  {name}: '{req}' labeled {want}, matcher said {have}")
    print("held-out set (written after tuning, never used to adjust the matcher)")
    h = run(Path(__file__).with_name("holdout.json"))
    print(f"{h['correct']}/{h['total']} labels agree ({h['accuracy']:.0%}); false matches: {h['false_matches']}; missed matches: {h['missed_matches']}")
    for name, req, want, have in h["misses"]:
        print(f"  {name}: '{req}' labeled {want}, matcher said {have}")
