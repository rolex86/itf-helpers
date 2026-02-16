from __future__ import annotations

import unittest

import pandas as pd

from src.transforms import split_emails


class SplitEmailsTests(unittest.TestCase):
    def test_splits_and_reports_invalid_with_source_trace(self) -> None:
        df = pd.DataFrame(
            {
                "email_raw": ["a@b.cz; c@d.cz", "invalid@", ""],
                "source_row_index": [1, 2, 3],
                "source_file": ["input.csv", "input.csv", "input.csv"],
            }
        )

        expanded, invalid = split_emails(df, [",", ";"])

        self.assertEqual(expanded["email"].tolist(), ["a@b.cz", "c@d.cz"])
        self.assertEqual(sorted(invalid["issue"].tolist()), ["invalid_email", "missing_email"])
        self.assertIn(2, invalid["source_row_index"].tolist())
        self.assertIn(3, invalid["source_row_index"].tolist())


if __name__ == "__main__":
    unittest.main()
