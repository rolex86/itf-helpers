from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.jobs import append_job_history, load_job_history, summarize_job_alerts


class JobsTests(unittest.TestCase):
    def test_append_and_load_job_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "jobs.jsonl"
            append_job_history({"mode": "csv_fallback", "status": "ok"}, path=path)
            append_job_history({"mode": "api_safe_import", "status": "failed"}, path=path)

            rows = load_job_history(path=path, limit=10)

            self.assertEqual(len(rows), 2)
            self.assertIn("timestamp", rows[0])

    def test_summarize_job_alerts(self) -> None:
        rows = [
            {"status": "ok"},
            {"status": "failed"},
            {"status": "error"},
            {"status": "ok"},
        ]

        summary = summarize_job_alerts(rows)

        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["recent_failures"], 2)
        self.assertAlmostEqual(summary["failure_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
