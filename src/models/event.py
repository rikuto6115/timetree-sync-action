from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo


@dataclass
class Event:
    SYNC_SOURCE: ClassVar[str] = "timetree-sync-action"

    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    location: str | None = None
    description: str | None = None
    # Missing optional keys mean unknown, not an instruction to erase Google data.
    provided_fields: frozenset[str] = field(
        default_factory=lambda: frozenset({"location", "note"}), repr=False
    )

    @classmethod
    def from_timetree(cls, event: dict):
        if not isinstance(event, dict):
            raise TypeError("Invalid TimeTree event")
        required = {
            "id",
            "title",
            "start_at",
            "end_at",
            "start_timezone",
            "end_timezone",
            "all_day",
        }
        if not required.issubset(event):
            raise ValueError("Incomplete TimeTree event; no changes made")
        event_id = event["id"]
        if (
            isinstance(event_id, bool)
            or not isinstance(event_id, (str, int))
            or not str(event_id).strip()
        ):
            raise ValueError("Invalid TimeTree event ID")
        if not isinstance(event["title"], str) or not event["title"].strip():
            raise ValueError("Missing TimeTree title; refusing to clear an event")
        if not isinstance(event["all_day"], bool):
            raise TypeError("Invalid TimeTree all_day value")
        for key in ("location", "note"):
            if event.get(key) is not None and not isinstance(event[key], str):
                raise ValueError("Invalid optional TimeTree event field")
        for key in ("start_at", "end_at"):
            if isinstance(event[key], bool) or not isinstance(event[key], (int, float)):
                raise TypeError("Invalid TimeTree event timestamp")
        start = datetime.fromtimestamp(
            event["start_at"] / 1000, tz=ZoneInfo(event["start_timezone"])
        )
        end = datetime.fromtimestamp(
            event["end_at"] / 1000, tz=ZoneInfo(event["end_timezone"])
        )
        if (event["all_day"] and end.date() < start.date()) or (
            not event["all_day"] and end.timestamp() <= start.timestamp()
        ):
            raise ValueError("Invalid TimeTree event duration")
        return cls(
            id=str(event_id),
            title=event["title"],
            start=start,
            end=end,
            all_day=event["all_day"],
            location=event.get("location"),
            description=event.get("note"),
            provided_fields=frozenset(event),
        )

    def to_google(self, calendar_code: str) -> dict:
        event = {
            "summary": self.title,
            "extendedProperties": {
                "private": {
                    "sync_source": self.SYNC_SOURCE,
                    "timetree_calendar_code": calendar_code,
                    "timetree_id": self.id,
                }
            },
        }
        if self.all_day:
            event["start"] = {"date": self.start.date().isoformat()}
            # TimeTree's last day is inclusive (see the bundled exporter).
            # Google Calendar requires an exclusive end date, even for one day.
            event["end"] = {"date": (self.end.date() + timedelta(days=1)).isoformat()}
        else:
            event["start"] = {
                "dateTime": self.start.isoformat(),
                "timeZone": self.start.tzinfo.key,
            }
            event["end"] = {
                "dateTime": self.end.isoformat(),
                "timeZone": self.end.tzinfo.key,
            }
        if "location" in self.provided_fields:
            event["location"] = self.location or ""
        if "note" in self.provided_fields:
            event["description"] = self.description or ""
        return event

    @staticmethod
    def _same_time(desired: dict, existing: dict) -> bool:
        if "date" in desired:
            return desired["date"] == existing.get("date") and not existing.get(
                "dateTime"
            )
        if existing.get("date") or not existing.get("dateTime"):
            return False
        try:
            same_instant = datetime.fromisoformat(
                desired["dateTime"]
            ) == datetime.fromisoformat(existing["dateTime"])
        except (TypeError, ValueError):
            return False
        return same_instant and desired["timeZone"] == existing.get("timeZone")

    def google_patch(self, google_event: dict, calendar_code: str) -> dict:
        """Only write source-owned fields; retain all other Google settings."""
        desired = self.to_google(calendar_code)
        patch = {}
        for key in ("summary", "location", "description"):
            if key in desired and desired[key] != (google_event.get(key) or ""):
                patch[key] = desired[key]
        for key in ("start", "end"):
            existing = google_event.get(key) or {}
            if not self._same_time(desired[key], existing):
                patch[key] = dict(desired[key])
                # PATCH merges nested objects: explicitly clear the other date
                # representation when switching between timed and all-day.
                if self.all_day:
                    patch[key].update(dateTime=None, timeZone=None)
                else:
                    patch[key]["date"] = None
        return patch

    def equals_google(self, google_event: dict, calendar_code: str) -> bool:
        return not self.google_patch(google_event, calendar_code)
