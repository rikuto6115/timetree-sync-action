from collections import Counter

from config import Config
from gcalendar import GoogleCalendarClient
from logger import logger
from models import Event
from timetree import TimeTree


def _private_properties(google_event: dict) -> dict:
    return (google_event.get("extendedProperties") or {}).get("private") or {}


def _is_managed_google_event(google_event: dict, calendar_code: str) -> bool:
    """Require explicit ownership; a legacy ID alone cannot prove the source."""
    props = _private_properties(google_event)
    return bool(props.get("timetree_id")) and (
        props.get("sync_source") == Event.SYNC_SOURCE
        and props.get("timetree_calendar_code") == calendar_code
    )


def sync(*, dry_run: bool = False):
    client = TimeTree()
    client.login(Config.TIMETREE_EMAIL, Config.TIMETREE_PASSWORD)
    calendar = client.get_calendar(Config.TIMETREE_CALENDAR_CODE)
    logger.info("Selected TimeTree calendar")

    raw_events = client.get_events(calendar)
    if not isinstance(raw_events, list):
        raise TypeError("Invalid TimeTree event list; no changes made")
    # Validate the entire snapshot BEFORE any Google writes. Never treat a
    # missing source event as a deletion, including an empty/partial response.
    try:
        events = [Event.from_timetree(raw) for raw in raw_events]
    except (ValueError, TypeError, KeyError, OverflowError):
        # Diagnose schema/placeholder records without logging private event data.
        reasons = Counter()
        shapes = Counter()
        for raw in raw_events:
            try:
                Event.from_timetree(raw)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                reason = (
                    str(exc)
                    if type(exc) in (ValueError, TypeError)
                    else type(exc).__name__
                )
                allowed = (
                    "Missing TimeTree title; refusing to clear an event",
                    "Incomplete TimeTree event; no changes made",
                    "Invalid TimeTree event duration",
                    "Invalid TimeTree event ID",
                    "Invalid TimeTree all_day value",
                    "Invalid optional TimeTree event field",
                    "Invalid TimeTree event timestamp",
                    "Invalid TimeTree event",
                )
                reasons[reason if reason in allowed else type(exc).__name__] += 1
                if isinstance(raw, dict):
                    for key in ("deleted_at", "deactivated_at", "deleted", "cancelled"):
                        if raw.get(key):
                            shapes[key] += 1
                    if raw.get("category") == 2:
                        shapes["memo_category"] += 1
                    if raw.get("title") is None:
                        shapes["null_title"] += 1
                    elif isinstance(raw.get("title"), str) and not raw["title"].strip():
                        shapes["blank_title"] += 1
        logger.error(
            "Source validation blocked: total=%d reasons=%s shapes=%s",
            len(raw_events),
            dict(reasons),
            dict(shapes),
        )
        raise

    if len({event.id for event in events}) != len(events):
        raise ValueError("Duplicate TimeTree IDs; no changes made")
    if not events:
        logger.warning("No source events returned; Google Calendar left unchanged")
        return

    google = GoogleCalendarClient(Config.GOOGLE_SERVICE_ACCOUNT_JSON)
    google_events = google.list_events(Config.GOOGLE_CALENDAR_ID)
    logger.info("Connected to Google Calendar; add/update only, deletion disabled")

    google_event_map: dict[str, list[dict]] = {}
    ambiguous_ids = set()
    for google_event in google_events:
        props = _private_properties(google_event)
        timetree_id = props.get("timetree_id")
        if not timetree_id:
            continue
        if _is_managed_google_event(google_event, Config.TIMETREE_CALENDAR_CODE):
            if not google_event.get("id"):
                raise ValueError("Invalid managed Google event; no changes made")
            google_event_map.setdefault(str(timetree_id), []).append(google_event)
        elif not props.get("sync_source") or not props.get("timetree_calendar_code"):
            # Unscoped legacy markers may belong to another calendar. Do not
            # overwrite them or create another copy until ownership is known.
            ambiguous_ids.add(str(timetree_id))

    operations = []
    created_count = updated_count = skipped_count = preserved_count = 0
    for event in events:
        matches = google_event_map.get(event.id, [])
        if event.id in ambiguous_ids or len(matches) > 1:
            # Preserve every duplicate; do not guess which copy a person edited.
            preserved_count += 1
            continue
        if not matches:
            operations.append(
                ("create", None, event.to_google(Config.TIMETREE_CALENDAR_CODE), None)
            )
            created_count += 1
            continue
        google_event = matches[0]
        patch = event.google_patch(google_event, Config.TIMETREE_CALENDAR_CODE)
        if not patch:
            skipped_count += 1
        elif not google_event.get("etag") or google_event.get("status") == "cancelled":
            preserved_count += 1
        else:
            operations.append(
                ("update", google_event["id"], patch, google_event["etag"])
            )
            updated_count += 1

    logger.info(
        "Sync plan: create=%d update=%d skip=%d preserve=%d delete=0",
        created_count,
        updated_count,
        skipped_count,
        preserved_count,
    )
    if preserved_count:
        logger.warning(
            "Ambiguous, duplicate, cancelled, or unversioned matches were left unchanged"
        )
    if dry_run:
        logger.info("Dry run complete; no Google Calendar changes made")
        return
    for action, event_id, body, etag in operations:
        if action == "create":
            google.create_event(Config.GOOGLE_CALENDAR_ID, body)
        else:
            google.update_event(Config.GOOGLE_CALENDAR_ID, event_id, body, etag=etag)
    logger.info(
        "Sync complete: created=%d updated=%d deleted=0", created_count, updated_count
    )
