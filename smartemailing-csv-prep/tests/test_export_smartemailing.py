from __future__ import annotations

import unittest

import pandas as pd

from src.export_smartemailing import build_import_df, drop_empty_columns, dataframe_to_csv_bytes
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


if __name__ == "__main__":
    unittest.main()
