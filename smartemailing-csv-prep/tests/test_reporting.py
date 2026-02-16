from __future__ import annotations

import unittest

import pandas as pd

from src.reporting import REPORT_COLUMNS, build_report, find_duplicates_by_email, find_duplicates_from_stats


class ReportingTests(unittest.TestCase):
    def test_duplicates_include_source_files(self) -> None:
        df = pd.DataFrame(
            {
                "email": ["dup@example.com", "dup@example.com", "unique@example.com"],
                "source_file": ["first.csv", "second.csv", "first.csv"],
            }
        )

        duplicates = find_duplicates_by_email(df)

        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates.at[0, "email"], "dup@example.com")
        self.assertEqual(duplicates.at[0, "count"], 2)
        self.assertEqual(duplicates.at[0, "source_files"], "first.csv,second.csv")

    def test_report_has_trace_columns(self) -> None:
        invalid_df = pd.DataFrame(
            {
                "issue": ["invalid_email"],
                "bad_email": ["bad@"],
                "email_raw": ["bad@"],
                "company": ["Acme"],
                "source_file": ["first.csv"],
                "source_row_index": [12],
            }
        )
        unknown_df = pd.DataFrame(
            {
                "unknown_code": ["X1"],
                "source_file": ["first.csv"],
                "source_row_index": [12],
                "row_index": [0],
            }
        )
        duplicates_df = pd.DataFrame(
            {
                "email": ["dup@example.com"],
                "count": [2],
                "source_files": ["first.csv,second.csv"],
            }
        )

        report = build_report(invalid_df, unknown_df, duplicates_df)

        self.assertEqual(list(report.columns), REPORT_COLUMNS)
        self.assertIn("first.csv", report["source_file"].astype(str).tolist())
        self.assertTrue((report["type"] == "duplicate_email").any())

    def test_duplicates_from_stats(self) -> None:
        counts = {"a@x.cz": 3, "b@x.cz": 1}
        source_files = {"a@x.cz": {"one.csv", "two.csv"}}

        report = find_duplicates_from_stats(counts, source_files)

        self.assertEqual(len(report), 1)
        self.assertEqual(report.at[0, "email"], "a@x.cz")
        self.assertEqual(report.at[0, "count"], 3)
        self.assertEqual(report.at[0, "source_files"], "one.csv,two.csv")

    def test_report_includes_summary_rows(self) -> None:
        report = build_report(
            invalid_emails_df=pd.DataFrame(),
            unknown_programs_df=pd.DataFrame(),
            duplicates_df=pd.DataFrame(),
            summary_metrics={"input_rows_total": 10, "output_rows_total": 9},
        )

        self.assertEqual(list(report.columns), REPORT_COLUMNS)
        summary = report[report["type"] == "summary"]
        self.assertEqual(len(summary), 2)
        self.assertIn("input_rows_total", summary["row_index"].tolist())


if __name__ == "__main__":
    unittest.main()
