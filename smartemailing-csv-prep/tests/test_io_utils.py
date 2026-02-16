from __future__ import annotations

import unittest

from src.io_utils import read_csv_best_effort


class ReadCsvBestEffortTests(unittest.TestCase):
    def test_prefers_comma_when_data_is_comma_separated(self) -> None:
        content = "firma,email\nAcme,a@b.cz\n".encode("utf-8")

        result = read_csv_best_effort(content)

        self.assertEqual(result.delimiter, ",")
        self.assertEqual(result.encoding, "utf-8-sig")
        self.assertEqual(list(result.df.columns), ["firma", "email"])

    def test_cp1250_fallback_for_legacy_files(self) -> None:
        content = "město;email\nPraha;a@b.cz\n".encode("cp1250")

        result = read_csv_best_effort(content)

        self.assertEqual(result.delimiter, ";")
        self.assertEqual(result.encoding, "cp1250")
        self.assertEqual(list(result.df.columns), ["město", "email"])


if __name__ == "__main__":
    unittest.main()
