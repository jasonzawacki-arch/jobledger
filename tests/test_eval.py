import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import run as ev  # noqa: E402


class Eval(unittest.TestCase):
    def test_floors(self):
        for name, floor in (("cases.json", 0.8), ("holdout.json", 0.7)):
            r = ev.run(ROOT / "eval" / name)
            self.assertEqual(r["false_matches"], 0, f"{name}: a true gap was called a match")
            self.assertGreaterEqual(r["accuracy"], floor, f"{name}: {r['misses']}")
