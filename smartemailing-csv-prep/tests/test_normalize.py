from __future__ import annotations

import unittest

import pandas as pd

from src.normalize import detect_source


class DetectSourceTests(unittest.TestCase):
    def test_matches_columns_case_insensitive_and_without_diacritics(self) -> None:
        df = pd.DataFrame(columns=["Firma", "EMAIL", "Země"])
        cfg = {
            "sample_source": {
                "detect_columns": ["firma", "email", "zeme"],
                "map": {
                    "company": "firma",
                    "email_raw": "email",
                    "country": "zeme",
                },
            }
        }

        detected = detect_source(df, cfg)

        self.assertEqual(detected.name, "sample_source")
        self.assertEqual(detected.mapping["company"], "Firma")
        self.assertEqual(detected.mapping["email_raw"], "EMAIL")
        self.assertEqual(detected.mapping["country"], "Země")


if __name__ == "__main__":
    unittest.main()
