"""The phone version of the matcher (src/engine/matcher.ts) must agree exactly with the Python one.
Skipped when Node is not installed."""
import json, shutil, subprocess, unittest
from pathlib import Path
from jobledger import matcher
from jobledger.seed import DEMO_POSTINGS, DEMO_RESUME

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def ts(fn, calls):
    out = subprocess.run([NODE, str(ROOT / "tools/ts_eval.mjs"), str(ROOT / "src/engine/matcher.ts"), fn],
                         input=json.dumps(calls), capture_output=True, text=True, timeout=120, cwd=ROOT)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


TRICKY = [
    ("Requirements:\n- Hands-on k8s and CI/CD experience\n- Knowledge base upkeep\n- C++ or C#\n- 5+ years of Node.js\n- Help-center docs",
     "- Ran Kubernetes clusters with continuous integration\n- Wrote help center articles\n- Built C# services\n- Node.js APIs"),
    ("We need someone with strong SQL. You will build dashboards. Must have Tableau experience. Nice to have: Python.",
     "• Built dashboards in Power BI\n• Wrote SQL daily\r\n• Python3 scripts"),
    ("1. Delivering projects on schedule\n2) Managing a Salesforce CRM\n– Coaching agents", "- Delivered 12 projects\n- Kept the HubSpot CRM clean\n- Coached six agents"),
    ("", "- anything"), ("Requirements:\n- Something", ""), ("Requirements:\n- Zoë’s café naïve résumé tools", "- Résumé café work"),
]


@unittest.skipUnless(NODE, "needs node")
class Parity(unittest.TestCase):
    def cases(self):
        out = []
        for name in ("cases.json", "holdout.json", "holdout2.json"):
            out += [(c["posting"], c["resume"]) for c in json.loads((ROOT / "eval" / name).read_text())]
        out += [(p, DEMO_RESUME) for p in DEMO_POSTINGS.values()]
        return out + TRICKY

    def test_match_is_identical(self):
        calls = [list(c) for c in self.cases()]
        got = ts("match", calls)
        for (posting, resume), g in zip(calls, got):
            want = json.loads(json.dumps(matcher.match(posting, resume)))  # tuples to lists, as JSON would
            self.assertEqual(g, want, posting[:80])

    def test_terms_are_identical(self):
        words = sorted({w for p, r in self.cases() for w in (p + " " + r).split()})
        got = ts("terms", [[w] for w in words])
        self.assertEqual(got, [matcher.terms(w) for w in words])


if __name__ == "__main__":
    unittest.main()
