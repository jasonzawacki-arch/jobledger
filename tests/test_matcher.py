"""Matcher building blocks: stemming, hyphens, tool-to-skill implications, verb weighting."""
import unittest
from jobledger import matcher as m


class Matcher(unittest.TestCase):
    def test_word_forms_agree(self):
        for group in (["deliver", "delivering", "delivered", "delivers"], ["write", "writing", "wrote", "writes"],
                      ["database", "databases"], ["container", "containers", "containerized"], ["demo", "demonstrations"]):
            self.assertEqual(len({tuple(m.terms(w)) for w in group}), 1, group)

    def test_hyphens(self):
        self.assertEqual(m.terms("help-center"), m.terms("help center"))
        self.assertNotIn("hand", m.terms("hands-on Kubernetes"))  # stop phrase stays a stop phrase

    def test_tool_implies_skill_on_resume_side_only(self):
        r = m.match("Requirements:\n- Experience with databases", "- Built an API on SQLite")
        self.assertEqual(r["requirements"][0]["status"], "match")
        r = m.match("Requirements:\n- Experience with relational databases", "- Built an API on SQLite")
        self.assertEqual(r["requirements"][0]["status"], "partial")  # "relational" is still unproven
        r = m.match("Requirements:\n- Experience with SQLite", "- Designed relational databases for reporting")
        self.assertNotEqual(r["requirements"][0]["status"], "match")  # a general skill does not prove the specific tool

    def test_everyday_verbs_count_less(self):
        r = m.match("Requirements:\n- Manage a Salesforce CRM", "- Kept a Salesforce CRM clean for 40 sellers")
        self.assertEqual(r["requirements"][0]["status"], "match")

    def test_evidence_is_always_a_resume_line(self):
        resume = "- Built an API on SQLite\n- Wrote tests"
        for item in m.match("Requirements:\n- Databases\n- Testing\n- Kubernetes", resume)["requirements"]:
            if "evidence" in item:
                self.assertIn(item["evidence"]["line"], resume)


if __name__ == "__main__":
    unittest.main()
