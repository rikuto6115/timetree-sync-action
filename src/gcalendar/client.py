import json
from typing import ClassVar

from google.oauth2 import service_account
from googleapiclient.discovery import build


class GoogleCalendarClient:
    SCOPES: ClassVar[list[str]] = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, credentials_json: str):
        credentials_info = json.loads(credentials_json)

        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=self.SCOPES,
        )

        self._service = build(
            "calendar",
            "v3",
            credentials=credentials,
        )

    def get_calendar(self, calendar_id: str):
        return self._service.calendars().get(calendarId=calendar_id).execute()

    def create_event(self, calendar_id: str, event: dict):
        return (
            self._service.events()
            .insert(
                calendarId=calendar_id,
                body=event,
            )
            .execute()
        )

    def list_events(self, calendar_id: str):
        events = []
        page_token = None
        seen_tokens = set()
        while True:
            response = (
                self._service.events()
                .list(
                    calendarId=calendar_id,
                    singleEvents=True,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("items", []), list
            ):
                raise TypeError("Invalid Google Calendar event page")
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
            if not isinstance(page_token, str) or page_token in seen_tokens:
                raise ValueError("Google Calendar pagination cursor did not advance")
            seen_tokens.add(page_token)
        return events

    def update_event(self, calendar_id: str, event_id: str, event: dict, *, etag: str):
        # PATCH leaves reminders, guests, sharing flags, attachments, and private
        # metadata unchanged. Reject concurrent edits rather than overwriting them.
        request = self._service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=event,
        )
        request.headers["If-Match"] = etag
        return request.execute()
