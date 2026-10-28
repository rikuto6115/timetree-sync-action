"""Ensure incomplete upstream pages fail before the sync can write."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from requests.exceptions import HTTPError

# Exercise edited bundled source, not a previously installed build.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "vendor/timetree_exporter")
)

from timetree_exporter.api.calendar import TimeTreeCalendar


class TimeTreePagesTests(unittest.TestCase):
    def setUp(self):
        self.api = TimeTreeCalendar("fake-test-session", capture_raw_responses=False)
        self.api.session = MagicMock()

    @staticmethod
    def response(body):
        response = MagicMock()
        response.json.return_value = body
        return response

    def test_full_snapshot_pages(self):
        self.api.session.get.side_effect = [
            self.response({"events": [{"id": 1}], "chunk": True, "since": 10}),
            self.response({"events": [{"id": 2}], "chunk": False}),
        ]
        self.assertEqual(self.api.get_events(1), [{"id": 1}, {"id": 2}])
        self.assertEqual(self.api.session.get.call_count, 2)

    def test_http_failure_in_any_page_aborts(self):
        for index in (0, 1):
            with self.subTest(page=index):
                responses = [
                    self.response({"events": [], "chunk": True, "since": 10}),
                    self.response({"events": [], "chunk": False}),
                ]
                responses[index].raise_for_status.side_effect = HTTPError("Test error")
                self.api.session.get.side_effect = responses
                with self.assertRaises(HTTPError):
                    self.api.get_events(1)

    def test_incomplete_payloads_rejected(self):
        for payload in (
            {},
            [],
            {"events": [], "chunk": "true"},
            {"events": [], "chunk": True},
            {"events": None, "chunk": False},
        ):
            with self.subTest(payload=payload):
                self.api.session.get.return_value = self.response(payload)
                with self.assertRaises(ValueError):
                    self.api.get_events(1)

    def test_repeated_cursor_aborts(self):
        self.api.session.get.return_value = self.response(
            {
                "events": [],
                "chunk": True,
                "since": 10,
            }
        )
        with self.assertRaisesRegex(ValueError, "cursor did not advance"):
            self.api.get_events(1)
        self.assertEqual(self.api.session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
