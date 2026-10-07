import unittest

from pandoc import phprep


SAMPLE = """
--------------------------------------------------------------------------------------------------------
SUMMARY OF THIS PREDICTION
       Group      pKa  model-pKa   ligand atom-type
   ASP  34 A     5.70       3.80
   GLU  50 A     4.10       4.50
   HIS 164 A     6.30       6.50
   LYS 201 B    10.20      10.50
--------------------------------------------------------------------------------------------------------
"""


class ProtonationParsingTests(unittest.TestCase):
    def test_propka_states_follow_selected_ph(self):
        rows = phprep.parse_propka_summary(SAMPLE, 5.0)
        by_residue = {row["residue"]: row for row in rows}
        self.assertEqual(by_residue["A:34"]["template"], "ASH")
        self.assertEqual(by_residue["A:50"]["template"], "GLU")
        self.assertEqual(by_residue["A:164"]["template"], "HIP")
        self.assertEqual(by_residue["B:201"]["template"], "LYS")

    def test_neutral_histidine_requires_tautomer_review(self):
        rows = phprep.parse_propka_summary(SAMPLE, 7.0)
        his = next(row for row in rows if row["residue"] == "A:164")
        self.assertIsNone(his["template"])
        self.assertTrue(his["review_required"])
        self.assertIn("HID/HIE", his["suggested_state"])

    def test_near_pka_is_flagged(self):
        rows = phprep.parse_propka_summary(SAMPLE, 5.0)
        asp = next(row for row in rows if row["residue"] == "A:34")
        self.assertTrue(asp["near_pKa"])

    def test_template_overrides_are_serialized(self):
        rows = phprep.parse_propka_summary(SAMPLE, 7.0)
        assignments = phprep.template_assignments(rows, {"A:164": "HID"})
        self.assertIn("A:164=HID", assignments)
        self.assertIn("A:34=ASP", assignments)


if __name__ == "__main__":
    unittest.main()
