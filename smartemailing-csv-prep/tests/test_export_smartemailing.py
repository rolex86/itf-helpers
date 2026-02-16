from __future__ import annotations

import unittest

import pandas as pd

from src.export_smartemailing import (
    build_import_df,
    dataframe_to_csv_bytes,
    deduplicate_import_df,
    drop_empty_columns,
)
from src.schema import Schema


class BuildImportDfTests(unittest.TestCase):
    def test_unknown_program_report_contains_source_trace(self) -> None:
        schema = Schema(columns=["E-mail", "Společnost", "KNOWN"], columns_set={"E-mail", "Společnost", "KNOWN"})
        cfg = {
            "smartemailing": {
                "field_map": {
                    "E-mail": "email",
                    "Společnost": "company",
                },
                "programs": {
                    "mode": "one_column_per_code",
                    "fill_value": "{code}",
                    "also_fill_combined_field": False,
                },
            }
        }
        df = pd.DataFrame(
            {
                "email": ["a@b.cz"],
                "company": ["Acme"],
                "programs_raw": ["KNOWN,UNKNOWN"],
                "source_row_index": [7],
                "source_file": ["input.csv"],
            }
        )

        import_df, unknown_df = build_import_df(df, schema, cfg)

        self.assertEqual(import_df.at[0, "KNOWN"], "KNOWN")
        self.assertEqual(len(unknown_df), 1)
        self.assertEqual(unknown_df.at[0, "unknown_code"], "UNKNOWN")
        self.assertEqual(unknown_df.at[0, "source_file"], "input.csv")
        self.assertEqual(unknown_df.at[0, "source_row_index"], 7)

    def test_drop_empty_columns_keeps_only_columns_with_content(self) -> None:
        df = pd.DataFrame(
            {
                "E-mail": ["a@b.cz", ""],
                "Společnost": ["", "   "],
                "Město": ["", "Brno"],
                "Poznámka": [" ", ""],
            }
        )

        out = drop_empty_columns(df)

        self.assertEqual(list(out.columns), ["E-mail", "Město"])

    def test_dataframe_to_csv_bytes_respects_encoding(self) -> None:
        df = pd.DataFrame({"Jméno": ["Žluťoučký"]})

        csv_bytes = dataframe_to_csv_bytes(df, sep=";", encoding="cp1250")
        decoded = csv_bytes.decode("cp1250")

        self.assertIn("Jméno", decoded)
        self.assertIn("Žluťoučký", decoded)

    def test_deduplicate_import_df_keeps_first_and_reports_removed(self) -> None:
        df = pd.DataFrame(
            {
                "E-mail": ["a@x.cz", "a@x.cz", "b@x.cz"],
                "country_bucket": ["CZ_SK", "EN", "EN"],
                "__row_order": [0, 1, 2],
            }
        )

        out, removed = deduplicate_import_df(df, email_column="E-mail", keep="first")

        self.assertEqual(removed, 1)
        self.assertEqual(out["E-mail"].tolist(), ["a@x.cz", "b@x.cz"])


if __name__ == "__main__":
    unittest.main()
