"""Offline safety checks: all calendar I/O is mocked, no credentials required."""

import ast
import sys
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "vendor/timetree_exporter")
)

from timetree_exporter.event import TimeTreeEvent
from timetree_exporter.formatter import ICalEventFormatter

import sync as sync_module
from config import Config
from gcalendar import GoogleCalendarClient
from models import Event

SOURCE = "source-calendar"
DEST = "destination-calendar"


def raw_event(event_id="source-1", **overrides):
    tz = ZoneInfo("Asia/Tokyo")
    value = {
        "id": event_id,
        "title": "Test event",
        "start_at": int(datetime(2026, 10, 28, 10, tzinfo=tz).timestamp() * 1000),
        "end_at": int(datetime(2026, 10, 28, 11, tzinfo=tz).timestamp() * 1000),
        "start_timezone": "Asia/Tokyo",
        "end_timezone": "Asia/Tokyo",
        "all_day": False,
        "location": "Room A",
        "note": "Source note",
    }
    value.update(overrides)
    return value


def google_event(raw=None, **overrides):
    value = Event.from_timetree(raw or raw_event()).to_google(SOURCE)
    value.update(id="google-1", etag='"version-1"', status="confirmed")
    value.update(overrides)
    return value


class EventTests(unittest.TestCase):
    def test_all_day_single_and_multiple_days_have_exclusive_end(self):
        # Matches bundled ICalEventFormatter: source end day is inclusive.
        tz = ZoneInfo("Asia/Tokyo")
        for final_day, exclusive in ((28, "2026-10-29"), (30, "2026-10-31")):
            with self.subTest(final_day=final_day):
                event = Event.from_timetree(
                    raw_event(
                        all_day=True,
                        start_at=int(
                            datetime(2026, 10, 28, tzinfo=tz).timestamp() * 1000
                        ),
                        end_at=int(
                            datetime(2026, 10, final_day, tzinfo=tz).timestamp() * 1000
                        ),
                    )
                )
                body = event.to_google(SOURCE)
                self.assertEqual(body["start"], {"date": "2026-10-28"})
                self.assertEqual(body["end"], {"date": exclusive})

    def test_all_day_end_matches_bundled_exporter_contract(self):
        raw = raw_event(all_day=True)
        expected = (
            datetime.strptime(
                ICalEventFormatter(TimeTreeEvent.from_dict(raw))
                .dtend.to_ical()
                .decode(),
                "%Y%m%d",
            )
            .date()
            .isoformat()
        )
        self.assertEqual(
            Event.from_timetree(raw).to_google(SOURCE)["end"]["date"], expected
        )

    def test_actual_source_timezones_and_offset(self):
        event = Event.from_timetree(
            raw_event(start_timezone="America/New_York", end_timezone="Europe/London")
        )
        body = event.to_google(SOURCE)
        self.assertEqual(body["start"]["timeZone"], "America/New_York")
        self.assertEqual(body["end"]["timeZone"], "Europe/London")
        self.assertEqual(
            datetime.fromisoformat(body["start"]["dateTime"]).timestamp() * 1000,
            raw_event()["start_at"],
        )

    def test_dst_fold_duration_validated_as_instants(self):
        tz = ZoneInfo("America/New_York")
        for start_fold, end_fold, valid in ((0, 1, True), (1, 0, False)):
            raw = raw_event(
                start_timezone="America/New_York",
                end_timezone="America/New_York",
                start_at=int(
                    datetime(2026, 11, 1, 1, 15, tzinfo=tz, fold=start_fold).timestamp()
                    * 1000
                ),
                end_at=int(
                    datetime(2026, 11, 1, 1, 45, tzinfo=tz, fold=end_fold).timestamp()
                    * 1000
                ),
            )
            if valid:
                Event.from_timetree(raw)
            else:
                with self.assertRaises(ValueError):
                    Event.from_timetree(raw)

    def test_equivalent_rfc3339_offsets_do_not_update(self):
        event = Event.from_timetree(raw_event())
        existing = google_event()
        existing["start"]["dateTime"] = "2026-10-28T01:00:00Z"
        self.assertTrue(event.equals_google(existing, SOURCE))

    def test_missing_optional_fields_preserved_explicit_empty_cleared(self):
        raw = raw_event()
        del raw["location"]
        del raw["note"]
        self.assertEqual(
            Event.from_timetree(raw).google_patch(google_event(), SOURCE), {}
        )
        body = Event.from_timetree(raw_event(location="", note=None)).google_patch(
            google_event(), SOURCE
        )
        self.assertEqual(body, {"location": "", "description": ""})

    def test_conversion_clears_incompatible_date_representation_only(self):
        raw = raw_event(all_day=True)
        body = Event.from_timetree(raw).google_patch(google_event(), SOURCE)
        self.assertEqual(body["start"]["dateTime"], None)
        self.assertEqual(body["end"]["timeZone"], None)
        self.assertEqual(body["end"]["date"], "2026-10-29")
        reverse = Event.from_timetree(raw_event()).google_patch(
            google_event(raw), SOURCE
        )
        self.assertIsNone(reverse["start"]["date"])
        self.assertIsNone(reverse["end"]["date"])

    def test_invalid_records_rejected(self):
        cases = [
            {"id": ""},
            {"id": None},
            {"id": True},
            {"title": None},
            {"title": ""},
            {"all_day": "false"},
            {"start_at": "bad"},
            {"end_at": 0},
            {"start_timezone": "Not/A_Zone"},
            {"location": {}},
        ]
        for overrides in cases:
            with (
                self.subTest(overrides=overrides),
                self.assertRaises((ValueError, KeyError, TypeError)),
            ):
                Event.from_timetree(raw_event(**overrides))

    def test_numeric_ids_normalized(self):
        self.assertEqual(Event.from_timetree(raw_event(event_id=123)).id, "123")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(
            Config, TIMETREE_CALENDAR_CODE=SOURCE, GOOGLE_CALENDAR_ID=DEST
        )
        self.config.start()
        self.addCleanup(self.config.stop)
        self.source_cls = patch.object(sync_module, "TimeTree")
        self.source = self.source_cls.start().return_value
        self.addCleanup(self.source_cls.stop)
        self.google_cls = patch.object(sync_module, "GoogleCalendarClient")
        self.google_type = self.google_cls.start()
        self.google = self.google_type.return_value
        self.addCleanup(self.google_cls.stop)
        self.source.get_events.return_value = [raw_event()]
        self.google.list_events.return_value = []

    def assert_no_writes(self):
        self.google.create_event.assert_not_called()
        self.google.update_event.assert_not_called()
        self.google.delete_event.assert_not_called()

    def test_new_event_created_once_and_repeat_is_noop(self):
        sync_module.sync()
        self.google.create_event.assert_called_once_with(
            DEST, Event.from_timetree(raw_event()).to_google(SOURCE)
        )
        self.google.reset_mock()
        self.google.list_events.return_value = [google_event()]
        sync_module.sync()
        self.assert_no_writes()

    def test_only_changed_managed_fields_patched(self):
        existing = google_event(summary="Old title", location="Old room")
        existing.update(
            attendees=[{"email": "person@example.test"}],
            reminders={"useDefault": False},
            colorId="4",
            visibility="private",
            guestsCanModify=False,
            attachments=[{"fileUrl": "https://example.test/file"}],
            conferenceData={"conferenceId": "keep"},
        )
        existing["extendedProperties"]["private"]["manual-note"] = "keep"
        original = deepcopy(existing)
        self.google.list_events.return_value = [existing]
        sync_module.sync()
        self.google.update_event.assert_called_once_with(
            DEST,
            "google-1",
            {"summary": "Test event", "location": "Room A"},
            etag='"version-1"',
        )
        self.assertEqual(existing, original)
        self.google.delete_event.assert_not_called()

    def test_missing_source_and_manual_events_never_deleted(self):
        native = {"id": "native", "summary": "Personal"}
        removed = google_event(raw_event("removed"))
        other = google_event()
        other["extendedProperties"]["private"]["timetree_calendar_code"] = "other"
        self.google.list_events.return_value = [native, removed, other, google_event()]
        sync_module.sync()
        self.assert_no_writes()

    def test_duplicate_google_events_preserved(self):
        self.google.list_events.return_value = [
            google_event(summary="Changed copy 1"),
            google_event(id="google-2", summary="Changed copy 2"),
        ]
        sync_module.sync()
        self.assert_no_writes()

    def test_legacy_unscoped_matches_preserved_not_migrated_or_duplicated(self):
        existing = google_event(summary="Legacy")
        existing["extendedProperties"]["private"] = {"timetree_id": "source-1"}
        self.google.list_events.return_value = [existing]
        sync_module.sync()
        self.assert_no_writes()

    def test_empty_source_no_google_access_or_changes(self):
        self.source.get_events.return_value = []
        sync_module.sync()
        self.google_type.assert_not_called()
        self.assert_no_writes()

    def test_entire_source_validated_before_any_writes(self):
        malformed = raw_event("invalid")
        del malformed["title"]
        for records in ([raw_event(), malformed], [raw_event(), raw_event()], None, {}):
            with self.subTest(records=records):
                self.source.get_events.return_value = records
                with self.assertRaises((ValueError, TypeError)):
                    sync_module.sync()
                self.google_type.assert_not_called()
                self.assert_no_writes()

    def test_blank_titles_and_invalid_duration_preserve_whole_records(self):
        blank = raw_event("blank", title="   ", note="private note")
        invalid = raw_event("invalid", end_at=0)
        self.source.get_events.return_value = [raw_event(), blank, invalid]
        self.google.list_events.return_value = [
            google_event(),
            google_event(raw_event("blank")),
            google_event(raw_event("invalid")),
        ]
        with self.assertLogs(sync_module.logger, level="INFO") as logs:
            sync_module.sync()
        self.assert_no_writes()
        self.assertTrue(any("validated=1 withheld=2" in line for line in logs.output))
        self.assertNotIn("private note", "\n".join(logs.output))

    def test_withheld_record_cannot_hide_duplicate_source_id(self):
        self.source.get_events.return_value = [raw_event(), raw_event(title="")]
        with self.assertRaisesRegex(ValueError, "Duplicate TimeTree IDs"):
            sync_module.sync()
        self.google_type.assert_not_called()
        self.assert_no_writes()

    def test_nonstring_title_is_still_hard_failure(self):
        self.source.get_events.return_value = [raw_event(title=None)]
        with self.assertRaises(ValueError):
            sync_module.sync()
        self.google_type.assert_not_called()
        self.assert_no_writes()

    def test_failed_source_fetch_does_not_touch_google(self):
        self.source.get_events.side_effect = RuntimeError("Failed page")
        with self.assertRaises(RuntimeError):
            sync_module.sync()
        self.google_type.assert_not_called()
        self.assert_no_writes()

    def test_failed_google_list_does_not_write(self):
        self.google.list_events.side_effect = RuntimeError("Failed page")
        with self.assertRaises(RuntimeError):
            sync_module.sync()
        self.assert_no_writes()

    def test_partial_source_retains_unseen_google_events(self):
        self.google.list_events.return_value = [google_event(raw_event("not-returned"))]
        sync_module.sync()
        self.google.create_event.assert_called_once()
        self.google.delete_event.assert_not_called()
        self.google.update_event.assert_not_called()

    def test_dry_run_has_counts_and_never_writes(self):
        self.source.get_events.return_value = [raw_event(), raw_event("new")]
        self.google.list_events.return_value = [google_event(summary="old")]
        with self.assertLogs(sync_module.logger, level="INFO") as logs:
            sync_module.sync(dry_run=True)
        self.assertTrue(any("create=1 update=1" in line for line in logs.output))
        self.assert_no_writes()

    def test_without_etag_or_cancelled_preserved(self):
        for overrides in ({"etag": None}, {"status": "cancelled"}):
            with self.subTest(overrides=overrides):
                self.google.list_events.return_value = [
                    google_event(summary="old", **overrides)
                ]
                sync_module.sync()
                self.assert_no_writes()

    def test_numeric_existing_id_matches_source(self):
        existing = google_event(raw_event(123))
        existing["extendedProperties"]["private"]["timetree_id"] = 123
        self.source.get_events.return_value = [raw_event(123)]
        self.google.list_events.return_value = [existing]
        sync_module.sync()
        self.assert_no_writes()

    def test_conflict_aborts_without_retry_or_following_writes(self):
        self.source.get_events.return_value = [raw_event(), raw_event("new")]
        self.google.list_events.return_value = [google_event(summary="old")]
        self.google.update_event.side_effect = RuntimeError("412 precondition failed")
        with self.assertRaisesRegex(RuntimeError, "412"):
            sync_module.sync()
        self.google.update_event.assert_called_once()
        self.google.create_event.assert_not_called()
        self.google.delete_event.assert_not_called()

    def test_no_delete_or_full_replacement_methods_remain(self):
        root = Path(__file__).resolve().parents[1]
        for file in (root / "src").rglob("*.py"):
            tree = ast.parse(file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    self.assertNotEqual(node.name, "delete_event", file)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ("delete", "delete_event"), file)


