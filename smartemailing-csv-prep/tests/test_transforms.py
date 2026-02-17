from __future__ import annotations

import unittest

import pandas as pd

from src.transforms import (
    apply_country_bucket,
    parse_name_fields,
    parse_program_codes,
    split_emails,
    validate_emails_without_split,
)


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

    def test_validate_emails_without_split_reports_invalid(self) -> None:
        df = pd.DataFrame(
            {
                "email_raw": ["ok@example.com", "bad@", ""],
                "source_row_index": [1, 2, 3],
            }
        )

        valid, invalid = validate_emails_without_split(df)

        self.assertEqual(valid["email"].tolist(), ["ok@example.com"])
        self.assertEqual(sorted(invalid["issue"].tolist()), ["invalid_email", "missing_email"])

    def test_parse_program_codes_supports_aliases_and_separators(self) -> None:
        codes = parse_program_codes(
            "CRM;Plus Mobile|PS",
            separators=[",", ";", "|"],
            aliases={"plus mobile": "PLUS_MOBILE", "ps": "PLUS_SYSTEM"},
        )

        self.assertEqual(codes, {"CRM", "PLUS_MOBILE", "PLUS_SYSTEM"})

    def test_country_bucket_supports_aliases(self) -> None:
        df = pd.DataFrame({"country": ["Česká republika", "Deutschland", "Spain"]})
        cfg = {
            "cz_sk": ["CZ", "SK"],
            "de_at_ch": ["DE", "AT", "CH"],
            "other": "EN",
            "aliases": {
                "česká republika": "CZ",
                "deutschland": "DE",
            },
        }

        out = apply_country_bucket(df, cfg)

        self.assertEqual(out["country_bucket"].tolist(), ["CZ_SK", "DE_AT_CH", "EN"])

    def test_parse_name_fields_extracts_titles_before_and_after(self) -> None:
        cfg = {
            "title_before_regex": r"(^|\s)(Bc\.|BcA\.|Ing\.|Ing\.arch\.|JUDr\.|MUDr\.|MVDr\.|MgA\.|Mgr\.|PhDr\.|RNDr\.|ThDr\.|ThLic\.|doc\.|prof\.)(\s|$)",
            "title_after_regex": r"(^|\s)(CSc\.|Dr\.|DrSc\.|Ph\.D\.|Th\.D\.|MBA|DiS\.|ACCA|FCCA)(\s|$)",
            "punctuation_strip_regex": r"[\.,;:!\?\-]",
        }

        title_before, first_name, last_name, title_after = parse_name_fields("Ing. Jan Novák, Ph.D.", cfg)

        self.assertEqual(title_before, "Ing.")
        self.assertEqual(first_name, "Jan")
        self.assertEqual(last_name, "Novák")
        self.assertEqual(title_after, "Ph.D.")

    def test_parse_name_fields_handles_missing_space_after_comma_and_no_dot_variants(self) -> None:
        cfg = {
            "title_before_regex": r"(^|\s)(Bc\.|BcA\.|Ing\.|Ing\.arch\.|JUDr\.|MUDr\.|MVDr\.|MgA\.|Mgr\.|PhDr\.|RNDr\.|ThDr\.|ThLic\.|doc\.|prof\.)(\s|$)",
            "title_after_regex": r"(^|\s)(CSc\.|Dr\.|DrSc\.|Ph\.D\.|Th\.D\.|MBA|DiS\.|ACCA|FCCA)(\s|$)",
            "punctuation_strip_regex": r"[\.,;:!\?\-]",
        }

        title_before, first_name, last_name, title_after = parse_name_fields("Ing Jan Novák,PhD", cfg)

        self.assertEqual(title_before, "Ing.")
        self.assertEqual(first_name, "Jan")
        self.assertEqual(last_name, "Novák")
        self.assertEqual(title_after, "Ph.D.")


if __name__ == "__main__":
    unittest.main()