class GoogleClientTests(unittest.TestCase):
    def setUp(self):
        self.client = GoogleCalendarClient.__new__(GoogleCalendarClient)
        self.client._service = MagicMock()

    def test_patch_with_etag_not_replace_or_delete(self):
        request = self.client._service.events.return_value.patch.return_value
        request.headers = {}
        self.client.update_event(DEST, "google-1", {"summary": "new"}, etag='"v1"')
        self.client._service.events.return_value.patch.assert_called_once_with(
            calendarId=DEST, eventId="google-1", body={"summary": "new"}
        )
        self.assertEqual(request.headers["If-Match"], '"v1"')
        self.client._service.events.return_value.update.assert_not_called()
        self.client._service.events.return_value.delete.assert_not_called()
        request.execute.assert_called_once()

    def test_google_invalid_pages_and_repeated_cursor_fail_closed(self):
        for response in ([], {"items": None}, {"items": [], "nextPageToken": "same"}):
            self.client._service.events.return_value.list.return_value.execute.return_value = response
            with self.assertRaises((ValueError, TypeError)):
                self.client.list_events(DEST)

    def test_google_pagination(self):
        self.client._service.events.return_value.list.return_value.execute.side_effect = [
            {"items": [{"id": "a"}], "nextPageToken": "next"},
            {"items": [{"id": "b"}]},
        ]
        self.assertEqual(self.client.list_events(DEST), [{"id": "a"}, {"id": "b"}])
        self.assertEqual(self.client._service.events.return_value.list.call_count, 2)


if __name__ == "__main__":
    unittest.main()
